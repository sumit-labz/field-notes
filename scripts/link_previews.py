#!/usr/bin/env python3
"""Fetch link previews once, cache them in config/link-previews.json.

Usage:
    python scripts/link_previews.py            # new links in posts/ only
    python scripts/link_previews.py --refresh  # re-fetch every link

A post line that is just a link — `https://...` or `Label: https://...` —
renders as a preview card (site/src/lib/link-preview.ts). This script fills
the cache those cards read: og:title / og:description / site name, and the
og:image resized to WebP and uploaded to R2 under `l/<hash>.webp` (images
never go in Git — SPEC.md §1). Nothing is fetched at build time, so the
build never depends on someone else's site being up.

A link whose site refuses (Instagram and LinkedIn often sit behind logins) is
still cached, with whatever it gave — the card falls back to label + domain.
Never touches git; publish_post.py / the caller commits the cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from PIL import Image

import ingest  # loads .env on import
from ingest import REPO_ROOT, redact_secrets

POSTS_DIR = REPO_ROOT / "posts"
CACHE_FILE = REPO_ROOT / "config" / "link-previews.json"
URL_RE = re.compile(r"https?://[^\s<]+[^\s<.,)]")
UA = "Mozilla/5.0 (compatible; field-notes link preview; +https://github.com/sumit-labz/field-notes)"
TRACKING = re.compile(r"^(utm_.*|igsh|igsi|fbclid|gclid|si|ref|ref_src)$", re.I)
THUMB_EDGE = 720


def log(msg: str) -> None:
    print(f"[links] {msg}", file=sys.stderr)


def clean_url(url: str) -> str:
    """Drop tracking params so the cache key and the rendered link are tidy."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def meta(html: str, *names: str) -> str | None:
    for name in names:
        for pattern in (
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']*)["\']',
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(name)}["\']',
        ):
            m = re.search(pattern, html, re.I)
            if m and m.group(1).strip():
                return unescape(m.group(1).strip())
    return None


def upload_thumb(image_url: str, key: str) -> str | None:
    try:
        resp = requests.get(image_url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        import io

        with Image.open(io.BytesIO(resp.content)) as img:
            img = img.convert("RGB")
            img.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=80)
        config = ingest.Config(
            telegram_token="",
            allowed_user_id=0,
            r2_access_key=ingest.require_env("R2_ACCESS_KEY"),
            r2_secret_key=ingest.require_env("R2_SECRET_KEY"),
            r2_endpoint=ingest.require_env("R2_ENDPOINT"),
            r2_bucket=ingest.require_env("R2_BUCKET"),
        )
        ingest.upload_to_r2(ingest.r2_client(config), config, key, buf.getvalue())
        return key
    except Exception as exc:  # noqa: BLE001
        log(f"thumbnail skipped for {urlsplit(image_url).netloc}: {redact_secrets(str(exc))}")
        return None


def fetch_preview(url: str) -> dict:
    entry: dict = {
        "title": None,
        "description": None,
        "site": urlsplit(url).netloc.removeprefix("www."),
        "image": None,
        "fetched": datetime.now().strftime("%Y-%m-%d"),
    }
    try:
        resp = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=20)
        resp.raise_for_status()
        html = resp.text[:400_000]
    except Exception as exc:  # noqa: BLE001
        log(f"{entry['site']}: fetch failed ({redact_secrets(str(exc))[:120]})")
        return entry
    title = meta(html, "og:title", "twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = unescape(m.group(1).strip()) if m else None
    entry["title"] = title
    entry["description"] = meta(html, "og:description", "twitter:description", "description")
    entry["site"] = meta(html, "og:site_name") or entry["site"]
    image = meta(html, "og:image", "twitter:image")
    if image:
        if image.startswith("//"):
            image = "https:" + image
        elif image.startswith("/"):
            p = urlsplit(url)
            image = f"{p.scheme}://{p.netloc}{image}"
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        entry["image"] = upload_thumb(image, f"l/{digest}.webp")
    return entry


def post_urls() -> list[str]:
    urls: list[str] = []
    for path in sorted(POSTS_DIR.glob("*.md")):
        for url in URL_RE.findall(path.read_text(encoding="utf-8")):
            cleaned = clean_url(url)
            if cleaned not in urls:
                urls.append(cleaned)
    return urls


def update_cache(refresh: bool = False) -> list[str]:
    """Fetch any uncached link in posts/. Returns the urls fetched."""
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    fetched: list[str] = []
    for url in post_urls():
        if url in cache and not refresh:
            continue
        cache[url] = fetch_preview(url)
        fetched.append(url)
        log(f"{url} -> {cache[url]['title'] or '(no title)'}{' +image' if cache[url]['image'] else ''}")
    if fetched:
        CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return fetched


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    fetched = update_cache(args.refresh)
    log(f"{len(fetched)} fetched; cache -> {CACHE_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
