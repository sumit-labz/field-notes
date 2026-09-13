#!/usr/bin/env python3
"""Register the bot's Telegram command menu (setMyCommands).

Usage:
    python scripts/set_bot_commands.py [--json]

Makes `/publish` show up as a tap-to-use suggestion with a hint whenever you
type `/` in the chat, so the syntax is discoverable from the phone. Purely a UX
convenience — the ingest/publish pipeline works without it.

Reuses ingest's config/env (reads TELEGRAM_BOT_TOKEN from the repo-root .env, the
same key ingest uses) and its redaction, so the token never lands in output. Safe
to re-run: setMyCommands replaces the whole list each call.
"""

from __future__ import annotations

import argparse
import json
import sys

import requests

import ingest
from ingest import IngestError, TELEGRAM_API, require_env, redact_secrets

# Telegram command names: lowercase, 1-32 chars, [a-z0-9_]. Descriptions 1-256.
COMMANDS = [
    {
        "command": "publish",
        "description": "Reply to a voice note to publish it. Optional: /publish <photo-id> | Title",
    },
]


def set_commands() -> dict:
    token = require_env("TELEGRAM_BOT_TOKEN")
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/bot{token}/setMyCommands",
            json={"commands": COMMANDS},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        raise IngestError(f"setMyCommands failed: {redact_secrets(str(exc))}") from exc
    if not body.get("ok"):
        raise IngestError(f"setMyCommands returned an error: {body.get('description', 'unknown')}")
    return {"ok": True, "commands": [c["command"] for c in COMMANDS]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Register the bot's Telegram command menu.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = set_commands()
    except Exception as exc:  # noqa: BLE001
        message = redact_secrets(str(exc))
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(f"[commands] ERROR: {message}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result) if args.json else f"[commands] registered: {result['commands']}")


if __name__ == "__main__":
    main()
