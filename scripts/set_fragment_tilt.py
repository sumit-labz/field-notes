#!/usr/bin/env python3
"""Set or clear a fragment's manual tilt override — a pure frontmatter edit,
no image bytes touched.

Usage:
    python scripts/set_fragment_tilt.py <fragment-id> <degrees|clear> [--no-push] [--json]

`degrees` is a number (positive = clockwise, e.g. 3.5 or -2); `clear` removes
the override so rendering falls back to the deterministic hash tilt (see
site/src/lib/rotation.ts's tiltForId). Invoked one-click from the internal
fragment inbox's tilt slider (site/src/dev/openrouter-plugin.mjs ->
POST /api/set-tilt); also runnable directly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from ingest import IngestError, REPO_ROOT, redact_secrets, git, run_git
from delete_fragment import find_fragment_file

CLEAR = "clear"

# A top-level scalar field, inserted/updated/removed right after `graded:`'s
# spot (or `media:` if there's no graded block) — targeted text surgery, same
# approach as apply_cinematic_grade.py's graded: block, so every other
# field's exact formatting is left untouched.
TILT_LINE_RE = re.compile(r"^tilt: .+\n", re.M)
GRADED_BLOCK_RE = re.compile(r'^graded:\n(?:  "\d+": .+\n)*', re.M)
MEDIA_BLOCK_RE = re.compile(r"^media:\n(?:  - .+\n)*", re.M)


def write_tilt(text: str, degrees: float | None) -> str:
    existing = TILT_LINE_RE.search(text)
    if degrees is None:
        return text[: existing.start()] + text[existing.end() :] if existing else text

    line = f"tilt: {degrees}\n"
    if existing:
        return text[: existing.start()] + line + text[existing.end() :]

    anchor = GRADED_BLOCK_RE.search(text) or MEDIA_BLOCK_RE.search(text)
    if not anchor:
        raise IngestError("fragment has no media: block to anchor a new tilt: field after")
    return text[: anchor.end()] + line + text[anchor.end() :]


def commit_and_push(fragment_id: str, rel_path: str, no_push: bool) -> bool:
    run_git(["add", rel_path])
    run_git(["commit", "-m", f"tilt: set on {fragment_id}"])
    if no_push:
        return False
    push = git(["push"])
    if push.returncode == 0:
        return True
    log("push rejected; pulling --rebase and retrying")
    run_git(["pull", "--rebase", "origin", "main"])
    push = git(["push"])
    if push.returncode != 0:
        raise IngestError(f"git push failed: {push.stderr.strip()}")
    return True


def log(msg: str) -> None:
    print(f"[tilt] {msg}", file=sys.stderr)


def set_tilt(fragment_id: str, degrees: float | None, no_push: bool) -> dict:
    fragment_path = find_fragment_file(fragment_id)
    text = fragment_path.read_text(encoding="utf-8")
    new_text = write_tilt(text, degrees)
    if new_text == text:
        return {"ok": True, "id": fragment_id, "tilt": degrees, "committed": False, "pushed": False}

    fragment_path.write_text(new_text, encoding="utf-8")
    rel_path = str(fragment_path.relative_to(REPO_ROOT)).replace("\\", "/")
    pushed = commit_and_push(fragment_id, rel_path, no_push)
    return {"ok": True, "id": fragment_id, "tilt": degrees, "committed": True, "pushed": pushed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Set or clear a fragment's manual tilt override.")
    parser.add_argument("fragment_id")
    parser.add_argument("degrees", help='a number, or "clear" to remove the override')
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        degrees = None if args.degrees == CLEAR else float(args.degrees)
        result = set_tilt(args.fragment_id, degrees, args.no_push)
    except Exception as exc:  # noqa: BLE001
        message = redact_secrets(str(exc))
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(f"[tilt] ERROR: {message}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result) if args.json else f"[tilt] done: {result}")


if __name__ == "__main__":
    main()
