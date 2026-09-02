#!/usr/bin/env python3
"""Bake a cinematic color grade into a fragment's photo, in place — overwrites
the R2 object (or the local media/photo/ file) with the graded version.

Usage:
    python scripts/apply_cinematic_grade.py <fragment-id> <media-index> <preset> [--no-push] [--json]

preset is one of: muted, teal_orange, noir (see PRESETS below).

Invoked one-click from the internal fragment inbox (site/src/dev/
openrouter-plugin.mjs -> POST /api/apply-grade), which shows a live CSS-filter
preview first — this script is only run once the maintainer accepts a preset,
and it is what actually changes the stored image. There is no undo: the
original bytes are gone once this runs (R2 put_object overwrites the key; a
local file is overwritten and the previous version only survives in git
history, same as any other tracked-file edit).

Reuses ingest.py's R2 client and delete_fragment.py's fragment-lookup helpers,
same pattern as the other one-off maintenance scripts in this directory.
"""

from __future__ import annotations

import argparse
import io
import json
import sys

from PIL import Image, ImageEnhance, ImageOps

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


PRESETS = {"muted": _muted, "teal_orange": _teal_orange, "noir": _noir}


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
    ingest.upload_to_r2(client, config, key, graded)
    return {
        "ok": True,
        "id": fragment_id,
        "media_index": media_index,
        "preset": preset,
        "key": key,
        "location": "r2",
        "committed": False,
        "pushed": False,
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
