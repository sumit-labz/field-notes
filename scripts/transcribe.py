#!/usr/bin/env python3
"""Transcribe an audio fragment via OpenRouter's OpenAI-compatible STT endpoint.

Usage:
    python scripts/transcribe.py --id <fragment-id> [--model <slug>] [--json]

A standalone counterpart to the inbox's POST /api/transcribe (site/src/dev/
openrouter-plugin.mjs), so the unattended publish chain can transcribe without
the Vite dev server running. Finds media/audio/<id>.<ext>, uploads it to
OpenRouter's transcription endpoint (multipart), and prints the raw transcript.

Reads OPENROUTER_API_KEY from site/.env (falling back to the process env and the
repo-root .env), the same key the inbox uses. Never publishes anything and never
touches git — it only returns text; save_fragment_text.py / publish_post.py do
the committing.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# Importing ingest runs load_dotenv() for the repo-root .env (harmless).
from ingest import REPO_ROOT

AUDIO_DIR = REPO_ROOT / "media" / "audio"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}$")
TRANSCRIBE_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_MODEL = "openai/gpt-4o-mini-transcribe"
# Same loose shape check the inbox applies to a model slug.
MODEL_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?/[a-z0-9]([a-z0-9._:-]*[a-z0-9])?$", re.I)
AUDIO_MIME = {
    "oga": "audio/ogg", "ogg": "audio/ogg", "opus": "audio/ogg", "mp3": "audio/mpeg",
    "m4a": "audio/mp4", "aac": "audio/aac", "wav": "audio/wav", "flac": "audio/flac",
    "webm": "audio/webm",
}


class TranscribeError(Exception):
    pass


def log(msg: str) -> None:
    print(f"[transcribe] {msg}", file=sys.stderr)


def load_api_key() -> str:
    # The OpenRouter key lives in site/.env (see openrouter-plugin.mjs); load it
    # without letting it clobber anything already in the environment.
    load_dotenv(REPO_ROOT / "site" / ".env", override=False)
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise TranscribeError(
            "OPENROUTER_API_KEY not set — add it to site/.env (same key the inbox uses)"
        )
    return key


def find_audio_file(fragment_id: str) -> Path:
    if not ID_RE.match(fragment_id):
        raise TranscribeError(f"not a valid fragment id: {fragment_id!r}")
    if not AUDIO_DIR.exists():
        raise TranscribeError(f"no audio directory at {AUDIO_DIR}")
    for path in AUDIO_DIR.iterdir():
        if path.is_file() and path.stem == fragment_id:
            return path
    raise TranscribeError(f"no audio file media/audio/{fragment_id}.* found")


def transcribe(fragment_id: str, model: str) -> dict:
    api_key = load_api_key()
    audio_path = find_audio_file(fragment_id)
    ext = audio_path.suffix.lstrip(".").lower()
    mime = AUDIO_MIME.get(ext) or mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    model = model if MODEL_SLUG_RE.match(model or "") else DEFAULT_MODEL

    log(f"transcribing {audio_path.name} ({mime}) via {model}")
    with audio_path.open("rb") as fh:
        resp = requests.post(
            TRANSCRIBE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "http://localhost/field-notes/internal/fragments",
                "X-Title": "field-notes publish",
            },
            data={"model": model, "response_format": "json"},
            files={"file": (audio_path.name, fh, mime)},
            timeout=300,
        )
    raw = resp.text
    try:
        body = resp.json()
    except ValueError as exc:
        raise TranscribeError(
            f"OpenRouter returned non-JSON ({resp.status_code}): {raw[:300]}"
        ) from exc
    if not resp.ok:
        err = body.get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        raise TranscribeError(msg or f"transcription error {resp.status_code}")
    text = body.get("text")
    if not isinstance(text, str):
        raise TranscribeError("transcription response had no text")

    cost = body.get("usage", {}).get("cost") if isinstance(body.get("usage"), dict) else None
    return {"ok": True, "id": fragment_id, "text": text.strip(), "cost_usd": cost}


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe an audio fragment via OpenRouter.")
    parser.add_argument("--id", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--json", action="store_true", help="print full JSON result on stdout")
    args = parser.parse_args()

    try:
        result = transcribe(args.id, args.model)
    except Exception as exc:  # noqa: BLE001
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"[transcribe] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # Plain mode prints just the transcript to stdout so it pipes cleanly;
    # --json prints the whole result (text + cost) for the watcher.
    print(json.dumps(result) if args.json else result["text"])


if __name__ == "__main__":
    main()
