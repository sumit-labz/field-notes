#!/usr/bin/env python3
"""Bake a cinematic color grade into a fragment's photo — non-destructively.

Usage:
    python scripts/apply_cinematic_grade.py <fragment-id> <media-index> <preset> [--no-push] [--json]

preset is one of: muted, teal_orange, noir, grayscale, vivid, sketch, or the
special value "original" (see PRESETS and REVERT below).

Invoked one-click from the internal fragment inbox (site/src/dev/
openrouter-plugin.mjs -> POST /api/apply-grade), which shows a live CSS-filter
preview first — this script is only run once the maintainer accepts a preset,
and it is what actually changes the stored image.

media[media_index] in the fragment's frontmatter is the ORIGINAL and this
script never touches it. Grading instead maintains a separate "graded" map in
the frontmatter (see site/src/content/config.ts) pointing each graded photo's
index at a derived copy; a preset always grades fresh from the untouched
original, uploads the result under a brand-new key (never the same key
twice — see graded_key_for), deletes whichever graded copy previously existed
for that index (independent of git succeeding — pure storage housekeeping),
and updates the map. "original" (preset="original") reverts: it removes the
index's map entry and deletes its graded copy, so rendering falls back to the
untouched media[index] — nothing is ever re-derived FROM a graded copy, only
ever from the true original, so repeated grading/reverting never compounds
quality loss or drifts from what was actually captured.

The new-key-per-grade behavior matters even with the original preserved:
overwriting the same key for the graded copy would have the identical stale-
cache problem this whole design avoids for the original — any client that
already loaded that URL keeps serving its own cached bytes indefinitely.

Local media/photo/ files get the same treatment (a derived path, original
untouched) for consistency, even though a local dev path doesn't have the
same caching exposure.

Reuses ingest.py's R2 client and delete_fragment.py's fragment-lookup helpers,
same pattern as the other one-off maintenance scripts in this directory.
"""

from __future__ import annotations

import argparse
import io
import json
import re
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

# Not a real grade — see REVERT handling in apply_grade().
REVERT = "original"


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


def graded_key_for(original_key: str, preset: str) -> str:
    """A new key for a graded copy — never the same key twice (name + preset
    + timestamp), so nothing (browser cache, any CDN) can serve stale bytes
    for a URL that used to point at a different grade. Works for both an R2
    key and a local media/photo/ path — it's a plain string transform,
    doesn't care which."""
    dot = original_key.rfind(".")
    stem, ext = (original_key[:dot], original_key[dot:]) if dot != -1 else (original_key, "")
    return f"{stem}-{preset}-{int(time.time())}{ext}"


# --- the `graded:` frontmatter block: { media[] index (as a string) -> graded
# key }. Parsed/written via targeted text surgery, not a full YAML
# round-trip, so every OTHER field's exact formatting (captured_at's
# timezone offset, unquoted nulls, etc. — see ingest.py's render_fragment)
# is left completely untouched. ---
GRADED_BLOCK_RE = re.compile(r'^graded:\n((?:  "\d+": .+\n)*)', re.M)
MEDIA_BLOCK_RE = re.compile(r'^media:\n(?:  - .+\n)*', re.M)


def read_graded_map(text: str) -> dict[int, str]:
    m = GRADED_BLOCK_RE.search(text)
    if not m:
        return {}
    result: dict[int, str] = {}
    for line in m.group(1).splitlines():
        entry = re.match(r'^  "(\d+)": (.+)$', line)
        if entry:
            result[int(entry.group(1))] = entry.group(2)
    return result


def write_graded_entry(text: str, index: int, new_key: str | None) -> str:
    """Set (new_key given) or clear (new_key=None, i.e. revert) the graded[]
    entry for this media index. Inserts a graded: block right after media:
    if none exists yet; removes the whole block if the last entry is cleared."""
    current = read_graded_map(text)
    if new_key is None:
        current.pop(index, None)
    else:
        current[index] = new_key

    new_body = "".join(f'  "{i}": {k}\n' for i, k in sorted(current.items()))
    m = GRADED_BLOCK_RE.search(text)

    if m:
        if new_body:
            return text[: m.start(1)] + new_body + text[m.end(1) :]
        return text[: m.start()] + text[m.end() :]  # last entry cleared — drop the whole block

    if not new_body:
        return text  # nothing to add, nothing existed
    mm = MEDIA_BLOCK_RE.search(text)
    if not mm:
        raise IngestError("fragment has no media: block to anchor a new graded: block after")
    return text[: mm.end()] + "graded:\n" + new_body + text[mm.end() :]


def commit_and_push(add_paths: list[str], fragment_id: str, preset: str, no_push: bool) -> bool:
    if add_paths:
        run_git(["add", *add_paths])
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


