"""Tests for scripts/generate-hosts-env.py.

Run via `task scripts:test` (pytest).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

_SPEC = importlib.util.spec_from_file_location(
    "gen_hosts_env",
    Path(__file__).parent / "generate-hosts-env.py",
)
gen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen)  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parent.parent
HOSTS_YML = REPO / "ansible" / "inventories" / "prod" / "hosts.yml"
HOSTS_ENV = REPO / "scripts" / "hosts.env"


def _minimal_inventory() -> dict:
    def host(ip):
        return {"ansible_host": ip}

    return {
        "all": {
            "children": {
                "proxmox": {"hosts": {"pve-a": host("10.0.0.2"), "pve-b": host("10.0.0.3")}},
                "dns": {"hosts": {"dns-01": host("10.0.0.150"), "dns-02": host("10.0.0.160")}},
                "mail": {"hosts": {"smtp": host("10.0.0.151")}},
                "plex_servers": {"hosts": {"plex": host("10.0.0.152")}},
                "gitlab_servers": {"hosts": {"gitlab": host("10.0.0.153")}},
                "nextcloud_servers": {"hosts": {"nextcloud": host("10.0.0.156")}},
                "services": {"hosts": {"home": host("10.0.0.154"), "windows": host("10.0.0.155")}},
                "k3s_servers": {"hosts": {"s1": host("10.0.0.222"), "s2": host("10.0.0.223")}},
                "k3s_agents": {"hosts": {"a1": host("10.0.0.202")}},
            }
        }
    }


class TestBuild:
    def test_pve_hosts_are_names_not_ips(self):
        pairs = dict(gen.build(_minimal_inventory()))
        assert pairs["PVE_HOSTS"] == "pve-a pve-b"
        assert pairs["PVE_IPS"] == "10.0.0.2 10.0.0.3"

    def test_k3s_split_server_agent(self):
        pairs = dict(gen.build(_minimal_inventory()))
        assert pairs["K3S_SERVERS"] == "10.0.0.222 10.0.0.223"
        assert pairs["K3S_AGENTS"] == "10.0.0.202"

    def test_all_ssh_excludes_windows(self):
        pairs = dict(gen.build(_minimal_inventory()))
        assert "10.0.0.155" not in pairs["ALL_SSH_IPS"].split()
        # but includes the home HAOS guest and every k3s VM
        assert "10.0.0.154" in pairs["ALL_SSH_IPS"].split()
        assert "10.0.0.222" in pairs["ALL_SSH_IPS"].split()

    def test_app_vms_in_all_ssh(self):
        pairs = dict(gen.build(_minimal_inventory()))
        # the SSH-managed app VMs each get a per-app key and land in ALL_SSH_IPS
        assert pairs["GITLAB_IP"] == "10.0.0.153"
        assert pairs["NEXTCLOUD_IP"] == "10.0.0.156"
        assert "10.0.0.153" in pairs["ALL_SSH_IPS"].split()
        assert "10.0.0.156" in pairs["ALL_SSH_IPS"].split()

    def test_missing_ansible_host_raises(self):
        inv = _minimal_inventory()
        inv["all"]["children"]["proxmox"]["hosts"]["pve-a"] = {}
        with pytest.raises(ValueError):
            gen.build(inv)

    def test_render_is_shell_sourceable(self):
        pairs = gen.build(_minimal_inventory())
        rendered = gen.render(pairs)
        # Every non-comment line is KEY="value"
        for line in rendered.splitlines():
            if line and not line.startswith("#"):
                assert '="' in line and line.endswith('"')


class TestInSyncWithCommittedFile:
    """Mirror of the hosts-env-sync CI drift guard: the committed hosts.env must
    equal what the generator produces from the live hosts.yml."""

    def test_committed_hosts_env_matches_generator(self):
        with HOSTS_YML.open() as f:
            data = yaml.safe_load(f)
        expected = gen.render(gen.build(data))
        assert HOSTS_ENV.read_text() == expected, (
            "scripts/hosts.env is out of sync with hosts.yml — run `task hosts:sync`"
        )
