"""Destructive-upgrade guards for the HelmReleases under kubernetes/.

Uninstalling a CRD-owning release cascade-deletes every CR and external-dns
prunes records it stops recognising, so every release is classified here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
K8S_ROOT = REPO / "kubernetes"

# Releases whose chart owns CRDs. Each names the shape that keeps them, so a
# values key the chart never reads fails instead of passing on the string alone.
CRD_KEEPERS = {
    "infrastructure/crds/release.yaml": "values",
    "infrastructure/controllers/cert-manager/release.yaml": "keep",
    "infrastructure/controllers/metallb/release.yaml": "postRenderer",
    "infrastructure/controllers/external-secrets/release.yaml": "values",
}

# Every other HelmRelease, with the reason an uninstall takes no CR with it. A
# release in neither mapping fails the walk below, so a chart that starts
# shipping CRDs in templates/ cannot reach the cluster unnoticed.
NO_CRDS = {
    "infrastructure/controllers/external-dns/release.yaml":
        "no CustomResourceDefinition; records are driven by annotations",
    "infrastructure/controllers/kured/release.yaml":
        "no CustomResourceDefinition; reboot state lives in node annotations",
    "infrastructure/controllers/metrics-server/release.yaml":
        "no CustomResourceDefinition; it serves the metrics.k8s.io API",
    "infrastructure/controllers/nvidia-device-plugin/release.yaml":
        "no CustomResourceDefinition; devices are advertised to the kubelet",
    "infrastructure/controllers/onepassword-connect/release.yaml":
        "no CustomResourceDefinition here; the OnePasswordItem operator is not "
        "installed, ESO reads Connect instead",
    "infrastructure/controllers/reloader/release.yaml":
        "no CustomResourceDefinition; it watches ConfigMaps and Secrets",
    "infrastructure/controllers/tailscale-operator/release.yaml":
        "the chart's CRDs ship in its crds/ directory, which an uninstall keeps",
    "infrastructure/controllers/traefik/release.yaml":
        "the chart's CRDs ship in its crds/ directory; crds: CreateReplace "
        "keeps their schema current",
    "infrastructure/controllers/vpa/release.yaml":
        "the chart's CRDs ship in its crds/ directory; crds: CreateReplace "
        "keeps their schema current",
    "infrastructure/observability/alloy/release.yaml":
        "no CustomResourceDefinition; collectors are values and ConfigMaps",
    "infrastructure/observability/alloy-syslog/release.yaml":
        "no CustomResourceDefinition; collectors are values and ConfigMaps",
    "infrastructure/observability/exporters/blackbox-exporter.yaml":
        "no CustomResourceDefinition; its probes are values and a ServiceMonitor",
    "infrastructure/observability/kube-prometheus-stack/release.yaml":
        "the monitoring.coreos.com CRDs belong to infrastructure/crds; this "
        "release sets crds.enabled false and crds: Skip",
    "infrastructure/observability/loki/release.yaml":
        "no CustomResourceDefinition; ruler rules arrive as ConfigMaps",
    "apps/authentik/release.yaml":
        "no CustomResourceDefinition; authentik state lives in its database "
        "and in terraform/authentik",
    "apps/gitlab-agent/release.yaml":
        "no CustomResourceDefinition; agent config lives in .gitlab/agents/",
    "apps/gitlab-runner/release.yaml":
        "no CustomResourceDefinition; runners register over the API",
    "apps/gitlab-runner-privileged/release.yaml":
        "no CustomResourceDefinition; runners register over the API",
}

# cert-manager keeps its CRDs through an uninstall via crds.keep, so it does not
# need the retry strategy the others use to avoid one.
CRD_RETRY_RELEASES = sorted(
    set(CRD_KEEPERS) - {"infrastructure/controllers/cert-manager/release.yaml"}
)

EXTERNAL_DNS_RELEASE = "infrastructure/controllers/external-dns/release.yaml"
# external-dns >= 0.22 defaults to external-dns.kubernetes.io/; the annotation
# the manifests carry is the alpha one, and the mismatch deletes every record.
ANNOTATION_PREFIX_ARG = "--annotation-prefix=external-dns.alpha.kubernetes.io/"
KEEP_POLICY = ("helm.sh/resource-policy", "keep")

# Flux's own manifest and the Kustomize component that patches a release in
# place: neither declares a chart this guard can classify.
NOT_A_RELEASE_FILE = (
    "clusters/weisssrv/flux-system/gotk-components.yaml",
    "components/gitlab-runner-common/kustomization.yaml",
)


def _release_files() -> list[str]:
    """Every file under kubernetes/ declaring a HelmRelease, repo-relative."""
    found = []
    for path in sorted(K8S_ROOT.rglob("*.yaml")):
        rel = path.relative_to(K8S_ROOT).as_posix()
        if rel in NOT_A_RELEASE_FILE:
            continue
        if "kind: HelmRelease" in path.read_text(encoding="utf-8"):
            found.append(rel)
    return found


def _helm_release(relpath: str) -> dict:
    path = K8S_ROOT / relpath
    assert path.is_file(), f"{relpath} is missing — this guard checked nothing"
    for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if isinstance(doc, dict) and doc.get("kind") == "HelmRelease":
            return doc
    raise AssertionError(f"{relpath} holds no HelmRelease")


def _values_keep_crds(doc: dict) -> bool:
    """`crds.annotations` — the values key the chart itself stamps."""
    key, value = KEEP_POLICY
    values = (doc.get("spec") or {}).get("values") or {}
    annotations = (values.get("crds") or {}).get("annotations") or {}
    return annotations.get(key) == value


def _post_renderer_keeps_crds(doc: dict) -> bool:
    """A kustomize patch whose target is the CRDs, not some other kind."""
    key, value = KEEP_POLICY
    for renderer in (doc.get("spec") or {}).get("postRenderers") or []:
        for entry in (renderer.get("kustomize") or {}).get("patches") or []:
            if (entry.get("target") or {}).get("kind") != "CustomResourceDefinition":
                continue
            try:
                patch = yaml.safe_load(entry.get("patch") or "")
            except yaml.YAMLError:
                continue
            if not isinstance(patch, dict):
                continue
            if ((patch.get("metadata") or {}).get("annotations") or {}).get(key) == value:
                return True
    return False


def _keep_flag_keeps_crds(doc: dict) -> bool:
    """`crds.keep` — the boolean cert-manager's chart turns into the annotation."""
    values = (doc.get("spec") or {}).get("values") or {}
    return (values.get("crds") or {}).get("keep") is True


