"""Hold the cross-cutting roles in step between site.yml and the app playbooks.

Asserts, per host, that the shared roles site.yml applies and the ones the
host's own app playbook applies are the same set.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
PLAYBOOKS = REPO / "ansible/playbooks"

# The roles the "Shared with site.yml" comments mark. Adding a third cross-cutting
# role to site.yml means adding it here and to every app playbook it covers.
SHARED_ROLES = frozenset({"node_exporter_host", "alloy_host"})

# Every app playbook that re-declares the shared roles for its own hosts.
APP_PLAYBOOKS = (
    "dns.yml",
    "gitlab.yml",
    "immich.yml",
    "immich-ml.yml",
    "k3s.yml",
    "mail.yml",
    "nextcloud.yml",
    "plex.yml",
    "storage.yml",
)


def _playbook_files() -> set[Path]:
    """Every playbook directly under ansible/playbooks/, either YAML suffix."""
    return {p for suffix in ("yml", "yaml") for p in PLAYBOOKS.glob(f"*.{suffix}")}


def _coverage_module():
    """Reuse check-deploy-host-coverage.py's inventory expansion and play parser."""
    return load_script("check-deploy-host-coverage.py")


@pytest.fixture(scope="module")
def coverage():
    return _coverage_module()


@pytest.fixture(scope="module")
def groups(coverage):
    return coverage.build_inventory(REPO)


def _shared_by_host(coverage, groups, playbook: str) -> dict[str, set[str]]:
    """host -> the SHARED_ROLES that playbook applies to it."""
    result: dict[str, set[str]] = {}
    for play in coverage.parse_playbook(playbook, REPO):
        shared = {
            role.rsplit(".", 1)[-1]
            for role in play["roles"]
            if role.rsplit(".", 1)[-1] in SHARED_ROLES
        }
        if not shared:
            continue
        for host in coverage.expand(play["hosts"], groups):
            result.setdefault(host, set()).update(shared)
    return result


@pytest.mark.parametrize("playbook", APP_PLAYBOOKS)
def test_app_playbook_matches_site_yml(coverage, groups, playbook):
    site = _shared_by_host(coverage, groups, "site.yml")
    app = _shared_by_host(coverage, groups, playbook)
    hosts = set()
    for play in coverage.parse_playbook(playbook, REPO):
        hosts |= coverage.expand(play["hosts"], groups)

    mismatches = []
    for host in sorted(hosts):
        expected = site.get(host, set()) & SHARED_ROLES
        actual = app.get(host, set())
        if expected != actual:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            mismatches.append(
                f"{host}: {playbook} is missing {missing or '[]'}, "
                f"declares unshared {extra or '[]'}"
            )
    assert not mismatches, (
        f"{playbook} and site.yml disagree about the cross-cutting roles "
        f"{sorted(SHARED_ROLES)}:\n  "
        + "\n  ".join(mismatches)
        + f"\n\nAdd the role to {playbook} (or to site.yml) so a standalone "
        "deploy and a full site run ship the same thing."
    )


def _playbooks_applying_shared_roles(coverage) -> set[str]:
    """Playbooks whose plays declare a SHARED_ROLES member, site.yml aside."""
    found = set()
    # Both suffixes: Ansible runs either, so globbing one spelling would drop a
    # playbook from the gate silently.
    for path in sorted(_playbook_files()):
        if path.name == "site.yml":
            continue
        for play in coverage.parse_playbook(path.name, REPO):
            if any(role.rsplit(".", 1)[-1] in SHARED_ROLES for role in play["roles"]):
                found.add(path.name)
                break
    return found


def test_every_playbook_applying_a_shared_role_is_listed(coverage):
    """Derived from the roles, so a playbook without the marker comment counts."""
    applying = _playbooks_applying_shared_roles(coverage)
    assert applying, "no playbook declares a shared role — the parser stopped matching"
    unlisted = sorted(applying - set(APP_PLAYBOOKS))
    assert not unlisted, (
        f"playbooks applying {sorted(SHARED_ROLES)} but absent from APP_PLAYBOOKS: "
        f"{unlisted} — add them so a standalone deploy is compared with site.yml."
    )


def test_every_marked_playbook_is_listed():
    """The cheaper extra: a playbook carrying the marker comment must be listed."""
    marker = "Shared with site.yml"
    marked = {
        path.name
        for path in _playbook_files()
        if marker in path.read_text() and path.name != "site.yml"
    }
    unlisted = sorted(marked - set(APP_PLAYBOOKS))
    assert not unlisted, (
        f"playbooks carrying the shared-role marker but absent from "
        f"APP_PLAYBOOKS: {unlisted}"
    )


def test_the_comparison_fails_when_a_shared_role_is_dropped(coverage, groups):
    """Mutation case: the gate must red when an app playbook loses a shared role.

    Without this, a comparison that silently compared empty sets would pass on
    every playbook and the gate would be decorative.
    """
    site = _shared_by_host(coverage, groups, "site.yml")
    app = _shared_by_host(coverage, groups, "dns.yml")
    assert app, "dns.yml declares no shared roles — the parser stopped matching"

    mutated = {host: roles - {"alloy_host"} for host, roles in app.items()}
    differing = [
        host
        for host in mutated
        if (site.get(host, set()) & SHARED_ROLES) != mutated[host]
    ]
    assert differing, (
        "dropping alloy_host from dns.yml produced no mismatch — the comparison "
        "is not actually comparing role sets"
    )
