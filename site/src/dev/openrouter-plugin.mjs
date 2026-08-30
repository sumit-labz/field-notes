// Dev-server-only OpenRouter proxy for the internal fragment inbox.
//
// SECURITY / ARCHITECTURE:
// This is a Vite plugin whose middleware only runs during `npm run dev`. It is
// NOT part of `astro build` output — the deployed static GitHub Pages site has
// no server and cannot transcribe. That is deliberate: the OpenRouter API key
// is read here, server-side, from site/.env (gitignored) and is NEVER sent to
// the browser or embedded in the build. The browser only ever calls the local
// endpoints below; it never sees the key.
//
// Endpoints (POST, localhost only):
//   /api/transcribe   { id }   -> { text }   audio fragment -> raw transcript
//   /api/fix-grammar  { text } -> { text }   raw text -> cleaned transcript
//
// Both require OPENROUTER_API_KEY in site/.env; without it they return 501 with
// a clear message so the UI can say "add your key" rather than silently break.

import { loadEnv } from 'vite';
import { readFile, mkdtemp, unlink } from 'node:fs/promises';
import { existsSync, readdirSync, statSync, createReadStream, createWriteStream } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath } from 'node:url';

const OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions';
// gpt-4o-mini-transcribe is a transcription model — it uses the dedicated
// OpenAI-compatible audio endpoint (multipart upload), NOT chat/completions.
const TRANSCRIBE_URL = 'https://openrouter.ai/api/v1/audio/transcriptions';
// Models chosen by the maintainer (see the fragment inbox spec):
const STT_MODEL = 'openai/gpt-4o-mini-transcribe'; // GPT-4o Mini Transcribe
const FIX_MODEL = 'deepseek/deepseek-chat'; // DeepSeek V3
// Approximate USD→INR for showing per-call cost in rupees. OpenRouter bills in
// USD credits; this is a display convenience, not an exact conversion.
const INR_PER_USD = 87.5;

// The grammar/cleanup prompt, verbatim from the maintainer. Sent as the system
// message; the raw transcript is the user message.
const FIX_SYSTEM_PROMPT = `You are an expert transcript editor specializing in Indian English audio cleanups.

Task:
Fix spelling mistakes, grammar, punctuation, and audio-to-text misspellings in the transcript below.

Rules:
1. Return ONLY the corrected raw text.
2. DO NOT add intro/outro greetings (e.g., "Here is the revised text").
3. DO NOT add Markdown code blocks (\`\`\`), watermarks, or extra conversational notes.
4. Keep the original structure, technical terminology, and Indian English idioms intact. Do NOT rewrite the natural speaking style into formal US/UK English.
5. Fix phonetic transcription errors (e.g., misheard names, missing words).`;

// site/src/dev/ -> repo root is three levels up.
const repoRoot = fileURLToPath(new URL('../../../', import.meta.url));
const audioDir = path.join(repoRoot, 'media', 'audio');

// scripts/delete_fragment.py needs the bot's Python deps (boto3, PyYAML, …),
// which live in a venv, not system Python. Resolve the interpreter: an explicit
// INGEST_PYTHON wins, then a scripts/.venv or repo-root .venv, else bare
// `python`. The endpoint surfaces a clear error if the chosen one lacks deps.
function resolvePython() {
  if (process.env.INGEST_PYTHON && existsSync(process.env.INGEST_PYTHON)) {
    return process.env.INGEST_PYTHON;
  }
  const candidates = [
    path.join(repoRoot, 'scripts', '.venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, 'scripts', '.venv', 'bin', 'python'),
    path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, '.venv', 'bin', 'python'),
  ];
  return candidates.find((p) => existsSync(p)) || 'python';
}

