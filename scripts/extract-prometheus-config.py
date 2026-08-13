#!/usr/bin/env python3
"""Extract the custom alert rules and the Alertmanager config into standalone
files that `promtool check rules` / `amtool check-config` can lint.

Alert exprs live in `additionalPrometheusRulesMap` inside the kube-prometheus-
stack HelmRelease AND in standalone PrometheusRule manifests under
observability/rules/; the Alertmanager config lives in an ExternalSecret
template. None of it is reachable by kubeconform/flux-lint (schema-only), so a
bad PromQL expr or a malformed route only surfaces post-merge.

LOCAL FORK of the weisssrv-lib script: the library copy reads the HelmRelease
alone. `rules` here is the union of both sources, so the check works before,
during and after the split (a group defined twice is caught by promtool's
duplicate-name check).

  extract-prometheus-config.py rules <out> [--release PATH] [--rules-dir PATH]
  extract-prometheus-config.py alertmanager <out> [--am-config PATH] [--dummy K=V]

`--dummy` (repeatable) overrides the value substituted for an ESO
`{{ .name | quote }}` placeholder. An unset name renders as a dummy https URL
when it ends in `url` (amtool parses webhook/API targets as URLs) and as the
literal `dummy` otherwise.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

DEFAULT_OBS = Path("kubernetes/infrastructure/observability/kube-prometheus-stack")
DEFAULT_RELEASE = DEFAULT_OBS / "release.yaml"
DEFAULT_AM_CONFIG = DEFAULT_OBS / "alertmanager-config.yaml"
DEFAULT_RULES_DIR = Path("kubernetes/infrastructure/observability/rules")

DUMMY_URL = "https://dummy.example/00000000-0000-0000-0000-000000000000"
DUMMY_SCALAR = "dummy"
_PLACEHOLDER_RE = re.compile(r"\{\{-?\s*\.(\w+)\s*(?:\|\s*quote\s*)?-?\}\}")


def _load(path: Path) -> dict:
    """Parse a single-document YAML file, always returning a mapping — an empty
    or explicitly-null document yields {} so callers can chain `.get()`."""
    with path.open() as f:
        doc = yaml.safe_load(f)
    return doc if isinstance(doc, dict) else {}


def dummy_for(name: str, overrides: dict[str, str] | None = None) -> str:
    if overrides and name in overrides:
        return overrides[name]
    return DUMMY_URL if name.lower().endswith("url") else DUMMY_SCALAR


def _release_groups(release: Path) -> list:
    """Rule groups declared inline in the HelmRelease values."""
    values = ((_load(release).get("spec") or {}).get("values") or {})
    rules_map = values.get("additionalPrometheusRulesMap") or {}
    groups: list = []
    for entry in rules_map.values():
        groups.extend((entry or {}).get("groups") or [])
    return groups


def _prometheusrule_groups(rules_dir: Path) -> list:
    """Rule groups declared as standalone PrometheusRule manifests."""
    groups: list = []
    if not rules_dir.is_dir():
        return groups
    for path in sorted(rules_dir.glob("*.yaml")):
        with path.open() as f:
            for doc in yaml.safe_load_all(f):
                if isinstance(doc, dict) and doc.get("kind") == "PrometheusRule":
                    groups.extend((doc.get("spec") or {}).get("groups") or [])
    return groups


def extract_rules(
    out: Path,
    release: Path = DEFAULT_RELEASE,
    rules_dir: Path = DEFAULT_RULES_DIR,
) -> int:
    groups = _release_groups(release) + _prometheusrule_groups(rules_dir)
    if not groups:
        print(
            f"ERROR: no rule groups found in {release} "
            f"(additionalPrometheusRulesMap) or {rules_dir}/*.yaml",
            file=sys.stderr,
        )
        return 1
    out.write_text(yaml.safe_dump({"groups": groups}, default_flow_style=False, sort_keys=False))
    print(f"Wrote {len(groups)} rule group(s) to {out}")
    return 0


def render_placeholders(template: str, overrides: dict[str, str] | None = None) -> str:
    return _PLACEHOLDER_RE.sub(
        lambda m: '"' + dummy_for(m.group(1), overrides) + '"', template
    )


def extract_alertmanager(
    out: Path,
    am_config: Path = DEFAULT_AM_CONFIG,
    overrides: dict[str, str] | None = None,
) -> int:
    doc = _load(am_config)
    # `(x or {})` at every hop: a key present with an explicit null value makes
    # .get(k, {}) return None, not the default.
    template = (
        (((doc.get("spec") or {}).get("target") or {}).get("template") or {}).get("data") or {}
    ).get("alertmanager.yaml")
    if not template:
        print(f"ERROR: alertmanager.yaml template not found in {am_config}", file=sys.stderr)
        return 1
    rendered = render_placeholders(template, overrides)
    if "{{" in rendered:
        print("ERROR: unrendered template expression remains after substitution", file=sys.stderr)
        print(rendered, file=sys.stderr)
        return 1
    out.write_text(rendered)
    print(f"Wrote rendered Alertmanager config to {out}")
    return 0


def _parse_dummy(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"--dummy expects NAME=VALUE, got {item!r}")
        out[key] = value
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=Path(argv[0]).name,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("subcommand", choices=("rules", "alertmanager"))
    parser.add_argument("out", type=Path)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--rules-dir", type=Path, default=DEFAULT_RULES_DIR)
    parser.add_argument("--am-config", type=Path, default=DEFAULT_AM_CONFIG)
    parser.add_argument("--dummy", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args(argv[1:])
    if args.subcommand == "rules":
        return extract_rules(args.out, args.release, args.rules_dir)
    return extract_alertmanager(args.out, args.am_config, _parse_dummy(args.dummy))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
