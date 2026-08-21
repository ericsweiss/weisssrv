"""Coverage for check-cluster-literals.py.

The gate's whole value is that it FAILS on a regression, so each exemption and
each detection arm is exercised against a fixture repo rather than the live tree
(which is expected to be clean and therefore proves nothing about failure).
"""
from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "check_cluster_literals", REPO / "scripts" / "check-cluster-literals.py"
)
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


CLUSTER_CONFIG = textwrap.dedent(
    """\
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: cluster-config
    data:
      cluster_internal_domain: "example.lan"
      cluster_external_domain: "example.com"
      cluster_node_label_domain: "example.lan"
      cluster_lan_cidr: "192.168.0.0/24"
      cluster_home_cidr: "10.0.20.0/24"
      cluster_home_admin_cidr: "10.0.20.8/29"
      cluster_pod_cidr: "10.42.0.0/16"
      cluster_service_cidr: "10.43.0.0/16"
      cluster_tailnet_cidr: "100.64.0.0/10"
      cluster_metallb_public_vip: "192.168.0.100"
      cluster_metallb_internal_vip: "192.168.0.101"
      cluster_wg_easy_vip: "192.168.0.99"
      cluster_api_vip: "192.168.0.161"
      cluster_upstream_dns_servers: "192.168.0.150 192.168.0.160"
    """
)

DNS_YML = textwrap.dedent(
    """\
    adguard_home_rewrites:
      - domain: app.example.lan
        answer: "192.168.0.101"
      - domain: pub.example.lan
        answer: "192.168.0.100"
    """
)

# One Flux Kustomization per substituted tree the fixtures write into. The gate
# derives its scan set from these, so a test that adds a tree adds a stage here.
def stage(name: str, path: str) -> str:
    return textwrap.dedent(
        f"""\
        apiVersion: kustomize.toolkit.fluxcd.io/v1
        kind: Kustomization
        metadata:
          name: {name}
        spec:
          path: ./{path}
          postBuild:
            substituteFrom:
              - kind: ConfigMap
                name: cluster-config
        """
    )

ALL_YML = textwrap.dedent(
    """\
    internal_domain: example.lan
    external_domain: example.com
    dns_servers:
      - 192.168.0.150
      - 192.168.0.160
    """
)

K3S_YML = (
    'k3s_cluster_cidr: "10.42.0.0/16"\n'
    'k3s_service_cidr: "10.43.0.0/16"\n'
    'k3s_api_vip: 192.168.0.161\n'
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / gate.CLUSTER_CONFIG).parent.mkdir(parents=True)
    (tmp_path / gate.CLUSTER_CONFIG).write_text(CLUSTER_CONFIG)
    (tmp_path / gate.ANSIBLE_ALL).parent.mkdir(parents=True)
    (tmp_path / gate.ANSIBLE_ALL).write_text(ALL_YML)
    (tmp_path / gate.ANSIBLE_K3S).write_text(K3S_YML)
    (tmp_path / gate.ANSIBLE_DNS).write_text(DNS_YML)
    (tmp_path / "kubernetes/apps/demo").mkdir(parents=True)
    (tmp_path / "kubernetes/infrastructure/observability").mkdir(parents=True)
    for extra in gate.EXTRA_TREES:
        (tmp_path / extra).mkdir(parents=True, exist_ok=True)
    write(tmp_path, f"{gate.CLUSTER_DIR}/apps.yaml", stage("apps", "kubernetes/apps"))
    write(
        tmp_path,
        f"{gate.CLUSTER_DIR}/infrastructure-observability.yaml",
        stage("infrastructure-observability", "kubernetes/infrastructure/observability"),
    )
    return tmp_path


def write(repo: Path, rel: str, body: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))


def run(repo: Path) -> list[str]:
    config = gate.load_config(repo)
    trees = gate.substituted_trees(repo)
    return gate.check_literals(repo, config, trees) + gate.check_inventory(repo, config)


def test_clean_tree_passes(repo: Path) -> None:
    write(repo, "kubernetes/apps/demo/route.yaml", """\
        apiVersion: traefik.io/v1alpha1
        kind: IngressRoute
        spec:
          routes:
            - match: Host(`app.${cluster_internal_domain}`)
        """)
    assert run(repo) == []


