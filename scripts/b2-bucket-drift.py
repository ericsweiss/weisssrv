#!/usr/bin/env python3
"""Drift check (and supervised apply) for a Backblaze B2 bucket's settings.

Stands in for a terraform module: the Backblaze terraform provider's READ path
returns empty attributes against B2's current API (verified 0.12.0 and 0.13.1,
2026-07: writes apply, every refresh/data source nulls bucket_type / SSE /
lifecycle), so a plan reports a permanent phantom "1 to change". The raw B2 API
reads and writes the same settings flawlessly, and a handful of settings on one
bucket do not need provider machinery — this script IS the codified config.

The bucket identity and desired state are consumer data, read from a JSON config
(--config, default b2-bucket.json under the CWD):

    {
      "account_id": "...",
      "bucket_id": "...",
      "bucket_name": "...",
      "desired": {
        "bucketType": "allPrivate",
        "defaultServerSideEncryption": {"mode": "SSE-B2", "algorithm": "AES256"},
        "lifecycleRules": [
          {"fileNamePrefix": "", "daysFromHidingToDeleting": 30,
           "daysFromUploadingToHiding": null}
        ],
        "defaultRetention": {"mode": null, "period": null}
      }
    }

Usage:
  b2-bucket-drift.py [--config FILE]            # exit 0 clean, 1 drift, 2 error
  b2-bucket-drift.py [--config FILE] --apply    # SUPERVISED: reconcile the bucket

Credentials: B2_APPLICATION_KEY_ID / B2_APPLICATION_KEY env vars (a bucket-scoped
key carrying the bucket-settings read/write capabilities).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

DEFAULT_CONFIG = "b2-bucket.json"
REQUIRED_CONFIG_KEYS = ("account_id", "bucket_id", "bucket_name", "desired")
DESIRED_KEYS = (
    "bucketType",
    "defaultServerSideEncryption",
    "lifecycleRules",
    "defaultRetention",
)


def load_config(path: Path) -> dict:
    with path.open() as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: top-level must be an object")
    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"{path}: missing {missing}")
    missing_desired = [k for k in DESIRED_KEYS if k not in cfg["desired"]]
    if missing_desired:
        raise ValueError(f"{path}: desired is missing {missing_desired}")
    return cfg


def _api(url: str, token: str | None = None, body: dict | None = None,
         basic: tuple[str, str] | None = None) -> dict:
    req = urllib.request.Request(url)
    if basic:
        cred = base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {cred}")
    elif token:
        req.add_header("Authorization", token)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _normalize_rule(rule: dict) -> dict:
    return {
        "fileNamePrefix": rule.get("fileNamePrefix", ""),
        "daysFromHidingToDeleting": rule.get("daysFromHidingToDeleting"),
        "daysFromUploadingToHiding": rule.get("daysFromUploadingToHiding"),
    }


def read_bucket(api_url: str, token: str, cfg: dict) -> dict:
    data = _api(
        f"{api_url}/b2api/v3/b2_list_buckets",
        token=token,
        body={"accountId": cfg["account_id"], "bucketId": cfg["bucket_id"]},
    )
    buckets = data.get("buckets", [])
    if len(buckets) != 1:
        raise RuntimeError(f"expected exactly one bucket, got {len(buckets)}")
    return buckets[0]


def diff_bucket(b: dict, desired: dict) -> list[str]:
    """Compare the live bucket against `desired`; return human-readable drift."""
    drift: list[str] = []
    if b.get("bucketType") != desired["bucketType"]:
        drift.append(f"bucketType: {b.get('bucketType')!r} != {desired['bucketType']!r}")

    sse = (b.get("defaultServerSideEncryption") or {})
    if not sse.get("isClientAuthorizedToRead", True):
        drift.append("SSE: key not authorized to read (fix the key capabilities)")
    else:
        val = sse.get("value") or {}
        want = desired["defaultServerSideEncryption"]
        got = {"mode": val.get("mode"), "algorithm": val.get("algorithm")}
        if got != want:
            drift.append(f"SSE: {got} != {want}")

    rules = [_normalize_rule(r) for r in (b.get("lifecycleRules") or [])]
    want_rules = [_normalize_rule(r) for r in desired["lifecycleRules"]]
    if rules != want_rules:
        drift.append(f"lifecycleRules: {rules} != {want_rules}")

    fl = b.get("fileLockConfiguration") or {}
    if not fl.get("isClientAuthorizedToRead", True):
        drift.append("fileLock: key not authorized to read (fix the key capabilities)")
    else:
        ret = ((fl.get("value") or {}).get("defaultRetention") or {})
        got_ret = {"mode": ret.get("mode"), "period": ret.get("period")}
        if got_ret != desired["defaultRetention"]:
            drift.append(f"defaultRetention: {got_ret} != {desired['defaultRetention']}")
    return drift


def apply_bucket(api_url: str, token: str, cfg: dict) -> dict:
    # defaultRetention is deliberately NOT in the update payload: file lock is a
    # create-time option, so where it is disabled retention cannot drift —
    # diff_bucket checks it only to surface capability-read gaps, and the
    # post-apply re-diff fails loudly if anything is left.
    desired = cfg["desired"]
    body = {
        "accountId": cfg["account_id"],
        "bucketId": cfg["bucket_id"],
        "bucketType": desired["bucketType"],
        "defaultServerSideEncryption": desired["defaultServerSideEncryption"],
        "lifecycleRules": [
            {k: v for k, v in r.items() if v is not None}
            for r in desired["lifecycleRules"]
        ],
    }
    return _api(f"{api_url}/b2api/v3/b2_update_bucket", token=token, body=body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B2 bucket settings drift check.")
    parser.add_argument("--config", type=Path, default=Path(DEFAULT_CONFIG))
    parser.add_argument("--apply", action="store_true", help="supervised reconcile")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}")
        return 2

    key_id = os.environ.get("B2_APPLICATION_KEY_ID", "")
    key = os.environ.get("B2_APPLICATION_KEY", "")
    if not key_id or not key:
        print("ERROR: B2_APPLICATION_KEY_ID / B2_APPLICATION_KEY must be set")
        return 2

    name = cfg["bucket_name"]
    try:
        auth = _api(
            "https://api.backblazeb2.com/b2api/v3/b2_authorize_account",
            basic=(key_id, key),
        )
        api_url = auth["apiInfo"]["storageApi"]["apiUrl"]
        token = auth["authorizationToken"]
        bucket = read_bucket(api_url, token, cfg)
    except Exception as e:  # noqa: BLE001 - a gate reports and exits
        print(f"ERROR: B2 API access failed: {e}")
        return 2

    if bucket.get("bucketName") != name:
        print(f"ERROR: bucket {cfg['bucket_id']} is named {bucket.get('bucketName')!r}, "
              f"expected {name!r} — refusing to touch it")
        return 2

    drift = diff_bucket(bucket, cfg["desired"])
    if not drift:
        print(f"OK: {name} matches the codified settings.")
        return 0

    print(f"DRIFT: {name} differs from the codified settings:")
    for d in drift:
        print(f"  - {d}")

    if not args.apply:
        print("Re-run with --apply to reconcile.")
        return 1

    # Supervised apply: a bad lifecycle rule can expire the only offsite copy,
    # so mutation requires an interactive confirmation.
    if not sys.stdin.isatty():
        print("ERROR: --apply requires an interactive terminal (supervised step)")
        return 2
    if input("Type 'yes' to apply these bucket setting changes: ") != "yes":
        print("ABORTED: bucket was not changed.")
        return 1

    try:
        apply_bucket(api_url, token, cfg)
        remaining = diff_bucket(read_bucket(api_url, token, cfg), cfg["desired"])
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: apply failed: {e}")
        return 2
    if remaining:
        print("ERROR: drift remains after apply:")
        for d in remaining:
            print(f"  - {d}")
        return 1
    print("APPLIED: bucket reconciled; re-read matches the codified settings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
