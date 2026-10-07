#!/usr/bin/env python3
"""Point the Cloudflare A records for this site at the current public IPv4.

Mounted into the cloudflare-ddns CronJob here; README.md describes the environment
variables and the record-ownership split. Terraform owns every field but `content`.
"""
from __future__ import annotations

import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.cloudflare.com/client/v4"
# Both come from the CronJob's env, which spells them as cluster-config
# placeholders Flux substitutes at reconcile time.
ZONE = os.environ.get("DDNS_ZONE", "").strip()

# IPv4-preferred detection endpoints. Only ipv4.icanhazip.com is strictly v4;
# the others can answer over IPv6, which the per-reply IPv4 check rejects.
IP_SERVICES = (
    "https://api.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://checkip.amazonaws.com",
)
IP_ATTEMPTS = 3
IP_RETRY_SLEEP = 5

def parse_records(spec, zone=None):
    """Parse a DDNS_RECORDS spec into (record name, proxied-on-create) pairs.

    Entries are comma-separated `label[:proxied]`, `@` is the apex, and `proxied`
    defaults to true, seeding creation only. A bad entry is kept for the validator.
    """
    zone = ZONE if zone is None else zone
    records = []
    for entry in (e.strip() for e in spec.split(",")):
        if not entry:
            continue
        if entry.count(":") > 1:
            records.append(entry)
            continue
        label, _, flag = entry.partition(":")
        flag = flag.strip().lower()
        if ":" in entry and flag not in ("true", "false"):
            records.append(entry)
            continue
        label = label.strip()
        # An empty or dot-edged label builds a name like `.zone`, which the
        # validator accepts and the API then creates as a new record.
        if not label or label.startswith(".") or label.endswith(".") or ".." in label:
            records.append(entry)
            continue
        name = zone if label == "@" else f"{label}.{zone}"
        records.append((name, flag != "false"))
    return tuple(records)


# The record list lives in cluster-config as cluster_ddns_records, so a WAN
# hostname is added there rather than in this program.
RECORDS = parse_records(os.environ.get("DDNS_RECORDS", "").strip())


