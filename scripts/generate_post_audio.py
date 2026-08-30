#!/usr/bin/env python3
"""Generate narration audio for a post and upload it to R2, in YOUR voice.

Uses OpenRouter's TTS endpoint (/api/v1/audio/speech) with a voice-cloning
model (default fish-audio/s2.1-pro): you supply a clean reference clip of your
own voice + its transcript, and the post's prose is synthesised to mimic it.
The mp3 is uploaded to R2 and the post's `audio:` frontmatter is set to the key
so the "Listen" player renders. Text stays the canonical content.

Usage:
  python scripts/generate_post_audio.py --slug <post-slug> \
      --ref-audio <path-to-clean-voice-sample> \
      --ref-text  <path-to-transcript.txt | "inline transcript text"> \
      [--voice-model fish-audio/s2.1-pro] [--commit] [--no-push]

Reference clip: record ~15-30s of clear speech (quiet room), point --ref-audio
at it, and give --ref-text the exact words you said. Reuse a clean voice-note +
its transcripts/<id>.txt if you have a good one.

Env: OPENROUTER_API_KEY (for TTS) and the R2 vars (R2_ACCESS_KEY / R2_SECRET_KEY
/ R2_ENDPOINT / R2_BUCKET) — the same credentials the bot uses. Loaded from the
repo-root .env and site/.env.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

import ingest
from ingest import IngestError, REPO_ROOT, redact_secrets, git, run_git
from delete_fragment import r2_config_from_env

# OpenRouter key commonly lives in site/.env (used by the dev server); R2 vars in
# the repo-root .env (used by the bot). Load both so either location works.
load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / "site" / ".env")

TTS_URL = "https://openrouter.ai/api/v1/audio/speech"
GENERATION_URL = "https://openrouter.ai/api/v1/generation"
DEFAULT_MODEL = "fish-audio/s2.1-pro"
INR_PER_USD = 87.5
POSTS_DIR = REPO_ROOT / "posts"
AUDIO_MIME = {
    "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg",
    "oga": "audio/ogg", "opus": "audio/ogg", "webm": "audio/webm", "flac": "audio/flac",
}


def log(msg: str) -> None:
    print(f"[tts] {msg}", file=sys.stderr)


def post_prose(body: str) -> str:
    """Plain narration text: drop fragment placeholders and light markdown so
    the TTS reads prose, not symbols."""
    text = re.sub(r"\{\{fragment:[^}]+\}\}", "", body)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # links -> label
    text = re.sub(r"[#>*_`]+", "", text)                      # md symbols
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_post(slug: str) -> tuple[Path, str, str]:
    path = POSTS_DIR / f"{slug}.md"
    if not path.exists():
        raise IngestError(f"no post found: {path}")
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^(---\n.*?\n---\n)(.*)$", raw, re.S)
    if not m:
        raise IngestError(f"{path} has no frontmatter block")
    return path, m.group(1), m.group(2)


def set_frontmatter_audio(frontmatter: str, key: str) -> str:
    if re.search(r"^audio:.*$", frontmatter, re.M):
        return re.sub(r"^audio:.*$", f"audio: {key}", frontmatter, flags=re.M)
    # Insert after the slug line (always present).
    return re.sub(r"^(slug:.*)$", rf"\1\naudio: {key}", frontmatter, count=1, flags=re.M)


def data_uri(path: Path) -> str:
    ext = path.suffix.lstrip(".").lower()
    mime = AUDIO_MIME.get(ext, "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def synth(api_key: str, model: str, text: str, ref_audio: Path, ref_text: str) -> tuple[bytes, str | None]:
    body = {
        "model": model,
        "input": text,
        "response_format": "mp3",
        "input_references": [
            {"type": "input_audio", "input_audio": {"data": data_uri(ref_audio)}},
            {"type": "text", "text": ref_text},
        ],
    }
    try:
        resp = requests.post(
            TTS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost/field-notes",
                "X-Title": "field-notes post narration",
            },
            json=body,
            timeout=300,
        )
    except requests.RequestException as exc:
        raise IngestError(f"TTS request failed: {redact_secrets(str(exc))}") from exc
    if resp.status_code != 200 or resp.headers.get("Content-Type", "").startswith("application/json"):
        # Errors come back as JSON even on this audio endpoint.
        try:
            err = resp.json().get("error", {})
            msg = err.get("message") if isinstance(err, dict) else err
        except Exception:
            msg = resp.text[:300]
        raise IngestError(f"TTS error {resp.status_code}: {msg}")
    return resp.content, resp.headers.get("X-Generation-Id")


def fetch_cost_inr(api_key: str, generation_id: str | None) -> float | None:
    if not generation_id:
        return None
    try:
        resp = requests.get(
            GENERATION_URL,
            params={"id": generation_id},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        cost = resp.json().get("data", {}).get("total_cost")
        return round(cost * INR_PER_USD, 4) if isinstance(cost, (int, float)) else None
    except Exception:
        return None


def upload_mp3(config, key: str, data: bytes) -> None:
    client = ingest.r2_client(config)
    try:
        client.put_object(Bucket=config.r2_bucket, Key=key, Body=data, ContentType="audio/mpeg")
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"failed to upload {key} to R2: {redact_secrets(str(exc))}") from exc


def generate(slug: str, ref_audio: Path, ref_text_arg: str, model: str, commit: bool, no_push: bool) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise IngestError("OPENROUTER_API_KEY not set (add it to site/.env or the repo-root .env)")
    if not ref_audio.exists():
        raise IngestError(f"reference audio not found: {ref_audio}")
    # ref-text is a file path if it exists, else treated as inline text.
    ref_path = Path(ref_text_arg)
    ref_text = ref_path.read_text(encoding="utf-8").strip() if ref_path.exists() else ref_text_arg
    if not ref_text.strip():
        raise IngestError("reference transcript (--ref-text) is empty")

    path, frontmatter, body = read_post(slug)
    prose = post_prose(body)
    if not prose:
        raise IngestError(f"post {slug} has no narratable prose")
    log(f"synthesising {len(prose)} chars for {slug} with {model}")

    audio, gen_id = synth(api_key, model, prose, ref_audio, ref_text)
    key = f"audio/posts/{slug}.mp3"
    config = r2_config_from_env()
    upload_mp3(config, key, audio)
    log(f"uploaded {key} ({len(audio)} bytes)")

    path.write_text(set_frontmatter_audio(frontmatter, key) + body, encoding="utf-8")
    log(f"set audio: {key} in {path.relative_to(REPO_ROOT)}")

    cost_inr = fetch_cost_inr(api_key, gen_id)
    pushed = False
    if commit:
        run_git(["add", str(path.relative_to(REPO_ROOT)).replace("\\", "/")])
        run_git(["commit", "-m", f"post({slug}): add narration audio"])
        if not no_push:
            push = git(["push"])
            if push.returncode != 0:
                raise IngestError(f"git push failed: {push.stderr.strip()}")
            pushed = True

    return {
        "ok": True, "slug": slug, "audio": key, "bytes": len(audio),
        "cost_inr": cost_inr, "committed": commit, "pushed": pushed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate voice-cloned narration for a post.")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--ref-audio", required=True)
    parser.add_argument("--ref-text", required=True, help="path to a transcript .txt, or the text inline")
    parser.add_argument("--voice-model", default=DEFAULT_MODEL)
    parser.add_argument("--commit", action="store_true", help="commit the post frontmatter change")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = generate(args.slug, Path(args.ref_audio), args.ref_text, args.voice_model, args.commit, args.no_push)
    except Exception as exc:  # noqa: BLE001
        message = redact_secrets(str(exc))
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(f"[tts] ERROR: {message}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result) if args.json else f"[tts] done: {result}")


if __name__ == "__main__":
    main()
