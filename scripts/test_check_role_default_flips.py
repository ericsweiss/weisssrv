"""Tests that check-role-default-flips.py catches a role default that changes unadopted."""
from __future__ import annotations

import pytest
from script_loader import load_script

gate = load_script("check-role-default-flips.py")


def _run(monkeypatch, capsys, tmp_path, old: dict, new: dict) -> tuple[int, str]:
    """Drive main() over two in-memory default sets and a fixture inventory."""
    group_vars = tmp_path / "prod" / "group_vars"
    group_vars.mkdir(parents=True)
    (group_vars / "all.yml").write_text("unrelated_var: 1\n", encoding="utf-8")
    monkeypatch.setattr(gate, "lib_root", lambda explicit=None: tmp_path)
    monkeypatch.setattr(gate, "role_defaults", lambda lib, ref: {"old": old, "new": new}[ref])
    rc = gate.main(["--from", "old", "--to", "new", "--inventory", str(tmp_path / "prod")])
    return rc, capsys.readouterr().err


def test_a_flipped_bool_is_reported() -> None:
    flips = gate.flipped_defaults(
        {"zfs_encryption": {"zfs_encryption_autostart": True}},
        {"zfs_encryption": {"zfs_encryption_autostart": False}},
    )
    assert flips == [("zfs_encryption", "zfs_encryption_autostart", True, False)]


def test_a_moved_string_is_reported() -> None:
    flips = gate.flipped_defaults(
        {"restic_offsite": {"restic_offsite_timer_calendar": "07:15"}},
        {"restic_offsite": {"restic_offsite_timer_calendar": "08:00"}},
    )
    assert flips == [("restic_offsite", "restic_offsite_timer_calendar", "07:15", "08:00")]


def test_a_mapping_change_is_not_a_flip() -> None:
    """Nested merges diff unreadably, so mappings stay out of scope."""
    assert gate.flipped_defaults({"qol": {"qol_aliases": {"a": "1"}}}, {"qol": {"qol_aliases": {"a": "2"}}}) == []


def test_a_new_key_is_not_a_flip() -> None:
    assert gate.flipped_defaults({"qol": {}}, {"qol": {"qol_new_toggle": True}}) == []


def test_a_role_the_old_ref_lacks_is_not_a_flip() -> None:
    assert gate.flipped_defaults({}, {"brand_new": {"brand_new_enabled": True}}) == []


def test_an_undeclared_flip_is_unadopted() -> None:
    flips = [("nic_tuning", "nic_tuning_bond_asa_guard", False, True)]
    assert gate.unadopted(flips, {"other_var"}, set())


def test_a_declared_flip_is_adopted() -> None:
    flips = [("nic_tuning", "nic_tuning_bond_asa_guard", False, True)]
    assert gate.unadopted(flips, {"nic_tuning_bond_asa_guard"}, set()) == []


def test_an_allowlisted_flip_is_adopted() -> None:
    flips = [("nic_tuning", "nic_tuning_bond_asa_guard", False, True)]
    assert gate.unadopted(flips, set(), {"nic_tuning_bond_asa_guard"}) == []


def test_a_moved_string_default_fails_the_gate(monkeypatch, capsys, tmp_path) -> None:
    rc, err = _run(
        monkeypatch,
        capsys,
        tmp_path,
        {"restic_offsite": {"restic_offsite_timer_calendar": "07:15"}},
        {"restic_offsite": {"restic_offsite_timer_calendar": "08:00"}},
    )
    assert rc == 1
    assert "restic_offsite_timer_calendar" in err


def test_an_emptied_list_default_fails_the_gate(monkeypatch, capsys, tmp_path) -> None:
    key = "nas_storage_archive_backup_on_success_units"
    rc, err = _run(
        monkeypatch,
        capsys,
        tmp_path,
        {"nas_storage": {key: ["restic-offsite.service"]}},
        {"nas_storage": {key: []}},
    )
    assert rc == 1
    assert key in err


def test_a_key_new_on_the_to_side_passes_the_gate(monkeypatch, capsys, tmp_path) -> None:
    """A default the old ref does not ship is out of scope, so the gate stays green."""
    rc, err = _run(
        monkeypatch,
        capsys,
        tmp_path,
        {"proxmox_firewall": {}},
        {"proxmox_firewall": {"proxmox_firewall_insecure_migration_ports": False}},
    )
    assert rc == 0
    assert err == ""


def test_an_empty_inventory_is_vacuous(tmp_path) -> None:
    with pytest.raises(gate.Vacuous):
        gate.inventory_keys(tmp_path)


def test_a_checkout_without_the_collection_is_vacuous(tmp_path) -> None:
    with pytest.raises(gate.Vacuous):
        gate.lib_root(str(tmp_path))


def test_a_missing_lib_exits_two(tmp_path) -> None:
    assert gate.main(["--from", "v0.0.1", "--to", "v0.0.2", "--lib", str(tmp_path)]) == 2