def api_request(token, url, method="GET", data=None, timeout=30):
    """Call the Cloudflare API; return parsed JSON, or None on any failure."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # Cloudflare returns structured JSON errors; capture the body so the
        # failure is greppable in logs.
        try:
            body_txt = e.read().decode(errors="replace")
        except Exception:
            body_txt = "<unreadable>"
        print(f"ERROR: HTTP {e.code} {e.reason} on {method} {url}: {body_txt}", file=sys.stderr)
    except urllib.error.URLError as e:
        print(f"ERROR: network error on {method} {url}: {e.reason}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - a failed run must not crash the pod
        print(f"ERROR: unexpected {type(e).__name__} on {method} {url}: {e}", file=sys.stderr)
    return None


def _fetch_ip(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode().strip()


def get_public_ip(services=IP_SERVICES, attempts=IP_ATTEMPTS, sleep=time.sleep):
    """Return the current public IPv4, or None.

    Retries across providers so one blip does not fail the run. Every reply must
    parse as a global IPv4 or an unreachable A record gets published.
    """
    last_err = None
    for attempt in range(1, attempts + 1):
        for url in services:
            try:
                ip = _fetch_ip(url)
            except Exception as e:  # noqa: BLE001 - try the next provider
                last_err = e
                print(f"WARN: get public IP via {url} (attempt {attempt}) failed: {e}", file=sys.stderr)
                continue
            try:
                if ipaddress.IPv4Address(ip).is_global:
                    return ip
                print(f"WARN: {url} returned non-public IPv4 {ip!r}, trying next", file=sys.stderr)
            except ValueError:
                if ip:
                    print(f"WARN: {url} returned non-IPv4 {ip!r}, trying next", file=sys.stderr)
        if attempt < attempts:
            sleep(IP_RETRY_SLEEP)
    print(
        "ERROR: Failed to get a valid IPv4 after retries: "
        f"{last_err or 'all providers returned non-IPv4 or empty responses'}"
    )
    return None


def zone_is_valid(zone):
    """CRITICAL: false unless DDNS_ZONE holds a real domain.

    An empty or unsubstituted zone makes `GET /zones?name=` an unfiltered list,
    so this is checked before any DNS call. The marker is assembled at runtime
    because a literal dollar-brace in this file is a Flux envsubst parse error.
    """
    marker = "$" + "{"
    return bool(zone) and marker not in zone and "." in zone


def records_are_valid(records=RECORDS):
    """Fail closed on the record list, before the first API call.

    An empty tuple makes the run exit 0 having managed nothing; a blank name
    sends `?type=A&name=`, which matches an arbitrary record and rewrites it.
    """
    if not records:
        print(
            "ERROR: RECORDS is empty — refusing a run that would manage no DNS at all",
            file=sys.stderr,
        )
        return False
    for entry in records:
        # Shape before content: a stray two-character string unpacks as
        # (name, proxied) and its first character would pass the blank-name
        # check below, sending a one-letter name into the API query.
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            print(
                f"ERROR: record entry {entry!r} is not a (name, proxied) pair",
                file=sys.stderr,
            )
            return False
        name, proxied = entry
        if not isinstance(name, str) or not name.strip():
            print(
                f"ERROR: record entry {entry!r} has no record name — "
                "an empty name queries the zone unfiltered",
                file=sys.stderr,
            )
            return False
        if not isinstance(proxied, bool):
            print(
                f"ERROR: record entry {entry!r} has a non-boolean proxied flag",
                file=sys.stderr,
            )
            return False
    return True


def get_zone_id(token, zone=None):
    """Return the Cloudflare zone id for `zone`, or None."""
    zone = ZONE if zone is None else zone
    query = urllib.parse.quote(zone, safe="")
    data = api_request(token, f"{API_BASE}/zones?name={query}")
    if not data or not data.get("success"):
        print("ERROR: Failed to get zone ID")
        return None
    zones = data.get("result") or []
    # Match by NAME, never `zones[0]`: a request that degrades to an unfiltered
    # list would otherwise hand back an arbitrary zone.
    match = next((z for z in zones if z.get("name") == zone), None)
    if match is None:
        print(f"ERROR: Cloudflare returned {len(zones)} zone(s), none named {zone!r}")
        return None
    return match.get("id")


# Record decorations Terraform sets and this script must carry forward. The PUT
# replaces the whole record, so any of these left out of the body is erased on
# the next address change — which reads as Terraform drift, not as a DDNS bug.
PRESERVED_FIELDS = ("comment", "tags", "settings")


def build_record_body(record_name, current_ip, proxied, existing=None):
    """Body for the full-body PUT/POST, preserving Terraform-owned fields."""
    existing = existing or {}
    body = {
        "type": "A",
        "name": record_name,
        "content": current_ip,
        "ttl": existing.get("ttl", 1) if existing else 1,
        "proxied": existing.get("proxied", proxied) if existing else proxied,
    }
    for key in PRESERVED_FIELDS:
        # `is not None`, not truthiness: Cloudflare returns an unset decoration
        # as null, while a deliberately-empty one (`tags: []`) is a value the
        # record actually has and re-sending it keeps the PUT a faithful echo.
        if existing.get(key) is not None:
            body[key] = existing[key]
    return body


def update_record(token, zone_id, record_name, current_ip, proxied):
    """Create or update one A record. Returns True on success/no-op."""
    print(f"\n=== Processing {record_name} (create-default proxied={proxied}) ===")
    query = urllib.parse.quote(record_name, safe="")
    url = f"{API_BASE}/zones/{zone_id}/dns_records?type=A&name={query}"
    data = api_request(token, url)
    if not data or not data.get("success"):
        print("ERROR: Failed to query existing record")
        return False

    records = data.get("result") or []
    if len(records) > 1:
        # Single-record ownership is this job's contract. Updating one of
        # several leaves the stale sibling answering intermittently, and picking
        # one guesses which record Terraform owns.
        print(
            f"ERROR: {len(records)} A records for {record_name}; "
            "ambiguous ownership, refusing a partial update",
            file=sys.stderr,
        )
        return False
    existing = records[0] if records else None
    existing_ip = existing.get("content") if existing else None
    record_id = existing.get("id") if existing else None
    print(f"Existing IP: {existing_ip or 'none'}")
    if current_ip == existing_ip:
        print("IP unchanged, skipping")
        return True

    print("Updating record...")
    body = build_record_body(record_name, current_ip, proxied, existing)
    if record_id:
        result = api_request(
            token, f"{API_BASE}/zones/{zone_id}/dns_records/{record_id}", method="PUT", data=body
        )
    else:
        result = api_request(
            token, f"{API_BASE}/zones/{zone_id}/dns_records", method="POST", data=body
        )
    if result and result.get("success"):
        print("Updated successfully" if record_id else "Created successfully")
        return True
    print(f"ERROR: {'Update' if record_id else 'Create'} failed")
    return False


def main() -> int:
    token = os.environ.get("CF_API_TOKEN")
    if not token:
        print("ERROR: CF_API_TOKEN not set")
        return 1

    if not zone_is_valid(ZONE):
        print(
            f"ERROR: DDNS_ZONE is empty or malformed ({ZONE!r}) — the cluster-config "
            "postBuild substitution did not run. Refusing to touch DNS.",
            file=sys.stderr,
        )
        return 1

    # Passed explicitly, not left to the default: the default binds the tuple at
    # def time, so the guard must read the same module global the update loop
    # below iterates.
    if not records_are_valid(RECORDS):
        return 1

    current_ip = get_public_ip()
    if not current_ip:
        return 1
    print(f"Current public IP: {current_ip}")

    zone_id = get_zone_id(token)
    if not zone_id:
        return 1
    print(f"Zone ID: {zone_id}")

    success = True
    for record_name, proxied in RECORDS:
        success = update_record(token, zone_id, record_name, current_ip, proxied) and success

    print("\nDDNS update complete")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
