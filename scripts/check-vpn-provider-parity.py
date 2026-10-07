#!/usr/bin/env python3
"""Keep the VPN provider -> required-credential-key map identical everywhere.

Three copies cannot be collapsed: scripts/vpn-credcheck.sh, the gluetun sidecar
env in the manifest, and the task wrapper's aliases. Option --repo-root DIR.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

CREDCHECK = "scripts/vpn-credcheck.sh"
SIDECAR = "kubernetes/apps/download-clients/_vpn-sidecar/vpn-sidecar.yaml"
WRAPPER = "scripts/downloads-vpn-provider.sh"

_ARM = re.compile(r'^\s*(?:"(?P<quoted>[^"]+)"|(?P<bare>[\w|" -]+?))\)\s*$')


def credcheck_map(text: str) -> dict[str, set[str]]:
    """{gluetun provider: required vpn-credentials keys} from the pre-flight."""
    body = _case_body(text, 'case "$provider" in')
    found: dict[str, set[str]] = {}
    provider = None
    for line in body:
        arm = _arm_name(line)
        if arm is not None:
            provider = None if arm == "*" else arm
            if provider:
                found[provider] = set()
            continue
        if provider and "req_keys=" in line:
            value = line.split("req_keys=", 1)[1].strip().strip('"').rstrip("\\")
            found[provider].update(value.split())
        elif provider and found.get(provider) and not line.strip().startswith(";;"):
            found[provider].update(line.strip().strip('"').rstrip("\\").split())
    return {p: k for p, k in found.items() if k}


def sidecar_map(text: str) -> dict[str, set[str]]:
    """{gluetun provider: keys} from the sidecar's SECRETFILE exports."""
    body = _case_body(text, 'case "$VPN_PROVIDER" in')
    found: dict[str, set[str]] = {}
    provider = None
    for line in body:
        arm = _arm_name(line)
        if arm is not None:
            provider = None if arm == "*" else arm
            if provider:
                found[provider] = set()
            continue
        match = re.search(r"_SECRETFILE=/vpn-secrets/(\S+)", line)
        if provider and match:
            found[provider].add(match.group(1))
    return {p: k for p, k in found.items() if k}


def wrapper_providers(text: str) -> set[str]:
    """The gluetun strings the operator-facing alias map can produce."""
    body = _case_body(text, 'case "$PROVIDER" in')
    return {
        m.group(1)
        for line in body
        for m in [re.search(r'GLUE="([^"]+)"', line)]
        if m
    }


def _case_body(text: str, header: str) -> list[str]:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        raise SystemExit(f"no `{header}` block found — the parser needs re-pointing")
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "esac")
    return lines[start + 1:end]


def _arm_name(line: str) -> str | None:
    """The provider a `case` arm opens, or None when the line is not an arm."""
    stripped = line.strip()
    if not stripped.endswith(")") or stripped.endswith("))") or "=" in stripped:
        return None
    if stripped == "*)":
        return "*"
    match = _ARM.match(stripped)
    if not match:
        return None
    name = match.group("quoted") or match.group("bare")
    return name.strip().strip('"') if name else None


def check(root: Path = REPO) -> list[str]:
    credcheck = credcheck_map((root / CREDCHECK).read_text())
    sidecar = sidecar_map((root / SIDECAR).read_text())
    wrapper = wrapper_providers((root / WRAPPER).read_text())

    problems = []
    if not credcheck:
        problems.append(f"{CREDCHECK}: parsed no provider arms")
    if not sidecar:
        problems.append(f"{SIDECAR}: parsed no provider arms")
    if not wrapper:
        problems.append(f"{WRAPPER}: parsed no alias targets")
    if problems:
        return problems

    if set(credcheck) != set(sidecar):
        problems.append(
            f"provider sets differ: {CREDCHECK} has {sorted(credcheck)}, "
            f"{SIDECAR} has {sorted(sidecar)}"
        )
    for provider in sorted(set(credcheck) & set(sidecar)):
        if credcheck[provider] != sidecar[provider]:
            problems.append(
                f"{provider}: {CREDCHECK} requires {sorted(credcheck[provider])} but "
                f"{SIDECAR} exports {sorted(sidecar[provider])}"
            )
    unknown = sorted(wrapper - set(credcheck))
    if unknown:
        problems.append(
            f"{WRAPPER} can select {unknown}, which {CREDCHECK} does not know — "
            "the wrapper would fail closed on rc=2 instead of pre-flighting"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    args = parser.parse_args()
    problems = check(args.repo_root)
    if problems:
        print("VPN provider map drift:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("VPN provider map is in step across the pre-flight, sidecar and wrapper.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
