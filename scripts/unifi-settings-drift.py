#!/usr/bin/env python3
"""Drift-check a console-owned UniFi settings section against a declared expectation.

Reads one section-addressed settings endpoint and compares the keys the config
declares. Exit 0 clean, 1 drift, 2 error. Contract: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent / "unifi-settings.json"
REQUIRED_CONFIG_KEYS = ("site", "desired")
DEFAULT_SECTION = "ips"
_VALUE_CHARS = 80


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: top-level must be an object")
    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"{path}: missing {missing}")
    if not isinstance(cfg["desired"], dict) or not cfg["desired"]:
        raise ValueError(f"{path}: desired must be a non-empty object")
    section = cfg.get("section", DEFAULT_SECTION)
    if not isinstance(section, str) or not section or "/" in section:
        raise ValueError(f"{path}: section must be a single path segment")
    cfg["section"] = section
    return cfg


def settings_url(base: str, site: str, section: str = DEFAULT_SECTION) -> str:
    """The section-addressed settings endpoint, never the unsectioned /rest/setting."""
    return f"{base.rstrip('/')}/proxy/network/api/s/{site}/get/setting/{section}"


# CRITICAL: read only the section-addressed /get/setting/<section>. The
# unsectioned /rest/setting returns the device SSH password, its hash and the
# site API token in cleartext, so the response body is never printed and values
# are truncated.
def fetch_section(url: str, api_key: str, verify: bool = True) -> dict:
    req = urllib.request.Request(url, headers={"X-API-KEY": api_key, "Accept": "application/json"})
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=30, context=context) as response:
        payload = json.load(response)
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise RuntimeError("unexpected response shape for the requested settings section")
    return data[0]


def _show(value: object) -> str:
    text = repr(sorted(value) if isinstance(value, list) else value)
    return text if len(text) <= _VALUE_CHARS else text[:_VALUE_CHARS] + "…"


def diff_settings(live: dict, desired: dict) -> list[str]:
    """Drift lines for the desired keys only; everything else stays unread."""
    drift = []
    for key, want in desired.items():
        if key not in live:
            drift.append(f"{key}: absent from the live section, expected {_show(want)}")
            continue
        got = live[key]
        if isinstance(want, list) and isinstance(got, list):
            if sorted(got) != sorted(want):
                drift.append(f"{key}: {_show(got)} != {_show(want)}")
        elif got != want:
            drift.append(f"{key}: {_show(got)} != {_show(want)}")
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UniFi console-owned settings drift check.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}")
        return 2

    base = os.environ.get("UNIFI_API_URL", "")
    api_key = os.environ.get("UNIFI_API_KEY", "")
    if not base or not api_key:
        print("ERROR: UNIFI_API_URL / UNIFI_API_KEY must be set")
        return 2
    verify = os.environ.get("UNIFI_ALLOW_INSECURE", "0") not in ("1", "true", "yes")
    if not verify:
        print("WARNING: TLS verification off (UNIFI_ALLOW_INSECURE)", file=sys.stderr)

    try:
        live = fetch_section(
            settings_url(base, cfg["site"], cfg["section"]), api_key, verify=verify
        )
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as e:
        # The exception type, not the body: a controller error page can quote
        # the request.
        print(f"ERROR: UniFi API read failed: {type(e).__name__}")
        return 2

    drift = diff_settings(live, cfg["desired"])
    if not drift:
        print(
            f"OK: site {cfg['site']} section {cfg['section']} matches the codified "
            "console settings."
        )
        return 0

    print(
        f"DRIFT: site {cfg['site']} section {cfg['section']} differs from the "
        "codified console settings:"
    )
    for line in drift:
        print(f"  - {line}")
    print("Reconcile in the console, or update the config to the new intent.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
