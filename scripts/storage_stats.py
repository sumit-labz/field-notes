#!/usr/bin/env python3
"""Report how much of the R2 and GitHub storage budgets the archive is using.

Usage:
    python scripts/storage_stats.py --json

Two numbers, both cheap to compute:
- Cloudflare R2: lists every object in the bucket (paginated) and sums their
  sizes. There's no cheaper way to get a real total via the S3-compatible API
  — Cloudflare doesn't expose bucket-level usage metrics through it — but the
  bucket is a few hundred small webp/media objects, so a full listing is fast.
- GitHub: `gh repo view --json diskUsage` reports the repo's size in KB as
  GitHub itself tracks it (what actually counts against any GitHub limit),
  reusing the same `gh` CLI auth the Telegram-fetch button already depends on.

Invoked by the inbox via GET /api/storage-stats (site/src/dev/openrouter-plugin.mjs),
which caches the result for a few minutes since the R2 listing, while fast, is
still a network round-trip worth avoiding on every page load.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import ingest
from ingest import IngestError, redact_secrets, load_config


def r2_usage() -> dict:
    config = load_config()
    client = ingest.r2_client(config)
    total_bytes = 0
    total_count = 0
    continuation_token = None
    while True:
        kwargs = {"Bucket": config.r2_bucket, "MaxKeys": 1000}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        try:
            resp = client.list_objects_v2(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise IngestError(f"R2 list_objects_v2 failed: {redact_secrets(str(exc))}") from exc
        for obj in resp.get("Contents", []):
            total_bytes += obj.get("Size", 0)
            total_count += 1
        if not resp.get("IsTruncated"):
            break
        continuation_token = resp.get("NextContinuationToken")
    return {"bytes": total_bytes, "count": total_count}


def github_usage() -> dict:
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "diskUsage,nameWithOwner"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise IngestError("GitHub CLI (gh) not found on PATH") from exc
    if result.returncode != 0:
        raise IngestError(f"gh repo view failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    # gh reports diskUsage in KB, matching the GitHub API's repo `size` field.
    return {"bytes": int(data["diskUsage"]) * 1024, "repo": data.get("nameWithOwner")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Report R2 and GitHub storage usage.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result: dict = {"ok": True}
    errors: dict = {}
    try:
        result["r2"] = r2_usage()
    except Exception as exc:  # noqa: BLE001
        errors["r2"] = redact_secrets(str(exc))
    try:
        result["github"] = github_usage()
    except Exception as exc:  # noqa: BLE001
        errors["github"] = redact_secrets(str(exc))
    if errors:
        result["errors"] = errors
    if "r2" not in result and "github" not in result:
        result["ok"] = False

    if args.json:
        print(json.dumps(result))
    else:
        print(result)
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
