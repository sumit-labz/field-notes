#!/usr/bin/env python3
"""Always-on local loop: ingest Telegram, then run any queued /publish command
through Claude and reply in Telegram with the result.

Usage:
    python scripts/publish_watcher.py [--once] [--interval 60]

This is the machine's SOLE Telegram consumer (the CI ingest cron is disabled —
see .github/workflows/ingest.yml). Each tick:
  1. runs scripts/ingest.py  (pulls new messages -> fragments + publish queue),
  2. for each file in ~/.field-notes/publish-queue (oldest first), invokes
     `claude -p "/publish-last-audio ..."` in the repo, which transcribes the
     voice note, runs the self-editing-pass raw cleanup, grades the cover, and
     publishes the post,
  3. on success deletes the queue file and Telegram-replies the post URL; on
     failure moves the queue file to publish-queue/failed/ and reports the error.

Environment (repo-root .env, same as ingest):
  TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_ID   — to send the reply
Optional:
  CLAUDE_BIN            (default "claude")            — the Claude Code CLI
  CLAUDE_PUBLISH_ARGS   (default "--dangerously-skip-permissions")
                        — extra CLI args so the run is non-interactive; the
                          command only runs the repo's own scripts + git push
  SITE_BASE_URL         (default "")                  — prepended to /posts/<slug>/
  POLL_SECONDS          (default 60)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import requests

import ingest
from ingest import PUBLISH_QUEUE_DIR, REPO_ROOT, TELEGRAM_API, IST, redact_secrets

FAILED_DIR = PUBLISH_QUEUE_DIR / "failed"
PUBLISHED_RE = re.compile(r"PUBLISHED:\s*(\S+)")


def log(msg: str) -> None:
    from datetime import datetime
    print(f"[watcher {datetime.now(tz=IST).strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def telegram_send(text: str, reply_to: int | None = None) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
    if not token or not chat_id:
        log("TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_USER_ID not set — cannot reply")
        return
    payload: dict[str, object] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    try:
        resp = requests.post(f"{TELEGRAM_API}/bot{token}/sendMessage", json=payload, timeout=30)
        if not resp.ok:
            log(f"telegram sendMessage failed ({resp.status_code}): {redact_secrets(resp.text[:200])}")
    except requests.RequestException as exc:
        log(f"telegram sendMessage error: {redact_secrets(str(exc))}")


def run_ingest() -> None:
    log("running ingest…")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "ingest.py")],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    tail = (result.stdout or result.stderr).strip().splitlines()[-3:]
    for line in tail:
        log(f"ingest: {line}")
    if result.returncode != 0:
        log(f"ingest exited {result.returncode} — continuing; will retry next tick")


def build_publish_prompt(entry: dict) -> str:
    parts = ["/publish-last-audio"]
    if entry.get("audio_id"):
        parts.append(f"--audio {entry['audio_id']}")
    if entry.get("cover_id"):
        parts.append(f"--cover {entry['cover_id']}")
    if entry.get("title"):
        parts.append(f'--title "{entry["title"]}"')
    return " ".join(parts)


def run_claude(prompt: str) -> subprocess.CompletedProcess:
    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    extra = os.environ.get("CLAUDE_PUBLISH_ARGS", "--dangerously-skip-permissions")
    cmd = [claude_bin, *shlex.split(extra), "-p", prompt]
    log(f"running: {claude_bin} … -p {prompt!r}")
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800)


def process_queue_file(path: Path) -> None:
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"unreadable queue file {path.name}: {exc} — moving to failed/")
        _fail(path, f"unreadable queue file: {exc}")
        return

    reply_to = entry.get("command_message_id")
    prompt = build_publish_prompt(entry)
    try:
        result = run_claude(prompt)
    except subprocess.TimeoutExpired:
        log(f"claude timed out on {path.name}")
        telegram_send("⏱️ Publish timed out — the draft may be partly done; check the repo.", reply_to)
        _fail(path, "claude run timed out")
        return

    out = (result.stdout or "") + "\n" + (result.stderr or "")
    match = PUBLISHED_RE.search(out)
    if result.returncode == 0 and match:
        slug = match.group(1)
        base = os.environ.get("SITE_BASE_URL", "").rstrip("/")
        url = f"{base}/posts/{slug}/" if base else f"/posts/{slug}/"
        log(f"published {slug}")
        telegram_send(f"✅ Published: {slug}\n{url}\n(site will rebuild)", reply_to)
        path.unlink(missing_ok=True)
    else:
        tail = "\n".join(out.strip().splitlines()[-8:])
        log(f"publish failed for {path.name} (exit {result.returncode})")
        telegram_send(f"❌ Publish failed (exit {result.returncode}).\n{tail[-600:]}", reply_to)
        _fail(path, f"exit {result.returncode}\n{tail}")


def _fail(path: Path, reason: str) -> None:
    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (FAILED_DIR / (path.stem + ".error.txt")).write_text(reason + "\n", encoding="utf-8")
        path.rename(FAILED_DIR / path.name)
    except Exception as exc:  # noqa: BLE001
        log(f"could not move failed queue file {path.name}: {exc}")
        path.unlink(missing_ok=True)


def queue_files() -> list[Path]:
    if not PUBLISH_QUEUE_DIR.exists():
        return []
    files = [p for p in PUBLISH_QUEUE_DIR.glob("*.json") if p.parent == PUBLISH_QUEUE_DIR]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def tick() -> None:
    run_ingest()
    for path in queue_files():
        process_queue_file(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Telegram-ingest + publish watcher.")
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLL_SECONDS", "60")))
    args = parser.parse_args()

    log(f"watcher up; queue at {PUBLISH_QUEUE_DIR}; interval {args.interval}s")
    if args.once:
        tick()
        return
    while True:
        try:
            tick()
        except KeyboardInterrupt:
            log("interrupted — exiting")
            return
        except Exception as exc:  # noqa: BLE001 — a bad tick must not kill the loop
            log(f"tick error (continuing): {redact_secrets(str(exc))}")
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    main()
