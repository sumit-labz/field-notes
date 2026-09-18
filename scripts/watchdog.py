#!/usr/bin/env python3
"""Watchdog for scripts/bot.py: checks the FieldNotesPublishWatcher task is
actually alive, restarts it if not, and pings Telegram on any state change
(down->restarted, or restart failed). Silent when everything is fine.

Intended to run every few minutes via its own Scheduled Task
(FieldNotesBotWatchdog) — see scripts/register_watchdog.ps1.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from ingest import load_config, TELEGRAM_API  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
STATE_FILE = Path(__file__).parent / ".watchdog_state"
TASK_NAME = "FieldNotesPublishWatcher"

# Windows only: keeps the powershell.exe helper calls below from flashing a
# console window when run from pythonw.exe under Task Scheduler.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _run_powershell(command: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def bot_is_running() -> bool:
    result = _run_powershell(
        "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
        "| Where-Object {$_.CommandLine -like '*bot.py*'} "
        "| Measure-Object).Count",
        timeout=30,
    )
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return False


def restart_bot() -> bool:
    result = _run_powershell(f"Start-ScheduledTask -TaskName '{TASK_NAME}'", timeout=30)
    return result.returncode == 0


def notify(config, text: str) -> None:
    try:
        requests.post(
            f"{TELEGRAM_API}/bot{config.telegram_token}/sendMessage",
            json={"chat_id": config.allowed_user_id, "text": text},
            timeout=15,
        )
    except Exception:
        pass  # best-effort; don't let notify failure crash the watchdog


def main() -> None:
    last_state = STATE_FILE.read_text().strip() if STATE_FILE.exists() else "up"
    running = bot_is_running()

    if running:
        if last_state == "down":
            STATE_FILE.write_text("up")
        return

    config = load_config()
    restarted = restart_bot()
    if restarted:
        notify(config, "watchdog: bot.py wasn't running — restarted it.")
        STATE_FILE.write_text("up")
    else:
        if last_state != "down":
            notify(config, "watchdog: bot.py is down and the restart attempt FAILED. Check the machine.")
        STATE_FILE.write_text("down")


if __name__ == "__main__":
    main()
