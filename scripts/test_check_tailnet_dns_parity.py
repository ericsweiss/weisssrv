"""Tests that check-tailnet-dns-parity.py fails on drift in either direction and never passes vacuously."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-tailnet-dns-parity.py"

CLUSTER_CONFIG = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-config
data:
  cluster_metallb_internal_vip: "10.0.10.101"
"""

REWRITES = """\
adguard_home_rewrites:
  - domain: "traefik.{{ internal_domain }}"
    answer: "10.0.10.101"
  - domain: "gitlab.{{ internal_domain }}"
    answer: "10.0.10.153"
  - domain: "immich-ml.{{ internal_domain }}"
    answer: "10.0.10.158"
"""

HOSTS = """\
all:
  children:
    core:
      hosts:
        pve-nas-01:
          ansible_host: 10.0.10.102
    guests:
      hosts:
        gitlab:
          ansible_host: 10.0.10.153
"""

COREFILE_HEAD = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: tailnet-dns-corefile
data:
  Corefile: |
"""


def _corefile(names):
    zone = " ".join(f"{n}.${{cluster_internal_domain}}" for n in names)
    return COREFILE_HEAD + textwrap.indent(
        f"# Override zone.\n{zone} {{\n    forward . 10.0.10.150\n}}\n\n"
        "${cluster_internal_domain} {\n    forward . 10.0.10.150\n}\n",
        " " * 4,
    )


def _write(root: Path, rewrites: str, names, hosts: str = HOSTS) -> Path:
    (root / "ansible/inventories/prod/group_vars").mkdir(parents=True)
    (root / "kubernetes/apps/tailnet-dns").mkdir(parents=True)
    (root / "kubernetes/infrastructure/sources").mkdir(parents=True)
    (root / "ansible/inventories/prod/hosts.yml").write_text(hosts)
    (root / "ansible/inventories/prod/group_vars/dns.yml").write_text(rewrites)
    (root / "kubernetes/apps/tailnet-dns/configmap.yaml").write_text(_corefile(names))
    (root / "kubernetes/infrastructure/sources/cluster-config.yaml").write_text(CLUSTER_CONFIG)
    return root


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(root)],
        capture_output=True, text=True,
    )


def test_in_sync_passes(tmp_path):
    result = _run(_write(tmp_path, REWRITES, ["gitlab"]))
    assert result.returncode == 0, result.stderr


def test_new_direct_ip_rewrite_missing_from_the_zone_fails(tmp_path):
    rewrites = REWRITES + '  - domain: "windows.{{ internal_domain }}"\n    answer: "10.0.10.155"\n'
    result = _run(_write(tmp_path, rewrites, ["gitlab"]))
    assert result.returncode == 1
    assert "windows" in result.stdout


def test_zone_name_without_a_rewrite_fails(tmp_path):
    result = _run(_write(tmp_path, REWRITES, ["gitlab", "windows"]))
    assert result.returncode == 1
    assert "windows" in result.stdout


def test_exempt_name_in_the_zone_is_reported(tmp_path):
    result = _run(_write(tmp_path, REWRITES, ["gitlab", "immich-ml"]))
    assert result.returncode == 1
    assert "EXEMPT" in result.stdout


def test_vip_only_rewrites_exit_two(tmp_path):
    rewrites = 'adguard_home_rewrites:\n  - domain: "traefik.{{ internal_domain }}"\n    answer: "10.0.10.101"\n'
    result = _run(_write(tmp_path, rewrites, ["gitlab"]))
    assert result.returncode == 2


def test_missing_zone_exits_two(tmp_path):
    root = _write(tmp_path, REWRITES, ["gitlab"])
    (root / "kubernetes/apps/tailnet-dns/configmap.yaml").write_text(
        COREFILE_HEAD + "    ${cluster_internal_domain} {\n        forward . 10.0.10.150\n    }\n"
    )
    result = _run(root)
    assert result.returncode == 2


def test_missing_cluster_config_exits_two(tmp_path):
    root = _write(tmp_path, REWRITES, ["gitlab"])
    (root / "kubernetes/infrastructure/sources/cluster-config.yaml").unlink()
    result = _run(root)
    assert result.returncode == 2


def test_real_repo_is_in_sync():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_inventory_host_in_the_zone_is_not_drift(tmp_path):
    """A host the DNS role rewrites from the inventory needs no dns.yml entry."""
    result = _run(_write(tmp_path, REWRITES, ["gitlab", "pve-nas-01"]))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_zone_name_that_is_no_longer_an_inventory_host_fails(tmp_path):
    """Mutation case: without the inventory arm a decommissioned host stays green."""
    hosts = HOSTS.replace("        pve-nas-01:", "        pve-nas-09:")
    result = _run(_write(tmp_path, REWRITES, ["gitlab", "pve-nas-01"], hosts=hosts))
    assert result.returncode == 1
    assert "neither an inventory host nor a rewrite" in result.stdout


def test_an_inventory_with_no_addresses_exits_two(tmp_path):
    result = _run(_write(tmp_path, REWRITES, ["gitlab"], hosts="all:\n  children: {}\n"))
    assert result.returncode == 2


def test_a_variable_spelled_vip_answer_is_not_a_direct_ip(tmp_path):
    """Mutation case: comparing the raw string counts `{{ metallb_internal_vip }}`
    as a direct answer and demands an override-zone entry for it."""
    rewrites = REWRITES + (
        '  - domain: "homarr.{{ internal_domain }}"\n'
        '    answer: "{{ metallb_internal_vip }}"\n'
    )
    result = _run(_write(tmp_path, rewrites, ["gitlab"]))
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_undeclared_extra_zone_name_fails_even_beside_strict_routes(tmp_path):
    """A name that is neither a direct-IP rewrite nor a STRICT_ROUTES entry fails.

    The subtraction is set-based, so a strict-route name in the same zone must
    not mask an undeclared neighbour beside it.
    """
    result = _run(_write(tmp_path, REWRITES, ["gitlab", "traefik", "windows"]))
    assert result.returncode == 1
    assert "windows" in result.stdout
    assert "traefik" not in result.stdout
