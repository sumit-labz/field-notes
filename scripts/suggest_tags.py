#!/usr/bin/env python3
"""Pick tags for a post from the controlled vocabulary in config/tags.yml.

Usage:
    # one post's tags as JSON (what publish_post.py calls in-process)
    python scripts/suggest_tags.py --title "..." --body-file body.md

    # backfill: suggest for every post, write a review table, touch nothing
    python scripts/suggest_tags.py --all --dry-run --out review.tsv

    # apply a reviewed table (slug<TAB>tag,tag) to posts/*.md frontmatter
    python scripts/suggest_tags.py --apply review.tsv

The model may only choose slugs listed in config/tags.yml; anything else is
dropped. Tags are a reading lens set at publish — never at capture. Reads
OPENROUTER_API_KEY from site/.env (same key as tidy_transcript.py). Never
touches git.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

from tidy_transcript import CHAT_URL, DEFAULT_MODEL, POSTS_DIR, REPO_ROOT, load_api_key

TAGS_FILE = REPO_ROOT / "config" / "tags.yml"
MAX_TAGS = 4
TAG_LINE = re.compile(r"^([a-z0-9-]+):\s*\{\s*label:\s*([^,]+?)\s*,\s*note:\s*(.+?)\s*\}\s*$")


def log(msg: str) -> None:
    print(f"[tags] {msg}", file=sys.stderr)


def load_vocab() -> dict[str, str]:
    """slug -> "label: note", in file order."""
    vocab: dict[str, str] = {}
    for line in TAGS_FILE.read_text(encoding="utf-8").splitlines():
        m = TAG_LINE.match(line)
        if m:
            vocab[m.group(1)] = f"{m.group(2)}: {m.group(3)}"
    return vocab


def suggest(title: str, body: str, model: str = DEFAULT_MODEL) -> list[str]:
    vocab = load_vocab()
    listing = "\n".join(f"- {slug} ({desc})" for slug, desc in vocab.items())
    system = (
        "You tag posts in a personal creative archive. Choose 1 to "
        f"{MAX_TAGS} tags that the post is genuinely about, ONLY from this list:\n"
        f"{listing}\n\n"
        "Reply with a JSON array of slugs and nothing else, e.g. [\"adhd\", \"code\"]."
    )
    resp = requests.post(
        CHAT_URL,
        headers={
            "Authorization": f"Bearer {load_api_key()}",
            "HTTP-Referer": "http://localhost/field-notes",
            "X-Title": "field-notes tags",
        },
        json={
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Title: {title}\n\n{body[:12000]}"},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    m = re.search(r"\[.*?\]", text, re.S)
    picked = json.loads(m.group(0)) if m else []
    out: list[str] = []
    for t in picked:
        if isinstance(t, str) and t in vocab and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def split_post(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        raise ValueError(f"no frontmatter in {path.name}")
    return m.group(1), m.group(2)


def tags_line(tags: list[str]) -> str:
    return f"tags: [{', '.join(tags)}]"


def write_tags(path: Path, tags: list[str]) -> None:
    fm, body = split_post(path)
    lines = [ln for ln in fm.split("\n") if not ln.startswith("tags:")]
    lines.append(tags_line(tags))
    path.write_text("---\n" + "\n".join(lines) + "\n---\n" + body, encoding="utf-8")


def post_title(fm: str) -> str:
    m = re.search(r"(?m)^title:\s*(.+)$", fm)
    if not m:
        return ""
    raw = m.group(1).strip()
    return json.loads(raw) if raw.startswith('"') else raw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--title", default="")
    ap.add_argument("--body-file")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="tag-review.tsv")
    ap.add_argument("--apply")
    args = ap.parse_args()

    if args.apply:
        vocab = load_vocab()
        for line in Path(args.apply).read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            slug, _, rest = line.partition("\t")
            tags = [t.strip() for t in rest.split("\t")[0].split(",") if t.strip()]
            bad = [t for t in tags if t not in vocab]
            if bad:
                sys.exit(f"[tags] {slug}: not in vocabulary: {bad}")
            write_tags(POSTS_DIR / f"{slug}.md", tags)
            log(f"{slug}: {', '.join(tags) or '(none)'}")
        return

    if args.all:
        rows = ["# slug\ttags\ttitle"]
        for path in sorted(POSTS_DIR.glob("*.md")):
            fm, body = split_post(path)
            title = post_title(fm)
            try:
                tags = suggest(title, body)
            except Exception as exc:  # noqa: BLE001
                log(f"{path.stem}: failed ({exc})")
                tags = []
            rows.append(f"{path.stem}\t{','.join(tags)}\t{title}")
            log(f"{path.stem}: {', '.join(tags)}")
        Path(args.out).write_text("\n".join(rows) + "\n", encoding="utf-8")
        log(f"review table -> {args.out} (nothing applied{'' if args.dry_run else '; use --apply'})")
        return

    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else ""
    print(json.dumps(suggest(args.title, body)))


if __name__ == "__main__":
    main()
