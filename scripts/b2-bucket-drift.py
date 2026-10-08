#!/usr/bin/env python3
"""Drift check (and supervised --apply) for a Backblaze B2 bucket's settings.

Exit 0 clean, 1 drift, 2 error. Config schema: examples/b2-bucket.example.json.
Credentials, rationale and usage: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
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


def is_revision_conflict(exc: urllib.error.HTTPError) -> bool:
    """B2 answers a stale `ifRevisionIs` with 409."""
    return exc.code == 409


def apply_bucket(api_url: str, token: str, cfg: dict, revision: int | None = None) -> dict:
    # defaultRetention stays out of the update payload: file lock is a
    # create-time option, so it cannot drift. diff_bucket checks it only to
    # surface capability-read gaps.
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
    # Optimistic concurrency: a console edit between the read and this write
    # loses the write rather than being clobbered.
    if revision is not None:
        body["ifRevisionIs"] = revision
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

    revision = bucket.get("revision")
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
        apply_bucket(api_url, token, cfg, revision)
        remaining = diff_bucket(read_bucket(api_url, token, cfg), cfg["desired"])
    except urllib.error.HTTPError as e:
        if is_revision_conflict(e):
            print("ERROR: the bucket changed since it was read; re-run the drift check")
            return 2
        print(f"ERROR: apply failed: {e}")
        return 2
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
