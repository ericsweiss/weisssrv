"""Tests for scripts/check-secretstore-scope.py."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "check_secretstore_scope",
    Path(__file__).resolve().parent / "check-secretstore-scope.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _run(stdin_text: str, monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    return mod.main()


def _store(conditions: str = "") -> str:
    return f"""
---
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata: {{name: onepassword-homelab}}
spec:
{conditions}  provider:
    onepassword: {{connectHost: "http://connect:8080"}}
"""


SCOPED = _store("""  conditions:
    - namespaces: [apps]
""")
UNSCOPED = _store()

EXTERNAL_SECRET = """
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata: {name: app-secrets, namespace: apps}
spec:
  secretStoreRef: {kind: ClusterSecretStore, name: onepassword-homelab}
"""

NAMESPACES = """
---
apiVersion: v1
kind: Namespace
metadata:
  name: apps
  labels: {esweiss.com/vault: "true"}
---
apiVersion: v1
kind: Namespace
metadata: {name: other}
"""


def test_unscoped_cluster_store_fails(monkeypatch):
    assert _run(UNSCOPED + EXTERNAL_SECRET, monkeypatch) == 1


def test_scoped_store_covering_its_consumer_passes(monkeypatch):
    assert _run(SCOPED + EXTERNAL_SECRET, monkeypatch) == 0


def test_consumer_outside_the_conditions_fails(monkeypatch):
    stray = EXTERNAL_SECRET.replace("namespace: apps", "namespace: other")
    assert _run(SCOPED + stray, monkeypatch) == 1


def test_namespace_selector_condition_is_honored(monkeypatch):
    selector_store = _store("""  conditions:
    - namespaceSelector:
        matchLabels: {esweiss.com/vault: "true"}
""")
    assert _run(selector_store + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 0
    stray = EXTERNAL_SECRET.replace("namespace: apps", "namespace: other")
    assert _run(selector_store + NAMESPACES + stray, monkeypatch) == 1


def test_namespace_regex_condition_is_honored(monkeypatch):
    regex_store = _store("""  conditions:
    - namespaceRegexes: ["^app.*$"]
""")
    assert _run(regex_store + EXTERNAL_SECRET, monkeypatch) == 0


def test_cluster_external_secret_fanout_must_be_admitted(monkeypatch):
    ces = """
---
apiVersion: external-secrets.io/v1
kind: ClusterExternalSecret
metadata: {name: cloudflare-api-token}
spec:
  namespaceSelectors:
    - matchLabels: {esweiss.com/vault: "true"}
  externalSecretSpec:
    secretStoreRef: {kind: ClusterSecretStore, name: onepassword-homelab}
"""
    # `apps` carries the fan-out label and is in the conditions -> OK.
    assert _run(SCOPED + NAMESPACES + ces, monkeypatch) == 0
    # Label `other` too and it becomes a consumer the conditions do not admit.
    labelled = NAMESPACES.replace(
        "metadata: {name: other}",
        'metadata: {name: other, labels: {esweiss.com/vault: "true"}}',
    )
    assert _run(SCOPED + labelled + ces, monkeypatch) == 1


def test_namespaced_secretstore_reference_is_ignored(monkeypatch):
    """A namespaced SecretStore is already namespace-bound — not our invariant."""
    local = EXTERNAL_SECRET.replace("kind: ClusterSecretStore", "kind: SecretStore")
    assert _run(local, monkeypatch) == 0


def test_matchexpressions_selector(monkeypatch):
    selector_store = _store("""  conditions:
    - namespaceSelector:
        matchExpressions:
          - {key: esweiss.com/vault, operator: Exists}
""")
    assert _run(selector_store + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 0


def test_unknown_store_is_reported_but_not_fatal(monkeypatch):
    """A store defined outside this corpus cannot be checked; don't fail the build."""
    assert _run(EXTERNAL_SECRET, monkeypatch) == 0
