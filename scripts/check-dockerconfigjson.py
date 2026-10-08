#!/usr/bin/env python3
"""Assert every hand-built .dockerconfigjson payload parses as a docker config.

Reads the rendered manifest corpus on stdin: exit 1 on a finding, 2 on an
operator error including a corpus declaring no payload.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

DOCKERCONFIG_TYPE = "kubernetes.io/dockerconfigjson"
PAYLOAD_KEY = ".dockerconfigjson"
REQUIRED_CRED_FIELDS = ("username", "password", "auth")
# An ESO template action. A value-position action renders its own quotes via
# toJson, so each one is stood in for according to whether it already sits
# inside a JSON string.
_ACTION = re.compile(r"\{\{.*?\}\}", re.S)
_STAND_IN = "gotmpl"
# A registry key is either a bare host (the kubelet matches the literal host in
# the image ref) or the legacy https:// form Docker Hub uses.
_HOST = re.compile(r"^(?:https?://)?[A-Za-z0-9._:-]+(?:/[A-Za-z0-9._/-]*)?$")


def substitute_actions(payload: str) -> str:
    """Replace every Go-template action with a JSON string stand-in.

    An action already wrapped in quotes becomes bare text; a value-position one
    becomes a quoted string, because `toJson` emits the quotes itself.
    """
    out: list[str] = []
    last = 0
    for match in _ACTION.finditer(payload):
        before = payload[match.start() - 1] if match.start() else ""
        after = payload[match.end()] if match.end() < len(payload) else ""
        quoted = before == '"' and after == '"'
        out.append(payload[last:match.start()])
        out.append(_STAND_IN if quoted else f'"{_STAND_IN}"')
        last = match.end()
    out.append(payload[last:])
    return "".join(out)


def _payloads(docs: list[dict]) -> list[tuple[str, str]]:
    """-> [(document label, raw payload)] for every dockerconfigjson Secret.

    Covers the ExternalSecret template that mints one and a Secret that carries
    it directly, in either `stringData` or base64 `data`.
    """
    found: list[tuple[str, str]] = []
    for doc in docs:
        kind = doc.get("kind")
        meta = doc.get("metadata") or {}
        label = f"{meta.get('namespace', '')}/{kind}/{meta.get('name', '?')}"
        spec = doc.get("spec") or {}
        if kind == "ExternalSecret":
            template = ((spec.get("target") or {}).get("template")) or {}
            if template.get("type") != DOCKERCONFIG_TYPE:
                continue
            raw = (template.get("data") or {}).get(PAYLOAD_KEY)
            if isinstance(raw, str):
                found.append((label, raw))
        elif kind == "Secret" and doc.get("type") == DOCKERCONFIG_TYPE:
            raw = (doc.get("stringData") or {}).get(PAYLOAD_KEY)
            if isinstance(raw, str):
                found.append((label, raw))
                continue
            encoded = (doc.get("data") or {}).get(PAYLOAD_KEY)
            if isinstance(encoded, str):
                try:
                    found.append((label, base64.b64decode(encoded, validate=True).decode()))
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    found.append((label, "<undecodable base64>"))
    return found


def payload_violations(label: str, raw: str) -> list[str]:
    """Findings for one payload: parse first, then the auths shape."""
    try:
        parsed = json.loads(substitute_actions(raw))
    except json.JSONDecodeError as exc:
        return [
            f"  {label}: {PAYLOAD_KEY} is not valid JSON ({exc.msg} at line "
            f"{exc.lineno} column {exc.colno}) — the kubelet reports this only "
            "as ImagePullBackOff"
        ]
    if not isinstance(parsed, dict):
        return [f"  {label}: {PAYLOAD_KEY} is not a JSON object"]
    auths = parsed.get("auths")
    if not isinstance(auths, dict) or not auths:
        return [
            f"  {label}: {PAYLOAD_KEY} has no non-empty `auths` object — the "
            "kubelet reads credentials only from `auths`"
        ]
    out: list[str] = []
    for host, cred in auths.items():
        if "${" in host:
            out.append(
                f"  {label}: registry key {host!r} still carries a ${{...}} "
                "placeholder — Flux substitution did not reach this payload"
            )
        elif not _HOST.match(host):
            out.append(
                f"  {label}: registry key {host!r} is not a registry host — the "
                "kubelet matches the literal host in the image ref"
            )
        if not isinstance(cred, dict):
            out.append(f"  {label}: credentials for {host!r} are not an object")
            continue
        missing = [f for f in REQUIRED_CRED_FIELDS if not cred.get(f)]
        if missing:
            out.append(
                f"  {label}: credentials for {host!r} omit {', '.join(missing)}"
            )
    return out


def violations(docs: list[dict]) -> tuple[list[str], int]:
    """-> (violations, payloads inspected). The count feeds the vacuity guard."""
    out: list[str] = []
    payloads = _payloads(docs)
    for label, raw in payloads:
        out.extend(payload_violations(label, raw))
    return out, len(payloads)


def main() -> int:
    docs: list[dict] = []
    try:
        for raw in yaml.safe_load_all(sys.stdin):
            if isinstance(raw, dict):
                if raw.get("kind") == "List" and isinstance(raw.get("items"), list):
                    docs.extend(i for i in raw["items"] if isinstance(i, dict))
                else:
                    docs.append(raw)
            elif isinstance(raw, list):
                docs.extend(i for i in raw if isinstance(i, dict))
    except yaml.YAMLError as exc:
        print(f"ERROR: failed to parse YAML input: {exc}", file=sys.stderr)
        return 2

    if not docs:
        print(
            "ERROR: empty corpus — no manifests on stdin. A gate that passes on nothing "
            "is not a gate; check the pipe and the `kustomize build` paths feeding it.",
            file=sys.stderr,
        )
        return 2

    found, seen = violations(docs)
    if found:
        print(
            "Registry-pull payloads that would not parse as a docker config:",
            file=sys.stderr,
        )
        print("\n".join(found), file=sys.stderr)
        return 1

    if not seen:
        print(
            f"ERROR: inspected 0 {PAYLOAD_KEY} payload(s) in {len(docs)} document(s) — "
            "a gate that checks nothing is not a gate. Check that the `kustomize build` "
            "paths feeding stdin cover the apps that pull from a private registry; if "
            "this cluster no longer ships one, drop this gate from "
            "scripts/flux-corpus-gates.sh.",
            file=sys.stderr,
        )
        return 2

    print(
        f"dockerconfigjson payloads OK — {seen} payload(s) across {len(docs)} "
        "document(s) parse as a docker config"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
