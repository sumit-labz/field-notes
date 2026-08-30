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
import time
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
# Video and audio are NOT uploaded to R2 (photos are). They are saved as-is
# into these repo-committed folders and referenced by a repo-relative path in
# the fragment frontmatter (e.g. "media/video/2026-08-28-192734.mp4"). The site
# tells local media from R2 keys by the "media/" prefix — see the fragments
# schema in site/src/content/config.ts and site/src/lib/local-media.ts.
MEDIA_DIR = REPO_ROOT / "media"
VIDEO_DIR = MEDIA_DIR / "video"
AUDIO_DIR = MEDIA_DIR / "audio"
JOURNEYS_CONFIG_PATH = REPO_ROOT / "config" / "journeys.yml"

TELEGRAM_API = "https://api.telegram.org"
IST = timezone(timedelta(hours=5, minutes=30))
MAX_LONG_EDGE = 1600
WEBP_QUALITY = 80
TELEGRAM_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Bot API getFile hard limit
EXIF_DATE_TIME_ORIGINAL = 36867
SECRET_ENV_VARS = ("TELEGRAM_BOT_TOKEN", "R2_ACCESS_KEY", "R2_SECRET_KEY")
PUSH_RETRY_ATTEMPTS = 3
PUSH_RETRY_BACKOFF_SECONDS = 5
UNMERGED_STATUS_CODES = {"UU", "AA", "DD", "AU", "UA", "DU", "UD"}
# Never let a git call block on an interactive prompt — this only ever runs
# unattended (CI, or a human running it locally who still shouldn't get
# stuck on an editor for a routine rebase --continue).
GIT_ENV = {**os.environ, "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"}


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


def telegram_download(config: Config, file_id: str) -> tuple[bytes, str]:
    """Return (content, telegram_file_path). The file_path suffix is Telegram's
    own extension for the file (e.g. "voice/file_5.oga", "videos/file_3.mp4")
    and is used to name locally-saved video/audio media."""
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
            timeout=120,
        )
        download.raise_for_status()
        return download.content, file_path
    except requests.RequestException as exc:
        raise IngestError(f"failed to download telegram file: {redact_secrets(str(exc))}") from exc


def telegram_file_bytes(config: Config, file_id: str) -> bytes:
    content, _ = telegram_download(config, file_id)
    return content


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
    """Every message becomes its own fragment — one entry, one segment. No
    grouping is applied: photo albums split into a fragment per photo, and a
    caption/text sent alongside media stays its own fragment. (A caption typed
    ON a photo is part of that single photo message and still rides with it;
    only *separate* messages are kept apart.)"""
    messages = sorted((u["message"] for u in updates), key=lambda m: m["date"])
    return [[message] for message in messages]


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


def media_extension(file_path: str, fallback: str) -> str:
    """Extension (without dot) for a locally-saved media file, taken from
    Telegram's file_path, falling back when it carries none."""
    suffix = Path(file_path).suffix.lstrip(".").lower()
    return suffix or fallback


def media_too_large(media_obj: dict) -> bool:
    """Telegram's Bot API refuses getFile downloads over 20 MB. Media objects
    (video/audio/document) carry file_size; when present and over the cap, the
    download is impossible so callers skip it instead of failing the batch."""
    size = media_obj.get("file_size")
    return isinstance(size, int) and size > TELEGRAM_MAX_DOWNLOAD_BYTES


def write_local_media(dir_path: Path, filename: str, data: bytes) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / filename).write_bytes(data)


# One piece of media collected from a Telegram message, tagged with how it
# should be persisted. Photos are resized to WebP and uploaded to R2; video and
# audio are saved raw into the repo (no R2). `data` is the bytes to persist and
# `ext` is only meaningful for the local kinds.
@dataclass
class MediaItem:
    kind: str  # "photo" | "video" | "audio"
    data: bytes
    ext: str = ""


