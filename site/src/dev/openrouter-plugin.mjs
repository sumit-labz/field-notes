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
import { readFile } from 'node:fs/promises';
import { existsSync, readdirSync, statSync, createReadStream } from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions';
// Models chosen by the maintainer (see the fragment inbox spec):
const STT_MODEL = 'openai/gpt-4o-mini-transcribe'; // GPT-4o Mini Transcribe
const FIX_MODEL = 'deepseek/deepseek-chat'; // DeepSeek V3

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

// input_audio only reliably accepts wav/mp3; anything else (Telegram voice
// notes are ogg/opus .oga) is transcoded to mp3 with ffmpeg first.
const PASSTHROUGH_FORMATS = new Set(['mp3', 'wav']);
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

function transcodeToMp3(inputPath) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    const errChunks = [];
    let proc;
    try {
      proc = spawn('ffmpeg', ['-i', inputPath, '-f', 'mp3', '-vn', '-']);
    } catch (err) {
      reject(new Error(`could not start ffmpeg: ${err.message}`));
      return;
    }
    proc.on('error', (err) => {
      reject(
        new Error(
          err.code === 'ENOENT'
            ? 'ffmpeg not found on PATH — needed to convert ogg/opus voice notes to mp3 before transcription.'
            : `ffmpeg failed to start: ${err.message}`
        )
      );
    });
    proc.stdout.on('data', (c) => chunks.push(c));
    proc.stderr.on('data', (c) => errChunks.push(c));
    proc.on('close', (code) => {
      if (code === 0) resolve(Buffer.concat(chunks));
      else reject(new Error(`ffmpeg exited ${code}: ${Buffer.concat(errChunks).toString().slice(-500)}`));
    });
  });
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
    body: JSON.stringify(body),
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
  return content.trim();
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
          let audioBuffer;
          let format;
          if (PASSTHROUGH_FORMATS.has(ext)) {
            audioBuffer = await readFile(file);
            format = ext;
          } else {
            audioBuffer = await transcodeToMp3(file);
            format = 'mp3';
          }

          const text = await callOpenRouter(apiKey, {
            model: STT_MODEL,
            messages: [
              {
                role: 'user',
                content: [
                  { type: 'text', text: 'Transcribe this audio verbatim.' },
                  { type: 'input_audio', input_audio: { data: audioBuffer.toString('base64'), format } },
                ],
              },
            ],
          });
          return sendJson(res, 200, { text });
        } catch (err) {
          return sendJson(res, 502, { error: String(err?.message || err) });
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
          const { text } = await readJsonBody(req);
          if (!text || !text.trim()) return sendJson(res, 400, { error: 'missing text' });

          const fixed = await callOpenRouter(apiKey, {
            model: FIX_MODEL,
            messages: [
              { role: 'system', content: FIX_SYSTEM_PROMPT },
              { role: 'user', content: text },
            ],
          });
          return sendJson(res, 200, { text: fixed });
        } catch (err) {
          return sendJson(res, 502, { error: String(err?.message || err) });
        }
      });
    },
  };
}
