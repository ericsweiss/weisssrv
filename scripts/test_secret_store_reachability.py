"""The secret-store backend stays reachable from the ESO controller namespace.

An egress default-deny with no allow for the backend lints clean and surfaces
only as every ExternalSecret failing to sync. The template mirrors this test.
"""
from __future__ import annotations

import re
from pathlib import Path

from conftest import REPO, k8s_documents

MANIFESTS = REPO / "kubernetes"

CONNECT_HOST = re.compile(
    r"https?://([a-z0-9][a-z0-9-]*)\.([a-z0-9][a-z0-9-]*)\.svc\.cluster\.local:(\d+)"
)
STORE_KINDS = {"ClusterSecretStore", "SecretStore"}

# (backend namespace, service) -> the labels the connect chart puts on its pods.
# A peer selector naming anything these pods do not carry reaches nothing.
BACKEND_LABELS = {
    ("external-secrets", "onepassword-connect"): {"app": "onepassword-connect"},
}

# client namespace -> the labels on the controller pods that need the egress.
# A policy selecting anything else leaves the controllers under the deny.
CLIENT_LABELS = {
    "external-secrets": {"app.kubernetes.io/instance": "external-secrets"},
}


# Manifests the last walk could not parse; a dropped SecretStore is not checked.
SKIPPED: list[str] = []


def _docs(root: Path):
    documents, unreadable = k8s_documents(root, suffix="*.yaml")
    SKIPPED[:] = unreadable
    yield from documents


def backend_targets(root: Path = MANIFESTS) -> set[tuple[str, str, str, int]]:
    """(client namespace, backend namespace, service, port) per secret store.

    The client namespace is the one holding the Connect token, i.e. where the
    ESO controllers run and where the egress allow has to live.
    """
    targets = set()
    for _path, doc in _docs(root):
        if doc.get("kind") not in STORE_KINDS:
            continue
        provider = ((doc.get("spec") or {}).get("provider") or {}).get("onepassword") or {}
        host = CONNECT_HOST.match(str(provider.get("connectHost") or ""))
        token_ref = (
            ((provider.get("auth") or {}).get("secretRef") or {}).get(
                "connectTokenSecretRef"
            )
            or {}
        )
        client = token_ref.get("namespace")
        if not host or not client:
            continue
        targets.add((client, host.group(2), host.group(1), int(host.group(3))))
    return targets


def policy_types(spec: dict) -> set[str]:
    """A NetworkPolicy's effective policyTypes, derived as the API derives them.

    The field is omitempty, so an absent or empty list takes the inferred
    default: Ingress, plus Egress when the policy carries egress rules.
    """
    declared = spec.get("policyTypes")
    if isinstance(declared, list) and declared:
        return {str(item) for item in declared}
    types = {"Ingress"}
    if spec.get("egress"):
        types.add("Egress")
    return types


def namespaces_restricting_egress(root: Path = MANIFESTS) -> set[str]:
    """Namespaces carrying a namespace-wide policy that restricts egress.

    Any Egress-type policy over `podSelector: {}` denies what its own rules do
    not allow, so a rule-carrying policy fences the namespace as a bare one does.
    """
    restricted = set()
    for _path, doc in _docs(root):
        if doc.get("kind") != "NetworkPolicy":
            continue
        spec = doc.get("spec") or {}
        namespace = (doc.get("metadata") or {}).get("namespace")
        if not namespace or spec.get("podSelector"):
            continue
        if "Egress" in policy_types(spec):
            restricted.add(namespace)
    return restricted


def _selects(selector_labels: dict | None, declared: dict | None) -> bool:
    """Whether a selector's matchLabels can select pods carrying `declared`.

    Lenient when the workload's labels are undeclared: an unknown backend or
    client is checked for rule shape only, as before.
    """
    if declared is None:
        return True
    labels = (selector_labels or {}).get("matchLabels") or {}
    return all(declared.get(key) == value for key, value in labels.items())


def _admits_port(entry: dict, port: int) -> bool:
    """A `ports:` entry admitting `port`, by number or by an unresolved name.

    A name resolves against the backend pod's containerPort, which the chart
    owns and this tree does not carry, so a name is credited.
    """
    value = entry.get("port")
    if isinstance(value, str) and not value.isdigit():
        return True
    return str(value) == str(port)


