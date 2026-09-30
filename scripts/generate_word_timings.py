#!/usr/bin/env python3
"""Word-level timings for a post's narration, for the site's Stage mode.

Downloads the post's narration mp3 (R2 or local media/), transcribes it with
OpenRouter's whisper-1 (the only STT model there that returns per-word
timestamps), and aligns those timed words onto the post's OWN prose — the
exact text the TTS read (generate_post_audio.post_prose) — so Stage shows the
real words and punctuation, not the transcriber's spelling of them.

Output: narration/<slug>.json
  {"v": 1, "duration": s, "paras": [first ~48 chars of each paragraph],
   "words": [[text, start, end, paragraph_index], ...]}

Usage:
  python scripts/generate_word_timings.py --slug <slug>
  python scripts/generate_word_timings.py --all [--force]
Env: OPENROUTER_API_KEY (repo-root .env or site/.env).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

from generate_post_audio import POSTS_DIR, REPO_ROOT, post_prose, read_post

load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / "site" / ".env")

STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
STT_MODEL = "openai/whisper-1"
R2_PUBLIC_BASE = "https://pub-7c2ee4a34fe74649ab6080392160547e.r2.dev"
OUT_DIR = REPO_ROOT / "narration"


def log(msg: str) -> None:
    print(f"[timings] {msg}", file=sys.stderr)


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower().replace("’", "'"))


def audio_key(frontmatter: str) -> str | None:
    m = re.search(r"^audio:\s*(\S+)\s*$", frontmatter, re.M)
    return m.group(1) if m else None


def fetch_audio(key: str) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "narration.mp3"
    if key.startswith("media/"):
        tmp.write_bytes((REPO_ROOT / key).read_bytes())
    else:
        r = requests.get(f"{R2_PUBLIC_BASE}/{key}", timeout=120)
        r.raise_for_status()
        tmp.write_bytes(r.content)
    return tmp


def transcribe_words(api_key: str, path: Path) -> tuple[list[dict], float]:
    with path.open("rb") as fh:
        r = requests.post(
            STT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": STT_MODEL, "response_format": "verbose_json", "timestamp_granularities[]": "word"},
            files={"file": (path.name, fh, "audio/mpeg")},
            timeout=600,
        )
    body = r.json()
    if r.status_code != 200 or "words" not in body:
        raise RuntimeError(f"transcription failed ({r.status_code}): {body.get('error', body)}")
    return body["words"], float(body.get("duration") or 0)


def align(prose: str, timed: list[dict], duration: float) -> tuple[list[list], list[str]]:
    """Give every canonical prose word a [start, end] from the matching timed
    word; words the transcriber missed or spelled differently are
    interpolated between their matched neighbours."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", prose) if p.strip()]
    tokens: list[tuple[str, int]] = []
    for pi, para in enumerate(paras):
        tokens.extend((w, pi) for w in para.split())

    a = [norm(w) for w, _ in tokens]
    b = [norm(w["word"]) for w in timed]
    times: list[tuple[float, float] | None] = [None] * len(tokens)
    for block in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            t = timed[block.b + k]
            times[block.a + k] = (float(t["start"]), float(t["end"]))

    # interpolate the gaps
    n = len(tokens)
    i = 0
    while i < n:
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < n and times[j] is None:
            j += 1
        left = times[i - 1][1] if i > 0 else 0.0
        right = times[j][0] if j < n else (duration or left + 0.4 * (j - i))
        step = max(right - left, 0.0) / (j - i + 1)
        for k in range(i, j):
            s = left + step * (k - i + 1)
            times[k] = (s, s + step)
        i = j

    words = [[w, round(s, 2), round(e, 2), pi] for (w, pi), (s, e) in zip(tokens, times)]
    return words, [p[:48] for p in paras]


def run(slug: str, force: bool) -> dict:
    out = OUT_DIR / f"{slug}.json"
    if out.exists() and not force:
        return {"slug": slug, "skipped": True}
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    _, frontmatter, body = read_post(slug)
    key = audio_key(frontmatter)
    if not key:
        return {"slug": slug, "skipped": True, "reason": "no audio"}
    prose = post_prose(body)
    timed, duration = transcribe_words(api_key, fetch_audio(key))
    words, paras = align(prose, timed, duration)
    matched = sum(1 for w in timed if norm(w["word"]))
    OUT_DIR.mkdir(exist_ok=True)
    out.write_text(json.dumps({"v": 1, "duration": round(duration, 2), "paras": paras, "words": words},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"{slug}: {len(words)} words, {len(timed)} timed, {duration:.0f}s")
    return {"slug": slug, "words": len(words), "timed": matched, "duration": duration}


def main() -> None:
    ap = argparse.ArgumentParser(description="Word timings for post narration (Stage mode).")
    ap.add_argument("--slug")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true", help="regenerate even if the json exists")
    args = ap.parse_args()
    slugs = [p.stem for p in sorted(POSTS_DIR.glob("*.md"))] if args.all else [args.slug]
    if not slugs or slugs == [None]:
        ap.error("pass --slug or --all")
    failed = 0
    for slug in slugs:
        try:
            print(json.dumps(run(slug, args.force)))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(json.dumps({"slug": slug, "ok": False, "error": str(exc)[:300]}))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