def test_domain_literal_is_reported(repo: Path) -> None:
    write(repo, "kubernetes/apps/demo/route.yaml", """\
        apiVersion: traefik.io/v1alpha1
        kind: IngressRoute
        spec:
          routes:
            - match: Host(`app.example.lan`)
        """)
    assert any("example.lan" in v for v in run(repo))


def test_yaml_comment_is_not_content(repo: Path) -> None:
    write(repo, "kubernetes/apps/demo/route.yaml", """\
        # app.example.lan is the internal name.
        apiVersion: v1
        kind: ConfigMap
        data: {}
        """)
    assert run(repo) == []


def test_comment_inside_a_block_scalar_is_not_content(repo: Path) -> None:
    write(repo, "kubernetes/apps/demo/cm.yaml", """\
        apiVersion: v1
        kind: ConfigMap
        data:
          Corefile: |
            # Split-DNS scopes queries to example.lan.
            . {
                forward . ${cluster_upstream_dns_servers}
            }
        """)
    assert run(repo) == []


def test_escaped_regex_spelling_is_exempt(repo: Path) -> None:
    write(repo, "kubernetes/apps/demo/runner.yaml", """\
        apiVersion: v1
        kind: ConfigMap
        data:
          config: |
            node_selector_overwrite_allowed = "^example\\\\.lan/cpu=(modern|legacy)$"
        """)
    assert run(repo) == []


def test_address_literal_is_reported(repo: Path) -> None:
    write(repo, "kubernetes/apps/demo/svc.yaml", """\
        apiVersion: v1
        kind: Service
        metadata:
          annotations:
            metallb.io/loadBalancerIPs: 192.168.0.101
        """)
    assert any("192.168.0.101" in v for v in run(repo))


def test_address_literal_is_exempt_inside_a_networkpolicy(repo: Path) -> None:
    write(repo, "kubernetes/apps/demo/networkpolicy.yaml", """\
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        spec:
          egress:
            - to:
                - ipBlock: {cidr: 192.168.0.101/32}
        """)
    assert run(repo) == []


def test_address_literal_is_exempt_in_the_rules_tree(repo: Path) -> None:
    write(repo, f"{gate.RULES_TREE}/infrastructure.yaml", """\
        apiVersion: monitoring.coreos.com/v1
        kind: PrometheusRule
        spec:
          groups:
            - name: vip
              rules:
                - expr: absent(x{ip="192.168.0.101"})
        """)
    assert run(repo) == []


def test_unadopted_address_is_not_reported(repo: Path) -> None:
    """Per-guest addresses are inventory data and deliberately stay literal."""
    write(repo, "kubernetes/apps/demo/endpointslice.yaml", """\
        apiVersion: discovery.k8s.io/v1
        kind: EndpointSlice
        endpoints:
          - addresses: ["192.168.0.157"]
        """)
    assert run(repo) == []


def test_inventory_drift_is_reported(repo: Path) -> None:
    (repo / gate.ANSIBLE_ALL).write_text(ALL_YML.replace("example.lan", "moved.lan"))
    assert any("cluster_internal_domain" in v for v in run(repo))


def test_dns_server_drift_is_reported(repo: Path) -> None:
    (repo / gate.ANSIBLE_ALL).write_text(ALL_YML.replace("192.168.0.160", "192.168.0.161"))
    assert any("cluster_upstream_dns_servers" in v for v in run(repo))


def test_metallb_vip_drift_is_reported(repo: Path) -> None:
    """The VIPs mirror as AdGuard rewrite answers, not as a named inventory key."""
    (repo / gate.ANSIBLE_DNS).write_text(DNS_YML.replace("192.168.0.101", "192.168.0.109"))
    assert any("cluster_metallb_internal_vip" in v for v in run(repo))


def test_a_substituted_tree_is_derived_not_hand_listed(repo: Path) -> None:
    """A new stage brings its tree into the scan without editing the gate."""
    write(
        repo,
        f"{gate.CLUSTER_DIR}/infrastructure-metrics-server.yaml",
        stage("infrastructure-metrics-server", "kubernetes/infrastructure/metrics-server"),
    )
    write(repo, "kubernetes/infrastructure/metrics-server/release.yaml", """\
        apiVersion: v1
        kind: ConfigMap
        data:
          host: metrics.example.lan
        """)
    assert "kubernetes/infrastructure/metrics-server" in gate.substituted_trees(repo)
    assert any("metrics-server/release.yaml" in v for v in run(repo))


