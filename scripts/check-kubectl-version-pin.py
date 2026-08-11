#!/usr/bin/env python3
"""Assert the CI kubectl pin stays within +/-1 minor of the cluster's k3s_version.

A hardcoded kubectl pin in .gitlab-ci.yml has no drift guard: a k3s_version bump
merged into the cluster-versions ConfigMap can push the pin to 2 minors of skew
with nothing catching it, at which point the kubectl calls in the deploy /
maintenance jobs can break. This asserts the pin stays within Kubernetes'
supported +/-1 minor skew of the cluster's k3s_version.

  check-kubectl-version-pin.py                       # repo defaults
  check-kubectl-version-pin.py <ci_yaml> <configmap> # explicit paths

The defaults assume the conventional layout (`.gitlab-ci.yml` +
`kubernetes/infrastructure/sources/versions-configmap.yaml` under the repo root);
pass both paths when the consumer's layout differs. $CI_FILE (repo-relative or
absolute, same name as the molecule scripts) retargets the first default, e.g.
a repo whose kubectl pin lives in a GitHub Actions workflow.

The check itself is forge-neutral: it regexes a `dl.k8s.io` download pin out of
whatever text it is given.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import re

REPO = Path(__file__).resolve().parent.parent
CI_YAML = REPO / (os.environ.get("CI_FILE") or ".gitlab-ci.yml")
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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=Path(argv[0]).name,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "ci_yaml", nargs="?", type=Path, default=CI_YAML,
        help=f"CI file carrying the kubectl pin (default: {CI_YAML})",
    )
    parser.add_argument(
        "configmap", nargs="?", type=Path, default=VERSIONS_CM,
        help=f"versions ConfigMap carrying k3s_version (default: {VERSIONS_CM})",
    )
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        ci_text = args.ci_yaml.read_text()
        cm_text = args.configmap.read_text()
    except (OSError, UnicodeDecodeError) as e:
        # An absent/unreadable input is an operator error (wrong path, bad
        # permissions), not a skew finding — exit 2 so CI can tell the two
        # apart, and print one line rather than a traceback.
        print(f"ERROR: could not read input file: {e}", file=sys.stderr)
        return 2
    code, message = check(ci_text, cm_text)
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
