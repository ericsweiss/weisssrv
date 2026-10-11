"""Every playbook that rewrites a cert target's authorized_keys re-seeds it.

base drops the pinned cert-distribution key and the loss only shows at the next
renewal, so the re-seed goes to `tasks/_reseed-cert-target.yml` or `acme_certs`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
PLAYBOOKS = REPO / "ansible/playbooks"
DNS_VARS = REPO / "ansible/inventories/prod/group_vars/dns.yml"
SHARED_TASK = "tasks/_reseed-cert-target.yml"
# The shared task reads the pubkey from the authority host when the env var is
# unset, so a play gating on this value skips a re-seed that would have worked.
PRIVATE_GATE = "acme_certs_ssh_public_key"


def _coverage():
    """Reuse check-deploy-host-coverage.py's inventory expansion and play parser."""
    return load_script("check-deploy-host-coverage.py")


@pytest.fixture(scope="module")
def coverage():
    return _coverage()


@pytest.fixture(scope="module")
def groups(coverage):
    return coverage.build_inventory(REPO)


@pytest.fixture(scope="module")
def cert_targets() -> set[str]:
    """Hosts dns-01 pushes the wildcard cert to, sudo targets only.

    An `ssh_no_sudo` target (HAOS) is seeded by a different path and is never
    a base-managed host, so it is out of scope here.
    """
    declared = yaml.safe_load(DNS_VARS.read_text())["_acme_certs_targets"]
    return {
        str(entry["host"])
        for entry in declared
        if entry.get("host_key") and not entry.get("ssh_no_sudo", False)
    }


def _playbook_files() -> list[Path]:
    """Every playbook directly under ansible/playbooks/, either YAML suffix."""
    return sorted(
        {p for suffix in ("yml", "yaml") for p in PLAYBOOKS.glob(f"*.{suffix}")}
    )


def _plays(path: Path) -> list[dict]:
    """The playbook's own plays; an `import_playbook` entry is not one."""
    docs = yaml.safe_load(path.read_text()) or []
    return [d for d in docs if isinstance(d, dict) and "import_playbook" not in d]


def _role_names(play: dict) -> set[str]:
    """Short role names a play declares, `- role:` and bare strings alike."""
    found = set()
    for entry in play.get("roles") or []:
        name = entry.get("role") if isinstance(entry, dict) else entry
        if name:
            found.add(str(name).rsplit(".", 1)[-1])
    return found


def _includes_shared_reseed(play: dict) -> bool:
    """The play hands a target to the shared re-seed task."""
    return any(
        str(task.get("ansible.builtin.include_tasks") or task.get("include_tasks") or "")
        == SHARED_TASK
        for task in play.get("tasks") or []
        if isinstance(task, dict)
    )


def reseed_problems(
    coverage, groups, cert_targets: set[str], override: dict[str, list[dict]] | None = None
) -> list[str]:
    """Playbooks that rewrite a cert target's keys without re-seeding after.

    `override` replaces one playbook's parsed plays, which is how the mutation
    case below drives the comparison without editing the tree.
    """
    problems: list[str] = []
    for path in _playbook_files():
        plays = (override or {}).get(path.name) or _plays(path)
        last_base = -1
        rewritten: set[str] = set()
        for index, play in enumerate(plays):
            if "base" not in _role_names(play):
                continue
            hosts = coverage.expand(play.get("hosts") or "", groups) & cert_targets
            if hosts:
                last_base = index
                rewritten |= hosts
        if last_base < 0:
            continue
        reseeds = any(
            _includes_shared_reseed(play) or "acme_certs" in _role_names(play)
            for play in plays[last_base + 1 :]
        )
        if not reseeds:
            problems.append(
                f"{path.name}: applies base to {sorted(rewritten)} but no later "
                f"play includes {SHARED_TASK} or runs the acme_certs role — the "
                "next renewal push to that target fails"
            )
    return problems


def test_every_base_playbook_reseeds_its_cert_targets(coverage, groups, cert_targets):
    assert cert_targets, f"{DNS_VARS.name} declares no sudo cert-distribution target"
    problems = reseed_problems(coverage, groups, cert_targets)
    assert not problems, "cert-distribution re-seed gaps:\n  " + "\n  ".join(problems)


def test_the_comparison_examines_real_playbooks(coverage, groups, cert_targets):
    """A parser that matched nothing would make the gate above vacuous."""
    examined = [
        path.name
        for path in _playbook_files()
        for play in _plays(path)
        if "base" in _role_names(play)
        and coverage.expand(play.get("hosts") or "", groups) & cert_targets
    ]
    assert {"base.yml", "mail.yml"} <= set(examined), (
        f"playbooks applying base to a cert target: {sorted(set(examined))} — "
        "the role or host parser stopped matching"
    )


def test_a_playbook_losing_its_reseed_is_caught(coverage, groups, cert_targets):
    """Mutation case: without this the gate would pass on an empty comparison."""
    original = _plays(PLAYBOOKS / "mail.yml")
    assert any(_includes_shared_reseed(play) for play in original), (
        "mail.yml no longer includes the shared re-seed task"
    )
    stripped = [play for play in original if not _includes_shared_reseed(play)]
    problems = reseed_problems(
        coverage, groups, cert_targets, override={"mail.yml": stripped}
    )
    assert any(p.startswith("mail.yml:") for p in problems), (
        f"stripping mail.yml's re-seed produced no finding: {problems}"
    )


@pytest.mark.parametrize("path", _playbook_files(), ids=lambda p: p.name)
def test_no_playbook_gates_a_reseed_on_the_env_pubkey(path):
    """A `when:` on the env pubkey skips a re-seed the shared task would make.

    The shared task falls back to the key file acme_certs wrote on the
    authority host, so a deploy job without DNS01_SSH_PUBLIC_KEY still seeds.
    """
    for play in _plays(path):
        for task in play.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            condition = str(task.get("when") or "")
            assert PRIVATE_GATE not in condition, (
                f"{path.name}: task {task.get('name')!r} gates on {PRIVATE_GATE} "
                f"— route the re-seed through {SHARED_TASK} instead"
            )
