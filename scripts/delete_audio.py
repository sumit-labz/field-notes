#!/usr/bin/env python3
"""Delete a fragment's local audio file(s) while keeping the fragment and its
transcript — the "audio is costly, text is cheap" cleanup for old voice notes.

Usage:
    python scripts/delete_audio.py <fragment-id> [--force] [--no-push] [--json]

Removes every media/audio/... entry from the fragment's `media:` frontmatter
list, deletes those files from disk (via git rm, same as delete_fragment.py),
and commits + pushes. Never touches transcripts/, the fragment markdown body,
or any non-audio media (e.g. a photo sent alongside the voice note).

Safety: refuses to delete unless transcripts/<id>.txt already exists, so audio
is never lost before its text survives it — unless --force is passed. The
inbox's UI never passes --force; it just doesn't show the button until a
transcript has been saved.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

# Sibling module. Importing it runs load_dotenv() (harmless) but NOT main().
import ingest
from ingest import IngestError, REPO_ROOT, FRAGMENTS_DIR, redact_secrets, git, run_git

TRANSCRIPTS_DIR = REPO_ROOT / "transcripts"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}$")


def log(msg: str) -> None:
    print(f"[delete-audio] {msg}", file=sys.stderr)


def find_fragment_file(fragment_id: str) -> Path:
    if not ID_RE.fullmatch(fragment_id):
        raise IngestError(f"not a valid fragment id: {fragment_id!r}")
    date_str, time_str = fragment_id[:10], fragment_id[11:]
    expected = FRAGMENTS_DIR / date_str / f"{time_str}.md"
    if expected.exists():
        return expected
    for path in FRAGMENTS_DIR.glob("*/*.md"):
        try:
            fm = parse_frontmatter(path)
        except Exception:
            continue
        if fm.get("id") == fragment_id:
            return path
    raise IngestError(f"no fragment file found for id {fragment_id}")


def parse_frontmatter(path: Path) -> tuple[dict, str]:
    """Returns (frontmatter dict, body text after the closing ---)."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not match:
        raise IngestError(f"{path} has no frontmatter block")
    fm = yaml.safe_load(match.group(1)) or {}
    return fm, match.group(2)


def rewrite_fragment_media(path: Path, remaining_media: list[str]) -> None:
    """Rewrites just the `media:` block of the fragment file in place, keeping
    every other line (and the body) untouched. Mirrors ingest.render_fragment's
    layout so re-running ingest-style tooling still parses this cleanly."""
    text = path.read_text(encoding="utf-8")
    header_match = re.match(r"^(---\n.*?\n---\n?)(.*)$", text, re.S)
    if not header_match:
        raise IngestError(f"{path} has no frontmatter block")
    frontmatter_block, body = header_match.groups()

    lines = frontmatter_block.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("media:"):
            i += 1
            while i < len(lines) and lines[i].startswith("  - "):
                i += 1
            if remaining_media:
                out.append("media:")
                out.extend(f"  - {key}" for key in remaining_media)
            # else: drop the `media:` key entirely — an empty list would be
            # written as `media: []`, which the schema also accepts, but
            # omitting it matches how a text-only fragment looks elsewhere.
            continue
        out.append(line)
        i += 1

    path.write_text("\n".join(out) + body, encoding="utf-8")


def git_remove(rel_path: str) -> None:
    result = git(["rm", "-f", "--", rel_path])
    if result.returncode == 0:
        return
    abs_path = REPO_ROOT / rel_path
    if abs_path.exists():
        abs_path.unlink()
        log(f"{rel_path} was untracked — removed from disk directly")
    else:
        log(f"{rel_path} already absent")


def commit_and_push(fragment_id: str, no_push: bool) -> bool:
    run_git(["commit", "-m", f"delete audio for {fragment_id} (transcript kept)"])
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


def delete_audio(fragment_id: str, force: bool, no_push: bool) -> dict:
    fragment_path = find_fragment_file(fragment_id)
    frontmatter, _body = parse_frontmatter(fragment_path)
    media = frontmatter.get("media") or []

    audio_paths = [m for m in media if str(m).startswith("media/audio/")]
    if not audio_paths:
        raise IngestError(f"fragment {fragment_id} has no local audio media to delete")

    transcript_path = TRANSCRIPTS_DIR / f"{fragment_id}.txt"
    if not transcript_path.exists() and not force:
        raise IngestError(
            f"no saved transcript at transcripts/{fragment_id}.txt — transcribe and save it first, "
            "or re-run with --force to delete the audio anyway"
        )

    remaining_media = [m for m in media if str(m) not in audio_paths]

    for rel in audio_paths:
        git_remove(rel)
        log(f"removed local audio {rel}")

    rewrite_fragment_media(fragment_path, remaining_media)
    fragment_rel = str(fragment_path.relative_to(REPO_ROOT)).replace("\\", "/")
    run_git(["add", fragment_rel])
    log(f"updated frontmatter in {fragment_rel}")

    pushed = commit_and_push(fragment_id, no_push)

    return {
        "ok": True,
        "id": fragment_id,
        "audio_deleted": audio_paths,
        "transcript_kept": str(transcript_path.exists()),
        "committed": True,
        "pushed": pushed,
        "forced": not transcript_path.exists() and force,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete a fragment's local audio, keeping its transcript.")
    parser.add_argument("fragment_id")
    parser.add_argument("--force", action="store_true", help="delete even with no saved transcript")
    parser.add_argument("--no-push", action="store_true", help="commit locally but do not push")
    parser.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    args = parser.parse_args()

    try:
        result = delete_audio(args.fragment_id, args.force, args.no_push)
    except Exception as exc:  # noqa: BLE001
        message = redact_secrets(str(exc))
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(f"[delete-audio] ERROR: {message}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result))
    else:
        log(f"done: {result}")


if __name__ == "__main__":
    main()