def _permits_backend(doc: dict, target: tuple[str, str], port: int) -> bool:
    """A rule whose peer selector actually selects the backend, on that port."""
    declared = BACKEND_LABELS.get(target)
    for rule in (doc.get("spec") or {}).get("egress") or []:
        peers = [peer for peer in rule.get("to") or [] if isinstance(peer, dict)]
        if not any(
            ("podSelector" in peer or "namespaceSelector" in peer)
            and _selects(peer.get("podSelector"), declared)
            for peer in peers
        ):
            continue
        ports = [entry for entry in rule.get("ports") or [] if isinstance(entry, dict)]
        if not ports or any(_admits_port(entry, port) for entry in ports):
            return True
    return False


def allows_egress_to(
    root: Path, namespace: str, target: tuple[str, str], port: int
) -> bool:
    """Whether some policy covering the client pods permits the backend hop."""
    client_labels = CLIENT_LABELS.get(namespace)
    return any(
        doc.get("kind") == "NetworkPolicy"
        and (doc.get("metadata") or {}).get("namespace") == namespace
        and _selects((doc.get("spec") or {}).get("podSelector"), client_labels)
        and _permits_backend(doc, target, port)
        for _path, doc in _docs(root)
    )


def unreachable_backends(root: Path = MANIFESTS) -> list[str]:
    restricted = namespaces_restricting_egress(root)
    return sorted(
        f"{client} -> {namespace}/{service}:{port}"
        for client, namespace, service, port in backend_targets(root)
        if client in restricted
        and not allows_egress_to(root, client, (namespace, service), port)
    )


def test_a_secret_store_names_an_in_cluster_backend():
    """A regex that stopped matching would make the assertion below vacuous."""
    assert backend_targets(), (
        "no (Cluster)SecretStore names a *.svc.cluster.local backend — "
        "this gate is examining nothing"
    )


def test_the_secret_store_backend_is_reachable():
    unreachable = unreachable_backends()
    assert not unreachable, (
        "namespaces restricting egress with no allow for the backend, "
        "so the secret store can never reach it:\n  "
        + "\n  ".join(unreachable)
    )


def test_a_deny_without_an_allow_is_reported(tmp_path: Path):
    """Mutation case: the deny alone, and a DNS-only allow, must both fail."""
    (tmp_path / "store.yaml").write_text(
        "apiVersion: external-secrets.io/v1\n"
        "kind: ClusterSecretStore\n"
        "metadata:\n"
        "  name: vault\n"
        "spec:\n"
        "  provider:\n"
        "    onepassword:\n"
        "      connectHost: http://connect.backend.svc.cluster.local:8080\n"
        "      auth:\n"
        "        secretRef:\n"
        "          connectTokenSecretRef:\n"
        "            name: connect-token\n"
        "            namespace: secrets\n"
        "            key: token\n"
    )
    (tmp_path / "netpol.yaml").write_text(
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata:\n"
        "  name: default-deny-egress\n"
        "  namespace: secrets\n"
        "spec:\n"
        "  podSelector: {}\n"
        "  policyTypes: [Egress]\n"
    )
    assert unreachable_backends(tmp_path) == ["secrets -> backend/connect:8080"]

    (tmp_path / "dns-only.yaml").write_text(
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata:\n"
        "  name: allow-egress-dns\n"
        "  namespace: secrets\n"
        "spec:\n"
        "  podSelector: {}\n"
        "  policyTypes: [Egress]\n"
        "  egress:\n"
        "    - to:\n"
        "        - namespaceSelector:\n"
        "            matchLabels:\n"
        "              kubernetes.io/metadata.name: kube-system\n"
        "      ports:\n"
        "        - protocol: UDP\n"
        "          port: 53\n"
    )
    assert unreachable_backends(tmp_path) == ["secrets -> backend/connect:8080"]

    (tmp_path / "allow.yaml").write_text(
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata:\n"
        "  name: allow-egress-connect\n"
        "  namespace: secrets\n"
        "spec:\n"
        "  podSelector:\n"
        "    matchLabels:\n"
        "      app: eso\n"
        "  policyTypes: [Egress]\n"
        "  egress:\n"
        "    - to:\n"
        "        - podSelector:\n"
        "            matchLabels:\n"
        "              app: connect\n"
        "      ports:\n"
        "        - protocol: TCP\n"
        "          port: 8080\n"
    )
    assert unreachable_backends(tmp_path) == []


