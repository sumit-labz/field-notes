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
import { existsSync, readdirSync } from 'node:fs';
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
