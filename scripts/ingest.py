#!/usr/bin/env python3
"""Pull new Telegram messages into fragments/, per SPEC.md §4.

Run by .github/workflows/ingest.yml on a 15-minute cron and on
workflow_dispatch. Not run directly against production secrets outside CI
except for local testing with your own .env.

Safety invariants this script is built around:

- The persisted offset in .state/telegram-offset.json is only written, and
  only committed, after every fragment in this run's batch has had its media
  uploaded to R2 and its markdown file written. If anything fails partway,
  nothing is committed and the offset file is untouched — the next run reads
  the same old offset and asks Telegram for the same batch again. Telegram
  only treats updates as delivered once a *later* offset is actually passed
  to getUpdates, and we only ever pass the last-committed offset, so a crash
  really does mean "try the same batch again," not "some messages vanish."
  R2 object keys are deterministic from the message timestamp, so a retried
  upload safely overwrites the same key rather than duplicating anything.

- Any exception aborts the whole run, opens a GitHub issue, and exits
  non-zero. Never fails silently — getUpdates discards messages older than
  24h, so a quiet break loses data permanently.

- Sender id is checked before anything else touches a message.

- Secret values are redacted from anything that might get logged or posted
  to a GitHub issue, since this repo and its Actions logs are public. The
  Telegram file-download URL embeds the bot token directly, which is the
  easiest place to leak it by accident, so that call is wrapped explicitly.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import requests
import yaml
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / ".state" / "telegram-offset.json"
FRAGMENTS_DIR = REPO_ROOT / "fragments"
JOURNEYS_CONFIG_PATH = REPO_ROOT / "config" / "journeys.yml"

TELEGRAM_API = "https://api.telegram.org"
IST = timezone(timedelta(hours=5, minutes=30))
GROUP_WINDOW_SECONDS = 120
MAX_LONG_EDGE = 1600
WEBP_QUALITY = 80
EXIF_DATE_TIME_ORIGINAL = 36867
SECRET_ENV_VARS = ("TELEGRAM_BOT_TOKEN", "R2_ACCESS_KEY", "R2_SECRET_KEY")


class IngestError(Exception):
    """Any failure that should open a GitHub issue and abort the run."""


def redact_secrets(text: str) -> str:
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name)
        if value:
            text = text.replace(value, f"***{name}***")
    return text


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise IngestError(f"missing required environment variable {name}")
    return value


@dataclass
class Config:
    telegram_token: str
    allowed_user_id: int
    r2_access_key: str
    r2_secret_key: str
    r2_endpoint: str
    r2_bucket: str


def load_config() -> Config:
    return Config(
        telegram_token=require_env("TELEGRAM_BOT_TOKEN"),
        allowed_user_id=int(require_env("TELEGRAM_ALLOWED_USER_ID")),
        r2_access_key=require_env("R2_ACCESS_KEY"),
        r2_secret_key=require_env("R2_SECRET_KEY"),
        r2_endpoint=require_env("R2_ENDPOINT"),
        r2_bucket=require_env("R2_BUCKET"),
    )


@dataclass
class Fragment:
    id: str
    captured_at: datetime
    type: str
    media: list[str]
    journey: str | None
    spark: bool
    body: str
    path: Path


# ---------------------------------------------------------------------------
# offset


def read_offset() -> int | None:
    if not STATE_PATH.exists():
        return None
    data = json.loads(STATE_PATH.read_text())
    return data.get("offset")


def write_offset(offset: int) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"offset": offset}) + "\n")


# ---------------------------------------------------------------------------
# telegram


def fetch_updates(config: Config, offset: int | None) -> list[dict]:
    params: dict[str, object] = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    try:
        resp = requests.get(
            f"{TELEGRAM_API}/bot{config.telegram_token}/getUpdates",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise IngestError(f"getUpdates failed: {redact_secrets(str(exc))}") from exc

    body = resp.json()
    if not body.get("ok"):
        raise IngestError(f"getUpdates returned an error: {body.get('description', 'unknown')}")
    return body["result"]


def telegram_file_bytes(config: Config, file_id: str) -> bytes:
    try:
        resp = requests.get(
            f"{TELEGRAM_API}/bot{config.telegram_token}/getFile",
            params={"file_id": file_id},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise IngestError(f"getFile failed: {body.get('description', 'unknown')}")
        file_path = body["result"]["file_path"]

        # This URL embeds the bot token (…/file/bot<TOKEN>/<path>). Never let
        # it, or an exception formatted from it, reach a print() or issue body.
        download = requests.get(
            f"{TELEGRAM_API}/file/bot{config.telegram_token}/{file_path}",
            timeout=60,
        )
        download.raise_for_status()
        return download.content
    except requests.RequestException as exc:
        raise IngestError(f"failed to download telegram file: {redact_secrets(str(exc))}") from exc


def message_sender_id(message: dict) -> int | None:
    return (message.get("from") or {}).get("id")


def split_by_sender(updates: list[dict], allowed_user_id: int) -> tuple[list[dict], list[dict]]:
    """Reject anything not from the allowed sender before it touches any
    grouping, download, or upload logic. The bot is discoverable; the
    archive is not a public inbox."""
    accepted, rejected = [], []
    for update in updates:
        message = update.get("message")
        if not message:
            print(f"[ingest] skipping update {update.get('update_id')}: not a plain message")
            rejected.append(update)
            continue
        sender_id = message_sender_id(message)
        if sender_id != allowed_user_id:
            print(f"[ingest] rejecting update {update.get('update_id')}: sender {sender_id} not allowed")
            rejected.append(update)
            continue
        accepted.append(update)
    return accepted, rejected


def group_messages(updates: list[dict]) -> list[list[dict]]:
    """Same media_group_id, OR same sender within a 120s window, become one
    fragment (SPEC.md §4 step 3)."""
    messages = sorted((u["message"] for u in updates), key=lambda m: m["date"])
    groups: list[list[dict]] = []

    for message in messages:
        media_group_id = message.get("media_group_id")
        if media_group_id is not None:
            existing = next(
                (g for g in groups if g[0].get("media_group_id") == media_group_id), None
            )
            if existing is not None:
                existing.append(message)
            else:
                groups.append([message])
            continue

        if groups:
            last_group = groups[-1]
            last_message = last_group[-1]
            same_sender = message_sender_id(last_message) == message_sender_id(message)
            within_window = message["date"] - last_message["date"] <= GROUP_WINDOW_SECONDS
            not_a_media_group = last_group[0].get("media_group_id") is None
            if same_sender and within_window and not_a_media_group:
                last_group.append(message)
                continue

        groups.append([message])

    return groups


# ---------------------------------------------------------------------------
# image handling


def resize_to_webp(image_bytes: bytes) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        long_edge = max(img.size)
        if long_edge > MAX_LONG_EDGE:
            scale = MAX_LONG_EDGE / long_edge
            new_size = (round(img.width * scale), round(img.height * scale))
            img = img.resize(new_size, Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="WEBP", quality=WEBP_QUALITY)
        return buffer.getvalue()


def extract_exif_datetime(image_bytes: bytes) -> datetime | None:
    """EXIF DateTimeOriginal if present; caller falls back to the Telegram
    message date otherwise (SPEC.md §4 step 6)."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            exif = img.getexif()
            if not exif:
                return None
            exif_ifd = exif.get_ifd(0x8769)  # Exif sub-IFD
            raw = exif_ifd.get(EXIF_DATE_TIME_ORIGINAL) or exif.get(EXIF_DATE_TIME_ORIGINAL)
            if not raw:
                return None
            naive = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
            return naive.replace(tzinfo=IST)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# R2


