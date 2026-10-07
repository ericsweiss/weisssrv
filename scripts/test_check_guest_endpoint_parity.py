"""Coverage for check-guest-endpoint-parity.py.

The live tree agrees, so it proves nothing about failure: each arm runs against
a fixture repo, and every drift case must FAIL.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
gate = load_script("check-guest-endpoint-parity.py")


HOSTS = textwrap.dedent(
    """\
    all:
      children:
        guests:
          hosts:
            plex:
              ansible_host: 10.0.10.152
            gitlab:
              ansible_host: 10.0.10.153
    """
)

CONFIG = textwrap.dedent(
    """\
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: cluster-config
    data:
      cluster_lan_cidr: "10.0.10.0/24"
      cluster_lan_gateway: "10.0.10.1"
    """
)

SLICE = textwrap.dedent(
    """\
    apiVersion: discovery.k8s.io/v1
    kind: EndpointSlice
    metadata:
      name: plex
    endpoints:
      - addresses:
          - 10.0.10.152
    ---
    apiVersion: discovery.k8s.io/v1
    kind: EndpointSlice
    metadata:
      name: router
    endpoints:
      - addresses:
          - 10.0.10.1
    """
)


def write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path, gate.HOSTS_YML, HOSTS)
    write(tmp_path, gate.CLUSTER_CONFIG, CONFIG)
    write(tmp_path, "kubernetes/apps/vm-ingress/services.yaml", SLICE)
    return tmp_path


def test_addresses_that_match_the_inventory_pass(repo: Path) -> None:
    problems, checked = gate.check(repo)
    assert problems == []
    assert checked == 1
    assert gate.main(["--repo-root", str(repo)]) == 0


def test_a_renumbered_guest_fails(repo: Path) -> None:
    write(
        repo,
        "kubernetes/apps/vm-ingress/services.yaml",
        SLICE.replace("10.0.10.152", "10.0.10.159"),
    )
    problems, _ = gate.check(repo)
    assert any("10.0.10.159" in p for p in problems)
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_the_gateway_needs_no_inventory_entry(repo: Path) -> None:
    problems, _ = gate.check(repo)
    assert not any("10.0.10.1" in p for p in problems)


def test_an_off_lan_address_is_out_of_scope(repo: Path) -> None:
    write(
        repo,
        "kubernetes/apps/vm-ingress/services.yaml",
        SLICE.replace("10.0.10.152", "192.168.1.9"),
    )
    with pytest.raises(gate.Vacuous):
        gate.check(repo)


def test_the_legacy_endpoints_kind_is_read_too(repo: Path) -> None:
    write(
        repo,
        "kubernetes/apps/legacy/endpoints.yaml",
        textwrap.dedent(
            """\
            apiVersion: v1
            kind: Endpoints
            metadata:
              name: legacy
            subsets:
              - addresses:
                  - ip: 10.0.10.199
            """
        ),
    )
    problems, _ = gate.check(repo)
    assert any("10.0.10.199" in p for p in problems)


def test_an_unparseable_manifest_is_reported_not_skipped(repo: Path) -> None:
    """A file this gate cannot read is a hole in its subject, not a pass."""
    write(repo, "kubernetes/apps/broken/endpoints.yaml", "a: [1,\n  b: {\n")
    problems, _ = gate.check(repo)
    assert any("kubernetes/apps/broken/endpoints.yaml" in p for p in problems)
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_no_endpoints_at_all_is_vacuous(repo: Path) -> None:
    (repo / "kubernetes/apps/vm-ingress/services.yaml").unlink()
    with pytest.raises(gate.Vacuous):
        gate.check(repo)
    assert gate.main(["--repo-root", str(repo)]) == 2


def test_a_missing_lan_cidr_is_vacuous(repo: Path) -> None:
    write(repo, gate.CLUSTER_CONFIG, CONFIG.replace('  cluster_lan_cidr: "10.0.10.0/24"\n', ""))
    with pytest.raises(gate.Vacuous):
        gate.check(repo)


def test_a_lan_cidr_with_host_bits_set_is_vacuous(repo: Path) -> None:
    """The strict parse rejects it, so the gate must name the value and exit 2
    rather than traceback."""
    write(
        repo,
        gate.CLUSTER_CONFIG,
        CONFIG.replace('cluster_lan_cidr: "10.0.10.0/24"', 'cluster_lan_cidr: "192.0.2.5/24"'),
    )
    with pytest.raises(gate.Vacuous) as raised:
        gate.check(repo)
    assert "192.0.2.5/24" in str(raised.value)
    assert gate.main(["--repo-root", str(repo)]) == 2


def test_a_cluster_with_no_gateway_key_loses_only_that_allowance(repo: Path) -> None:
    """The gateway is an allowance, not a requirement: without it the gate is
    stricter, never vacuous."""
    write(repo, gate.CLUSTER_CONFIG, CONFIG.replace('  cluster_lan_gateway: "10.0.10.1"\n', ""))
    problems, _ = gate.check(repo)
    assert any("10.0.10.1" in problem for problem in problems)


def test_the_live_tree_is_clean() -> None:
    problems, checked = gate.check(REPO)
    assert problems == []
    assert checked, "the live tree compared no LAN address"


def test_a_cluster_config_placeholder_resolves_before_the_ip_parse():
    """Identity values are spelled as placeholders, so the gate must substitute."""
    config = {"cluster_lan_gateway": "10.0.10.1"}
    assert gate.substitute("${cluster_lan_gateway}", config) == "10.0.10.1"


def test_an_unknown_placeholder_is_left_alone_so_a_typo_still_fails():
    assert gate.substitute("${cluster_lan_gatway}", {"cluster_lan_gateway": "10.0.10.1"}) == (
        "${cluster_lan_gatway}"
    )


def test_a_whole_list_roster_placeholder_is_resolved(repo: Path) -> None:
    """A slice may spell its endpoints as one `${cluster_*}` key holding a JSON
    list; the roster behind it is what drifts from the inventory."""
    write(
        repo,
        "kubernetes/apps/vm-ingress/roster.yaml",
        "apiVersion: discovery.k8s.io/v1\n"
        "kind: EndpointSlice\n"
        "metadata:\n"
        "  name: roster\n"
        "addressType: IPv4\n"
        "endpoints: ${cluster_roster_addresses}\n",
    )
    write(
        repo,
        gate.CLUSTER_CONFIG,
        CONFIG + '  cluster_roster_addresses: \'[{"addresses": ["10.0.10.240"]}]\'\n',
    )
    problems, _ = gate.check(repo)
    assert any("10.0.10.240" in problem for problem in problems)


def test_an_unresolvable_roster_placeholder_is_reported_not_skipped(repo: Path) -> None:
    """A slice whose endpoints resolve to no list drops out of the gate's subject,
    which must be a failure rather than a quiet pass."""
    write(
        repo,
        "kubernetes/apps/vm-ingress/roster.yaml",
        "apiVersion: discovery.k8s.io/v1\n"
        "kind: EndpointSlice\n"
        "metadata:\n"
        "  name: roster\n"
        "addressType: IPv4\n"
        "endpoints: ${cluster_nope}\n",
    )
    problems, _ = gate.check(repo)
    assert any("kubernetes/apps/vm-ingress/roster.yaml" in p for p in problems), problems
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_the_ok_line_reports_the_number_actually_compared(repo: Path, capsys) -> None:
    """The count is the only operator-visible sign the gate still has a subject,
    so it must track the addresses compared, not a constant."""
    assert gate.main(["--repo-root", str(repo)]) == 0
    assert "(1 LAN address(es) checked)" in capsys.readouterr().out
    write(
        repo,
        "kubernetes/apps/vm-ingress/extra.yaml",
        SLICE.split("---")[0].replace("name: plex", "name: gitlab").replace(
            "10.0.10.152", "10.0.10.153"
        ),
    )
    assert gate.main(["--repo-root", str(repo)]) == 0
    assert "(2 LAN address(es) checked)" in capsys.readouterr().out


MGMT_SLICE = textwrap.dedent(
    """\
    apiVersion: discovery.k8s.io/v1
    kind: EndpointSlice
    metadata:
      name: idrac
    endpoints:
      - addresses:
          - 10.0.20.9
    """
)


def test_a_second_lan_cidr_key_brings_its_range_in_scope(repo: Path) -> None:
    """A cluster with a management VLAN declares a key per range; an address in
    the second one is compared instead of silently out of scope."""
    write(repo, gate.CLUSTER_CONFIG, CONFIG + '  cluster_mgmt_cidr: "10.0.20.0/24"\n')
    write(repo, "kubernetes/apps/vm-ingress/idrac.yaml", MGMT_SLICE)

    problems, checked = gate.check(repo)
    assert problems == []
    assert checked == 1

    keys = (gate.DEFAULT_LAN_CIDR_KEY, "cluster_mgmt_cidr")
    problems, checked = gate.check(repo, keys)
    assert any("10.0.20.9" in problem for problem in problems), problems
    assert checked == 2
    assert gate.main(
        ["--repo-root", str(repo), "--lan-cidr-key", gate.DEFAULT_LAN_CIDR_KEY,
         "--lan-cidr-key", "cluster_mgmt_cidr"]
    ) == 1


def test_an_extra_lan_cidr_covers_a_range_the_config_does_not_name(repo: Path) -> None:
    write(repo, "kubernetes/apps/vm-ingress/idrac.yaml", MGMT_SLICE)
    problems, _ = gate.check(repo, extra_lan_cidrs=("10.0.20.0/24",))
    assert any("10.0.20.9" in problem for problem in problems), problems
    assert gate.main(
        ["--repo-root", str(repo), "--extra-lan-cidr", "10.0.20.0/24"]
    ) == 1


EXPORT_HOSTS = textwrap.dedent(
    """\
    all:
      children:
        k3s_servers:
          hosts:
            k3s-srv-a:
              ansible_host: 10.0.10.222
            k3s-srv-b:
              ansible_host: 10.0.10.227
        k3s_agents:
          hosts:
            k3s-agt-a:
              ansible_host: 10.0.10.202
    """
)


def exports(*specs: str) -> str:
    clients = "".join(f'      - spec: "{spec}"\n        options: "rw"\n' for spec in specs)
    return "nas_storage_exports:\n  - path: /export/appdata\n    clients:\n" + clients


@pytest.fixture
def nas(repo: Path) -> Path:
    """The endpoint fixture plus a k3s inventory and one NFS export that agrees."""
    write(repo, gate.HOSTS_YML, EXPORT_HOSTS)
    write(repo, "kubernetes/apps/vm-ingress/services.yaml", SLICE.split("---")[1])
    write(
        repo,
        f"{gate.INVENTORY}/host_vars/nas.yml",
        exports("10.0.10.200/29", "10.0.10.220/29", "10.0.10.227/32"),
    )
    return repo


def test_an_export_covering_every_k3s_node_passes(nas: Path) -> None:
    problems, checked = gate.check(nas)
    assert problems == []
    assert checked == 3, "the three client specs did not reach the comparison"
    assert gate.main(["--repo-root", str(nas)]) == 0


def test_an_export_that_admits_part_of_a_k3s_group_fails(nas: Path) -> None:
    """A per-host client list frozen before a node joined: that node alone
    mounts nothing, which no pod-level symptom names."""
    write(
        nas,
        f"{gate.INVENTORY}/host_vars/nas.yml",
        exports("10.0.10.200/29", "10.0.10.220/29"),
    )
    problems, _ = gate.check(nas)
    assert any("k3s_servers" in p and "10.0.10.227" in p for p in problems), problems
    assert gate.main(["--repo-root", str(nas)]) == 1


def test_an_export_spec_covering_no_inventory_host_fails(nas: Path) -> None:
    write(
        nas,
        f"{gate.INVENTORY}/host_vars/nas.yml",
        exports("10.0.10.200/29", "10.0.10.220/29", "10.0.10.227/32", "10.0.10.240/32"),
    )
    problems, _ = gate.check(nas)
    assert any("10.0.10.240/32" in p for p in problems), problems
    assert gate.main(["--repo-root", str(nas)]) == 1


def test_an_off_lan_export_spec_is_out_of_scope(nas: Path) -> None:
    """A VPN or remote range the gate was not pointed at must not be reported."""
    write(
        nas,
        f"{gate.INVENTORY}/host_vars/nas.yml",
        exports("10.0.10.200/29", "10.0.10.220/29", "10.0.10.227/32", "192.168.9.0/24"),
    )
    problems, _ = gate.check(nas)
    assert problems == []


def test_a_hostname_export_spec_is_left_alone(nas: Path) -> None:
    """A hostname or wildcard client is legal and resolves to no address here."""
    write(
        nas,
        f"{gate.INVENTORY}/host_vars/nas.yml",
        exports("10.0.10.200/29", "10.0.10.220/29", "10.0.10.227/32", "*.esweiss.com"),
    )
    problems, _ = gate.check(nas)
    assert problems == []


def test_an_unparseable_inventory_var_file_is_reported_not_skipped(nas: Path) -> None:
    write(nas, f"{gate.INVENTORY}/group_vars/broken.yml", "a: [1,\n  b: {\n")
    problems, _ = gate.check(nas)
    assert any("group_vars/broken.yml" in p for p in problems), problems
    assert gate.main(["--repo-root", str(nas)]) == 1


def test_the_live_tree_declares_nfs_exports() -> None:
    """The export arm is only a gate while it has a subject to read."""
    found, skipped = gate.nfs_exports(REPO)
    assert skipped == []
    assert found, f"no {gate.EXPORTS_KEY} found under {gate.INVENTORY}/"


def test_a_cidr_key_naming_nothing_is_vacuous(repo: Path) -> None:
    """Every declared key empty leaves no LAN to scope to, which is exit 2."""
    with pytest.raises(gate.Vacuous):
        gate.check(repo, ("cluster_nope",))
    assert gate.main(["--repo-root", str(repo), "--lan-cidr-key", "cluster_nope"]) == 2
