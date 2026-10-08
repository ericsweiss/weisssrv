"""Version pins duplicated in all.yml and the pinned collection stay equal.

The library side is read at the ref `.gitlab-ci.yml` pins, falling back to the
checkout (`$WEISSSRV_LIB_PATH`, else `../weisssrv-lib`) when the tag is uncut.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from test_vendored_byte_identity import _lib_root, _pinned_ref

REPO = Path(__file__).resolve().parent.parent
ALL_VARS = REPO / "ansible/inventories/prod/group_vars/all.yml"
ROLE_PATH = "ansible_collections/weisssrv/infra/roles/{role}/defaults/main.yml"

# inventory key -> the role whose defaults/main.yml carries the same key.
DUPLICATED_PINS = {
    "qol_omz_commit": "qol",
    "qol_vundle_version": "qol",
    "proxmox_lxc_template": "proxmox_lxc",
    "proxmox_vm_virtio_win_version": "proxmox_vm",
    "proxmox_vm_virtio_win_checksum": "proxmox_vm",
    "restic_offsite_rclone_version": "restic_offsite",
    "restic_offsite_rclone_deb_sha256": "restic_offsite",
    "unbound_exporter_version": "unbound_exporter",
    "unbound_exporter_checksum": "unbound_exporter",
}


def role_defaults(role: str) -> dict:
    """The role's defaults at the pinned ref, else from the working tree."""
    lib, relpath = _lib_root(), ROLE_PATH.format(role=role)
    assert (lib / "ansible_collections/weisssrv/infra").is_dir(), (
        f"{lib} carries no weisssrv.infra collection. This gate never skips."
    )
    shown = subprocess.run(
        ["git", "-C", str(lib), "show", f"{_pinned_ref()}:{relpath}"],
        capture_output=True,
        text=True,
        check=False,
    )
    source = shown.stdout if shown.returncode == 0 else (lib / relpath).read_text()
    return yaml.safe_load(source) or {}


def pin_mismatches(inventory: dict) -> list[str]:
    problems = []
    for key, role in sorted(DUPLICATED_PINS.items()):
        defaults = role_defaults(role)
        if key not in inventory:
            problems.append(f"{key}: absent from group_vars/all.yml")
        elif key not in defaults:
            problems.append(f"{key}: absent from the {role} role defaults")
        elif inventory[key] != defaults[key]:
            problems.append(
                f"{key}: all.yml has {inventory[key]!r}, the {role} role default "
                f"has {defaults[key]!r}"
            )
    return problems


@pytest.fixture(scope="module")
def inventory() -> dict:
    return yaml.safe_load(ALL_VARS.read_text())


def test_the_pin_list_is_not_empty():
    assert DUPLICATED_PINS, "no pins registered — this gate is examining nothing"


def test_every_duplicated_pin_agrees_with_the_role_default(inventory):
    problems = pin_mismatches(inventory)
    assert not problems, (
        "pins that drifted from the pinned collection (bump BOTH, then cut a "
        "library tag and move the pin here):\n  " + "\n  ".join(problems)
    )


def test_a_drifted_pin_is_caught(inventory):
    broken = {**inventory, "qol_vundle_version": "v0.0.0-not-a-tag"}
    assert pin_mismatches(broken)


def test_a_dropped_pin_is_caught(inventory):
    broken = {k: v for k, v in inventory.items() if k != "proxmox_lxc_template"}
    assert pin_mismatches(broken)
