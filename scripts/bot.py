#!/usr/bin/env python3
"""Interactive Telegram bot: capture + a tap-to-select publish wizard.

Usage:
    python scripts/bot.py [--once]

This is the machine's SOLE Telegram consumer (replaces publish_watcher.py; the
CI ingest cron stays disabled). It long-polls getUpdates and:

  - ingests any media/text you send into fragments (reusing ingest.py), exactly
    as the old pipeline did;
  - when you REPLY to a voice note / text / photo with `/publish`, walks an
    inline-button wizard: Obsession → Journey → Cover → Title → Confirm, then
    publishes at the `raw` self-editing stage and replies with the post URL.

Content types:
  - voice/audio → transcribe + self-editing-pass raw cleanup (via the
    `/publish-last-audio` slash command, run through `claude -p`);
  - text       → the message text becomes the body (raw);
  - photo      → the photo is the cover; its caption becomes the body.

Only the `raw` stage is offered for now (the deeper feedback/self-edited rungs
need you in the loop — do those in a Claude session).

State is in-memory per chat; a restart drops any half-finished wizard (just
resend `/publish`). Env comes from the repo-root .env via ingest.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import ingest
from ingest import (
    IST, REPO_ROOT, TELEGRAM_API, DEFAULT_COVER_ID,
    load_config, r2_client, load_journey_map, redact_secrets,
    build_fragment, build_marginalia, write_fragment_file, write_marginalia_file,
    commit_and_push, read_offset, write_offset, fragment_id_and_path,
    parse_publish_command, MEDIA_DIR, MARGINALIA_DIR,
)
from delete_fragment import parse_frontmatter

JOURNEYS_DIR = REPO_ROOT / "journeys"
IDENTITIES_DIR = REPO_ROOT / "identities"
FRAGMENTS_DIR = REPO_ROOT / "fragments"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CLAUDE_ARGS = os.environ.get("CLAUDE_PUBLISH_ARGS", "--dangerously-skip-permissions")
PY = sys.executable
PUBLISHED_RE = re.compile(r"PUBLISHED:\s*(\S+)")

# In-memory wizard state, keyed by chat_id.
STATE: dict[int, dict] = {}


def log(msg: str) -> None:
    print(f"[bot {datetime.now(tz=IST).strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# journeys / identities (for the buttons)

def load_journeys() -> list[dict]:
    out = []
    if not JOURNEYS_DIR.exists():
        return out
    for path in sorted(JOURNEYS_DIR.glob("*.md")):
        try:
            fm = parse_frontmatter(path)
        except Exception:
            continue
        if fm.get("status") == "active" and fm.get("slug"):
            out.append({"slug": str(fm["slug"]), "identity": str(fm.get("identity") or "")})
    return out


def load_obsessions(journeys: list[dict]) -> list[str]:
    slugs = set()
    if IDENTITIES_DIR.exists():
        for path in sorted(IDENTITIES_DIR.glob("*.md")):
            try:
                fm = parse_frontmatter(path)
            except Exception:
                continue
            if fm.get("slug"):
                slugs.add(str(fm["slug"]))
    # Union in any identity a journey claims (e.g. studio-zero) so every journey
    # is reachable from an obsession button.
    for j in journeys:
        if j["identity"]:
            slugs.add(j["identity"])
    return sorted(slugs)


def newest_photo_fragment() -> str | None:
    if not FRAGMENTS_DIR.exists():
        return None
    best = None
    for path in FRAGMENTS_DIR.glob("*/*.md"):
        try:
            fm = parse_frontmatter(path)
        except Exception:
            continue
        if fm.get("type") == "photo" and fm.get("id"):
            fid = str(fm["id"])
            if best is None or fid > best:
                best = fid
    return best


# ---------------------------------------------------------------------------
# telegram api

def tg(method: str, **params) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    resp = requests.post(f"{TELEGRAM_API}/bot{token}/{method}", json=params, timeout=60)
    body = resp.json()
    if not body.get("ok"):
        log(f"telegram {method} error: {body.get('description')}")
    return body


def kb(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for (t, d) in row] for row in rows]}


def send_message(chat_id: int, text: str, keyboard: dict | None = None, reply_to: int | None = None) -> int | None:
    params: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if keyboard:
        params["reply_markup"] = keyboard
    if reply_to:
        params["reply_to_message_id"] = reply_to
    body = tg("sendMessage", **params)
    return (body.get("result") or {}).get("message_id")


def edit_message(chat_id: int, message_id: int, text: str, keyboard: dict | None = None) -> None:
    params: dict = {"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": True}
    params["reply_markup"] = keyboard or {"inline_keyboard": []}
    tg("editMessageText", **params)


def answer_callback(cb_id: str, text: str | None = None) -> None:
    params: dict = {"callback_query_id": cb_id}
    if text:
        params["text"] = text
    tg("answerCallbackQuery", **params)


# ---------------------------------------------------------------------------
# wizard

def chunk(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def render_obsession(chat_id: int, st: dict) -> None:
    obs = load_obsessions(st["journeys"])
    rows = chunk([(o, f"obs:{o}") for o in obs], 2)
    text = f"Publishing your {st['kind']}.\n\n① Obsession — which area?"
    _render_step(chat_id, st, text, rows)


def render_journey(chat_id: int, st: dict, show_all: bool = False) -> None:
    journeys = st["journeys"]
    if not show_all and st.get("obsession"):
        sel = [j for j in journeys if j["identity"] == st["obsession"]]
    else:
        sel = journeys
    rows = chunk([(j["slug"], f"jou:{j['slug']}") for j in sel], 2)
    nav = [("‹ back", "nav:obs")]
    if not show_all:
        nav.append(("▸ all journeys", "jou:__all"))
    rows.append(nav)
    scope = "all journeys" if show_all else f"in {st['obsession']}"
    _render_step(chat_id, st, f"② Journey ({scope}) — required.", rows)


def render_cover(chat_id: int, st: dict) -> None:
    # Photo posts are their own cover; skip straight to title.
    if st["kind"] == "photo":
        st["cover_id"] = st["content_id"]
        return render_title(chat_id, st)
    rows = [[(f"default ({DEFAULT_COVER_ID})", "cov:default")]]
    newest = st.get("newest_photo")
    if newest and newest != DEFAULT_COVER_ID:
        rows.append([(f"latest photo ({newest})", f"cov:{newest}")])
    rows.append([("no cover", "cov:none")])
    rows.append([("‹ back", "nav:jou")])
    _render_step(chat_id, st, "③ Cover photo?", rows)


def render_title(chat_id: int, st: dict) -> None:
    st["awaiting_title"] = True
    rows = [[("auto-generate", "ttl:auto")], [("‹ back", "nav:cov")]]
    _render_step(chat_id, st, "④ Title — send it as a message, or tap auto-generate.", rows)


def render_confirm(chat_id: int, st: dict) -> None:
    st["awaiting_title"] = False
    cover = st.get("cover_id") or "none"
    obs = st.get("obsession") or "—"
    # obsession is only a real cross-tag when it differs from the journey's home
    home = next((j["identity"] for j in st["journeys"] if j["slug"] == st.get("journey")), None)
    cross = st.get("obsession") if st.get("obsession") and st.get("obsession") != home else None
    lines = [
        "⑤ Ready to publish (raw):",
        f"• kind: {st['kind']}",
        f"• journey: {st.get('journey')}",
        f"• obsession: {obs}" + ("" if cross else "  (home — not cross-tagged)"),
        f"• cover: {cover}",
        f"• title: {st.get('title') or '(auto)'}",
    ]
    st["cross_obsession"] = cross
    rows = [[("✓ publish", "pub:go"), ("✕ cancel", "pub:cancel")], [("‹ back", "nav:ttl")]]
    _render_step(chat_id, st, "\n".join(lines), rows)


def _render_step(chat_id: int, st: dict, text: str, rows: list) -> None:
    keyboard = kb(rows)
    if st.get("msg_id"):
        edit_message(chat_id, st["msg_id"], text, keyboard)
    else:
        st["msg_id"] = send_message(chat_id, text, keyboard, reply_to=st.get("trigger_msg_id"))


def start_wizard(chat_id: int, content: dict, trigger_msg_id: int) -> None:
    STATE[chat_id] = {
        "step": "obs",
        "kind": content["kind"],
        "content_id": content["id"],
        "body": content.get("body", ""),
        "journeys": load_journeys(),
        "newest_photo": newest_photo_fragment(),
        "trigger_msg_id": trigger_msg_id,
        "msg_id": None,
        "cover_id": None,
        "obsession": None,
        "journey": None,
        "title": None,
    }
    render_obsession(chat_id, STATE[chat_id])


def handle_callback(cb: dict) -> None:
    data = cb.get("data") or ""
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    answer_callback(cb["id"])
    st = STATE.get(chat_id)
    if not st:
        return
    kind, _, value = data.partition(":")

    if kind == "nav":
        st["awaiting_title"] = False
        return {"obs": render_obsession, "jou": render_journey, "cov": render_cover, "ttl": render_title}[value](chat_id, st)
    if kind == "obs":
        st["obsession"] = value
        st["step"] = "jou"
        return render_journey(chat_id, st)
    if kind == "jou":
        if value == "__all":
            return render_journey(chat_id, st, show_all=True)
        st["journey"] = value
        st["step"] = "cov"
        return render_cover(chat_id, st)
    if kind == "cov":
        st["cover_id"] = None if value == "none" else (DEFAULT_COVER_ID if value == "default" else value)
        st["step"] = "ttl"
        return render_title(chat_id, st)
    if kind == "ttl" and value == "auto":
        st["title"] = None
        st["step"] = "confirm"
        return render_confirm(chat_id, st)
    if kind == "pub":
        if value == "cancel":
            edit_message(chat_id, st["msg_id"], "✕ Cancelled.")
            STATE.pop(chat_id, None)
            return
        if value == "go":
            threading.Thread(target=do_publish, args=(chat_id, dict(st)), daemon=True).start()
            STATE.pop(chat_id, None)  # wizard done; thread owns the message now
            return


def handle_title_text(chat_id: int, text: str) -> None:
    st = STATE.get(chat_id)
    if not st or not st.get("awaiting_title"):
        return
    st["title"] = text.strip()
    st["step"] = "confirm"
    render_confirm(chat_id, st)


# ---------------------------------------------------------------------------
# publish execution (threaded)

def do_publish(chat_id: int, st: dict) -> None:
    msg_id = st["msg_id"]
    edit_message(chat_id, msg_id, "⏳ Publishing… (transcribe → raw cleanup → grade → post)")
    try:
        slug = _run_publish(st)
    except Exception as exc:  # noqa: BLE001
        log(f"publish failed: {redact_secrets(str(exc))}")
        edit_message(chat_id, msg_id, f"❌ Publish failed:\n{redact_secrets(str(exc))[:500]}")
        return
    edit_message(chat_id, msg_id, f"✅ Published: {slug}\n/posts/{slug}/\n(site will rebuild)")


def _run_publish(st: dict) -> str:
    if st["kind"] == "audio":
        return _publish_audio(st)
    return _publish_direct(st)


def _publish_audio(st: dict) -> str:
    parts = ["/publish-last-audio", f"--audio {st['content_id']}"]
    if st.get("cover_id"):
        parts.append(f"--cover {st['cover_id']}")
    if st.get("journey"):
        parts.append(f"--journey {st['journey']}")
    if st.get("cross_obsession"):
        parts.append(f"--obsession {st['cross_obsession']}")
    if st.get("title"):
        parts.append(f'--title "{st["title"]}"')
    prompt = " ".join(parts)
    cmd = [CLAUDE_BIN, *CLAUDE_ARGS.split(), "-p", prompt]
    log(f"claude publish: {prompt}")
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800)
    out = (result.stdout or "") + "\n" + (result.stderr or "")
    m = PUBLISHED_RE.search(out)
    if result.returncode == 0 and m:
        return m.group(1)
    tail = "\n".join(out.strip().splitlines()[-6:])
    raise RuntimeError(f"claude exit {result.returncode}: {tail[-400:]}")


def _publish_direct(st: dict) -> str:
    """Text/photo posts: no transcription, no skill — the body is the message
    text (or a photo's caption), published raw via publish_post.py."""
    body = (st.get("body") or "").strip() or (st.get("title") or "Untitled")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(body)
        body_file = fh.name
    try:
        cmd = [PY, str(REPO_ROOT / "scripts" / "publish_post.py"),
               "--body-file", body_file, "--stage", "raw", "--json"]
        cover = st["content_id"] if st["kind"] == "photo" else st.get("cover_id")
        if cover:
            cmd += ["--cover-id", cover]
        if st.get("journey"):
            cmd += ["--journey", st["journey"]]
        if st.get("cross_obsession"):
            cmd += ["--obsession", st["cross_obsession"]]
        if st.get("title"):
            cmd += ["--title", st["title"]]
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=600)
        line = (result.stdout or "").strip().splitlines()[-1] if result.stdout.strip() else ""
        try:
            data = json.loads(line)
        except Exception:
            raise RuntimeError((result.stderr or result.stdout or "publish_post failed").strip()[-400:])
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "publish_post failed"))
        return data["slug"]
    finally:
        try:
            os.unlink(body_file)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# content resolution + ingest

