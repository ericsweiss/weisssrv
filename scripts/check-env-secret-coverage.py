#!/usr/bin/env python3
"""Verify every op-run task exports the env secrets its playbook's roles need.

The failure this exists to prevent: `task infra:deploy` (and `infra:check`)
ran `playbooks/site.yml`, which runs the `restic_offsite` role on pve-nas-01,
whose secrets resolve via `lookup('ansible.builtin.env', 'B2_KEY_ID')` etc. —
but the `&infra_env` anchor exported none of the three. The role's assert then
raised AnsibleUndefinedVariable and aborted the NAS play before proxmox_backup
and zfs_exporter, so the documented whole-estate converge could not run at all.
Only `task storage:deploy` carried those three, and nothing compared the lists.

The model
---------
1. Every Ansible var assigned from `lookup('ansible.builtin.env', 'X')` is
   collected, with the file that defines it (role defaults/vars, or an
   inventory group_vars/host_vars file).
2. A var defined in `roles/<r>/{defaults,vars}/` is needed by role `<r>`.
   A var defined in the inventory is needed by whichever roles reference it by
   name (grep over the role tree) — the inventory itself consumes nothing.
3. Every Taskfile task that runs `ansible-playbook … playbooks/<p>.yml` must
   export, in its `env:`, the env vars of every role that playbook's `roles:`
   lists.  YAML aliases (`env: *infra_env`) are resolved by the YAML load, so a
   shared anchor is checked once per consuming task.

What the gate gives up
----------------------
Roles pulled in via `include_role`/`import_role` from a play's `tasks:` are not
resolved (only `roles:` blocks are). Ansible is also lazy: a var is only
dereferenced when a task reads it, so a role that references a var solely on a
`when:`-skipped path is over-required here rather than under-required. Both
directions are safe — this gate can demand an export that is not strictly
needed, never miss one that is. Add a rationale-carrying entry to
EXPECTED_MISSING below if that ever bites.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ANSIBLE = REPO / "ansible"
PLAYBOOKS = ANSIBLE / "playbooks"
ROLES = ANSIBLE / "roles"
INVENTORY = ANSIBLE / "inventories" / "prod"
TASKFILE = REPO / "Taskfile.yml"

ENV_LOOKUP_RE = re.compile(
    r"lookup\(\s*'ansible\.builtin\.env'\s*,\s*'([A-Z0-9_]+)'\s*\)"
)
VAR_KEY_RE = re.compile(r"^([a-z][a-z0-9_]*):")

# (task, ENV_VAR) pairs deliberately NOT required, each with a rationale.
# Keep this list short: every entry is a hole in the gate.
EXPECTED_MISSING: dict[tuple[str, str], str] = {
    # alloy_host's Loki creds default to '' and the role's own assert produces a
    # readable failure; the k3s provisioning playbook runs it only on nodes that
    # already have the credential file staged by the k3s deploy task.
    ("k3s:provision-vms", "LOKI_PUSH_USER"): (
        "alloy_host defaults to '' and asserts loudly; VM provisioning does not "
        "ship logs"
    ),
    ("k3s:provision-vms", "LOKI_PUSH_PASSWORD"): (
        "alloy_host defaults to '' and asserts loudly; VM provisioning does not "
        "ship logs"
    ),
}


def _yaml_load(path: Path):
    with path.open() as fh:
        return yaml.safe_load(fh)


def collect_env_vars() -> dict[str, set[str]]:
    """Map ansible var name -> set of env var names it resolves from."""
    out: dict[str, set[str]] = {}
    files = list(ROLES.rglob("defaults/main.yml")) + list(ROLES.rglob("vars/main.yml"))
    files += list(INVENTORY.rglob("*.yml"))
    for path in files:
        if "/molecule/" in str(path):
            continue
        current = None
        for line in path.read_text().splitlines():
            key = VAR_KEY_RE.match(line)
            if key:
                current = key.group(1)
            if current:
                for env in ENV_LOOKUP_RE.findall(line):
                    out.setdefault(current, set()).add(env)
    return out


def role_of(path: Path) -> str | None:
    try:
        rel = path.relative_to(ROLES)
    except ValueError:
        return None
    return rel.parts[0]


def var_owner_roles(var: str, env_vars: dict[str, set[str]]) -> set[str]:
    """Roles that need `var`: the defining role, or every role referencing it."""
    owners: set[str] = set()
    word = re.compile(rf"\b{re.escape(var)}\b")
    for role_dir in sorted(p for p in ROLES.iterdir() if p.is_dir()):
        for path in role_dir.rglob("*"):
            if not path.is_file() or "/molecule/" in str(path):
                continue
            if path.suffix not in {".yml", ".yaml", ".j2", ".cfg", ".conf"}:
                continue
            try:
                if word.search(path.read_text()):
                    owners.add(role_dir.name)
                    break
            except UnicodeDecodeError:
                continue
    return owners


def playbook_roles(path: Path) -> set[str]:
    data = _yaml_load(path)
    roles: set[str] = set()
    if not isinstance(data, list):
        return roles
    for play in data:
        if not isinstance(play, dict):
            continue
        for entry in play.get("roles") or []:
            if isinstance(entry, str):
                roles.add(entry)
            elif isinstance(entry, dict) and "role" in entry:
                roles.add(entry["role"])
    return roles


PLAYBOOK_RE = re.compile(r"playbooks/([A-Za-z0-9_-]+)\.yml")


def taskfile_playbook_tasks(taskfile: Path | None = None) -> list[tuple[str, str, set[str]]]:
    """(task name, playbook stem, exported env keys) for each op-run task."""
    data = _yaml_load(taskfile or TASKFILE)
    out = []
    for name, task in (data.get("tasks") or {}).items():
        if not isinstance(task, dict):
            continue
        cmds = task.get("cmds") or []
        joined = " ".join(c for c in cmds if isinstance(c, str))
        if "ansible-playbook" not in joined:
            continue
        match = PLAYBOOK_RE.search(joined)
        if not match:
            continue
        env = set((task.get("env") or {}).keys())
        out.append((name, match.group(1), env))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # --taskfile lets the test suite point the check at a mutated COPY rather
    # than editing the tracked Taskfile.yml in place.
    taskfile = TASKFILE
    if "--taskfile" in argv:
        taskfile = Path(argv[argv.index("--taskfile") + 1])

    env_vars = collect_env_vars()
    # ansible var -> roles that need it (cached across playbooks)
    needed_by: dict[str, set[str]] = {v: var_owner_roles(v, env_vars) for v in env_vars}

    failures: list[str] = []
    checked = 0
    for task_name, stem, exported in taskfile_playbook_tasks(taskfile):
        pb = PLAYBOOKS / f"{stem}.yml"
        if not pb.exists():
            continue
        roles = playbook_roles(pb)
        if not roles:
            continue
        checked += 1
        required: dict[str, str] = {}
        for var, envs in env_vars.items():
            if needed_by.get(var, set()) & roles:
                for env in envs:
                    required[env] = var
        for env, var in sorted(required.items()):
            if env in exported:
                continue
            if (task_name, env) in EXPECTED_MISSING:
                continue
            failures.append(
                f"  task {task_name} -> playbooks/{stem}.yml: {env} is not "
                f"exported but {var} (needed by "
                f"{', '.join(sorted(needed_by[var] & roles))}) resolves from it"
            )

    if failures:
        print("env-secret coverage FAILED:")
        print("\n".join(failures))
        print(
            "\nAdd the op:// reference to the task's env: block, or add an "
            "EXPECTED_MISSING entry with a rationale in "
            "scripts/check-env-secret-coverage.py."
        )
        return 1
    print(f"env-secret coverage OK ({checked} playbook-running tasks checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
