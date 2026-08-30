#!/usr/bin/env python3
"""Create a fragment from a media file captured in the web inbox (recorded
audio, uploaded photo/video, pasted screenshot) — the browser counterpart to
the Telegram ingest bot.

Usage:
    python scripts/create_fragment.py --type {photo,audio,video} --file <path> \
        [--ext EXT] [--note "caption"] [--json]

Unlike the bot, ALL media is stored locally under media/{photo,audio,video}/
(photos are re-encoded to WebP like the bot; audio/video kept as-is), so the
web flow needs no R2 credentials. The renderer tells local media from R2 keys
by the "media/" prefix. Writes the fragment .md, then commits and pushes.

Invoked by the inbox via site/src/dev/openrouter-plugin.mjs
(POST /api/create-fragment); also runnable directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Sibling module (load_dotenv runs on import, but no telegram/R2 env is needed
# for the local-only web flow — only photo->WebP via Pillow, already a dep).
import ingest
from ingest import (
    IngestError,
    REPO_ROOT,
    MEDIA_DIR,
    IST,
    Fragment,
    fragment_id_and_path,
    resize_to_webp,
    render_fragment,
    write_fragment_file,
    write_local_media,
    redact_secrets,
    git,
    run_git,
)

VALID_TYPES = ("photo", "audio", "video")


def log(msg: str) -> None:
    print(f"[create] {msg}", file=sys.stderr)


def commit_and_push(fragment_id: str, add_paths: list[str], no_push: bool) -> bool:
    run_git(["add", *add_paths])
    run_git(["commit", "-m", f"web: create fragment {fragment_id}"])
    if no_push:
        return False
    push = git(["push"])
    if push.returncode == 0:
        return True
    # A concurrent push (e.g. the ingest bot) advanced main — rebase and retry.
    log("push rejected; pulling --rebase and retrying")
    run_git(["pull", "--rebase", "origin", "main"])
    push = git(["push"])
    if push.returncode != 0:
        raise IngestError(f"git push failed: {push.stderr.strip()}")
    return True


def create_fragment(kind: str, source: Path, ext: str, note: str, no_push: bool) -> dict:
    if kind not in VALID_TYPES:
        raise IngestError(f"invalid type {kind!r}; expected one of {VALID_TYPES}")
    if not source.exists():
        raise IngestError(f"source file not found: {source}")

    captured_at = datetime.now(tz=IST)
    fragment_id, path, date_str, time_str = fragment_id_and_path(captured_at)

    add_paths = ["fragments"]
    if kind == "photo":
        webp = resize_to_webp(source.read_bytes())
        filename = f"{fragment_id}.webp"
        write_local_media(MEDIA_DIR / "photo", filename, webp)
        media_path = f"media/photo/{filename}"
        add_paths.append("media")
    else:
        clean_ext = (ext or source.suffix.lstrip(".") or ("webm" if kind == "audio" else "mp4")).lstrip(".").lower()
        filename = f"{fragment_id}.{clean_ext}"
        write_local_media(MEDIA_DIR / kind, filename, source.read_bytes())
        media_path = f"media/{kind}/{filename}"
        add_paths.append("media")

    fragment = Fragment(
        id=fragment_id,
        captured_at=captured_at,
        type=kind,
        media=[media_path],
        journey=None,
        spark=False,
        body=note.strip(),
        path=path,
    )
    write_fragment_file(fragment)
    log(f"wrote {path.relative_to(REPO_ROOT)} and {media_path}")

    pushed = commit_and_push(fragment_id, add_paths, no_push)
    return {
        "ok": True,
        "id": fragment_id,
        "type": kind,
        "media": media_path,
        "committed": True,
        "pushed": pushed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a fragment from a web-captured media file.")
    parser.add_argument("--type", required=True, choices=VALID_TYPES)
    parser.add_argument("--file", required=True)
    parser.add_argument("--ext", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = create_fragment(args.type, Path(args.file), args.ext, args.note, args.no_push)
    except Exception as exc:  # noqa: BLE001
        message = redact_secrets(str(exc))
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(f"[create] ERROR: {message}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result) if args.json else f"[create] done: {result}")


if __name__ == "__main__":
    main()
