#!/usr/bin/env python3
"""Assert every k8s-sidecar INIT container the Grafana chart renders lists once
and exits, rather than watching forever and hanging the pod in PodInitializing.
Exit 0 clean, 1 a watching init container, 2 the gate could not inspect it.
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

REPO = Path(__file__).resolve().parent.parent

MANIFEST = Path("kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml")
VERSIONS_CONFIGMAP = Path("kubernetes/infrastructure/sources/versions-configmap.yaml")
CLUSTER_CONFIG = Path("kubernetes/infrastructure/sources/cluster-config.yaml")
CHART = "kube-prometheus-stack"
CHART_REPO = "https://prometheus-community.github.io/helm-charts"
# The chart names every k8s-sidecar container <release>-sc-<kind>, and the init
# variant <release>-init-sc-<kind>.
SIDECAR_MARKER = "-sc-"
# k8s-sidecar reads its mode from METHOD. Only LIST terminates.
METHOD_ENV = "METHOD"
LIST_METHOD = "LIST"
RENDER_TIMEOUT_SECONDS = 300

# A stalled chart repo must fail with a message, not hang until the job timeout.
_VALIDATOR = "validate-helm-values.py"


class GateError(RuntimeError):
    """The gate could not inspect its subject: exit 2, never a finding."""


def _validator():
    """Reuse the sibling gate's substitution and HelmRelease extraction."""
    src = Path(__file__).resolve().parent / _VALIDATOR
    if not src.is_file():
        raise GateError(f"{_VALIDATOR} must sit next to this script")
    spec = importlib.util.spec_from_file_location("validate_helm_values", str(src))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def substitutions(root: Path, validator) -> dict:
    """Both Flux substitution ConfigMaps, which the manifest's ${vars} resolve from."""
    versions = validator.load_versions(str(root), str(root / VERSIONS_CONFIGMAP))
    config_path = root / CLUSTER_CONFIG
    if not config_path.is_file():
        raise GateError(f"cluster identity ConfigMap not found: {CLUSTER_CONFIG}")
    config = (yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}).get("data") or {}
    if not config:
        raise GateError(f"no cluster identity keys in {CLUSTER_CONFIG}")
    return {**versions, **config}


def render(root: Path, validator) -> list[dict]:
    """The chart as Flux installs it: pinned version, substituted values."""
    manifest = root / MANIFEST
    if not manifest.is_file():
        raise GateError(f"HelmRelease not found: {MANIFEST}")
    text, missing = validator.substitute(manifest.read_text(encoding="utf-8"),
                                         substitutions(root, validator))
    if missing:
        raise GateError(f"{MANIFEST} references unknown ConfigMap key(s): {missing}")
    spec = validator.extract_helmrelease_from_text(text, str(manifest)).get("spec", {})
    version = str(spec.get("chart", {}).get("spec", {}).get("version", ""))
    if not version:
        raise GateError(f"could not determine the chart version pinned in {MANIFEST}")
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as values_file:
        yaml.safe_dump(spec.get("values", {}), values_file, sort_keys=False)
        values_file.flush()
        cmd = [
            "helm", "template", CHART, CHART,
            "--repo", CHART_REPO,
            "--version", version,
            "--namespace", spec.get("targetNamespace") or "default",
            "-f", values_file.name,
            "--skip-tests",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=RENDER_TIMEOUT_SECONDS, check=False)
        except FileNotFoundError as exc:
            raise GateError("helm is not on PATH, so the chart cannot be rendered") from exc
        except subprocess.TimeoutExpired as exc:
            raise GateError(f"`helm template {CHART}@{version}` timed out") from exc
    if proc.returncode != 0:
        raise GateError(
            f"`helm template {CHART}@{version}` failed: "
            f"{(proc.stderr or proc.stdout).strip().splitlines()[-1:] or ['no output']}"
        )
    return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def init_sidecars(docs: list[dict]) -> list[tuple[str, str, str | None]]:
    """(workload, container, METHOD) for every k8s-sidecar INIT container."""
    found = []
    for doc in docs:
        pod = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
        for container in pod.get("initContainers") or []:
            name = str(container.get("name", ""))
            if SIDECAR_MARKER not in name:
                continue
            env = {e.get("name"): e.get("value") for e in container.get("env") or []}
            found.append((str(doc.get("metadata", {}).get("name", "?")), name,
                          env.get(METHOD_ENV)))
    return found


def check(root: Path = REPO) -> list[str]:
    containers = init_sidecars(render(root, _validator()))
    # A gate that inspects nothing is not a gate: the values enable an init
    # sidecar, so a render with none means the knob or the chart moved.
    if not containers:
        raise GateError(
            f"{MANIFEST} renders no `*{SIDECAR_MARKER}*` init container, so this gate "
            "checked nothing. Either the chart renamed it or initDatasources is off; "
            "update this gate with whichever it is."
        )
    return [
        f"{workload}: init container {name} runs with {METHOD_ENV}="
        f"{method or '<unset>'}, not {LIST_METHOD}"
        for workload, name, method in containers
        if method != LIST_METHOD
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Grafana's k8s-sidecar init containers must list once and exit.",
    )
    parser.add_argument("--repo-root", default=REPO, type=Path)
    args = parser.parse_args(argv)
    try:
        problems = check(args.repo_root)
    except GateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("ERROR: a k8s-sidecar INIT container does not terminate, so the pod "
              "stays in PodInitializing and the Helm upgrade times out:")
        for problem in problems:
            print(f"  - {problem}")
        print(f"  Set the matching sidecar's watchMethod to {LIST_METHOD} in {MANIFEST}.")
        return 1
    print(f"Every rendered k8s-sidecar init container runs {METHOD_ENV}={LIST_METHOD}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
