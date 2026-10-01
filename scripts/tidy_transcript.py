#!/usr/bin/env python3
"""Clean-verbatim pass: tidy a spoken transcript into a readable post body.

Usage:
    # a raw transcript file → tidied text on stdout (what the bot runs)
    python scripts/tidy_transcript.py --file raw.txt [--out body.txt] [--json]

    # an already-published post, rewritten in place (made_with → tidied)
    python scripts/tidy_transcript.py --post <slug> [--dry-run] [--json]

The rules are NOT in this file: they're read from docs/editing/clean-verbatim.md
(everything from "## Rules" down) so there is one source of truth for every
path that edits a voice note. Fragment markers, "> " quotes, ">> " pull-quotes,
URLs-only lines and italic notes are shielded behind placeholders before the
model sees the text and must all come back unchanged, or the run fails.

Reads OPENROUTER_API_KEY from site/.env (same key as transcribe.py). Never
touches git — publish_post.py / the caller commits.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_FILE = REPO_ROOT / "docs" / "editing" / "clean-verbatim.md"
POSTS_DIR = REPO_ROOT / "posts"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
GENERATION_URL = "https://openrouter.ai/api/v1/generation"
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
INR_PER_USD = 87.5
STAGE = "tidied"


class TidyError(Exception):
    pass


def log(msg: str) -> None:
    print(f"[tidy] {msg}", file=sys.stderr)


def load_api_key() -> str:
    load_dotenv(REPO_ROOT / "site" / ".env", override=False)
    load_dotenv(REPO_ROOT / ".env", override=False)
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise TidyError("OPENROUTER_API_KEY not set — add it to site/.env")
    return key


def load_rules() -> str:
    text = RULES_FILE.read_text(encoding="utf-8")
    i = text.find("## Rules")
    if i == -1:
        raise TidyError(f"no '## Rules' section in {RULES_FILE}")
    return text[i:].strip()


# --- shielding ------------------------------------------------------------------

def _is_protected(block: str) -> bool:
    s = block.strip()
    return (
        s.startswith(">")
        or re.fullmatch(r"\{\{fragment:[^}]+\}\}", s) is not None
        or re.fullmatch(r"https?://\S+", s) is not None
        or (s.startswith("*") and s.endswith("*") and not s.startswith("**"))
        or re.match(r"^[A-Za-z ]+:\s*https?://", s) is not None  # "Label: url" lines
    )


def shield(body: str) -> tuple[str, dict[str, str]]:
    """Swap every protected block for ⟦Pn⟧ so the model can't touch it."""
    blocks = re.split(r"\n\s*\n", body.strip())
    saved: dict[str, str] = {}
    out = []
    for b in blocks:
        if _is_protected(b):
            token = f"⟦P{len(saved)}⟧"
            saved[token] = b.strip()
            out.append(token)
        else:
            out.append(b.strip())
    return "\n\n".join(out), saved


def unshield(text: str, saved: dict[str, str]) -> str:
    for token, block in saved.items():
        if text.count(token) != 1:
            raise TidyError(f"model dropped or duplicated protected block {token}")
        text = text.replace(token, block)
    return text


# --- the call ---------------------------------------------------------------------