def r2_client(config: Config):
    return boto3.client(
        "s3",
        endpoint_url=config.r2_endpoint,
        aws_access_key_id=config.r2_access_key,
        aws_secret_access_key=config.r2_secret_key,
        region_name="auto",
    )


def upload_to_r2(client, config: Config, key: str, data: bytes) -> None:
    try:
        client.put_object(Bucket=config.r2_bucket, Key=key, Body=data, ContentType="image/webp")
    except Exception as exc:
        raise IngestError(f"failed to upload {key} to R2: {exc}") from exc


# ---------------------------------------------------------------------------
# caption parsing


HASHTAG_RE = re.compile(r"#(\w+)")


def load_journey_map() -> dict[str, str]:
    if not JOURNEYS_CONFIG_PATH.exists():
        return {}
    data = yaml.safe_load(JOURNEYS_CONFIG_PATH.read_text()) or {}
    return {str(k): str(v) for k, v in data.items()}


def parse_caption(caption: str, journey_map: dict[str, str]) -> tuple[str, str | None, bool]:
    """SPEC.md §3.1: a hashtag matching config/journeys.yml sets journey and
    is stripped; an unmatched hashtag stays in the body verbatim. "!" sets
    spark and is stripped."""
    spark = "!" in caption
    body = caption.replace("!", "")

    journey: str | None = None

    def replace(match: re.Match) -> str:
        nonlocal journey
        tag = match.group(1)
        if tag in journey_map:
            if journey is None:
                journey = journey_map[tag]
            return ""
        return match.group(0)

    body = HASHTAG_RE.sub(replace, body)
    body = re.sub(r"[ \t]+", " ", body).strip()
    return body, journey, spark


# ---------------------------------------------------------------------------
# fragment assembly


def fragment_id_and_path(captured_at: datetime) -> tuple[str, Path, str, str]:
    date_str = captured_at.strftime("%Y-%m-%d")
    time_str = captured_at.strftime("%H%M%S")
    fragment_id = f"{date_str}-{time_str}"
    path = FRAGMENTS_DIR / date_str / f"{time_str}.md"
    return fragment_id, path, date_str, time_str


