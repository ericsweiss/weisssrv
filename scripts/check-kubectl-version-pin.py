#!/usr/bin/env python3
"""Assert the CI kubectl pin stays within +/-1 minor of the cluster's k3s_version.

The CI kubectl pin (.kubectl-setup in .gitlab-ci.yml) has no drift guard, unlike
the FLUX_VERSION pin. A k3s_version bump merged via all.yml -> versions-configmap
could push the hardcoded kubectl pin to 2 minors of skew with nothing catching
it, at which point the kubectl calls in the deploy-verify / maintenance jobs
could break. This asserts the pin stays within Kubernetes' supported +/-1 minor
skew of the cluster's k3s_version; on failure, bump the kubectl version + sha256
in .kubectl-setup (.gitlab-ci.yml).

Extracted from the inline kubectl-version-pin-check CI job so the exact same
check runs locally (`task lint:kubectl-version-pin`) and is unit-tested
(scripts/test_check_kubectl_version_pin.py).

Run via `pytest scripts/` or directly:
  scripts/check-kubectl-version-pin.py                       # scan the repo
  scripts/check-kubectl-version-pin.py <ci_yaml> <configmap> # explicit paths
"""
from __future__ import annotations

import sys
from pathlib import Path

import re

REPO = Path(__file__).resolve().parent.parent
CI_YAML = REPO / ".gitlab-ci.yml"
VERSIONS_CM = REPO / "kubernetes/infrastructure/sources/versions-configmap.yaml"

_KUBECTL_RE = re.compile(r"dl\.k8s\.io/release/v(\d+)\.(\d+)\.\d+/bin")
_K3S_RE = re.compile(r"^\s*k3s_version:\s*v?(\d+)\.(\d+)", re.M)


def check(ci_text: str, cm_text: str) -> tuple[int, str]:
    """Return (exit_code, message). 0 = pin within the supported +/-1 minor skew."""
    m = _KUBECTL_RE.search(ci_text)
    if not m:
        return 1, "Could not extract the kubectl pin from .kubectl-setup in .gitlab-ci.yml"
    kmaj, kmin = int(m.group(1)), int(m.group(2))

    m2 = _K3S_RE.search(cm_text)
    if not m2:
        return 1, "Could not extract k3s_version from versions-configmap.yaml"
    smaj, smin = int(m2.group(1)), int(m2.group(2))

    prefix = f"CI kubectl pin: v{kmaj}.{kmin}.x / cluster k3s_version: v{smaj}.{smin}.x\n"
    if kmaj != smaj or abs(kmin - smin) > 1:
        return 1, prefix + (
            f"kubectl pin v{kmaj}.{kmin} is outside Kubernetes' supported +/-1 minor "
            f"skew of the cluster (k3s v{smaj}.{smin}) — bump the kubectl version + "
            f"sha256 in .kubectl-setup (.gitlab-ci.yml)."
        )
    return 0, prefix + "kubectl pin is within the supported +/-1 minor skew of k3s_version."


def main(argv: list[str]) -> int:
    ci_path = Path(argv[1]).resolve() if len(argv) > 1 else CI_YAML
    cm_path = Path(argv[2]).resolve() if len(argv) > 2 else VERSIONS_CM
    code, message = check(ci_path.read_text(), cm_path.read_text())
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
