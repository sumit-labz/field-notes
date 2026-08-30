#!/usr/bin/env python3
"""Delete a fragment everywhere: R2 (photos), local repo files (video/audio),
the fragment markdown itself — then commit and push the removal to GitHub main.

Usage:
    python scripts/delete_fragment.py <fragment-id> [--force] [--json]

This is the counterpart to ingest.py and reuses its R2 client, git helpers and
environment (the repo-root .env with R2_ACCESS_KEY / R2_SECRET_KEY / R2_ENDPOINT
/ R2_BUCKET). It is invoked one-click by the internal fragment inbox
(site/src/dev/openrouter-plugin.mjs -> POST /api/delete-fragment), and is also
runnable directly from a shell.

Safety:
- Refuses to delete a fragment whose id still appears in any posts/*.md (a
  published post references it) unless --force — deleting it would break the
  site build. The inbox never passes --force.
- Deleting from R2 and pushing to main is irreversible; the inbox gates the
  button behind a type-the-id-to-confirm prompt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml

# Sibling module. Importing it runs load_dotenv() (harmless) but NOT main().
import ingest
from ingest import IngestError, REPO_ROOT, FRAGMENTS_DIR, redact_secrets, git, run_git

POSTS_DIR = REPO_ROOT / "posts"
R2_ENV_VARS = ("R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ENDPOINT", "R2_BUCKET")


def log(msg: str) -> None:
    # Human-readable progress goes to stderr; stdout carries the JSON result so
    # the dev endpoint can parse it cleanly.
    print(f"[delete] {msg}", file=sys.stderr)


def r2_config_from_env() -> ingest.Config:
    missing = [name for name in R2_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise IngestError(
            "missing R2 environment variables: "
            + ", ".join(missing)
            + " (set them in the repo-root .env, same as the ingest bot)"
        )
    # ingest.r2_client only reads the R2 fields; telegram fields are unused here.
    return ingest.Config(
        telegram_token="",
        allowed_user_id=0,
        r2_access_key=os.environ["R2_ACCESS_KEY"],
        r2_secret_key=os.environ["R2_SECRET_KEY"],
        r2_endpoint=os.environ["R2_ENDPOINT"],
        r2_bucket=os.environ["R2_BUCKET"],
    )


def find_fragment_file(fragment_id: str) -> Path:
    """Locate fragments/<date>/<time>.md for a fragment id, verifying the id in
    the frontmatter rather than trusting the filename alone."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}", fragment_id):
        raise IngestError(f"not a valid fragment id: {fragment_id!r}")
    date_str, time_str = fragment_id[:10], fragment_id[11:]
    expected = FRAGMENTS_DIR / date_str / f"{time_str}.md"
    if expected.exists():
        return expected
    # Fall back to a scan in case the file was renamed.
    for path in FRAGMENTS_DIR.glob("*/*.md"):
        try:
            fm = parse_frontmatter(path)
        except Exception:
            continue
        if fm.get("id") == fragment_id:
            return path
    raise IngestError(f"no fragment file found for id {fragment_id}")


def parse_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not match:
        raise IngestError(f"{path} has no frontmatter block")
    return yaml.safe_load(match.group(1)) or {}


def posts_referencing(fragment_id: str) -> list[str]:
    if not POSTS_DIR.exists():
        return []
    hits = []
    for post in POSTS_DIR.glob("*.md"):
        if fragment_id in post.read_text(encoding="utf-8"):
            hits.append(post.name)
    return hits


def delete_r2_object(client, bucket: str, key: str) -> None:
    try:
        # Idempotent: S3/R2 delete_object succeeds even if the key is absent.
        client.delete_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"failed to delete R2 object {key}: {redact_secrets(str(exc))}") from exc


def git_remove(rel_path: str) -> None:
    """Remove a tracked file via git; fall back to a plain unlink for anything
    git doesn't track (so an untracked local media file is still removed)."""
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
    run_git(["commit", "-m", f"delete fragment {fragment_id}"])
    if no_push:
        return False
    push = git(["push"])
    if push.returncode == 0:
        return True
    # Someone else advanced main between our commit and push — rebase and retry.
    log("push rejected; pulling --rebase and retrying")
    run_git(["pull", "--rebase", "origin", "main"])
    push = git(["push"])
    if push.returncode != 0:
        raise IngestError(f"git push failed: {push.stderr.strip()}")
    return True


def delete_fragment(fragment_id: str, force: bool, no_push: bool) -> dict:
    fragment_path = find_fragment_file(fragment_id)
    frontmatter = parse_frontmatter(fragment_path)
    media = frontmatter.get("media") or []

    referencing = posts_referencing(fragment_id)
    if referencing and not force:
        raise IngestError(
            f"fragment {fragment_id} is still referenced by: {', '.join(referencing)}. "
            "Deleting it would break those posts. Remove the reference first, or re-run with --force."
        )

    r2_keys = [m for m in media if not str(m).startswith("media/")]
    local_paths = [m for m in media if str(m).startswith("media/")]

    # R2 first: if this fails we abort before touching git, leaving a consistent
    # tree. (Local + git deletion below is what actually removes it from GitHub.)
    if r2_keys:
        config = r2_config_from_env()
        client = ingest.r2_client(config)
        for key in r2_keys:
            delete_r2_object(client, config.r2_bucket, key)
            log(f"deleted R2 object {key}")

    for rel in local_paths:
        git_remove(rel)
        log(f"removed local media {rel}")

    fragment_rel = str(fragment_path.relative_to(REPO_ROOT)).replace("\\", "/")
    git_remove(fragment_rel)
    log(f"removed fragment {fragment_rel}")

    pushed = commit_and_push(fragment_id, no_push)

    return {
        "ok": True,
        "id": fragment_id,
        "r2_deleted": r2_keys,
        "local_deleted": local_paths,
        "fragment_deleted": fragment_rel,
        "committed": True,
        "pushed": pushed,
        "forced": bool(referencing) and force,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete a fragment and its media everywhere.")
    parser.add_argument("fragment_id")
    parser.add_argument("--force", action="store_true", help="delete even if a post references it")
    parser.add_argument("--no-push", action="store_true", help="commit locally but do not push")
    parser.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    args = parser.parse_args()

    try:
        result = delete_fragment(args.fragment_id, args.force, args.no_push)
    except Exception as exc:  # noqa: BLE001
        message = redact_secrets(str(exc))
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(f"[delete] ERROR: {message}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result))
    else:
        log(f"done: {result}")


if __name__ == "__main__":
    main()
