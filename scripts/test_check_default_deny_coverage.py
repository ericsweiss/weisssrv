"""Coverage for check-default-deny-coverage.py.

The gate exists to FAIL on the third unfenced namespace, so every arm is proved
against a fixture corpus — the live tree is expected to pass and therefore
proves nothing about failure.
"""
from __future__ import annotations

import importlib.util
import io
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "check_default_deny_coverage", REPO / "scripts" / "check-default-deny-coverage.py"
)
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


FENCED = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: {ns}
spec:
  podSelector: {{}}
  policyTypes: [Ingress]
"""

DEPLOY = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
  namespace: {ns}
"""


def run(monkeypatch, corpus: str, argv: list[str] | None = None) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(textwrap.dedent(corpus)))
    return gate.main(argv or [])


def test_fenced_namespace_passes(monkeypatch, capsys) -> None:
    corpus = DEPLOY.format(ns="apps") + "---\n" + FENCED.format(ns="apps")
    assert run(monkeypatch, corpus) == 0
    assert "Ingress default-deny OK" in capsys.readouterr().out


def test_unfenced_namespace_fails(monkeypatch, capsys) -> None:
    assert run(monkeypatch, DEPLOY.format(ns="apps")) == 1
    assert "apps: owns workloads" in capsys.readouterr().err


def test_a_helmrelease_puts_its_target_namespace_in_scope(monkeypatch) -> None:
    """A chart's workloads never appear in a kustomize corpus."""
    corpus = """\
        apiVersion: helm.toolkit.fluxcd.io/v2
        kind: HelmRelease
        metadata:
          name: thing
          namespace: flux-system
        spec:
          targetNamespace: charted
        """
    assert run(monkeypatch, corpus) == 1


def test_an_app_scoped_policy_does_not_fence_the_namespace(monkeypatch, capsys) -> None:
    corpus = DEPLOY.format(ns="apps") + textwrap.dedent(
        """\
        ---
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-one-app
          namespace: apps
        spec:
          podSelector:
            matchLabels: {app: one}
          policyTypes: [Ingress]
        """
    )
    assert run(monkeypatch, corpus) == 1
    assert "apps: owns workloads" in capsys.readouterr().err


def test_a_namespace_wide_allow_all_policy_does_not_fence(monkeypatch, capsys) -> None:
    """The false fence: it satisfied "has an Ingress policyType" while fencing nothing."""
    corpus = DEPLOY.format(ns="apps") + textwrap.dedent(
        """\
        ---
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-all-ingress
          namespace: apps
        spec:
          podSelector: {}
          policyTypes: [Ingress]
          ingress: [{}]
        """
    )
    assert run(monkeypatch, corpus) == 1
    assert "apps: owns workloads" in capsys.readouterr().err


def test_an_allow_all_policy_defeats_a_sibling_default_deny(monkeypatch) -> None:
    """NetworkPolicies are additive — the open one wins, so the namespace is unfenced."""
    corpus = (
        DEPLOY.format(ns="apps")
        + "---\n"
        + FENCED.format(ns="apps")
        + textwrap.dedent(
            """\
            ---
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-all-ingress
              namespace: apps
            spec:
              podSelector: {}
              policyTypes: [Ingress]
              ingress: [{}]
            """
        )
    )
    assert run(monkeypatch, corpus) == 1


def test_a_namespace_wide_policy_with_real_rules_still_fences(monkeypatch) -> None:
    """Only a rule with neither `from` nor `ports` is wide open; a narrowed one counts."""
    corpus = DEPLOY.format(ns="apps") + textwrap.dedent(
        """\
        ---
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-scrape
          namespace: apps
        spec:
          podSelector: {}
          policyTypes: [Ingress]
          ingress:
            - from:
                - namespaceSelector:
                    matchLabels: {kubernetes.io/metadata.name: observability}
        """
    )
    assert run(monkeypatch, corpus) == 0