def test_an_inferred_egress_policy_type_is_reported(tmp_path: Path):
    """policyTypes is omitempty: a namespace-wide policy carrying egress rules
    and no policyTypes restricts egress, so the backend is still unreachable."""
    (tmp_path / "store.yaml").write_text(
        "apiVersion: external-secrets.io/v1\n"
        "kind: ClusterSecretStore\n"
        "metadata:\n"
        "  name: vault\n"
        "spec:\n"
        "  provider:\n"
        "    onepassword:\n"
        "      connectHost: http://connect.backend.svc.cluster.local:8080\n"
        "      auth:\n"
        "        secretRef:\n"
        "          connectTokenSecretRef:\n"
        "            name: connect-token\n"
        "            namespace: secrets\n"
        "            key: token\n"
    )
    (tmp_path / "netpol.yaml").write_text(
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata:\n"
        "  name: allow-egress-dns-only\n"
        "  namespace: secrets\n"
        "spec:\n"
        "  podSelector: {}\n"
        "  egress:\n"
        "    - to:\n"
        "        - namespaceSelector:\n"
        "            matchLabels:\n"
        "              kubernetes.io/metadata.name: kube-system\n"
        "      ports:\n"
        "        - protocol: UDP\n"
        "          port: 53\n"
    )
    assert namespaces_restricting_egress(tmp_path) == {"secrets"}
    assert unreachable_backends(tmp_path) == ["secrets -> backend/connect:8080"]


def test_an_empty_policy_types_list_infers_the_same_default():
    """`policyTypes: []` round-trips an absent field; it is not "no direction"."""
    assert policy_types({"policyTypes": [], "egress": [{}]}) == {"Ingress", "Egress"}
    assert policy_types({"policyTypes": []}) == {"Ingress"}
    assert policy_types({"policyTypes": ["Ingress"], "egress": [{}]}) == {"Ingress"}


LIVE_STORE = REPO / "kubernetes/infrastructure/configs/cluster-secret-store.yaml"
LIVE_NETPOL = REPO / "kubernetes/infrastructure/controllers/external-secrets/networkpolicy.yaml"
LIVE_TARGET = "external-secrets -> external-secrets/onepassword-connect:8080"


def _live_pair(tmp_path: Path, netpol_text: str) -> Path:
    (tmp_path / "store.yaml").write_text(LIVE_STORE.read_text(encoding="utf-8"))
    (tmp_path / "netpol.yaml").write_text(netpol_text)
    return tmp_path


def test_the_live_pair_is_reachable_on_its_own(tmp_path: Path):
    """The baseline the two renames below mutate."""
    root = _live_pair(tmp_path, LIVE_NETPOL.read_text(encoding="utf-8"))
    assert unreachable_backends(root) == []


def test_a_renamed_backend_peer_label_is_reported(tmp_path: Path):
    """The peer selects the connect pods by `app`; a rename reaches nothing."""
    text = LIVE_NETPOL.read_text(encoding="utf-8")
    mutated = text.replace("app: onepassword-connect", "app: onepassword-connect-v2")
    assert mutated != text
    assert unreachable_backends(_live_pair(tmp_path, mutated)) == [LIVE_TARGET]


def test_a_renamed_client_policy_selector_is_reported(tmp_path: Path):
    """The policy must cover the ESO controller pods, or they stay denied."""
    text = LIVE_NETPOL.read_text(encoding="utf-8")
    mutated = text.replace(
        "app.kubernetes.io/instance: external-secrets",
        "app.kubernetes.io/instance: external-secrets-v2",
    )
    assert mutated != text
    assert unreachable_backends(_live_pair(tmp_path, mutated)) == [LIVE_TARGET]


def test_a_port_spelled_as_a_name_or_a_string_is_credited(tmp_path: Path):
    """A named port is unresolvable here, and a quoted number is still that port."""
    text = LIVE_NETPOL.read_text(encoding="utf-8")
    for spelling in ("port: connect-api", 'port: "8080"'):
        mutated = text.replace("port: 8080", spelling)
        assert mutated != text
        assert unreachable_backends(_live_pair(tmp_path, mutated)) == []
    wrong = text.replace("port: 8080", "port: 8081")
    assert wrong != text
    assert unreachable_backends(_live_pair(tmp_path, wrong)) == [LIVE_TARGET]


def test_the_client_namespace_is_the_token_holder():
    """The allow has to live where ESO runs, not where the backend Service is."""
    assert ("external-secrets", "external-secrets", "onepassword-connect", 8080) in (
        backend_targets()
    )


def test_no_manifest_is_dropped_as_unparseable():
    """An unreadable manifest hides a SecretStore from the walk."""
    backend_targets()
    assert SKIPPED == []