def resolve_content(message: dict) -> dict | None:
    """From a `/publish` reply, describe the item to publish. Returns None with a
    reason handled by the caller when there's nothing to act on."""
    reply = message.get("reply_to_message")
    if not reply:
        return None
    date = reply.get("date")
    if not isinstance(date, int):
        return None
    captured = datetime.fromtimestamp(date, tz=timezone.utc).astimezone(IST)
    fid = fragment_id_and_path(captured)[0]
    if reply.get("voice") or reply.get("audio"):
        return {"kind": "audio", "id": fid, "body": ""}
    if reply.get("photo"):
        return {"kind": "photo", "id": fid, "body": reply.get("caption") or ""}
    if reply.get("text"):
        return {"kind": "text", "id": fid, "body": reply["text"]}
    return None


def ingest_content_messages(messages: list[dict], config, journey_map) -> None:
    """Turn plain content messages (not /publish, not wizard title input) into
    fragments — same persistence as ingest.main, committed in one batch."""
    if not messages:
        return
    client = r2_client(config)
    fragments, marginalia = [], []
    for m in messages:
        try:
            entry = build_marginalia(client, config, [m])
            if entry is not None:
                marginalia.append(entry)
            else:
                fragments.append(build_fragment(client, config, [m], journey_map))
        except Exception as exc:  # noqa: BLE001 — never let one bad message kill the loop
            log(f"ingest of message {m.get('message_id')} failed: {redact_secrets(str(exc))}")
    for f in fragments:
        write_fragment_file(f)
    for e in marginalia:
        write_marginalia_file(e)
    if fragments or marginalia:
        commit_and_push(len(fragments), len(marginalia), 0)
        log(f"ingested {len(fragments)} fragments, {len(marginalia)} marginalia")