def delete_derived_copy(key: str) -> None:
    """Best-effort cleanup of a graded copy that's about to be replaced or
    removed — deliberately independent of git succeeding (pure storage
    housekeeping) and non-fatal (delete failing just orphans some storage,
    it was never the source of truth)."""
    if key.startswith("media/"):
        result = git(["rm", "-f", "--", key])
        if result.returncode != 0:
            log(f"could not remove old local graded file {key} (harmless): {result.stderr.strip()}")
        return
    config = r2_config_from_env()
    client = ingest.r2_client(config)
    try:
        client.delete_object(Bucket=config.r2_bucket, Key=key)
    except Exception as exc:  # noqa: BLE001
        log(f"could not delete old R2 graded object {key} (harmless): {redact_secrets(str(exc))}")


def apply_grade(fragment_id: str, media_index: int, preset: str, no_push: bool) -> dict:
    fragment_path = find_fragment_file(fragment_id)
    frontmatter = parse_frontmatter(fragment_path)
    media = frontmatter.get("media") or []
    if not (0 <= media_index < len(media)):
        raise IngestError(f"fragment {fragment_id} has no media[{media_index}] (media has {len(media)} entries)")

    original_key = str(media[media_index])
    if not original_key.lower().endswith((".webp", ".jpg", ".jpeg", ".png")):
        raise IngestError(f"media[{media_index}] ({original_key}) doesn't look like a photo")

    is_local = original_key.startswith("media/")
    text = fragment_path.read_text(encoding="utf-8")
    current_graded = read_graded_map(text).get(media_index)

    if preset == REVERT:
        if current_graded is None:
            # Nothing to revert — already showing the original.
            return {
                "ok": True,
                "id": fragment_id,
                "media_index": media_index,
                "preset": REVERT,
                "key": original_key,
                "location": "local" if is_local else "r2",
                "committed": False,
                "pushed": False,
                "reverted": False,
            }
        delete_derived_copy(current_graded)
        new_text = write_graded_entry(text, media_index, None)
        fragment_path.write_text(new_text, encoding="utf-8")
        fragment_rel = str(fragment_path.relative_to(REPO_ROOT)).replace("\\", "/")
        pushed = commit_and_push([fragment_rel], fragment_id, "revert", no_push)
        return {
            "ok": True,
            "id": fragment_id,
            "media_index": media_index,
            "preset": REVERT,
            "key": original_key,
            "location": "local" if is_local else "r2",
            "committed": True,
            "pushed": pushed,
            "reverted": True,
        }

    # A real preset: always grade fresh FROM THE UNTOUCHED ORIGINAL (never
    # from whatever copy happens to be currently graded) — so switching
    # presets, or grading/reverting repeatedly, never compounds quality loss
    # or drifts from what was actually captured.
    if is_local:
        abs_path = REPO_ROOT / original_key
        if not abs_path.exists():
            raise IngestError(f"local media file missing: {original_key}")
        original_bytes = abs_path.read_bytes()
    else:
        config = r2_config_from_env()
        client = ingest.r2_client(config)
        try:
            original_bytes = client.get_object(Bucket=config.r2_bucket, Key=original_key)["Body"].read()
        except Exception as exc:  # noqa: BLE001
            raise IngestError(f"failed to download {original_key} from R2: {redact_secrets(str(exc))}") from exc

    graded_bytes = grade_bytes(original_bytes, preset)
    new_key = graded_key_for(original_key, preset)

    if is_local:
        new_path = REPO_ROOT / new_key
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(graded_bytes)
    else:
        config = r2_config_from_env()
        client = ingest.r2_client(config)
        ingest.upload_to_r2(client, config, new_key, graded_bytes)

    if current_graded:
        delete_derived_copy(current_graded)

    new_text = write_graded_entry(text, media_index, new_key)
    fragment_path.write_text(new_text, encoding="utf-8")
    fragment_rel = str(fragment_path.relative_to(REPO_ROOT)).replace("\\", "/")

    add_paths = [fragment_rel] + ([new_key] if is_local else [])
    pushed = commit_and_push(add_paths, fragment_id, preset, no_push)

    return {
        "ok": True,
        "id": fragment_id,
        "media_index": media_index,
        "preset": preset,
        "key": new_key,
        "old_graded_key": current_graded,
        "original_key": original_key,
        "location": "local" if is_local else "r2",
        "committed": True,
        "pushed": pushed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bake a cinematic color grade into a fragment's photo, non-destructively.")
    parser.add_argument("fragment_id")
    parser.add_argument("media_index", type=int)
    parser.add_argument("preset", choices=sorted(PRESETS) + [REVERT])
    parser.add_argument("--no-push", action="store_true", help="commit locally but do not push")
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
