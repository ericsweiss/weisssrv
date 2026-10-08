"""Site policy pinned against check-default-deny-coverage.py.

Pins the site data: the exemption set and the kube-system ingress allows that no
scrape gate can see. The gate's own failure paths are proved in weisssrv-lib.
"""
from __future__ import annotations

import io
import textwrap
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
gate = load_script("check-default-deny-coverage.py")


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


def test_the_declared_exemptions_are_honoured(monkeypatch) -> None:
    assert run(monkeypatch, DEPLOY.format(ns="flux-system")) == 0


def test_kube_system_is_no_longer_exempt(monkeypatch) -> None:
    """It carries a real default-deny now (configs/kube-system-policies/), so an
    unfenced kube-system in the corpus is a violation like any other."""
    assert "kube-system" not in gate.EXEMPT_NAMESPACES
    assert run(monkeypatch, DEPLOY.format(ns="kube-system")) == 1
    corpus = DEPLOY.format(ns="kube-system") + "---\n" + FENCED.format(ns="kube-system")
    assert run(monkeypatch, corpus) == 0


@pytest.mark.parametrize("ns", sorted(gate.EXEMPT_NAMESPACES))
def test_every_exemption_carries_a_reason(ns: str) -> None:
    assert len(gate.EXEMPT_NAMESPACES[ns]) > 40


# --- The kube-system allows no gate can see -----------------------------------
#
# check-scrape-netpol.py matches `serviceMonitor.enabled` / `podMonitor.enabled`,
# which kured, CoreDNS and metrics-server's :10250 API are not.

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


def test_metrics_server_keeps_its_aggregated_api_allow_on_10250() -> None:
    # Label-scoped on purpose (the file says why), so the selector is pinned
    # too: widening it to podSelector: {} would open :10250 namespace-wide.
    selector = (("app.kubernetes.io/name", "metrics-server"),)
    assert (selector, 10250) in _ingress_allows("allow-metrics-server.yaml")
