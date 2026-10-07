"""Inventory variables must be real role variables and must not restate a default.

Read at the pinned collection version. Pins and identity mirrors are derived
exemptions; ACKNOWLEDGED is restatement debt, PRESTAGED awaits a collection bump.
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
INVENTORY = REPO / "ansible/inventories/prod"
LIB_CHECKOUT = Path(os.environ.get("WEISSSRV_LIB_PATH") or REPO.parent / "weisssrv-lib")
_ROLES_RELPATH = "ansible_collections/weisssrv/infra/roles"

# Checksum partners of pins the registry carries only in untracked_allowlist:
# they live beside their version in the vars file by the same rule.
_PARTNER_PINS = {"restic_offsite_rclone_deb_sha256"}

# (inventory file relative to ansible/inventories/prod, variable) pairs that
# restate their role default. Delete the inventory line and the entry together.
ACKNOWLEDGED: set[tuple[str, str]] = {
    ("group_vars/all.yml", "qol_omz_commit"),
    ("group_vars/dns.yml", "adguard_home_dhcp_enabled"),
    ("group_vars/dns.yml", "adguard_home_http_port"),
    ("group_vars/dns.yml", "adguard_home_install_path"),
    ("group_vars/dns.yml", "adguard_home_ratelimit"),
    ("group_vars/dns.yml", "adguard_home_tls_enabled"),
    ("group_vars/dns.yml", "unbound_port"),
    ("group_vars/gitlab_servers.yml", "gitlab_backup_nfs_options"),
    ("group_vars/immich_servers.yml", "immich_nginx_real_ip_groups"),
    ("group_vars/k3s.yml", "k3s_api_port"),
    ("group_vars/k3s.yml", "nfs_tls_scrub_client_cert"),
    ("group_vars/k3s.yml", "k3s_flannel_backend"),
    ("group_vars/nextcloud_servers.yml", "nextcloud_mail_from_address"),
    ("group_vars/nextcloud_servers.yml", "nextcloud_oidc_allow_local_remote_servers"),
    ("group_vars/proxmox.yml", "nfs_tls_scrub_client_cert"),
    ("group_vars/proxmox.yml", "tailscale_accept_routes"),
    ("host_vars/dns-01.yml", "adguard_sync_schedule"),
    ("host_vars/dns-01.yml", "proxmox_lxc_onboot"),
    ("host_vars/dns-02.yml", "proxmox_lxc_onboot"),
    ("host_vars/plex.yml", "plex_config_dir"),
    ("host_vars/plex.yml", "plex_media_dir"),
    ("host_vars/plex.yml", "plex_transcode_dir"),
    ("host_vars/pve-nas-01.yml", "nas_storage_media_mover_schedule"),
    ("host_vars/pve-nas-01.yml", "nas_storage_nfs_disable_delegations"),
    ("host_vars/pve-nas-01.yml", "nas_storage_smartd_enabled"),
    ("host_vars/pve-nas-01.yml", "nas_storage_swap_clean_schedule"),
    ("host_vars/pve-nas-01.yml", "nas_storage_zfs_scrub_enabled"),
    ("host_vars/pve-nas-01.yml", "nas_storage_zfs_scrub_schedule"),
    ("host_vars/pve-nas-01.yml", "restic_offsite_timer_calendar"),
    ("host_vars/smtp-relay.yml", "proxmox_lxc_onboot"),
}

# (inventory file, variable) pairs whose prefix collides with a role name but
# which are not role variables at all.
NOT_ROLE_VARS: set[tuple[str, str]] = {
    # In-cluster Helm chart pins; the `gitlab` role prefix is a collision.
    ("group_vars/all.yml", "gitlab_agent_helm_version"),
    ("group_vars/all.yml", "gitlab_runner_helm_version"),
    # Delegate target for the maintenance playbooks' cluster-wide kubectl; the
    # `k3s` role prefix is a collision.
    ("group_vars/k3s.yml", "k3s_delegate_server"),
}

# Variables staged ahead of the collection release that consumes them: inert
# until the pin moves. Each entry expires at that bump, enforced below.
PRESTAGED: set[tuple[str, str]] = {
    ("group_vars/all.yml", "proxmox_firewall_host_egress_extra_ports"),
    ("group_vars/all.yml", "proxmox_firewall_smtp_relay_extra_egress_ports"),
    ("group_vars/all.yml", "proxmox_firewall_smtp_relay_sources"),
    ("group_vars/all.yml", "resolv_conf_options"),
    ("group_vars/all.yml", "tailscale_require_authkey"),
    ("group_vars/bonded_hosts.yml", "nic_tuning_bond_primary"),
    ("group_vars/gitlab_servers.yml", "gitlab_bundled_alertmanager_enabled"),
    ("group_vars/gitlab_servers.yml", "gitlab_bundled_prometheus_enabled"),
    ("group_vars/immich_servers.yml", "immich_server_mem_limit"),
    ("group_vars/proxmox.yml", "node_exporter_host_processes_collector"),
    ("host_vars/pve-nas-01.yml", "nas_storage_archive_backup_exclude"),
    ("host_vars/pve-nas-01.yml", "nas_storage_archive_backup_on_success_units"),
    ("host_vars/pve-nas-01.yml", "nas_storage_export_root"),
    ("host_vars/pve-nas-01.yml", "nas_storage_swap_clean_conflicting_units"),
    ("host_vars/pve-nas-01.yml", "node_exporter_host_slab_caches"),
    ("host_vars/pve-nas-01.yml", "node_exporter_host_slabinfo_collector"),
    ("host_vars/pve-nas-01.yml", "restic_offsite_conflicting_units"),
}


def _load(name: str):
    return load_script(name)


def pinned_collection_version() -> str:
    """The weisssrv.infra version ansible/requirements.yml installs."""
    requirements = yaml.safe_load((REPO / "ansible/requirements.yml").read_text())
    for entry in requirements.get("collections") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", ""))
        if "weisssrv-lib" in name or name == "weisssrv.infra":
            return str(entry.get("version", "")).lstrip("v")
    raise AssertionError("ansible/requirements.yml no longer pins weisssrv.infra")


def _git(lib: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(["git", "-C", str(lib), *args], capture_output=True, text=True)
    return result.returncode, result.stdout


def _defaults_from_disk(roles: Path) -> dict[str, list[tuple[str, object]]]:
    found: dict[str, list[tuple[str, object]]] = {}
    for role in sorted(roles.iterdir()):
        path = role / "defaults" / "main.yml"
        if not path.is_file():
            continue
        for key, value in (yaml.safe_load(path.read_text()) or {}).items():
            found.setdefault(key, []).append((role.name, value))
    assert found, f"parsed no role defaults out of {roles}"
    return found


@functools.lru_cache(maxsize=1)
def pinned_source() -> tuple[str, str]:
    """Where the pinned collection is readable: ('disk', roles path) or ('git', tag).

    An installed copy at that exact version wins; otherwise the library checkout
    is read at the matching tag.
    """
    pinned = pinned_collection_version()
    for candidate in (
        # `task ansible:lint` installs the pinned collection here.
        REPO / ".ansible-home/collections/ansible_collections/weisssrv/infra",
        Path.home() / ".ansible/collections/ansible_collections/weisssrv/infra",
    ):
        manifest = candidate / "MANIFEST.json"
        if not manifest.is_file():
            continue
        match = re.search(r'"version"\s*:\s*"([^"]+)"', manifest.read_text())
        if match and match.group(1) == pinned and (candidate / "roles").is_dir():
            return "disk", str(candidate / "roles")

    ref = f"v{pinned}"
    assert (LIB_CHECKOUT / ".git").exists(), (
        f"no weisssrv.infra {pinned} installed and no weisssrv-lib checkout at "
        f"{LIB_CHECKOUT} (set $WEISSSRV_LIB_PATH). This gate never skips."
    )
    code, _ = _git(LIB_CHECKOUT, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    assert code == 0, (
        f"{LIB_CHECKOUT} has no {ref} — fetch it, or install the collection with "
        "`ansible-galaxy install -r ansible/requirements.yml --force`"
    )
    return "git", ref


def _defaults_from_git(ref: str) -> dict[str, list[tuple[str, object]]]:
    code, listing = _git(
        LIB_CHECKOUT, "ls-tree", "-r", "--name-only", f"{ref}:{_ROLES_RELPATH}"
    )
    assert code == 0, f"could not list the roles of {ref} in {LIB_CHECKOUT}"

    found: dict[str, list[tuple[str, object]]] = {}
    for relpath in listing.split():
        if not relpath.endswith("defaults/main.yml"):
            continue
        role = relpath.split("/", 1)[0]
        code, blob = _git(LIB_CHECKOUT, "show", f"{ref}:{_ROLES_RELPATH}/{relpath}")
        if code != 0:
            continue
        for key, value in (yaml.safe_load(blob) or {}).items():
            found.setdefault(key, []).append((role, value))
    assert found, f"parsed no role defaults out of {ref}"
    return found


def role_defaults() -> dict[str, list[tuple[str, object]]]:
    """{variable: [(role, default), ...]} for the pinned collection."""
    kind, source = pinned_source()
    if kind == "disk":
        return _defaults_from_disk(Path(source))
    return _defaults_from_git(source)


@functools.lru_cache(maxsize=1)
def role_names() -> tuple[str, ...]:
    """The pinned collection's role directory names, longest first so the most
    specific prefix of a variable wins."""
    kind, source = pinned_source()
    if kind == "disk":
        names = [path.name for path in Path(source).iterdir() if path.is_dir()]
    else:
        code, listing = _git(
            LIB_CHECKOUT, "ls-tree", "--name-only", f"{source}:{_ROLES_RELPATH}"
        )
        assert code == 0, f"could not list the roles of {source} in {LIB_CHECKOUT}"
        names = [line.rstrip("/") for line in listing.split()]
    assert names, f"the pinned collection lists no roles under {source}"
    return tuple(sorted(names, key=len, reverse=True))


def collection_mentions(candidates: set[str]) -> set[str]:
    """Which of `candidates` the pinned collection names anywhere in its roles.

    Defaults alone are too narrow: plenty of role variables are only read in
    tasks or templates, with no entry in defaults/main.yml.
    """
    if not candidates:
        return set()
    alternation = "|".join(sorted(map(re.escape, candidates)))
    kind, source = pinned_source()
    if kind == "disk":
        pattern = re.compile(rf"\b(?:{alternation})\b")
        found: set[str] = set()
        for path in Path(source).rglob("*"):
            if path.is_file():
                found.update(pattern.findall(path.read_text(errors="replace")))
        return found
    code, out = _git(
        LIB_CHECKOUT, "grep", "-h", "-o", "-w", "-E", alternation, source,
        "--", _ROLES_RELPATH,
    )
    assert code in (0, 1), f"git grep failed against {source} in {LIB_CHECKOUT}"
    return {token for token in re.findall(r"[A-Za-z0-9_]+", out) if token in candidates}


def _inventory_files(inventory: Path) -> list[Path]:
    """Every vars file in the inventory, both YAML suffixes.

    The emptiness assert is a floor: a renamed or moved tree would otherwise
    make every check over these files pass by inspecting nothing.
    """
    paths = sorted(
        path
        for sub in ("group_vars", "host_vars")
        for suffix in ("*.yml", "*.yaml")
        for path in (inventory / sub).rglob(suffix)
    )
    assert paths, f"no vars files under {inventory}/group_vars or host_vars"
    return paths


def role_prefixed_keys(inventory: Path, names: tuple[str, ...]) -> list[tuple[str, str]]:
    """(file, variable) for every inventory key that starts with `<role>_`."""
    found: list[tuple[str, str]] = []
    for path in _inventory_files(inventory):
        site = yaml.safe_load(path.read_text()) or {}
        if not isinstance(site, dict):
            continue
        rel = str(path.relative_to(inventory))
        for key in site:
            if any(str(key).startswith(f"{role}_") for role in names):
                found.append((rel, str(key)))
    return found


def derived_exemptions() -> set[str]:
    """Variables another gate requires the inventory to spell out."""
    registry = _load("version-registry.py")
    literals = _load("check-cluster-literals.py")
    tracked: set[str] = set(registry.CONFIG["untracked_allowlist"])
    for svc in registry.CONFIG["services"]:
        tracked.add(svc["var_name"].split(".")[0])
        tracked.update(svc.get("coupled_vars") or [])
        if svc.get("checksum_var"):
            tracked.add(svc["checksum_var"])
    mirrors = {
        key
        for _file, key in (
            *literals.INVENTORY_MIRRORS.values(),
            *literals.SECONDARY_MIRRORS.values(),
        )
    }
    return tracked | mirrors | _PARTNER_PINS | {"dns_servers"}


def restatements(inventory: Path, defaults) -> list[tuple[str, str, str]]:
    """(file, variable, role) for every inventory value equal to a role default."""
    exempt = derived_exemptions()
    found = []
    for path in _inventory_files(inventory):
        site = yaml.safe_load(path.read_text()) or {}
        if not isinstance(site, dict):
            continue
        for key, value in site.items():
            if key in exempt:
                continue
            for role, default in defaults.get(key, []):
                if default == value:
                    found.append((str(path.relative_to(inventory)), key, role))
                    break
    return found


@pytest.fixture(scope="module")
def found() -> list[tuple[str, str, str]]:
    return restatements(INVENTORY, role_defaults())


def test_no_new_variable_restates_its_role_default(found):
    new = sorted((f, k) for f, k, _role in found if (f, k) not in ACKNOWLEDGED)
    assert not new, (
        f"inventory variables that only restate the role default: {new} — delete "
        "them; the role already supplies the value, and a pinned copy silently "
        "outlives the next default change."
    )


def test_acknowledged_entries_still_restate(found):
    """An entry whose value has diverged is a real override now, so leaving the
    exemption in would hide the next accidental copy of that same key."""
    live = {(f, k) for f, k, _role in found}
    stale = sorted(ACKNOWLEDGED - live)
    assert not stale, (
        f"these no longer match their role default: {stale} — drop them from "
        "ACKNOWLEDGED; the list is debt, not configuration."
    )


def test_the_collector_reports_a_restatement(tmp_path):
    """Mutation case: the comparison has to fire on a copied default."""
    inventory = tmp_path / "inv"
    (inventory / "group_vars").mkdir(parents=True)
    (inventory / "host_vars").mkdir(parents=True)
    (inventory / "group_vars" / "all.yml").write_text("demo_port: 8080\ndemo_mode: lax\n")
    defaults = {"demo_port": [("demo", 8080)], "demo_mode": [("demo", "strict")]}
    assert restatements(inventory, defaults) == [
        ("group_vars/all.yml", "demo_port", "demo")
    ]


@pytest.fixture(scope="module")
def prefixed() -> list[tuple[str, str]]:
    return role_prefixed_keys(INVENTORY, role_names())


def test_no_inventory_variable_is_unknown_to_the_pinned_collection(prefixed):
    """A `<role>_` variable the collection never reads is silently inert: the
    role's `| default(...)` guard takes the default and nothing fails."""
    known = collection_mentions({key for _file, key in prefixed})
    unknown = sorted(
        pair for pair in prefixed
        if pair[1] not in known and pair not in NOT_ROLE_VARS and pair not in PRESTAGED
    )
    assert not unknown, (
        f"inventory variables the pinned collection never reads: {unknown} — fix "
        "the spelling, or stage the variable in PRESTAGED until the bump that "
        "ships it lands in ansible/requirements.yml."
    )


