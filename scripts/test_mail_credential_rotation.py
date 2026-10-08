"""Drift guard: the mail-credential rotation must reach every null client.

An app VM added with `postfix_null_client` in its own playbook but missing from
rotate-mail-credential.yml's host pattern keeps the revoked password. See docs/15.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
PLAYBOOKS = REPO / "ansible" / "playbooks"
ROTATION = PLAYBOOKS / "rotate-mail-credential.yml"
ROLE = "postfix_null_client"


def _plays(path: Path) -> list[dict]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [p for p in loaded if isinstance(p, dict)]


def _role_entries(play: dict) -> list[dict]:
    """The play's roles as `{role: short-name, tags: [...]}` dicts.

    Roles ship FQCN'd from the weisssrv.infra collection; the namespace is
    stripped so the comparisons below stay keyed on the bare role name.
    """
    entries = []
    for entry in play.get("roles") or []:
        if isinstance(entry, str):
            entries.append({"role": entry.rsplit(".", 1)[-1], "tags": []})
        elif isinstance(entry, dict) and "role" in entry:
            tags = entry.get("tags") or []
            entries.append({
                "role": str(entry["role"]).rsplit(".", 1)[-1],
                "tags": [tags] if isinstance(tags, str) else tags,
            })
    return entries


def _selected_groups(pattern: str) -> set[str]:
    """The groups a `hosts:` pattern SELECTS — exclusions/intersections dropped.

    `dns:!deploy_skipped` selects `dns`; the ledger exclusion is not a target.
    """
    return {t for t in str(pattern).split(":") if t and t[0] not in "!&"}


def _null_client_plays() -> tuple[set[str], dict[str, str]]:
    """(groups reachable via `--tags postfix_null_client`, {playbook: pattern}).

    A play that tags the role is already rotated by the documented site.yml
    command; every other play's groups are the rotation playbook's remit.
    """
    tag_reachable: set[str] = set()
    untagged: dict[str, str] = {}
    # Both suffixes: Ansible runs either, so globbing one spelling would drop a
    # play from the gate silently.
    for path in sorted(
        {p for suffix in ("yml", "yaml") for p in PLAYBOOKS.rglob(f"*.{suffix}")}
    ):
        if path == ROTATION:
            continue
        rel = str(path.relative_to(PLAYBOOKS))
        for play in _plays(path):
            for entry in _role_entries(play):
                if entry["role"] != ROLE:
                    continue
                if ROLE in entry["tags"]:
                    tag_reachable |= _selected_groups(play.get("hosts", ""))
                else:
                    untagged[rel] = str(play.get("hosts", ""))
    return tag_reachable, untagged


def _rotation_targets() -> set[str]:
    plays = _plays(ROTATION)
    assert len(plays) == 1, f"{ROTATION.name} should be a single play"
    assert ROLE in {e["role"] for e in _role_entries(plays[0])}, (
        f"{ROTATION.name} no longer applies {ROLE} — it rotates nothing"
    )
    return _selected_groups(plays[0]["hosts"])


def test_rotation_playbook_covers_every_untagged_null_client():
    tag_reachable, untagged = _null_client_plays()
    assert untagged, (
        "no play carries postfix_null_client untagged — either the role moved "
        "or this guard is reading the wrong thing"
    )
    targets = _rotation_targets()
    missing = {
        pb: sorted(_selected_groups(hosts) - tag_reachable - targets)
        for pb, hosts in untagged.items()
        if _selected_groups(hosts) - tag_reachable - targets
    }
    assert not missing, (
        f"{ROTATION.name} does not target {missing}. Those hosts get "
        f"postfix_null_client only from a play that does not tag it, which "
        f"`--tags postfix_null_client` cannot drive, so a credential rotation "
        f"would leave them authenticating with the revoked password."
    )


def test_rotation_playbook_targets_nothing_extra():
    """A target with no playbook behind it is a typo or a removed app."""
    _, untagged = _null_client_plays()
    covered = set().union(*(_selected_groups(h) for h in untagged.values()))
    extra = _rotation_targets() - covered
    assert not extra, (
        f"{ROTATION.name} targets {sorted(extra)}, which no play applies {ROLE} "
        f"to — a stale or misspelled pattern silently matches no hosts"
    )


def test_rotation_playbook_gathers_facts():
    """The whole point is that the app playbooks' deferred gathering breaks --tags."""
    play = _plays(ROTATION)[0]
    assert play.get("gather_facts") is True, (
        f"{ROTATION.name} must set gather_facts: true explicitly — "
        f"postfix_null_client branches on os_family"
    )