def test_a_nested_stage_path_does_not_double_the_scan(repo: Path) -> None:
    write(
        repo,
        f"{gate.CLUSTER_DIR}/infrastructure-nested.yaml",
        stage("infrastructure-nested", "kubernetes/apps/demo"),
    )
    trees = gate.substituted_trees(repo)
    assert "kubernetes/apps" in trees and "kubernetes/apps/demo" not in trees


def test_a_stage_path_absent_from_disk_is_vacuous(repo: Path) -> None:
    write(
        repo,
        f"{gate.CLUSTER_DIR}/infrastructure-gone.yaml",
        stage("infrastructure-gone", "kubernetes/infrastructure/gone"),
    )
    with pytest.raises(gate.Vacuous):
        gate.substituted_trees(repo)


def test_no_substituting_stage_at_all_is_vacuous(repo: Path) -> None:
    for path in (repo / gate.CLUSTER_DIR).glob("*.yaml"):
        path.unlink()
    with pytest.raises(gate.Vacuous):
        gate.substituted_trees(repo)


def test_the_fixture_config_carries_every_key_the_gate_checks(repo: Path) -> None:
    """Otherwise the fixture would exercise a smaller gate than production runs."""
    gate.require_keys(gate.load_config(repo))


def test_a_missing_cluster_config_is_vacuous_not_a_traceback(repo: Path) -> None:
    (repo / gate.CLUSTER_CONFIG).unlink()
    with pytest.raises(gate.Vacuous):
        gate.load_config(repo)


def test_an_empty_cluster_config_data_block_is_vacuous(repo: Path) -> None:
    (repo / gate.CLUSTER_CONFIG).write_text("data: {}\n")
    with pytest.raises(gate.Vacuous):
        gate.load_config(repo)


@pytest.mark.parametrize(
    "key",
    ["cluster_internal_domain", "cluster_metallb_public_vip", gate.DNS_SERVERS_KEY],
)
def test_a_disappeared_key_is_vacuous_not_a_quiet_pass(repo: Path, key: str) -> None:
    """Each arm skips an absent key, so the whole gate would shrink in silence."""
    config = gate.load_config(repo)
    del config[key]
    # The arms themselves still find nothing — that is precisely the hazard.
    assert gate.check_inventory(repo, config) == []
    with pytest.raises(gate.Vacuous) as excinfo:
        gate.require_keys(config)
    assert key in str(excinfo.value)


def test_the_success_count_reflects_the_checks_that_ran(repo: Path) -> None:
    config = gate.load_config(repo)
    full = gate.mirror_check_count(config)
    assert full == len(gate.INVENTORY_MIRRORS) + len(gate.VIP_MIRROR_KEYS) + 1
    del config["cluster_metallb_public_vip"]
    assert gate.mirror_check_count(config) == full - 1


def test_a_missing_dns_servers_mirror_is_a_violation_not_a_skip(repo: Path) -> None:
    (repo / gate.ANSIBLE_ALL).write_text(
        "internal_domain: example.lan\nexternal_domain: example.com\n"
    )
    assert any("dns_servers not found" in v for v in run(repo))


def test_dashboard_json_literal_is_reported(repo: Path) -> None:
    """A *.yaml-only walk missed the generator sources rendered into ConfigMaps."""
    write(repo, "kubernetes/infrastructure/observability/dashboards/x.json", """\
        {"panels": [{"expr": "probe_success{instance=\\"https://git.example.lan\\"}"}]}
        """)
    assert any("x.json" in v for v in run(repo))


def test_generator_source_comment_is_not_content(repo: Path) -> None:
    write(repo, "kubernetes/infrastructure/observability/gen.py", """\
        # The zone is example.com in production.
        ZONE = "${cluster_external_domain}"
        """)
    assert run(repo) == []


def test_markdown_beside_a_manifest_is_not_scanned(repo: Path) -> None:
    write(repo, "kubernetes/apps/demo/README.md", "Reachable at app.example.lan.\n")
    assert run(repo) == []