def test_prestaged_entries_are_still_inert(prefixed):
    """An entry expires with the collection bump that ships its variable, and
    with the inventory line it stages."""
    known = collection_mentions({key for _file, key in PRESTAGED})
    landed = sorted(pair for pair in PRESTAGED if pair[1] in known)
    assert not landed, (
        f"the pinned collection now reads these: {landed} — drop them from "
        "PRESTAGED; the list is a staging window, not configuration."
    )
    gone = sorted(PRESTAGED - set(prefixed))
    assert not gone, (
        f"these PRESTAGED entries are no longer in the inventory: {gone} — drop them."
    )


def test_a_misspelled_role_variable_is_unknown(tmp_path):
    """Mutation case: a one-character typo must not read as a real role variable."""
    inventory = tmp_path / "inv"
    (inventory / "group_vars").mkdir(parents=True)
    (inventory / "host_vars").mkdir(parents=True)
    (inventory / "group_vars" / "all.yml").write_text(
        "qol_omz_commit: abc\nqol_omz_commmit: abc\nunprefixed_key: 1\n"
    )
    found = role_prefixed_keys(inventory, role_names())
    assert ("group_vars/all.yml", "qol_omz_commmit") in found
    assert ("group_vars/all.yml", "unprefixed_key") not in found
    known = collection_mentions({key for _file, key in found})
    assert "qol_omz_commit" in known
    assert "qol_omz_commmit" not in known