// Serve a locally-committed media file (media/audio|video/…) straight from disk
// so it's always current — new files that arrive via pull/ingest while the dev
// server runs play without a restart. Supports Range requests so <audio>/<video>
// scrubbing works. Only paths under media/ are allowed (no traversal).
const MEDIA_CONTENT_TYPES = {
  oga: 'audio/ogg', ogg: 'audio/ogg', opus: 'audio/ogg', mp3: 'audio/mpeg',
  m4a: 'audio/mp4', aac: 'audio/aac', wav: 'audio/wav', flac: 'audio/flac',
  mp4: 'video/mp4', webm: 'video/webm', mov: 'video/quicktime', mkv: 'video/x-matroska',
};

function serveLocalMedia(req, res) {
  // connect strips the '/__localmedia' mount prefix, leaving e.g.
  // "/media/audio/x.oga".
  let rel;
  try {
    rel = decodeURIComponent((req.url || '').split('?')[0]).replace(/^\/+/, '');
  } catch {
    res.statusCode = 400;
    res.end('bad path');
    return;
  }
  if (!rel.startsWith('media/') || rel.includes('..')) {
    res.statusCode = 400;
    res.end('forbidden path');
    return;
  }
  const abs = path.join(repoRoot, rel);
  let stat;
  try {
    stat = statSync(abs);
  } catch {
    res.statusCode = 404;
    res.end('not found');
    return;
  }
  const ext = rel.slice(rel.lastIndexOf('.') + 1).toLowerCase();
  res.setHeader('Content-Type', MEDIA_CONTENT_TYPES[ext] || 'application/octet-stream');
  res.setHeader('Accept-Ranges', 'bytes');

  const range = req.headers.range;
  if (range) {
    const m = /bytes=(\d*)-(\d*)/.exec(range);
    let start = m && m[1] ? parseInt(m[1], 10) : 0;
    let end = m && m[2] ? parseInt(m[2], 10) : stat.size - 1;
    if (Number.isNaN(start)) start = 0;
    if (Number.isNaN(end)) end = stat.size - 1;
    end = Math.min(end, stat.size - 1);
    if (start > end) {
      res.statusCode = 416;
      res.setHeader('Content-Range', `bytes */${stat.size}`);
      res.end();
      return;
    }
    res.statusCode = 206;
    res.setHeader('Content-Range', `bytes ${start}-${end}/${stat.size}`);
    res.setHeader('Content-Length', end - start + 1);
    createReadStream(abs, { start, end }).pipe(res);
  } else {
    res.setHeader('Content-Length', stat.size);
    createReadStream(abs).pipe(res);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Run a command to completion, capturing output. Never rejects — returns a
// {code, stdout, stderr} record (code -1 means the process couldn't start).
function runCmd(cmd, args) {
  return new Promise((resolve) => {
    let proc;
    try {
      proc = spawn(cmd, args, { cwd: repoRoot });
    } catch (err) {
      resolve({ code: -1, stdout: '', stderr: String(err.message) });
      return;
    }
    const out = [];
    const errOut = [];
    proc.on('error', (err) =>
      resolve({ code: -1, stdout: '', stderr: err.code === 'ENOENT' ? `${cmd} not found` : err.message })
    );
    proc.stdout?.on('data', (c) => out.push(c));
    proc.stderr?.on('data', (c) => errOut.push(c));
    proc.on('close', (code) =>
      resolve({ code, stdout: Buffer.concat(out).toString(), stderr: Buffer.concat(errOut).toString() })
    );
  });
}

// Trigger the Telegram ingest GitHub Action, wait for that run to finish, then
// pull its commit. Uses the user's `gh` auth (workflow scope) — no stored token.
// Bounded so the request can't hang forever; on timeout it tells the user to
// just use "pull latest" shortly, since the run may still be finishing.
const FETCH_TIMEOUT_MS = 180_000;

async function fetchFromTelegram() {
  // Small backward skew so the run we dispatch reliably counts as "after start".
  const startIso = new Date(Date.now() - 10_000).toISOString();

  const dispatch = await runCmd('gh', ['workflow', 'run', 'ingest.yml', '--ref', 'main']);
  if (dispatch.code !== 0) {
    const msg = (dispatch.stderr || dispatch.stdout).trim();
    if (dispatch.code === -1) {
      return { ok: false, error: 'GitHub CLI (gh) not found on PATH. Install it and run `gh auth login`.' };
    }
    return { ok: false, error: `could not dispatch the ingest workflow: ${msg}` };
  }

  const deadline = Date.now() + FETCH_TIMEOUT_MS;
  let run = null;
  while (Date.now() < deadline) {
    await sleep(4000);
    const list = await runCmd('gh', [
      'run', 'list', '--workflow', 'ingest.yml', '--event', 'workflow_dispatch',
      '--limit', '5', '--json', 'databaseId,status,conclusion,createdAt',
    ]);
    if (list.code !== 0) continue;
    let runs;
    try {
      runs = JSON.parse(list.stdout);
    } catch {
      continue;
    }
    const candidate = runs
      .filter((r) => r.createdAt >= startIso)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
    if (candidate) {
      run = candidate;
      if (candidate.status === 'completed') break;
    }
  }

  if (!run) {
    return { ok: false, error: 'ingest was triggered but its run did not appear in time. Try “pull latest” in a minute.' };
  }
  if (run.status !== 'completed') {
    return { ok: false, error: 'ingest is still running on GitHub. Click “pull latest” once it finishes.' };
  }
  if (run.conclusion !== 'success') {
    return { ok: false, error: `the ingest run finished as “${run.conclusion}” — check the Actions log.` };
  }

  const pull = await runGitPull();
  return { ...pull, conclusion: run.conclusion };
}

// Fast-forward the repo to origin so fragments the Telegram bot committed on
// GitHub appear locally. --ff-only keeps it safe: it never makes a merge commit
// and fails loudly if the local branch has diverged.
function runGitPull() {
  return new Promise((resolve) => {
    let proc;
    try {
      proc = spawn('git', ['pull', '--ff-only'], { cwd: repoRoot });
    } catch (err) {
      resolve({ ok: false, error: `could not start git: ${err.message}` });
      return;
    }
    const out = [];
    const errOut = [];
    proc.on('error', (err) => resolve({ ok: false, error: `git not found: ${err.message}` }));
    proc.stdout.on('data', (c) => out.push(c));
    proc.stderr.on('data', (c) => errOut.push(c));
    proc.on('close', (code) => {
      const stdout = Buffer.concat(out).toString().trim();
      const stderr = Buffer.concat(errOut).toString().trim();
      if (code !== 0) {
        resolve({ ok: false, error: stderr || stdout || `git pull exited ${code}` });
        return;
      }
      const updated = !/already up to date/i.test(stdout);
      resolve({ ok: true, updated, summary: stdout.split('\n').slice(-3).join(' ').slice(0, 300) });
    });
  });
}

// Run scripts/delete_fragment.py <id> --json and return its parsed JSON result.
function runDeleteScript(id) {
  return new Promise((resolve, reject) => {
    const python = resolvePython();
    const script = path.join('scripts', 'delete_fragment.py');
    let proc;
    try {
      proc = spawn(python, [script, id, '--json'], { cwd: repoRoot });
    } catch (err) {
      reject(new Error(`could not start Python (${python}): ${err.message}`));
      return;
    }
    const out = [];
    const errOut = [];
    proc.on('error', (err) => {
      reject(
        new Error(
          err.code === 'ENOENT'
            ? `Python not found (${python}). Set INGEST_PYTHON in site/.env, or create scripts/.venv and install scripts/requirements.txt.`
            : `failed to start Python: ${err.message}`
        )
      );
    });
    proc.stdout.on('data', (c) => out.push(c));
    proc.stderr.on('data', (c) => errOut.push(c));
    proc.on('close', () => {
      const stdout = Buffer.concat(out).toString().trim();
      const stderr = Buffer.concat(errOut).toString().trim();
      // The script prints exactly one JSON object on stdout (ok:true/false).
      const lastLine = stdout.split('\n').filter(Boolean).pop() || '';
      try {
        resolve(JSON.parse(lastLine));
      } catch {
        reject(
          new Error(
            stderr.includes('ModuleNotFoundError')
              ? `The Python at ${python} is missing the bot's dependencies. Install scripts/requirements.txt into it (or set INGEST_PYTHON).`
              : `delete script gave no JSON result. stderr: ${stderr.slice(-400) || '(none)'}`
          )
        );
      }
    });
  });
}

// Run scripts/create_fragment.py to turn a web-captured file into a fragment.
function runCreateScript(type, filePath, ext, note, noPush) {
  return new Promise((resolve, reject) => {
    const python = resolvePython();
    const args = ['scripts/create_fragment.py', '--type', type, '--file', filePath];
    if (ext) args.push('--ext', ext);
    if (note) args.push('--note', note);
    if (noPush) args.push('--no-push');
    args.push('--json');
    let proc;
    try {
      proc = spawn(python, args, { cwd: repoRoot });
    } catch (err) {
      reject(new Error(`could not start Python (${python}): ${err.message}`));
      return;
    }
    const out = [];
    const errOut = [];
    proc.on('error', (err) =>
      reject(
        new Error(
          err.code === 'ENOENT'
            ? `Python not found (${python}). Set INGEST_PYTHON in site/.env, or create scripts/.venv and install scripts/requirements.txt.`
            : `failed to start Python: ${err.message}`
        )
      )
    );
    proc.stdout.on('data', (c) => out.push(c));
    proc.stderr.on('data', (c) => errOut.push(c));
    proc.on('close', () => {
      const stdout = Buffer.concat(out).toString().trim();
      const stderr = Buffer.concat(errOut).toString().trim();
      const lastLine = stdout.split('\n').filter(Boolean).pop() || '';
      try {
        resolve(JSON.parse(lastLine));
      } catch {
        reject(
          new Error(
            stderr.includes('ModuleNotFoundError')
              ? `The Python at ${python} is missing the bot's dependencies. Install scripts/requirements.txt into it (or set INGEST_PYTHON).`
              : `create script gave no JSON result. stderr: ${stderr.slice(-400) || '(none)'}`
          )
        );
      }
    });
  });
}

const ID_RE = /^[0-9A-Za-z_-]+$/;

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json');
  res.end(body);
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => {
      data += chunk;
      if (data.length > 1_000_000) reject(new Error('request body too large'));
    });
    req.on('end', () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch (err) {
        reject(err);
      }
    });
    req.on('error', reject);
  });
}