def build_fragment(client, config: Config, group: list[dict], journey_map: dict[str, str]) -> Fragment:
    first = group[0]
    caption = first.get("caption") or first.get("text") or ""

    items: list[MediaItem] = []
    exif_dt: datetime | None = None

    for message in group:
        photo = message.get("photo")
        document = message.get("document")
        doc_mime = (document.get("mime_type") or "") if document else ""
        # Accept video/audio sent either as a native Telegram media message OR
        # as an uploaded file (document) with a video/* or audio/* mime type.
        video = message.get("video") or message.get("video_note")
        if not video and document and doc_mime.startswith("video/"):
            video = document
        audio = message.get("voice") or message.get("audio")
        if not audio and document and doc_mime.startswith("audio/"):
            audio = document

        if photo:
            file_id = photo[-1]["file_id"]  # largest size Telegram offers
            original = telegram_file_bytes(config, file_id)
            if exif_dt is None:
                exif_dt = extract_exif_datetime(original)
            items.append(MediaItem(kind="photo", data=resize_to_webp(original)))
        elif document and doc_mime.startswith("image/"):
            file_id = document["file_id"]
            original = telegram_file_bytes(config, file_id)
            if exif_dt is None:
                exif_dt = extract_exif_datetime(original)
            items.append(MediaItem(kind="photo", data=resize_to_webp(original)))
        elif video:
            # Telegram's Bot API caps file downloads at 20 MB. A larger video
            # can't be fetched by any bot, so don't abort the whole run over it
            # (that would just retry-loop until the message ages out) — skip the
            # media with a visible warning and keep the caption as text.
            if media_too_large(video):
                print(f"[ingest] message {message.get('message_id')}: video exceeds Telegram's 20MB bot-download limit — media skipped, caption kept")
                continue
            # Saved as-is into the repo, NOT uploaded to R2 (see MEDIA_DIR).
            data, file_path = telegram_download(config, video["file_id"])
            items.append(MediaItem(kind="video", data=data, ext=media_extension(file_path, "mp4")))
        elif audio:
            if media_too_large(audio):
                print(f"[ingest] message {message.get('message_id')}: audio exceeds Telegram's 20MB bot-download limit — media skipped, caption kept")
                continue
            data, file_path = telegram_download(config, audio["file_id"])
            items.append(MediaItem(kind="audio", data=data, ext=media_extension(file_path, "oga")))
        else:
            continue

    captured_at = exif_dt or datetime.fromtimestamp(first["date"], tz=timezone.utc).astimezone(IST)
    fragment_id, path, date_str, time_str = fragment_id_and_path(captured_at)

    # Fragment type is the kind of its first media item; a group with no media
    # is a plain text fragment. (Groups are homogeneous in practice — several
    # photos, or a single video/voice note.)
    fragment_type = items[0].kind if items else "text"

    media_keys: list[str] = []
    photo_index = 0
    local_index = 0
    for item in items:
        if item.kind == "photo":
            photo_index += 1
            key = f"f/{date_str}/{time_str}-{photo_index}.webp"
            upload_to_r2(client, config, key, item.data)
            media_keys.append(key)
        else:
            local_index += 1
            dir_path = VIDEO_DIR if item.kind == "video" else AUDIO_DIR
            suffix = "" if local_index == 1 else f"-{local_index}"
            filename = f"{fragment_id}{suffix}.{item.ext}"
            write_local_media(dir_path, filename, item.data)
            # Repo-relative POSIX path, matching what the site expects.
            media_keys.append(f"media/{item.kind}/{filename}")

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


def git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, env=GIT_ENV)


def run_git(args: list[str]) -> None:
    result = git(args)
    if result.returncode != 0:
        raise IngestError(f"git {args[0]} failed: {result.stderr.strip()}")


def unmerged_paths() -> set[str]:
    result = git(["status", "--porcelain=v1"])
    paths = set()
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:]
        if code in UNMERGED_STATUS_CODES:
            paths.add(path)
    return paths


def rebase_onto_remote(new_offset: int) -> None:
    pull = git(["pull", "--rebase", "--autostash", "origin", "main"])
    if pull.returncode == 0:
        return

    conflicts = unmerged_paths()
    offset_rel = str(STATE_PATH.relative_to(REPO_ROOT)).replace("\\", "/")

    if conflicts != {offset_rel}:
        git(["rebase", "--abort"])  # best-effort cleanup; the real error is below
        raise IngestError(f"git pull --rebase failed: {pull.stderr.strip()}")

    # The offset file is the one place a rebase can conflict — e.g. a
    # concurrent run already advanced past a different batch. Don't let git
    # text-merge the two JSON values: our in-memory new_offset, computed
    # from this run's own getUpdates call, is the only value that's
    # actually correct for the batch this run processed. Re-write it
    # directly and continue the rebase rather than trusting the merge.
    write_offset(new_offset)
    run_git(["add", offset_rel])
    run_git(["rebase", "--continue"])


def commit_and_push(fragment_count: int, new_offset: int) -> None:
    # "media/" holds locally-saved video/audio. Only stage it when it exists —
    # a photos/text-only batch never creates it, and `git add` of a missing
    # pathspec would fail the run.
    add_paths = ["fragments", str(STATE_PATH.relative_to(REPO_ROOT))]
    if MEDIA_DIR.exists():
        add_paths.append("media")
    run_git(["add", *add_paths])
    run_git(
        [
            "-c", "user.name=field-notes-ingest",
            "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", f"ingest: {fragment_count} fragments",
        ]
    )
    push_with_retry(new_offset)


def push_with_retry(new_offset: int) -> None:
    last_error = ""
    for attempt in range(1, PUSH_RETRY_ATTEMPTS + 1):
        result = git(["push"])
        if result.returncode == 0:
            if attempt > 1:
                print(f"[ingest] push succeeded on attempt {attempt}/{PUSH_RETRY_ATTEMPTS}")
            return

        last_error = result.stderr.strip()
        if attempt == PUSH_RETRY_ATTEMPTS:
            break

        print(f"[ingest] push rejected (attempt {attempt}/{PUSH_RETRY_ATTEMPTS}): {last_error}")
        print("[ingest] pulling and rebasing before retry")
        rebase_onto_remote(new_offset)
        time.sleep(PUSH_RETRY_BACKOFF_SECONDS * attempt)

    raise IngestError(f"git push failed after {PUSH_RETRY_ATTEMPTS} attempts: {last_error}")


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
    commit_and_push(len(fragments), new_offset)
    print(f"[ingest] committed {len(fragments)} fragments, offset now {new_offset}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — any failure must be reported, never swallowed
        report_failure(exc)
        sys.exit(1)
