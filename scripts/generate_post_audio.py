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
import subprocess
import sys
import tempfile
import time
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
STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
STT_MODEL = "openai/gpt-4o-mini-transcribe"
GENERATION_URL = "https://openrouter.ai/api/v1/generation"
DEFAULT_MODEL = "fish-audio/s2.1-pro"
INR_PER_USD = 87.5
# Cloning models reject long/large references; a short mono WAV is what works.
REF_MAX_SECONDS = 30
REF_SAMPLE_RATE = 16000
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


def prepare_reference(ref_audio: Path) -> Path:
    """Trim + downsample the reference to a short mono WAV. fish-audio cloning
    rejects long/large clips (a 2-min mp3 returns 400); ~30s mono works well."""
    out = Path(tempfile.gettempdir()) / f"fn-ref-{os.getpid()}.wav"
    cmd = ["ffmpeg", "-y", "-i", str(ref_audio), "-t", str(REF_MAX_SECONDS),
           "-ar", str(REF_SAMPLE_RATE), "-ac", "1", str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise IngestError("ffmpeg not found on PATH — needed to prepare the reference clip") from exc
    if proc.returncode != 0:
        raise IngestError(f"ffmpeg failed to prepare reference: {proc.stderr[-300:]}")
    return out


def transcribe_reference(api_key: str, path: Path) -> str:
    """Transcribe the reference clip so voice cloning has the matching text."""
    ext = path.suffix.lstrip(".").lower()
    mime = AUDIO_MIME.get(ext, "application/octet-stream")
    try:
        with path.open("rb") as fh:
            resp = requests.post(
                STT_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": STT_MODEL, "response_format": "json"},
                files={"file": (path.name, fh, mime)},
                timeout=300,
            )
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"failed to transcribe reference clip: {redact_secrets(str(exc))}") from exc
    if resp.status_code != 200 or "text" not in body:
        raise IngestError(f"reference transcription failed ({resp.status_code}): {body.get('error', body)}")
    return body["text"].strip()


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
    return _tts_result(resp)


def synth_preset(api_key: str, model: str, text: str, voice: str) -> tuple[bytes, str | None]:
    """TTS with a preset voice (no cloning) — e.g. openai/gpt-4o-mini-tts."""
    body = {"model": model, "input": text, "voice": voice, "response_format": "mp3"}
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
    return _tts_result(resp)


def _tts_result(resp) -> tuple[bytes, str | None]:
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
    """OpenRouter computes the generation's cost a few seconds after the call,
    so poll the generation endpoint until it's available (it 404s until then)."""
    if not generation_id:
        return None
    for _ in range(10):
        time.sleep(2)
        try:
            resp = requests.get(
                GENERATION_URL,
                params={"id": generation_id},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            if resp.status_code == 200:
                cost = resp.json().get("data", {}).get("total_cost")
                if isinstance(cost, (int, float)):
                    return round(cost * INR_PER_USD, 4)
        except Exception:
            pass
    return None


def upload_mp3(config, key: str, data: bytes) -> None:
    client = ingest.r2_client(config)
    try:
        client.put_object(Bucket=config.r2_bucket, Key=key, Body=data, ContentType="audio/mpeg")
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"failed to upload {key} to R2: {redact_secrets(str(exc))}") from exc


PRESET_MODEL = "hexgrad/kokoro-82m"  # cheap, many preset voices (af_* = female)


def generate(slug: str, ref_audio: Path, ref_text_arg: str, model: str, voice: str, local: bool, commit: bool, no_push: bool) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise IngestError("OPENROUTER_API_KEY not set (add it to site/.env or the repo-root .env)")

    path, frontmatter, body = read_post(slug)
    prose = post_prose(body)
    if not prose:
        raise IngestError(f"post {slug} has no narratable prose")

    if voice:
        # Preset voice (no cloning) — e.g. a gender-neutral/female OpenAI voice.
        tts_model = model if model != DEFAULT_MODEL else PRESET_MODEL
        log(f"synthesising {len(prose)} chars for {slug} with preset voice '{voice}' ({tts_model})")
        audio, gen_id = synth_preset(api_key, tts_model, prose, voice)
    else:
        # Cloned voice from a reference clip.
        if not ref_audio.exists():
            raise IngestError(f"reference audio not found: {ref_audio} (or pass --voice for a preset voice)")
        ref_clip = prepare_reference(ref_audio)
        log(f"prepared reference clip (≤{REF_MAX_SECONDS}s mono wav)")
        if ref_text_arg:
            ref_path = Path(ref_text_arg)
            ref_text = ref_path.read_text(encoding="utf-8").strip() if ref_path.exists() else ref_text_arg
        else:
            log("no --ref-text given — transcribing the reference clip for the clone…")
            ref_text = transcribe_reference(api_key, ref_clip)
            log(f"reference transcript: {ref_text[:80]}…")
        if not ref_text.strip():
            raise IngestError("reference transcript is empty")
        log(f"synthesising {len(prose)} chars for {slug} with {model}")
        audio, gen_id = synth(api_key, model, prose, ref_clip, ref_text)

    add_paths = [str(path.relative_to(REPO_ROOT)).replace("\\", "/")]
    if local:
        # No R2 write creds needed — save under the repo like other local media.
        out_dir = REPO_ROOT / "media" / "posts"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{slug}.mp3").write_bytes(audio)
        key = f"media/posts/{slug}.mp3"
        add_paths.append("media")
        log(f"saved {key} locally ({len(audio)} bytes)")
    else:
        key = f"audio/posts/{slug}.mp3"
        upload_mp3(r2_config_from_env(), key, audio)
        log(f"uploaded {key} to R2 ({len(audio)} bytes)")

    path.write_text(set_frontmatter_audio(frontmatter, key) + body, encoding="utf-8")
    log(f"set audio: {key} in {path.relative_to(REPO_ROOT)}")

    cost_inr = fetch_cost_inr(api_key, gen_id)
    pushed = False
    if commit:
        run_git(["add", *add_paths])
        run_git(["commit", "-m", f"post({slug}): add narration audio"])
        if not no_push:
            push = git(["push"])
            if push.returncode != 0:
                raise IngestError(f"git push failed: {push.stderr.strip()}")
            pushed = True

    return {
        "ok": True, "slug": slug, "audio": key, "bytes": len(audio),
        "cost_inr": cost_inr, "local": local, "committed": commit, "pushed": pushed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate voice-cloned narration for a post.")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--ref-audio", default="", help="reference clip for voice cloning (omit when using --voice)")
    parser.add_argument("--ref-text", default="", help="transcript .txt path or inline text; omitted → auto-transcribed")
    parser.add_argument("--voice", default="", help="preset voice name (e.g. alloy, sage, nova) → no cloning")
    parser.add_argument("--voice-model", default=DEFAULT_MODEL)
    parser.add_argument("--local", action="store_true", help="save under media/posts/ instead of R2 (no R2 creds needed)")
    parser.add_argument("--commit", action="store_true", help="commit the post + audio change")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = generate(args.slug, Path(args.ref_audio), args.ref_text, args.voice_model, args.voice, args.local, args.commit, args.no_push)
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
