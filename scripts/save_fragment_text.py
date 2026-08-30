#!/usr/bin/env python3
"""Persist a fragment's transcript / grammar-fixed text as a committed text file.

Usage:
    python scripts/save_fragment_text.py --id <fragment-id> \
        --kind {transcript,cleaned} --file <path> [--no-push] [--json]

Saves plain text into the repo-root transcripts/ dir, named by fragment id:
    transcript  -> transcripts/<id>.txt          (raw, "before cleanup")
    cleaned     -> transcripts/<id>.cleaned.txt  (grammar-fixed)

Durable and committed, so the transcription/cleanup work survives a browser
clear. The post `transcript:` frontmatter can point at the raw file directly.
Invoked by the inbox via POST /api/save-text; also runnable directly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import ingest
from ingest import IngestError, REPO_ROOT, redact_secrets, git, run_git

TRANSCRIPTS_DIR = REPO_ROOT / "transcripts"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}$")
SUFFIX = {"transcript": ".txt", "cleaned": ".cleaned.txt"}


def log(msg: str) -> None:
    print(f"[save] {msg}", file=sys.stderr)


def commit_and_push(fragment_id: str, kind: str, rel_path: str, no_push: bool) -> bool:
    run_git(["add", rel_path])
    run_git(["commit", "-m", f"text: save {kind} for {fragment_id}"])
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


def save_text(fragment_id: str, kind: str, source: Path, no_push: bool) -> dict:
    if not ID_RE.match(fragment_id):
        raise IngestError(f"not a valid fragment id: {fragment_id!r}")
    if kind not in SUFFIX:
        raise IngestError(f"invalid kind {kind!r}; expected transcript or cleaned")
    if not source.exists():
        raise IngestError(f"source file not found: {source}")

    text = source.read_text(encoding="utf-8")
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{fragment_id}{SUFFIX[kind]}"
    out_path = TRANSCRIPTS_DIR / filename
    # Normalise to a trailing newline, no trailing spaces per line.
    out_path.write_text(text.rstrip() + "\n", encoding="utf-8")
    rel_path = f"transcripts/{filename}"
    log(f"wrote {rel_path} ({len(text)} chars)")

    pushed = commit_and_push(fragment_id, kind, rel_path, no_push)
    return {"ok": True, "id": fragment_id, "kind": kind, "file": rel_path, "committed": True, "pushed": pushed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Save a fragment's transcript/cleaned text.")
    parser.add_argument("--id", required=True)
    parser.add_argument("--kind", required=True, choices=list(SUFFIX))
    parser.add_argument("--file", required=True)
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = save_text(args.id, args.kind, Path(args.file), args.no_push)
    except Exception as exc:  # noqa: BLE001
        message = redact_secrets(str(exc))
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(f"[save] ERROR: {message}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result) if args.json else f"[save] done: {result}")


if __name__ == "__main__":
    main()
