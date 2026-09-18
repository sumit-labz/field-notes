#!/usr/bin/env python3
"""Crop a fragment's photo to recenter its subject — non-destructively, same
pipeline as apply_cinematic_grade.py: always crop fresh from the untouched
original, upload the result under a brand-new key, update the fragment's
`graded:` map to point at it. media[index] (the original) is never touched,
so this is trivially revertible via:
    python scripts/apply_cinematic_grade.py <fragment-id> <media-index> original

Usage:
    python scripts/crop_fragment.py <fragment-id> <media-index> L,T,R,B [--no-push] [--json]

L,T,R,B are fractions (0-1) of the ORIGINAL image's width/height marking the
crop box, e.g. "0,0,0.97,1" trims 3% off the right edge.

Deliberately does not commit/push itself (unlike apply_cinematic_grade.py,
which is invoked one-click from the dev inbox and needs to be self-contained)
— the caller commits with a message that explains *why* the crop was made.
"""

from __future__ import annotations

import argparse
import io
import json
import sys

from PIL import Image, ImageOps

import ingest
from ingest import IngestError, WEBP_QUALITY, REPO_ROOT, redact_secrets
from delete_fragment import find_fragment_file, parse_frontmatter, r2_config_from_env
from apply_cinematic_grade import read_graded_map, write_graded_entry, graded_key_for, delete_derived_copy


def log(msg: str) -> None:
    print(f"[crop] {msg}", file=sys.stderr)


def crop_bytes(data: bytes, box: tuple[float, float, float, float]) -> bytes:
    left, top, right, bottom = box
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        w, h = img.size
        cropped = img.crop((round(left * w), round(top * h), round(right * w), round(bottom * h)))
        buffer = io.BytesIO()
        cropped.save(buffer, format="WEBP", quality=WEBP_QUALITY)
        return buffer.getvalue()


def apply_crop(fragment_id: str, media_index: int, box: tuple[float, float, float, float]) -> dict:
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

    cropped_bytes = crop_bytes(original_bytes, box)
    new_key = graded_key_for(original_key, "crop")

    if is_local:
        new_path = REPO_ROOT / new_key
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(cropped_bytes)
    else:
        config = r2_config_from_env()
        client = ingest.r2_client(config)
        ingest.upload_to_r2(client, config, new_key, cropped_bytes)

    if current_graded:
        delete_derived_copy(current_graded)

    new_text = write_graded_entry(text, media_index, new_key)
    fragment_path.write_text(new_text, encoding="utf-8")
    fragment_rel = str(fragment_path.relative_to(REPO_ROOT)).replace("\\", "/")

    return {
        "ok": True,
        "id": fragment_id,
        "media_index": media_index,
        "key": new_key,
        "old_graded_key": current_graded,
        "original_key": original_key,
        "box": box,
        "location": "local" if is_local else "r2",
        "fragment_path": fragment_rel,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Crop a fragment's photo to recenter its subject, non-destructively.")
    parser.add_argument("fragment_id")
    parser.add_argument("media_index", type=int)
    parser.add_argument("box", help="L,T,R,B as fractions 0-1 of the original image, e.g. 0,0,0.97,1")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        left, top, right, bottom = (float(x) for x in args.box.split(","))
        result = apply_crop(args.fragment_id, args.media_index, (left, top, right, bottom))
    except Exception as exc:  # noqa: BLE001
        message = redact_secrets(str(exc))
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(f"[crop] ERROR: {message}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result))
    else:
        log(f"done: {result}")


if __name__ == "__main__":
    main()
