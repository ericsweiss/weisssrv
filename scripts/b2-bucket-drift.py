#!/usr/bin/env python3
"""Drift check (and supervised apply) for the weisssrv-backup B2 bucket.

Replaces the retired terraform/b2 module. The Backblaze terraform provider's
READ path returns empty attributes against B2's current API (verified 0.12.0
and 0.13.1, macOS and Linux, 2026-07-22: writes apply, every refresh/data
source nulls bucket_type / SSE / lifecycle), so a terraform plan reported a
permanent phantom "1 to change" no matter what. The raw B2 API reads and
writes the same settings flawlessly, and four settings on one bucket do not
need provider machinery: this script IS the codified bucket config.

The desired state below mirrors what the terraform module enforced:
- bucketType allPrivate
- SSE-B2 AES256 default encryption (belt-and-suspenders; restic's client-side
  encryption is the primary at-rest layer)
- exactly one lifecycle rule: hidden versions expire 30 days after hiding
  (rclone deletes-by-hiding for the hide-only restic key; a live version is
  never auto-hidden — daysFromUploadingToHiding stays null)
- no file-lock default retention

Usage:
  b2-bucket-drift.py            # drift check: exit 0 clean, 1 drift, 2 error
  b2-bucket-drift.py --apply    # SUPERVISED: reconcile the live bucket

Credentials: B2_APPLICATION_KEY_ID / B2_APPLICATION_KEY env vars (CI and the
Taskfile inject them via `op run` from the "B2 Archive Backup" item's
b2_key_id / b2_application_key fields — the bucket-scoped key with the
bucket-settings read/write capabilities).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request

BUCKET_ID = "4ef45c874b3188409cf10a11"
BUCKET_NAME = "weisssrv-backup"
ACCOUNT_ID = "e4c7b180c1a1"

DESIRED = {
    "bucketType": "allPrivate",
    "defaultServerSideEncryption": {"mode": "SSE-B2", "algorithm": "AES256"},
    "lifecycleRules": [
        {
            "fileNamePrefix": "",
            "daysFromHidingToDeleting": 30,
            "daysFromUploadingToHiding": None,
        }
    ],
    "defaultRetention": {"mode": None, "period": None},
}


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


def read_bucket(api_url: str, token: str) -> dict:
    data = _api(
        f"{api_url}/b2api/v3/b2_list_buckets",
        token=token,
        body={"accountId": ACCOUNT_ID, "bucketId": BUCKET_ID},
    )
    buckets = data.get("buckets", [])
    if len(buckets) != 1:
        raise RuntimeError(f"expected exactly one bucket, got {len(buckets)}")
    return buckets[0]


def diff_bucket(b: dict) -> list[str]:
    """Compare the live bucket against DESIRED; return human-readable drift."""
    drift: list[str] = []
    if b.get("bucketType") != DESIRED["bucketType"]:
        drift.append(f"bucketType: {b.get('bucketType')!r} != {DESIRED['bucketType']!r}")

    sse = (b.get("defaultServerSideEncryption") or {})
    if not sse.get("isClientAuthorizedToRead", True):
        drift.append("SSE: key not authorized to read (fix the key capabilities)")
    else:
        val = sse.get("value") or {}
        want = DESIRED["defaultServerSideEncryption"]
        got = {"mode": val.get("mode"), "algorithm": val.get("algorithm")}
        if got != want:
            drift.append(f"SSE: {got} != {want}")

    rules = [_normalize_rule(r) for r in (b.get("lifecycleRules") or [])]
    want_rules = [_normalize_rule(r) for r in DESIRED["lifecycleRules"]]
    if rules != want_rules:
        drift.append(f"lifecycleRules: {rules} != {want_rules}")

    fl = b.get("fileLockConfiguration") or {}
    if not fl.get("isClientAuthorizedToRead", True):
        drift.append("fileLock: key not authorized to read (fix the key capabilities)")
    else:
        ret = ((fl.get("value") or {}).get("defaultRetention") or {})
        got_ret = {"mode": ret.get("mode"), "period": ret.get("period")}
        if got_ret != DESIRED["defaultRetention"]:
            drift.append(f"defaultRetention: {got_ret} != {DESIRED['defaultRetention']}")
    return drift


def apply_bucket(api_url: str, token: str) -> dict:
    # defaultRetention is deliberately NOT in the update payload: file lock is
    # permanently disabled on this bucket (create-time option), so retention
    # cannot drift — diff_bucket checks it only to surface capability-read
    # gaps, and the post-apply re-diff fails loudly if anything is left.
    body = {
        "accountId": ACCOUNT_ID,
        "bucketId": BUCKET_ID,
        "bucketType": DESIRED["bucketType"],
        "defaultServerSideEncryption": DESIRED["defaultServerSideEncryption"],
        "lifecycleRules": [
            {k: v for k, v in r.items() if v is not None}
            for r in DESIRED["lifecycleRules"]
        ],
    }
    return _api(f"{api_url}/b2api/v3/b2_update_bucket", token=token, body=body)


def main(argv: list[str]) -> int:
    apply_mode = "--apply" in argv[1:]
    key_id = os.environ.get("B2_APPLICATION_KEY_ID", "")
    key = os.environ.get("B2_APPLICATION_KEY", "")
    if not key_id or not key:
        print("ERROR: B2_APPLICATION_KEY_ID / B2_APPLICATION_KEY must be set (op run)")
        return 2

    try:
        auth = _api(
            "https://api.backblazeb2.com/b2api/v3/b2_authorize_account",
            basic=(key_id, key),
        )
        api_url = auth["apiInfo"]["storageApi"]["apiUrl"]
        token = auth["authorizationToken"]
        bucket = read_bucket(api_url, token)
    except Exception as e:  # noqa: BLE001 - a gate reports and exits
        print(f"ERROR: B2 API access failed: {e}")
        return 2

    if bucket.get("bucketName") != BUCKET_NAME:
        print(f"ERROR: bucket {BUCKET_ID} is named {bucket.get('bucketName')!r}, "
              f"expected {BUCKET_NAME!r} — refusing to touch it")
        return 2

    drift = diff_bucket(bucket)
    if not drift:
        print(f"OK: {BUCKET_NAME} matches the codified settings "
              "(allPrivate, SSE-B2/AES256, hide->delete 30d, no retention).")
        return 0

    print(f"DRIFT: {BUCKET_NAME} differs from the codified settings:")
    for d in drift:
        print(f"  - {d}")

    if not apply_mode:
        print("Run scripts/b2-bucket-drift.py --apply (task b2:apply) to reconcile.")
        return 1

    # Supervised apply: a bad lifecycle rule can expire the only offsite copy,
    # so mutation requires an interactive confirmation (the same property the
    # retired terraform task enforced by refusing -auto-approve).
    if not sys.stdin.isatty():
        print("ERROR: --apply requires an interactive terminal (supervised step)")
        return 2
    if input("Type 'yes' to apply these bucket setting changes: ") != "yes":
        print("ABORTED: bucket was not changed.")
        return 1

    try:
        apply_bucket(api_url, token)
        remaining = diff_bucket(read_bucket(api_url, token))
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
    sys.exit(main(sys.argv))