// Find media/audio/<id>.<ext> for a given fragment id.
function findAudioFile(id) {
  if (!ID_RE.test(id)) return null;
  if (!existsSync(audioDir)) return null;
  const match = readdirSync(audioDir).find(
    (name) => name.slice(0, name.lastIndexOf('.')) === id
  );
  return match ? path.join(audioDir, match) : null;
}

// Transcribe an audio file via OpenRouter's OpenAI-compatible transcription
// endpoint (multipart upload). Accepts common containers directly (ogg/opus,
// webm, m4a, mp3, wav…) so Telegram voice notes and web recordings need no
// transcoding.
async function transcribeAudio(apiKey, buffer, filename, mime) {
  const form = new FormData();
  form.append('model', STT_MODEL);
  form.append('response_format', 'json');
  form.append('file', new Blob([buffer], { type: mime || 'application/octet-stream' }), filename);
  const resp = await fetch(TRANSCRIBE_URL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'HTTP-Referer': 'http://localhost/field-notes/internal/fragments',
      'X-Title': 'field-notes fragment inbox',
    },
    body: form,
  });
  const raw = await resp.text();
  let json;
  try {
    json = JSON.parse(raw);
  } catch {
    throw new Error(`OpenRouter transcription returned non-JSON (${resp.status}): ${raw.slice(0, 300)}`);
  }
  if (!resp.ok) {
    throw new Error(json?.error?.message || json?.error || `transcription error ${resp.status}`);
  }
  if (typeof json.text !== 'string') {
    throw new Error('transcription response had no text');
  }
  // The audio endpoint may include usage.cost (USD) when available.
  const costUsd = typeof json?.usage?.cost === 'number' ? json.usage.cost : null;
  return { text: json.text.trim(), costUsd };
}

