#!/usr/bin/env python3
"""Assert every service/systemd task names a UNIT, not an Ansible role FQCN.

A bulk role rename to `weisssrv.infra.<role>` over-applied and rewrote two
systemd task `name:` values in a maintenance playbook. systemd appends
`.service` only when the name carries no unit suffix, so `weisssrv.infra.k3s`
resolves to `weisssrv.infra.k3s.service` — a unit that does not exist. Ansible
reports that as a task failure at best, and where the task carries
`failed_when: false` it is a silent no-op forever.

Nothing else catches it: ansible-lint validates module ARGUMENTS, not whether a
unit name is real, and the playbooks only run against live hosts.

The rule: under a service-managing module, a `name:` containing a dot must end
in a real systemd unit suffix. Jinja expressions and variables are skipped —
their value is not knowable here, and the role-rename shape this exists to catch
is always a literal.

Usage: scripts/check-ansible-service-names.py [--root ansible]

Exit codes: 0 clean, 1 violations, 2 the gate could not inspect its subject.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.exit("PyYAML required: pip install pyyaml")

SERVICE_MODULES = {
    "service",
    "systemd",
    "systemd_service",
    "sysvinit",
    "ansible.builtin.service",
    "ansible.builtin.systemd",
    "ansible.builtin.systemd_service",
    "ansible.builtin.sysvinit",
}

# systemd.unit(5). A name whose dotted suffix is not one of these is not a unit.
UNIT_SUFFIXES = (
    ".service", ".socket", ".target", ".device", ".mount", ".automount",
    ".swap", ".path", ".timer", ".slice", ".scope",
)


class Loader(yaml.SafeLoader):
    """SafeLoader tolerating the `!vault` tag inventory files may carry."""


Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def unit_names(args) -> list[str]:
    """Literal unit names a module's argument block asks for."""
    if isinstance(args, str):
        # free-form `systemd: name=foo state=restarted`
        return [tok.split("=", 1)[1] for tok in args.split() if tok.startswith("name=")]
    if not isinstance(args, dict):
        return []
    value = args.get("name")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def is_bad_unit(name: str) -> bool:
    if "{{" in name or "$" in name:
        return False
    if "." not in name:
        # No suffix at all: systemd appends `.service`, which is the normal form.
        return False
    return not name.endswith(UNIT_SUFFIXES)


def walk(node, path: Path, found: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SERVICE_MODULES:
                for name in unit_names(value):
                    if is_bad_unit(name):
                        found.append(f"{path}: {key}: name: {name}")
            walk(value, path, found)
    elif isinstance(node, list):
        for item in node:
            walk(item, path, found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="ansible", help="tree to scan (default: ansible)")
    opts = parser.parse_args()

    root = Path(opts.root)
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory")
        return 2

    files = sorted(p for p in root.rglob("*") if p.suffix in (".yml", ".yaml"))
    if not files:
        print(f"ERROR: no YAML found under {root} — the gate would pass by seeing nothing")
        return 2

    found: list[str] = []
    for path in files:
        try:
            docs = list(yaml.load_all(path.read_text(), Loader=Loader))
        except yaml.YAMLError:
            # Syntax is ansible-lint's / yamllint's job, not this gate's.
            continue
        for doc in docs:
            walk(doc, path, found)

    if found:
        print("ERROR: service/systemd tasks naming something that is not a systemd unit:")
        for line in found:
            print(f"  - {line}")
        print(
            "\nA role FQCN (weisssrv.infra.<role>) is not a unit name. Use the unit "
            "(k3s, k3s-agent), or a full name ending in a systemd unit suffix."
        )
        return 1

    print(f"OK: {len(files)} files scanned; every service/systemd task names a unit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