def build_fragment(client, config: Config, group: list[dict], journey_map: dict[str, str]) -> Fragment:
    first = group[0]
    caption = first.get("caption") or first.get("text") or ""

    fragment_type = "text"
    raw_media: list[bytes] = []
    exif_dt: datetime | None = None

    for message in group:
        photo = message.get("photo")
        document = message.get("document")
        if photo:
            file_id = photo[-1]["file_id"]  # largest size Telegram offers
        elif document and (document.get("mime_type") or "").startswith("image/"):
            file_id = document["file_id"]
        elif message.get("voice") or message.get("video") or message.get("video_note") or message.get("audio"):
            # Explicitly out of v1 scope (SPEC.md §9). Skipped, not silently
            # dropped: this print is the signal that something didn't make
            # it into the archive, visible in the Actions log for this run.
            print(f"[ingest] message {message.get('message_id')} has unsupported media (voice/video/audio) — caption kept, media skipped")
            continue
        else:
            continue

        original = telegram_file_bytes(config, file_id)
        if exif_dt is None:
            exif_dt = extract_exif_datetime(original)
        raw_media.append(resize_to_webp(original))
        fragment_type = "photo"

    captured_at = exif_dt or datetime.fromtimestamp(first["date"], tz=timezone.utc).astimezone(IST)
    fragment_id, path, date_str, time_str = fragment_id_and_path(captured_at)

    media_keys: list[str] = []
    for index, webp_bytes in enumerate(raw_media, start=1):
        key = f"f/{date_str}/{time_str}-{index}.webp"
        upload_to_r2(client, config, key, webp_bytes)
        media_keys.append(key)

    body, journey, spark = parse_caption(caption, journey_map)

    return Fragment(
        id=fragment_id,
        captured_at=captured_at,
        type=fragment_type,
        media=media_keys,
        journey=journey,
        spark=spark,
        body=body,
        path=path,
    )


def render_fragment(fragment: Fragment) -> str:
    lines = ["---", f"id: {fragment.id}"]
    lines.append(f"captured_at: {fragment.captured_at.strftime('%Y-%m-%dT%H:%M:%S')}+05:30")
    lines.append(f"type: {fragment.type}")
    if fragment.media:
        lines.append("media:")
        lines.extend(f"  - {key}" for key in fragment.media)
    lines.append(f"journey: {fragment.journey if fragment.journey else 'null'}")
    lines.append(f"spark: {'true' if fragment.spark else 'false'}")
    lines.append("consumed_by: null")
    lines.append("source: telegram")
    lines.append("---")
    lines.append("")
    lines.append(fragment.body)
    lines.append("")
    return "\n".join(lines)


def write_fragment_file(fragment: Fragment) -> None:
    fragment.path.parent.mkdir(parents=True, exist_ok=True)
    fragment.path.write_text(render_fragment(fragment), encoding="utf-8")


# ---------------------------------------------------------------------------
# git


def run_git(args: list[str]) -> None:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise IngestError(f"git {args[0]} failed: {result.stderr.strip()}")


def commit_and_push(fragment_count: int) -> None:
    run_git(["add", "fragments", str(STATE_PATH.relative_to(REPO_ROOT))])
    run_git(
        [
            "-c", "user.name=field-notes-ingest",
            "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", f"ingest: {fragment_count} fragments",
        ]
    )
    run_git(["push"])


# ---------------------------------------------------------------------------
# failure reporting


def report_failure(exc: Exception) -> None:
    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    title = f"ingest failed {date_str}"
    body = f"```\n{type(exc).__name__}: {redact_secrets(str(exc))}\n```"

    github_token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not github_token or not repo:
        print(f"[ingest] {title}\n{body}", file=sys.stderr)
        print("[ingest] GITHUB_TOKEN or GITHUB_REPOSITORY not set — could not open an issue", file=sys.stderr)
        return

    try:
        resp = requests.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
            },
            json={"title": title, "body": body},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as issue_exc:
        print(f"[ingest] {title}\n{body}", file=sys.stderr)
        print(f"[ingest] failed to open GitHub issue: {redact_secrets(str(issue_exc))}", file=sys.stderr)


# ---------------------------------------------------------------------------
# main


def main() -> None:
    config = load_config()
    offset = read_offset()

    updates = fetch_updates(config, offset)
    if not updates:
        print("[ingest] no new messages")
        return  # SPEC.md §4 step 11: exit without committing

    accepted, _rejected = split_by_sender(updates, config.allowed_user_id)
    groups = group_messages(accepted)
    journey_map = load_journey_map()
    client = r2_client(config)

    # All media uploads for every group in this batch must succeed before any
    # fragment file is written, and every file must be written before the
    # offset is persisted — see the module docstring for why.
    fragments = [build_fragment(client, config, group, journey_map) for group in groups]
    for fragment in fragments:
        write_fragment_file(fragment)

    new_offset = max(u["update_id"] for u in updates) + 1
    write_offset(new_offset)
    commit_and_push(len(fragments))
    print(f"[ingest] committed {len(fragments)} fragments, offset now {new_offset}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — any failure must be reported, never swallowed
        report_failure(exc)
        sys.exit(1)