# ---------------------------------------------------------------------------
# main loop

def process_updates(updates: list[dict], config, journey_map) -> None:
    allowed = config.allowed_user_id
    content_msgs: list[dict] = []
    commands: list[dict] = []
    callbacks: list[dict] = []
    title_inputs: list[tuple[int, str]] = []

    for u in updates:
        if "callback_query" in u:
            cb = u["callback_query"]
            if (cb.get("from") or {}).get("id") == allowed:
                callbacks.append(cb)
            continue
        m = u.get("message")
        if not m or (m.get("from") or {}).get("id") != allowed:
            continue
        chat_id = (m.get("chat") or {}).get("id")
        text = m.get("text")
        if parse_publish_command(text) is not None:
            commands.append(m)
        elif chat_id in STATE and STATE[chat_id].get("awaiting_title") and text:
            title_inputs.append((chat_id, text))
        else:
            content_msgs.append(m)

    # Order: ingest content first (so a /publish reply's fragment exists), then
    # commands, then title inputs, then button taps.
    ingest_content_messages(content_msgs, config, journey_map)

    for m in commands:
        chat_id = (m.get("chat") or {}).get("id")
        content = resolve_content(m)
        if content is None:
            send_message(chat_id, "Reply /publish to a voice note, text, or photo you want to publish.",
                         reply_to=m.get("message_id"))
            continue
        start_wizard(chat_id, content, m.get("message_id"))

    for chat_id, text in title_inputs:
        handle_title_text(chat_id, text)
    for cb in callbacks:
        try:
            handle_callback(cb)
        except Exception as exc:  # noqa: BLE001
            log(f"callback error: {redact_secrets(str(exc))}")


def poll_once(config, journey_map) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    offset = read_offset()
    params: dict = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"{TELEGRAM_API}/bot{token}/getUpdates", params=params, timeout=40)
    body = resp.json()
    if not body.get("ok"):
        log(f"getUpdates error: {body.get('description')}")
        return
    updates = body["result"]
    if not updates:
        return
    process_updates(updates, config, journey_map)
    write_offset(max(u["update_id"] for u in updates) + 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive Telegram capture + publish bot.")
    parser.add_argument("--once", action="store_true", help="one long-poll cycle then exit")
    args = parser.parse_args()

    config = load_config()
    journey_map = load_journey_map()
    log(f"bot up; allowed user {config.allowed_user_id}; long-polling")
    if args.once:
        poll_once(config, journey_map)
        return
    while True:
        try:
            poll_once(config, journey_map)
        except KeyboardInterrupt:
            log("interrupted — exiting")
            return
        except Exception as exc:  # noqa: BLE001 — a bad cycle must not kill the bot
            log(f"cycle error (continuing): {redact_secrets(str(exc))}")
            time.sleep(5)


if __name__ == "__main__":
    main()
