#!/usr/bin/env python3
"""Point the Cloudflare A records for this site at the current public IPv4.

Mounted into the cloudflare-ddns CronJob from the configMapGenerator in this
directory (kustomize refuses generator sources outside the kustomization root,
which is why this is not in scripts/; its tests are
scripts/test_cloudflare_ddns.py). Stdlib only — the job runs a bare
python:3-slim image with no pip step.

Division of ownership with Terraform (terraform/cloudflare): Terraform owns
`proxied`, `ttl` and `comment` on every record; this script owns `content`
only. Updates therefore re-send the record's existing values for the fields it
does not own, because the Cloudflare record API is a full-body PUT.
"""
from __future__ import annotations

import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api.cloudflare.com/client/v4"
# Flux substitutes this from the cluster-config ConfigMap when the
# configMapGenerator output is reconciled — the same placeholder every manifest
# in a substituted tree uses, and what scripts/check-cluster-literals.py
# enforces. The raw file (what pytest imports) therefore carries the
# placeholder, not the zone.
ZONE = "${cluster_external_domain}"

# IPv4-preferred detection endpoints. Only ipv4.icanhazip.com is strictly v4;
# the others can answer over IPv6, which the per-reply IPv4 check rejects.
IP_SERVICES = (
    "https://api.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://checkip.amazonaws.com",
)
IP_ATTEMPTS = 3
IP_RETRY_SLEEP = 5

# (record name, proxied-on-create). `proxied` seeds creation only: a fresh
# record would otherwise default to false and expose the origin IP for the root
# domain. On update the record's live value wins.
RECORDS = (
    (ZONE, True),
    (f"direct.{ZONE}", False),
    (f"git.{ZONE}", False),
    (f"vpn.{ZONE}", False),
)


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

    Retries across providers so neither a transient egress blip (a node
    reconverging mid-maintenance) nor one provider being down fails the run.
    Every reply must parse as a global IPv4: a dual-stack provider answering
    AAAA, or a captive portal answering with private space, would otherwise
    publish an unreachable A record.
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


def zone_is_substituted(zone=ZONE):
    """True once Flux has replaced the cluster_external_domain placeholder.

    The zone stopped being a literal when it became a substituted placeholder,
    and an unsubstituted one is not a harmless no-op: Flux renders an unknown key
    as an EMPTY STRING, `GET /zones?name=` is an UNFILTERED list, and the run
    would then create/update records named ``, `direct.`, `git.` and `vpn.` in
    whichever zone the API happened to return first. So the value is checked
    before any DNS call, and the check lives in a function rather than at module
    scope because the raw file (what pytest imports, and what
    check-cluster-literals.py requires) legitimately carries the placeholder.

    The marker is assembled at runtime because this file ships inside a
    ConfigMap that Flux envsubst POST-PROCESSES: a literal dollar-brace in
    source is a parse error there (its Go envsubst rejects what GNU envsubst
    ignores), which broke the whole configs stage at reconcile.
    """
    marker = "$" + "{"
    return bool(zone) and marker not in zone and "." in zone


def get_zone_id(token, zone=ZONE):
    """Return the Cloudflare zone id for `zone`, or None."""
    data = api_request(token, f"{API_BASE}/zones?name={zone}")
    if not data or not data.get("success"):
        print("ERROR: Failed to get zone ID")
        return None
    zones = data.get("result") or []
    # Match by NAME, never `zones[0]`: with a name filter that returns one or
    # zero rows the index was safe, but any request that degrades to an
    # unfiltered list would otherwise hand back an arbitrary zone.
    match = next((z for z in zones if z.get("name") == zone), None)
    if match is None:
        print(f"ERROR: Cloudflare returned {len(zones)} zone(s), none named {zone!r}")
        return None
    return match.get("id")


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
    comment = existing.get("comment")
    if comment:
        body["comment"] = comment
    return body


def update_record(token, zone_id, record_name, current_ip, proxied):
    """Create or update one A record. Returns True on success/no-op."""
    print(f"\n=== Processing {record_name} (create-default proxied={proxied}) ===")
    url = f"{API_BASE}/zones/{zone_id}/dns_records?type=A&name={record_name}"
    data = api_request(token, url)
    if not data or not data.get("success"):
        print("ERROR: Failed to query existing record")
        return False

    records = data.get("result") or []
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


def main(argv=None):
    token = os.environ.get("CF_API_TOKEN")
    if not token:
        print("ERROR: CF_API_TOKEN not set")
        return 1

    if not zone_is_substituted():
        print(
            f"ERROR: ZONE is unsubstituted or malformed ({ZONE!r}) — the cluster-config "
            "postBuild substitution did not run. Refusing to touch DNS.",
            file=sys.stderr,
        )
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
