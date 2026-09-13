#!/usr/bin/env python3
"""Publish an edited voice note as a blog post — in one commit.

Usage:
    python scripts/publish_post.py \
        --audio-id <fragment-id> \
        --body-file <path>          # the edited prose -> post body \
        [--transcript-file <path>]  # the raw transcript -> "before cleanup" \
        [--cover-id <fragment-id>]  # a photo fragment -> hero/opener \
        [--title "My Title"] [--slug <slug>] \
        [--journey <slug>] [--obsession <slug>] \
        [--stage raw|self-edited|feedback|unaided]  (default raw) \
        [--published YYYY-MM-DD] [--no-push] [--json]

The last step of the Telegram /publish chain (transcribe -> self-editing-pass
-> apply grade -> HERE). Writes:
  - transcripts/<audio-id>.txt  (raw, referenced by the post's `transcript:`),
  - posts/<slug>.md             (frontmatter + edited body),
and stamps `consumed_by: <slug>` on the audio fragment for provenance — all in
a single commit, then pushes (unless --no-push).

Mirrors save_fragment_text.py / delete_fragment.py: reuses ingest's git helpers
and delete_fragment's fragment-lookup + frontmatter parsing. The cover photo is
referenced (as `cover:` and in `fragments:`) but never marked consumed — a cover
is reusable decoration, not source content.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import ingest
from ingest import IST, IngestError, REPO_ROOT, redact_secrets, git, run_git
from delete_fragment import find_fragment_file, parse_frontmatter

POSTS_DIR = REPO_ROOT / "posts"
TRANSCRIPTS_DIR = REPO_ROOT / "transcripts"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}$")
STAGES = ("raw", "self-edited", "feedback", "unaided")


def log(msg: str) -> None:
    print(f"[publish] {msg}", file=sys.stderr)


def slugify(text: str, max_words: int = 8) -> str:
    words = re.sub(r"[^a-z0-9\s-]", "", text.lower()).split()
    return "-".join(words[:max_words]).strip("-") or "untitled"


def derive_title(body: str) -> str:
    """First sentence of the body, trimmed to a headline-ish length."""
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "Untitled")
    # Cut at the first sentence end if there is one reasonably early.
    m = re.search(r"[.!?]", first)
    if m and m.start() < 80:
        first = first[: m.start()]
    words = first.split()
    if len(words) > 10:
        first = " ".join(words[:10])
    return first.strip().rstrip(".!?,;:") or "Untitled"


def unique_post_path(slug: str) -> tuple[str, Path]:
    """Return a (slug, path) that doesn't collide with an existing post."""
    candidate = slug
    n = 2
    while (POSTS_DIR / f"{candidate}.md").exists():
        candidate = f"{slug}-{n}"
        n += 1
    return candidate, POSTS_DIR / f"{candidate}.md"


def yaml_str(value: str) -> str:
    """Double-quote + escape a scalar so titles with colons/quotes stay valid."""
    return json.dumps(value)


def render_post(
    *,
    slug: str,
    title: str,
    published: str,
    journey: str | None,
    obsession: str | None,
    fragments: list[str],
    transcript_name: str | None,
    cover_id: str | None,
    stage: str,
    body: str,
) -> str:
    lines = ["---", f"slug: {slug}", f"title: {yaml_str(title)}", f"published: {published}"]
    lines.append(f"journey: {journey if journey else 'null'}")
    if obsession:
        lines.append(f"obsession: {obsession}")
    if fragments:
        lines.append("fragments:")
        lines.extend(f"  - {fid}" for fid in fragments)
    else:
        lines.append("fragments: []")
    lines.append("status: published")
    lines.append("kind: post")
    if transcript_name:
        lines.append(f"transcript: {transcript_name}")
    if cover_id:
        lines.append(f"cover: {cover_id}")
    lines.append(f"made_with: {stage}")
    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    return "\n".join(lines)


