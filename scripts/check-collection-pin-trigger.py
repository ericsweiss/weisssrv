#!/usr/bin/env python3
"""Every deploy job that runs a playbook must also trigger on the collection pin.

All roles ship in the weisssrv.infra collection, so `ansible/requirements.yml`
is the ONLY in-repo signal that a role's content changed. A deploy job whose
`rules:changes:` lists playbooks but not the pin keeps deploying the old roles
after a library bump, silently and indefinitely.

check-deploy-coverage.sh cannot see this: requirements.yml is not a role,
playbook or inventory path, so it falls outside that gate's whole model.

Run from the repo root. Exit 0 clean, 1 on a finding.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CI_FILE = REPO / ".gitlab-ci.yml"
PIN = "ansible/requirements.yml"


class CILoader(yaml.SafeLoader):
    """SafeLoader tolerating GitLab's !reference tags, subclassed so the
    constructor is not registered on the global SafeLoader."""


CILoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def rule_paths(job: dict) -> set[str]:
    """Every literal path in a job's `rules:changes:`.

    Jobs that inherit their rules wholesale via `!reference` contribute no
    literal paths (the tag loads as None) and are covered by the job they
    inherit from.
    """
    paths: set[str] = set()
    for rule in job.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        changes = rule.get("changes") or []
        if isinstance(changes, dict):
            changes = changes.get("paths", [])
        paths.update(p for p in changes if isinstance(p, str))
    return paths


def jobs_missing_the_pin(ci: dict) -> list[str]:
    missing = []
    for name, job in ci.items():
        if not isinstance(job, dict) or not name.startswith("deploy-"):
            continue
        if job.get("stage") != "deploy":
            continue
        paths = rule_paths(job)
        if any(p.startswith("ansible/playbooks/") for p in paths) and PIN not in paths:
            missing.append(name)
    return sorted(missing)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ci_file = Path(argv[0]) if argv else CI_FILE
    ci = yaml.load(ci_file.read_text(), Loader=CILoader) or {}
    missing = jobs_missing_the_pin(ci)
    if missing:
        print("deploy jobs that run a playbook but do not trigger on the collection")
        print(f"pin ({PIN}): " + ", ".join(missing))
        print("A weisssrv.infra bump would leave those targets on the old roles.")
        return 1
    print(f"every playbook-running deploy job triggers on {PIN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