async function callOpenRouter(apiKey, body) {
  const resp = await fetch(OPENROUTER_URL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
      // OpenRouter attribution headers (optional but recommended).
      'HTTP-Referer': 'http://localhost/field-notes/internal/fragments',
      'X-Title': 'field-notes fragment inbox',
    },
    // usage.include asks OpenRouter to return the actual credits spent.
    body: JSON.stringify({ ...body, usage: { include: true } }),
  });
  const text = await resp.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    throw new Error(`OpenRouter returned non-JSON (${resp.status}): ${text.slice(0, 300)}`);
  }
  if (!resp.ok) {
    throw new Error(json?.error?.message || `OpenRouter error ${resp.status}`);
  }
  const content = json?.choices?.[0]?.message?.content;
  if (typeof content !== 'string') {
    throw new Error('OpenRouter response had no message content');
  }
  const costUsd = typeof json?.usage?.cost === 'number' ? json.usage.cost : null;
  return { text: content.trim(), costUsd };
}

function costInr(costUsd) {
  return typeof costUsd === 'number' ? Math.round(costUsd * INR_PER_USD * 10000) / 10000 : null;
}

export function openrouterDevPlugin() {
  let apiKey = '';
  return {
    name: 'field-notes:openrouter-dev',
    apply: 'serve', // dev only — never affects `astro build`
    configResolved(config) {
      // Read the key from site/.env (config.envDir), server-side only.
      const env = loadEnv(config.mode, config.envDir, '');
      apiKey = env.OPENROUTER_API_KEY || process.env.OPENROUTER_API_KEY || '';
    },
    configureServer(server) {
      // Always-fresh local media (audio/video) for the inbox — see local-media.ts.
      server.middlewares.use('/__localmedia', (req, res) => serveLocalMedia(req, res));

      server.middlewares.use('/api/transcribe', async (req, res, next) => {
        if (req.method !== 'POST') return next();
        try {
          if (!apiKey) {
            return sendJson(res, 501, {
              error: 'OPENROUTER_API_KEY not set in site/.env — add it and restart `npm run dev`.',
            });
          }
          const { id } = await readJsonBody(req);
          if (!id) return sendJson(res, 400, { error: 'missing fragment id' });

          const file = findAudioFile(id);
          if (!file) return sendJson(res, 404, { error: `no audio file found for ${id}` });

          const ext = file.slice(file.lastIndexOf('.') + 1).toLowerCase();
          const audioBuffer = await readFile(file);
          // Some models key format off the filename extension; ".oga" is less
          // widely recognised than ".ogg", so normalise it.
          const sendName = `${path.basename(file, path.extname(file))}.${ext === 'oga' ? 'ogg' : ext}`;
          const mime = MEDIA_CONTENT_TYPES[ext] || 'application/octet-stream';
          const { text, costUsd } = await transcribeAudio(apiKey, audioBuffer, sendName, mime);
          return sendJson(res, 200, { text, model: STT_MODEL, costInr: costInr(costUsd) });
        } catch (err) {
          return sendJson(res, 502, { error: String(err?.message || err) });
        }
      });

      // Models + the default (editable) grammar prompt, for the settings panel.
      server.middlewares.use('/api/config', (req, res, next) => {
        if (req.method !== 'GET') return next();
        return sendJson(res, 200, {
          transcribeModel: STT_MODEL,
          fixModel: FIX_MODEL,
          fixPrompt: FIX_SYSTEM_PROMPT,
          hasKey: !!apiKey,
        });
      });

      server.middlewares.use('/api/create-fragment', async (req, res, next) => {
        if (req.method !== 'POST') return next();
        // Media arrives as the raw request body; metadata rides in headers so we
        // avoid multipart parsing. Stream it to a temp file (video can be big),
        // then hand it to create_fragment.py.
        let tmpPath;
        try {
          const type = String(req.headers['x-fragment-type'] || '');
          if (!['photo', 'audio', 'video'].includes(type)) {
            return sendJson(res, 400, { error: 'missing or bad X-Fragment-Type header' });
          }
          const ext = String(req.headers['x-fragment-ext'] || '').replace(/[^a-z0-9]/gi, '').slice(0, 8);
          let note = '';
          try {
            note = decodeURIComponent(String(req.headers['x-fragment-note'] || ''));
          } catch {
            note = '';
          }
          const MAX_BYTES = 300 * 1024 * 1024;
          const len = parseInt(String(req.headers['content-length'] || '0'), 10);
          if (len && len > MAX_BYTES) {
            return sendJson(res, 413, { error: 'file exceeds 300 MB' });
          }
          const dir = await mkdtemp(path.join(os.tmpdir(), 'fn-upload-'));
          tmpPath = path.join(dir, `upload${ext ? '.' + ext : ''}`);
          await pipeline(req, createWriteStream(tmpPath));

          // Optional: commit but don't push (X-Fragment-No-Push: 1).
          const noPush = String(req.headers['x-fragment-no-push'] || '') === '1';
          const result = await runCreateScript(type, tmpPath, ext, note, noPush);
          return sendJson(res, result.ok ? 200 : 502, result);
        } catch (err) {
          return sendJson(res, 502, { error: String(err?.message || err) });
        } finally {
          if (tmpPath) unlink(tmpPath).catch(() => {});
        }
      });

      server.middlewares.use('/api/fetch-telegram', async (req, res, next) => {
        if (req.method !== 'POST') return next();
        try {
          const result = await fetchFromTelegram();
          return sendJson(res, result.ok ? 200 : 502, result);
        } catch (err) {
          return sendJson(res, 502, { error: String(err?.message || err) });
        }
      });

      server.middlewares.use('/api/pull', async (req, res, next) => {
        if (req.method !== 'POST') return next();
        try {
          const result = await runGitPull();
          return sendJson(res, result.ok ? 200 : 502, result);
        } catch (err) {
          return sendJson(res, 502, { error: String(err?.message || err) });
        }
      });

      server.middlewares.use('/api/delete-fragment', async (req, res, next) => {
        if (req.method !== 'POST') return next();
        try {
          const { id, confirm } = await readJsonBody(req);
          if (!id) return sendJson(res, 400, { error: 'missing fragment id' });
          // Type-to-confirm: the browser must echo the exact id back. Guards
          // against an accidental/programmatic call deleting a fragment.
          if (confirm !== id) {
            return sendJson(res, 400, { error: 'confirmation did not match the fragment id' });
          }
          const result = await runDeleteScript(id);
          if (!result.ok) return sendJson(res, 502, { error: result.error || 'delete failed' });
          return sendJson(res, 200, result);
        } catch (err) {
          return sendJson(res, 502, { error: String(err?.message || err) });
        }
      });

      server.middlewares.use('/api/fix-grammar', async (req, res, next) => {
        if (req.method !== 'POST') return next();
        try {
          if (!apiKey) {
            return sendJson(res, 501, {
              error: 'OPENROUTER_API_KEY not set in site/.env — add it and restart `npm run dev`.',
            });
          }
          const { text, prompt } = await readJsonBody(req);
          if (!text || !text.trim()) return sendJson(res, 400, { error: 'missing text' });

          // Use the caller's edited prompt if supplied, else the default.
          const systemPrompt = typeof prompt === 'string' && prompt.trim() ? prompt : FIX_SYSTEM_PROMPT;
          const { text: fixed, costUsd } = await callOpenRouter(apiKey, {
            model: FIX_MODEL,
            messages: [
              { role: 'system', content: systemPrompt },
              { role: 'user', content: text },
            ],
          });
          return sendJson(res, 200, { text: fixed, model: FIX_MODEL, costInr: costInr(costUsd) });
        } catch (err) {
          return sendJson(res, 502, { error: String(err?.message || err) });
        }
      });
    },
  };
}
