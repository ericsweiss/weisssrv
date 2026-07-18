#!/usr/bin/env python3
"""Schema-validate the value-heavy Flux HelmReleases via `helm template`.

`kustomize build | kubeconform` (task flux:lint) emits a HelmRelease verbatim as
a Flux CR and never renders its chart, so the chart-specific keys inside
`.spec.values` are NOT validated — the HelmRelease CRD treats `.spec.values` as a
free-form object. A typo in a values key (e.g. `prometheuss:` for `prometheus:`)
therefore slips past lint and silently no-ops in-cluster.

This closes that gap for the value-heavy releases listed in RELEASES by
extracting each release's `.spec.values`, substituting the `${...}` postBuild
placeholders from the cluster-versions ConfigMap, and running `helm template`
against the pinned chart version. That:
  - hard-fails on a typo'd key for charts that ship a values.schema.json
    (traefik does; helm validates values against it),
  - hard-fails on any values that produce an unrenderable template (all charts),
  - and (optionally) pipes the rendered output to kubeconform for structural
    validation of the produced resources.

It requires network access (it does `helm repo add`/`update` against the public
chart repos). The flux-lint CI job and `task flux:lint` invoke it. It is scoped
to exactly the releases below; add an entry to RELEASES to cover another.

Usage:
  validate-helm-values.py [--kubeconform] [--repo-root DIR]

Exit code is non-zero if any release fails to template (or fails kubeconform
when --kubeconform is given).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

# Single source of truth for the no-CPU-limits policy: load the allowlist AND
# the violation scanner from check-hpa-vpa-invariant.py so the kustomize-side
# check (run by `task flux:lint`) and this helm-rendered-side check can never
# diverge. The sibling has a hyphenated filename (not importable normally) and a
# `__main__` guard, so loading it here does not run its main().
_HPA_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "check-hpa-vpa-invariant.py")
_hpa_spec = importlib.util.spec_from_file_location("check_hpa_vpa_invariant", _HPA_SRC)
_hpa = importlib.util.module_from_spec(_hpa_spec)
_hpa_spec.loader.exec_module(_hpa)

# The traefik chart's servicemonitor.yaml hard-fails templating unless the
# prometheus-operator API is present in .Capabilities.APIVersions (the cluster
# installs it via kube-prometheus-stack). Declare it so `helm template` of a
# serviceMonitor-enabled release succeeds offline. Charts commonly gate on the
# kind-qualified form (.Capabilities.APIVersions.Has
# "monitoring.coreos.com/v1/ServiceMonitor"), so declare the kind-qualified CRDs
# the cluster actually has, not just the bare group/version.
# Kubernetes version for capability-gated rendering, shared by `helm template`
# (--kube-version) and kubeconform (-kubernetes-version) so version-gated chart
# templates render the way both tools see them. Derived at runtime from
# k3s_version in the cluster-versions ConfigMap (see derive_kube_version) so it
# tracks the live cluster; this fallback only applies if that key is missing.
KUBE_VERSION_FALLBACK = "1.36.0"

HELM_API_VERSIONS = [
    "monitoring.coreos.com/v1",
    "monitoring.coreos.com/v1/ServiceMonitor",
    "monitoring.coreos.com/v1/PodMonitor",
    "monitoring.coreos.com/v1/PrometheusRule",
]

# Value-heavy HelmReleases worth rendering. Each: the release manifest, the
# chart name, and the Helm repo (name + classic HTTPS url). The chart version is
# read straight from the manifest's `.spec.chart.spec.version` — a literal or a
# "${configmap_key}" placeholder resolved from versions-configmap — so there is
# no separate version key to keep in sync here.
RELEASES = [
    {
        "name": "traefik",
        "manifest": "kubernetes/infrastructure/controllers/traefik/release.yaml",
        "chart": "traefik",
        "repo_name": "traefik",
        "repo_url": "https://traefik.github.io/charts",
    },
    {
        "name": "kube-prometheus-stack",
        "manifest": "kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml",
        "chart": "kube-prometheus-stack",
        "repo_name": "prometheus-community",
        "repo_url": "https://prometheus-community.github.io/helm-charts",
    },
    {
        "name": "authentik",
        "manifest": "kubernetes/apps/authentik/release.yaml",
        "chart": "authentik",
        "repo_name": "authentik",
        "repo_url": "https://charts.goauthentik.io",
    },
    {
        "name": "kured",
        "manifest": "kubernetes/infrastructure/controllers/kured/release.yaml",
        "chart": "kured",
        "repo_name": "kured",
        "repo_url": "https://kubereboot.github.io/charts",
    },
    {
        "name": "reloader",
        "manifest": "kubernetes/infrastructure/controllers/reloader/release.yaml",
        "chart": "reloader",
        "repo_name": "stakater",
        "repo_url": "https://stakater.github.io/stakater-charts",
    },
    {
        "name": "tailscale-operator",
        "manifest": "kubernetes/infrastructure/controllers/tailscale-operator/release.yaml",
        "chart": "tailscale-operator",
        "repo_name": "tailscale",
        "repo_url": "https://pkgs.tailscale.com/helmcharts",
    },
]

VERSIONS_CONFIGMAP = "kubernetes/infrastructure/sources/versions-configmap.yaml"
PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_versions(repo_root: str) -> dict:
    """Return the cluster-versions ConfigMap data map."""
    path = os.path.join(repo_root, VERSIONS_CONFIGMAP)
    with open(path) as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict):
        raise SystemExit(
            f"ERROR: {VERSIONS_CONFIGMAP} must be a mapping, got {type(doc).__name__}"
        )
    data = doc.get("data", {}) or {}
    if not data:
        raise SystemExit(f"ERROR: no data keys in {VERSIONS_CONFIGMAP}")
    return {k: str(v) for k, v in data.items()}


def derive_kube_version(versions: dict) -> str:
    """Cluster Kubernetes version (X.Y.Z) for helm/kubeconform, from k3s_version.

    e.g. "v1.36.2+k3s1" -> "1.36.2". Falls back to KUBE_VERSION_FALLBACK when the
    key is absent or unparseable so a malformed pin can't break flux:lint.
    """
    m = re.match(r"v?(\d+\.\d+\.\d+)", str(versions.get("k3s_version", "")))
    return m.group(1) if m else KUBE_VERSION_FALLBACK


def substitute(text: str, versions: dict) -> tuple[str, list[str]]:
    """Replace ${var} placeholders from versions; return (text, missing keys)."""
    missing: list[str] = []

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in versions:
            missing.append(key)
            return m.group(0)
        return versions[key]

    return PLACEHOLDER_RE.sub(repl, text), sorted(set(missing))


def extract_helmrelease_from_text(text: str, source: str) -> dict:
    """Return the (first) HelmRelease document from manifest text."""
    docs = [d for d in yaml.safe_load_all(text)
            if isinstance(d, dict) and d.get("kind") == "HelmRelease"]
    if not docs:
        raise SystemExit(f"ERROR: no HelmRelease found in {source}")
    return docs[0]


def extract_helmrelease(manifest_path: str) -> dict:
    """Return the (first) HelmRelease document from a manifest file."""
    with open(manifest_path) as f:
        return extract_helmrelease_from_text(f.read(), manifest_path)


def validate_release(rel: dict, versions: dict, repo_root: str, run_kubeconform: bool,
                     kube_version: str) -> bool:
    """Template one release; return True on success."""
    manifest = os.path.join(repo_root, rel["manifest"])
    # Substitute ${placeholders} in the RAW manifest text first — exactly like
    # Flux's postBuild.substituteFrom — so a quoted placeholder keeps its YAML
    # type after substitution (e.g. "${redis_version}" stays a string, not a
    # number parsed from the bare value), matching the object Flux applies.
    with open(manifest) as f:
        rendered_manifest, missing = substitute(f.read(), versions)
    if missing:
        print(f"ERROR [{rel['name']}]: manifest references unknown configmap key(s): {missing}")
        return False
    hr = extract_helmrelease_from_text(rendered_manifest, manifest)
    spec = hr.get("spec", {})

    version = str(spec.get("chart", {}).get("spec", {}).get("version", ""))
    if not version:
        print(f"ERROR [{rel['name']}]: could not determine chart version")
        return False

    # Values are already resolved (with their original YAML types preserved)
    # from the text substitution above; just dump .spec.values for helm.
    values = spec.get("values", {})
    values_yaml = yaml.safe_dump(values, default_flow_style=False, sort_keys=False)

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as vf:
        vf.write(values_yaml)
        values_file = vf.name

    try:
        # Render with the HelmRelease's actual release identity so charts that
        # key off .Release.Name/.Release.Namespace validate the same output Flux
        # produces (falls back to the registry name / "default").
        meta = hr.get("metadata", {})
        release_name = spec.get("releaseName") or meta.get("name") or rel["name"]
        namespace = spec.get("targetNamespace") or meta.get("namespace") or "default"
        cmd = [
            "helm", "template", release_name,
            f"{rel['repo_name']}/{rel['chart']}",
            "--version", version,
            "--namespace", namespace,
            "--kube-version", kube_version,
            "-f", values_file,
            "--skip-tests",
        ]
        for api in HELM_API_VERSIONS:
            cmd += ["--api-versions", api]
        print(f"=== helm template {rel['name']} ({rel['chart']}@{version}) ===")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"ERROR [{rel['name']}]: helm template failed:")
            # helm can emit useful render diagnostics on stdout too, not just stderr.
            if proc.stdout.strip():
                print("stdout:", proc.stdout.strip())
            print("stderr:", proc.stderr.strip())
            return False

        # No-CPU-limits policy on the CHART-RENDERED pods — the gap
        # check-hpa-vpa-invariant.py can't see: it scans only the HelmRelease
        # `.spec.values`, never the chart's default pod specs, so a chart default
        # could introduce a CPU limit unnoticed. Reuse that script's scanner +
        # allowlist (loaded above) so the two checks stay identical. Runs before
        # the optional kubeconform so the policy holds for `task flux:lint` too.
        rendered_docs = [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]
        cpu_viol = _hpa._cpu_limit_violations(rendered_docs)
        if cpu_viol:
            print(
                f"ERROR [{rel['name']}]: chart-rendered pods set a CPU limit "
                "(a compressible resource; CFS throttling distorts latency and "
                "CPU-based HPAs — see docs/33-autoscaling.md). To intentionally "
                "permit one, add its 'namespace/Kind/name' key to "
                "CPU_LIMIT_ALLOWLIST in check-hpa-vpa-invariant.py. Offenders:"
            )
            print("\n".join(cpu_viol))
            return False

        if run_kubeconform:
            kc = subprocess.run(
                [
                    "kubeconform", "-strict", "-ignore-missing-schemas",
                    "-kubernetes-version", kube_version,
                    "-schema-location", "default",
                    "-schema-location",
                    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/"
                    "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json",
                    "-summary",
                ],
                input=proc.stdout, capture_output=True, text=True,
            )
            print(kc.stdout.strip())
            if kc.returncode != 0:
                print(f"ERROR [{rel['name']}]: kubeconform failed:")
                print(kc.stderr.strip())
                return False
        return True
    finally:
        os.unlink(values_file)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kubeconform", action="store_true",
        help="also pipe rendered output through kubeconform",
    )
    parser.add_argument(
        "--repo-root", default=".",
        help="repo root (default: cwd)",
    )
    args = parser.parse_args()

    if shutil.which("helm") is None:
        print("ERROR: helm not found on PATH")
        return 1
    if args.kubeconform and shutil.which("kubeconform") is None:
        print("ERROR: kubeconform not found on PATH (--kubeconform given)")
        return 1

    versions = load_versions(args.repo_root)
    kube_version = derive_kube_version(versions)

    # Add/refresh the chart repos once (network).
    for rel in RELEASES:
        add = subprocess.run(
            # --force-update keeps repeated local flux:lint runs idempotent (a
            # plain `repo add` errors when the repo already exists) and refreshes
            # a changed URL.
            ["helm", "repo", "add", rel["repo_name"], rel["repo_url"], "--force-update"],
            capture_output=True, text=True,
        )
        if add.returncode != 0:
            print(f"ERROR: failed to add/update Helm repo {rel['repo_name']}:")
            print(add.stderr.strip())
            return 1
    upd = subprocess.run(["helm", "repo", "update"], capture_output=True, text=True)
    if upd.returncode != 0:
        print("ERROR: helm repo update failed:")
        print(upd.stderr.strip())
        return 1

    failed = 0
    for rel in RELEASES:
        if not validate_release(rel, versions, args.repo_root, args.kubeconform, kube_version):
            failed += 1
    if failed:
        print(f"\n{failed} release(s) failed helm-values validation")
        return 1
    print("\nAll value-heavy HelmReleases validated via helm template")
    return 0


if __name__ == "__main__":
    sys.exit(main())