def stamp_consumed_by(audio_id: str, slug: str) -> str | None:
    """Set consumed_by: <slug> on the audio fragment if it's currently null.
    Returns the repo-relative path if changed, else None (and logs why)."""
    try:
        frag_path = find_fragment_file(audio_id)
    except IngestError as exc:
        log(f"could not locate audio fragment to stamp consumed_by: {exc}")
        return None
    text = frag_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(frag_path)
    existing = fm.get("consumed_by")
    if existing:
        log(f"audio fragment {audio_id} already consumed_by {existing!r} — leaving as is")
        return None
    new_text, count = re.subn(r"(?m)^consumed_by:\s*null\s*$", f"consumed_by: {slug}", text)
    if count != 1:
        log(f"audio fragment {audio_id}: no `consumed_by: null` line to update — skipped")
        return None
    frag_path.write_text(new_text, encoding="utf-8")
    return str(frag_path.relative_to(REPO_ROOT)).replace("\\", "/")


def commit_and_push(slug: str, rel_paths: list[str], no_push: bool) -> bool:
    run_git(["add", *rel_paths])
    run_git(["commit", "-m", f"publish: {slug}"])
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


def publish(args: argparse.Namespace) -> dict:
    if not ID_RE.match(args.audio_id):
        raise IngestError(f"not a valid audio fragment id: {args.audio_id!r}")
    if args.cover_id and not ID_RE.match(args.cover_id):
        raise IngestError(f"not a valid cover fragment id: {args.cover_id!r}")
    if args.stage not in STAGES:
        raise IngestError(f"invalid stage {args.stage!r}; expected one of {', '.join(STAGES)}")

    body = Path(args.body_file).read_text(encoding="utf-8").strip()
    if not body:
        raise IngestError(f"body file is empty: {args.body_file}")

    published = args.published or datetime.now(IST).strftime("%Y-%m-%d")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", published):
        raise IngestError(f"--published must be YYYY-MM-DD, got {published!r}")

    title = (args.title or "").strip() or derive_title(body)
    base_slug = args.slug.strip() if args.slug else f"{published}-{slugify(title)}"
    slug, post_path = unique_post_path(base_slug)

    rel_paths: list[str] = []

    # Raw transcript -> transcripts/<audio-id>.txt, referenced by `transcript:`.
    transcript_name: str | None = None
    if args.transcript_file:
        raw = Path(args.transcript_file).read_text(encoding="utf-8").rstrip() + "\n"
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        transcript_name = f"{args.audio_id}.txt"
        (TRANSCRIPTS_DIR / transcript_name).write_text(raw, encoding="utf-8")
        rel_paths.append(f"transcripts/{transcript_name}")

    fragments = [args.cover_id] if args.cover_id else []
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    post_path.write_text(
        render_post(
            slug=slug,
            title=title,
            published=published,
            journey=args.journey,
            obsession=args.obsession,
            fragments=fragments,
            transcript_name=transcript_name,
            cover_id=args.cover_id,
            stage=args.stage,
            body=body,
        ),
        encoding="utf-8",
    )
    rel_paths.append(f"posts/{slug}.md")
    log(f"wrote posts/{slug}.md ({len(body)} chars, cover={args.cover_id or 'none'})")

    consumed_rel = stamp_consumed_by(args.audio_id, slug)
    if consumed_rel:
        rel_paths.append(consumed_rel)

    pushed = commit_and_push(slug, rel_paths, args.no_push)
    return {
        "ok": True,
        "slug": slug,
        "title": title,
        "published": published,
        "post": f"posts/{slug}.md",
        "transcript": transcript_name,
        "cover": args.cover_id,
        "audio_id": args.audio_id,
        "stage": args.stage,
        "consumed_marked": bool(consumed_rel),
        "committed": True,
        "pushed": pushed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish an edited voice note as a blog post.")
    parser.add_argument("--audio-id", required=True)
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--transcript-file")
    parser.add_argument("--cover-id")
    parser.add_argument("--title")
    parser.add_argument("--slug")
    parser.add_argument("--journey")
    parser.add_argument("--obsession")
    parser.add_argument("--stage", default="raw")
    parser.add_argument("--published")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = publish(args)
    except Exception as exc:  # noqa: BLE001
        message = redact_secrets(str(exc))
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(f"[publish] ERROR: {message}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result) if args.json else f"[publish] done: {result}")


if __name__ == "__main__":
    main()