def fetch_cost_inr(api_key: str, gen_id: str | None) -> float | None:
    if not gen_id:
        return None
    for _ in range(8):
        r = requests.get(GENERATION_URL, params={"id": gen_id},
                         headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        if r.ok:
            cost = (r.json().get("data") or {}).get("total_cost")
            if cost is not None:
                return round(float(cost) * INR_PER_USD, 2)
        time.sleep(2)
    return None


def tidy_text(raw: str, model: str = DEFAULT_MODEL) -> dict:
    api_key = load_api_key()
    shielded, saved = shield(raw)
    note = (
        "\n\nTokens like ⟦P0⟧ stand for blocks you must not see or change: keep "
        "each one exactly once, on its own line, in the same place."
        if saved else ""
    )
    resp = requests.post(
        CHAT_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "http://localhost/field-notes",
            "X-Title": "field-notes tidy",
        },
        json={
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": load_rules() + note},
                {"role": "user", "content": shielded},
            ],
        },
        timeout=600,
    )
    try:
        body = resp.json()
    except ValueError as exc:
        raise TidyError(f"OpenRouter returned non-JSON ({resp.status_code}): {resp.text[:300]}") from exc
    if not resp.ok:
        err = body.get("error")
        raise TidyError((err.get("message") if isinstance(err, dict) else err) or f"error {resp.status_code}")
    text = (body["choices"][0]["message"]["content"] or "").strip()
    if not text:
        raise TidyError("model returned empty text")
    text = unshield(text, saved)
    # sanity: clean verbatim only ever removes; a big growth means it rewrote
    raw_words, new_words = len(raw.split()), len(text.split())
    if new_words > raw_words * 1.05 or new_words < raw_words * 0.55:
        raise TidyError(f"word count moved too far ({raw_words} → {new_words}); refusing")
    return {
        "text": text,
        "model": model,
        "words_before": raw_words,
        "words_after": new_words,
        "cost_inr": fetch_cost_inr(api_key, body.get("id")),
    }


# --- post mode --------------------------------------------------------------------

def split_post(path: Path) -> tuple[str, str]:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n.*?\n---\n", raw, re.S)
    if not m:
        raise TidyError(f"{path.name}: no frontmatter")
    return raw[: m.end()], raw[m.end():]


def tidy_post(slug: str, model: str, dry_run: bool) -> dict:
    path = POSTS_DIR / f"{slug}.md"
    if not path.exists():
        matches = list(POSTS_DIR.glob(f"*{slug}*.md"))
        if len(matches) != 1:
            raise TidyError(f"no single post matching {slug!r}")
        path = matches[0]
    fm, body = split_post(path)
    result = tidy_text(body, model)
    # frontmatter ends just before the closing --- line
    head, close = fm[: fm.rstrip("\n").rfind("\n---")], "\n---\n"
    if re.search(r"^made_with:.*$", head, re.M):
        head = re.sub(r"^made_with:.*$", f"made_with: {STAGE}", head, flags=re.M)
    else:
        head += f"\nmade_with: {STAGE}"
    # how many spoken words came out — the badge shows it (cumulative if re-run)
    removed = max(0, result["words_before"] - result["words_after"])
    prev = re.search(r"^trimmed:\s*(\d+)\s*$", head, re.M)
    total = removed + (int(prev.group(1)) if prev else 0)
    head = re.sub(r"\ntrimmed:.*", "", head) + f"\ntrimmed: {total}"
    out = head + close + "\n" + result["text"] + "\n"
    if not dry_run:
        path.write_text(out, encoding="utf-8", newline="\n")
    result.update({"post": path.name, "written": not dry_run})
    if dry_run:
        result["preview"] = out
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="raw transcript text file")
    src.add_argument("--post", help="post slug (or unique part of the filename) to tidy in place")
    p.add_argument("--out", help="write tidied text here (file mode)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true", help="post mode: don't write, print preview")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    try:
        if a.post:
            res = tidy_post(a.post, a.model, a.dry_run)
        else:
            res = tidy_text(Path(a.file).read_text(encoding="utf-8"), a.model)
            if a.out:
                Path(a.out).write_text(res["text"] + "\n", encoding="utf-8")
    except (TidyError, requests.RequestException) as exc:
        if a.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            log(f"error: {exc}")
        sys.exit(1)
    if a.json:
        print(json.dumps({"ok": True, **res}, ensure_ascii=False))
    else:
        log(f"{res['words_before']} → {res['words_after']} words · ~₹{res['cost_inr']}")
        if a.post and a.dry_run:
            print(res["preview"])
        elif not a.out:
            print(res["text"])


if __name__ == "__main__":
    main()
