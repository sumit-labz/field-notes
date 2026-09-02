#!/usr/bin/env python3
"""Bake a cinematic color grade into a fragment's photo.

Usage:
    python scripts/apply_cinematic_grade.py <fragment-id> <media-index> <preset> [--no-push] [--json]

preset is one of: muted, teal_orange, noir, grayscale, vivid, sketch (see
PRESETS below).

Invoked one-click from the internal fragment inbox (site/src/dev/
openrouter-plugin.mjs -> POST /api/apply-grade), which shows a live CSS-filter
preview first — this script is only run once the maintainer accepts a preset,
and it is what actually changes the stored image.

R2 photos: uploaded under a NEW key (never the same one twice) and the
fragment file's media[] entry is updated to point at it, then committed and
pushed — overwriting the same key left old cached bytes visible at that URL
indefinitely for anyone who'd already loaded it (browser cache, any CDN in
front of R2), since a PUT to an existing key doesn't reliably invalidate
those. The old object is deleted afterwards (best-effort cleanup).

Local media/photo/ files: overwritten in place and committed — a local dev
path doesn't have the same caching exposure. There is no undo either way: the
original bytes are gone once this runs; only git history keeps the fragment
file's own edit (the media path swap), not the image bytes themselves.

Reuses ingest.py's R2 client and delete_fragment.py's fragment-lookup helpers,
same pattern as the other one-off maintenance scripts in this directory.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

import ingest
from ingest import IngestError, REPO_ROOT, WEBP_QUALITY, redact_secrets, git, run_git
from delete_fragment import find_fragment_file, parse_frontmatter, r2_config_from_env


def log(msg: str) -> None:
    print(f"[grade] {msg}", file=sys.stderr)


# Each preset is a rough PIL equivalent of the CSS filter the inbox previews
# live (see the `data-grade-css` values in fragments.astro) — "rough" because
# a CSS filter chain and a real pixel transform are never going to match
# exactly, especially the split-tone. Close enough to not surprise anyone
# comparing the preview to the accepted result.
def _muted(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(0.55)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Brightness(img).enhance(0.97)
    return img


def _noir(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(0.15)
    img = ImageEnhance.Contrast(img).enhance(1.25)
    img = ImageEnhance.Brightness(img).enhance(0.92)
    return img


def _teal_orange(img: Image.Image) -> Image.Image:
    # Cheap split-tone: a black-to-white gradient map colorized teal->orange,
    # blended back over the original at low opacity. Not true per-channel
    # shadow/highlight grading, but reads as a warm/cool cinematic cast
    # without pulling in a numpy dependency for real curves.
    gray = ImageOps.grayscale(img)
    duotone = ImageOps.colorize(gray, black="#0a2a2e", white="#f2c98a", mid="#7c7568").convert(img.mode)
    graded = Image.blend(img, duotone, alpha=0.35)
    return ImageEnhance.Contrast(graded).enhance(1.05)


def _grayscale(img: Image.Image) -> Image.Image:
    # True black & white (not just heavily desaturated, like noir) — converted
    # back to the original mode so saving stays consistent with every other
    # preset here.
    gray = ImageOps.grayscale(img).convert(img.mode)
    return ImageEnhance.Contrast(gray).enhance(1.05)


def _vivid(img: Image.Image) -> Image.Image:
    # The opposite of muted: punchier color, not a cinematic look so much as
    # a "make it pop" one.
    img = ImageEnhance.Color(img).enhance(1.5)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Brightness(img).enhance(1.03)
    return img


def _sketch(img: Image.Image) -> Image.Image:
    # For photographed pencil/ink sketches: autocontrast stretches the
    # existing tonal range so faint graphite darkens and the paper actually
    # whitens (a photo of a sketch is usually low-contrast to begin with —
    # cheap unfiltered daylight, not a scanner), then unsharp-mask crisps up
    # the linework itself. This is the one preset CSS categorically can't
    # approximate (no filter does per-pixel local contrast/sharpening), so
    # its CSS preview is the roughest match of the six.
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))


PRESETS = {
    "muted": _muted,
    "teal_orange": _teal_orange,
    "noir": _noir,
    "grayscale": _grayscale,
    "vivid": _vivid,
    "sketch": _sketch,
}


def grade_bytes(data: bytes, preset: str) -> bytes:
    fn = PRESETS.get(preset)
    if fn is None:
        raise IngestError(f"unknown preset {preset!r} — choose one of: {', '.join(PRESETS)}")
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        graded = fn(img)
        buffer = io.BytesIO()
        graded.save(buffer, format="WEBP", quality=WEBP_QUALITY)
        return buffer.getvalue()


def versioned_key(key: str, preset: str) -> str:
    """A new R2 key for the graded bytes — never the same key twice, so
    nothing (browser cache, any CDN in front of R2) can serve stale bytes for
    a URL that used to point at the un-graded photo. Overwriting the SAME key
    looked like it worked (the object really is updated), but any client that
    already had that exact URL cached — including a plain page reload, no
    special action needed — kept showing the old image indefinitely, since
    the URL itself never changed."""
    dot = key.rfind(".")
    stem, ext = (key[:dot], key[dot:]) if dot != -1 else (key, "")
    return f"{stem}-{preset}-{int(time.time())}{ext}"


def update_fragment_media_key(fragment_path, old_key: str, new_key: str) -> None:
    """Point the fragment's media[] entry at the new key. A plain text
    replace, not a YAML re-serialize — the media key is a unique URL-like
    string, so an exact-match replace can't clobber anything else in the file
    and leaves formatting/comments/everything else untouched."""
    text = fragment_path.read_text(encoding="utf-8")
    marker = f"- {old_key}"
    replacement = f"- {new_key}"
    if marker not in text:
        raise IngestError(f"could not find media entry {old_key!r} in {fragment_path} to update")
    fragment_path.write_text(text.replace(marker, replacement, 1), encoding="utf-8")


def commit_and_push_local(rel_path: str, fragment_id: str, preset: str, no_push: bool) -> bool:
    run_git(["add", rel_path])
    run_git(["commit", "-m", f"grade: {preset} on {fragment_id}"])
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


def apply_grade(fragment_id: str, media_index: int, preset: str, no_push: bool) -> dict:
    fragment_path = find_fragment_file(fragment_id)
    frontmatter = parse_frontmatter(fragment_path)
    media = frontmatter.get("media") or []
    if not (0 <= media_index < len(media)):
        raise IngestError(f"fragment {fragment_id} has no media[{media_index}] (media has {len(media)} entries)")

    key = str(media[media_index])
    if not key.lower().endswith((".webp", ".jpg", ".jpeg", ".png")):
        raise IngestError(f"media[{media_index}] ({key}) doesn't look like a photo")

    is_local = key.startswith("media/")

    if is_local:
        abs_path = REPO_ROOT / key
        if not abs_path.exists():
            raise IngestError(f"local media file missing: {key}")
        original = abs_path.read_bytes()
        graded = grade_bytes(original, preset)
        abs_path.write_bytes(graded)
        pushed = commit_and_push_local(key, fragment_id, preset, no_push)
        return {
            "ok": True,
            "id": fragment_id,
            "media_index": media_index,
            "preset": preset,
            "key": key,
            "location": "local",
            "committed": True,
            "pushed": pushed,
        }

    config = r2_config_from_env()
    client = ingest.r2_client(config)
    try:
        original = client.get_object(Bucket=config.r2_bucket, Key=key)["Body"].read()
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"failed to download {key} from R2: {redact_secrets(str(exc))}") from exc

    graded = grade_bytes(original, preset)
    new_key = versioned_key(key, preset)
    ingest.upload_to_r2(client, config, new_key, graded)

    # Best-effort cleanup of the now-unreferenced old key. Deliberately BEFORE
    # the git commit/push below and independent of whether that succeeds —
    # this is pure R2 housekeeping, unrelated to git. A push can fail for
    # reasons that have nothing to do with the grade itself (no upstream
    # configured, a transient network blip); the old key would otherwise sit
    # around orphaned until the maintainer noticed, for no good reason. Not
    # fatal if the delete itself fails — delete_object is idempotent anyway.
    try:
        client.delete_object(Bucket=config.r2_bucket, Key=key)
    except Exception as exc:  # noqa: BLE001
        log(f"could not delete old R2 object {key} (harmless, just orphaned storage): {redact_secrets(str(exc))}")

    # The fragment file must record the NEW key — everything that renders
    # this photo (the inbox, a public-site rebuild, `mediaUrl()`) reads the
    # key from here, not from R2 directly.
    update_fragment_media_key(fragment_path, key, new_key)
    fragment_rel = str(fragment_path.relative_to(REPO_ROOT)).replace("\\", "/")
    pushed = commit_and_push_local(fragment_rel, fragment_id, preset, no_push)

    return {
        "ok": True,
        "id": fragment_id,
        "media_index": media_index,
        "preset": preset,
        "key": new_key,
        "old_key": key,
        "location": "r2",
        "committed": True,
        "pushed": pushed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bake a cinematic color grade into a fragment's photo, in place.")
    parser.add_argument("fragment_id")
    parser.add_argument("media_index", type=int)
    parser.add_argument("preset", choices=sorted(PRESETS))
    parser.add_argument("--no-push", action="store_true", help="commit locally but do not push (local media only)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    args = parser.parse_args()

    try:
        result = apply_grade(args.fragment_id, args.media_index, args.preset, args.no_push)
    except Exception as exc:  # noqa: BLE001
        message = redact_secrets(str(exc))
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(f"[grade] ERROR: {message}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result))
    else:
        log(f"done: {result}")


if __name__ == "__main__":
    main()