KEEP_SHAPES = {
    "values": _values_keep_crds,
    "postRenderer": _post_renderer_keeps_crds,
    "keep": _keep_flag_keeps_crds,
}


def _install_strategy(doc: dict) -> str | None:
    """`spec.install.strategy.name` — the only path the HelmRelease CRD defines."""
    strategy = ((doc.get("spec") or {}).get("install") or {}).get("strategy")
    return strategy.get("name") if isinstance(strategy, dict) else None


def test_every_helmrelease_is_classified():
    """A new release lands in neither mapping, so its chart's CRD ownership is
    reviewed here before it ships."""
    classified = set(CRD_KEEPERS) | set(NO_CRDS)
    found = set(_release_files())
    assert found, "found no HelmRelease under kubernetes/ — this guard checked nothing"
    assert not found - classified, (
        "HelmRelease(s) classified neither as CRD owners (CRD_KEEPERS) nor as "
        f"CRD-free (NO_CRDS): {sorted(found - classified)}"
    )
    assert not classified - found, (
        f"classified releases that no longer exist: {sorted(classified - found)}"
    )


@pytest.mark.parametrize("relpath,shape", sorted(CRD_KEEPERS.items()))
def test_crd_installing_releases_keep_their_crds(relpath, shape):
    assert KEEP_SHAPES[shape](_helm_release(relpath)), (
        f"{relpath} no longer sets {KEEP_POLICY[0]}: {KEEP_POLICY[1]} on the CRDs "
        f"its chart owns, at the {shape} path the chart reads — the next uninstall "
        "or chart-version change takes every CR with them"
    )


@pytest.mark.parametrize("relpath", CRD_RETRY_RELEASES)
def test_crd_installing_releases_retry_instead_of_uninstalling(relpath):
    """The default install remediation remediates by uninstalling, which is the
    cascade above, so every CRD-owning release retries in place instead."""
    assert _install_strategy(_helm_release(relpath)) == "RetryOnFailure", (
        f"{relpath} install remediation is back to uninstalling on failure, which "
        "deletes the chart's CRDs and every CR"
    )


def _extra_args(doc: dict) -> list:
    """`spec.values.extraArgs` — a mapping form is not the list the chart reads."""
    args = ((doc.get("spec") or {}).get("values") or {}).get("extraArgs")
    return args if isinstance(args, list) else []


def test_external_dns_pins_the_annotation_prefix():
    assert ANNOTATION_PREFIX_ARG in _extra_args(_helm_release(EXTERNAL_DNS_RELEASE)), (
        f"{EXTERNAL_DNS_RELEASE} dropped {ANNOTATION_PREFIX_ARG}: external-dns "
        "stops seeing the annotations the manifests carry and prunes the records "
        "it owns"
    )


# Mutation cases: each classifier, against the shape that must NOT pass.

def test_a_release_without_the_keep_annotation_is_reported():
    assert not _values_keep_crds({"spec": {"values": {"crds": {"enabled": True}}}})
    assert _values_keep_crds(
        {"spec": {"values": {"crds": {"annotations": {KEEP_POLICY[0]: KEEP_POLICY[1]}}}}}
    )


def test_a_post_renderer_patching_another_kind_is_not_read_as_keeping_crds():
    patch = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: p\n"
        "  annotations:\n    helm.sh/resource-policy: keep\n"
    )
    doc = {"spec": {"postRenderers": [
        {"kustomize": {"patches": [{"target": {"kind": "Deployment"}, "patch": patch}]}}
    ]}}
    assert not _post_renderer_keeps_crds(doc)


def test_a_keep_flag_that_is_a_string_is_not_read_as_true():
    assert not _keep_flag_keeps_crds({"spec": {"values": {"crds": {"keep": "true"}}}})
    assert _keep_flag_keeps_crds({"spec": {"values": {"crds": {"keep": True}}}})


def test_an_install_strategy_outside_the_crd_path_is_not_read_as_configured():
    """A bare string is not the mapping the HelmRelease CRD defines, so
    Kubernetes prunes it and the gate must report it missing."""
    assert _install_strategy(
        {"spec": {"install": {"strategy": {"name": "RetryOnFailure"}}}}
    ) == "RetryOnFailure"
    assert _install_strategy({"spec": {"install": {"strategy": "RetryOnFailure"}}}) is None
    assert _install_strategy({}) is None


def test_extra_args_in_a_shape_the_chart_does_not_read_is_reported():
    assert _extra_args({"spec": {"values": {"extraArgs": [ANNOTATION_PREFIX_ARG]}}})
    assert _extra_args({"spec": {"values": {"extraArgs": {"annotation-prefix": "x"}}}}) == []
    assert _extra_args({}) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