def test_an_egress_only_policy_does_not_fence_the_namespace(monkeypatch) -> None:
    corpus = DEPLOY.format(ns="apps") + textwrap.dedent(
        """\
        ---
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-egress-dns
          namespace: apps
        spec:
          podSelector: {}
          policyTypes: [Egress]
          egress: [{}]
        """
    )
    assert run(monkeypatch, corpus) == 1


def test_the_declared_exemptions_are_honoured(monkeypatch) -> None:
    assert run(monkeypatch, DEPLOY.format(ns="flux-system")) == 0


def test_kube_system_is_no_longer_exempt(monkeypatch) -> None:
    """It carries a real default-deny now (configs/kube-system-policies/), so an
    unfenced kube-system in the corpus is a violation like any other."""
    assert "kube-system" not in gate.EXEMPT_NAMESPACES
    assert run(monkeypatch, DEPLOY.format(ns="kube-system")) == 1
    corpus = DEPLOY.format(ns="kube-system") + "---\n" + FENCED.format(ns="kube-system")
    assert run(monkeypatch, corpus) == 0


def test_a_cli_exemption_needs_a_reason(monkeypatch) -> None:
    assert run(monkeypatch, DEPLOY.format(ns="apps"), ["--exempt", "apps"]) == 2


def test_a_cli_exemption_with_a_reason_is_honoured(monkeypatch) -> None:
    assert run(monkeypatch, DEPLOY.format(ns="apps"), ["--exempt", "apps=because"]) == 0


def test_an_empty_corpus_is_an_operator_error(monkeypatch) -> None:
    assert run(monkeypatch, "") == 2


def test_a_corpus_without_workloads_is_an_operator_error(monkeypatch, capsys) -> None:
    """The shape a render loop that never reached an app stage produces."""
    assert run(monkeypatch, FENCED.format(ns="apps")) == 2
    assert "0 workload namespaces" in capsys.readouterr().err


@pytest.mark.parametrize("ns", sorted(gate.EXEMPT_NAMESPACES))
def test_every_exemption_carries_a_reason(ns: str) -> None:
    assert len(gate.EXEMPT_NAMESPACES[ns]) > 40


# --- The two kube-system scrape allows no gate can see ------------------------
#
# check-scrape-netpol.py matches `serviceMonitor.enabled` / `podMonitor.enabled`
# in HelmRelease values, and neither kube-system monitor is spelled that way:
# kured's comes from `metrics.create: true`, and CoreDNS's is rendered by
# kube-prometheus-stack (chart-rendered monitors never enter the flux:lint
# corpus at all). So kube-system is the one fenced namespace where deleting or
# mistyping a scrape allow passes every gate in `task lint`. These pin the two
# rules directly. The gate script is vendored from weisssrv-lib, so teaching it
# the `metrics.create` spelling is a library change, not a local edit.

KUBE_SYSTEM_POLICIES = REPO / "kubernetes" / "infrastructure" / "configs" / "kube-system-policies"


def _ingress_allows(filename: str):
    """(podSelector matchLabels, port) pairs from every ingress rule in a file."""
    pairs = []
    for doc in yaml.safe_load_all((KUBE_SYSTEM_POLICIES / filename).read_text()):
        if not doc or doc.get("kind") != "NetworkPolicy":
            continue
        labels = (doc["spec"].get("podSelector") or {}).get("matchLabels") or {}
        for rule in doc["spec"].get("ingress") or []:
            for port in rule.get("ports") or []:
                pairs.append((tuple(sorted(labels.items())), port.get("port")))
    return pairs


def test_coredns_keeps_its_scrape_allow_on_9153() -> None:
    selector = (("k8s-app", "kube-dns"),)
    assert (selector, 9153) in _ingress_allows("allow-coredns.yaml")


def test_kured_keeps_its_scrape_allow_on_8080() -> None:
    selector = (("app.kubernetes.io/instance", "kured"), ("app.kubernetes.io/name", "kured"))
    assert (selector, 8080) in _ingress_allows("allow-kured.yaml")
