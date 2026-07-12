#!/usr/bin/env python3
"""Extract the kube-prometheus-stack alert rules and Alertmanager config into
standalone files that `promtool check rules` / `amtool check-config` can lint.

The ~50 custom alert exprs live in additionalPrometheusRulesMap inside the
HelmRelease .spec.values, and the Alertmanager config lives in an ExternalSecret
template — neither is reachable by kubeconform/flux-lint (schema-only), so a bad
PromQL expr or malformed route only surfaces post-merge when the operator's
admission webhook rejects the rendered CR. This shifts that check left.

Subcommands (run from repo root):
  rules <out>         merge every additionalPrometheusRulesMap group into a
                      single promtool-format {groups: [...]} file.
  alertmanager <out>  extract the alertmanager.yaml template and render the ESO
                      `{{ .x | quote }}` secret placeholders with dummy values
                      (amtool validates structure, not secret contents).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

REPO = Path(__file__).resolve().parent.parent
OBS = REPO / "kubernetes" / "infrastructure" / "observability" / "kube-prometheus-stack"
RELEASE = OBS / "release.yaml"
AM_CONFIG = OBS / "alertmanager-config.yaml"

# Dummy values for the ESO template placeholders. amtool checks structure, not
# whether a webhook URL is real; URLs still need to parse as URLs.
DUMMY_VALUES = {
    "discordWebhookUrl": "https://discord.example/api/webhooks/1/abc",
    "healthchecksPingUrl": "https://hc-ping.example/00000000-0000-0000-0000-000000000000",
    "smtpPassword": "dummy-smtp-password",
}
_PLACEHOLDER_RE = re.compile(r"\{\{-?\s*\.(\w+)\s*(?:\|\s*quote\s*)?-?\}\}")


def _load(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def extract_rules(out: Path) -> int:
    doc = _load(RELEASE)
    rules_map = (doc.get("spec", {}).get("values", {}) or {}).get(
        "additionalPrometheusRulesMap"
    )
    if not rules_map:
        print("ERROR: additionalPrometheusRulesMap not found in release.yaml", file=sys.stderr)
        return 1
    groups: list = []
    for entry in rules_map.values():
        groups.extend((entry or {}).get("groups", []) or [])
    if not groups:
        print("ERROR: no rule groups extracted", file=sys.stderr)
        return 1
    out.write_text(yaml.safe_dump({"groups": groups}, default_flow_style=False, sort_keys=False))
    print(f"Wrote {len(groups)} rule group(s) to {out}")
    return 0


def _render_placeholders(match: re.Match) -> str:
    var = match.group(1)
    return '"' + DUMMY_VALUES.get(var, "dummy") + '"'


def extract_alertmanager(out: Path) -> int:
    doc = _load(AM_CONFIG)
    template = (
        doc.get("spec", {})
        .get("target", {})
        .get("template", {})
        .get("data", {})
        .get("alertmanager.yaml")
    )
    if not template:
        print("ERROR: alertmanager.yaml template not found in alertmanager-config.yaml", file=sys.stderr)
        return 1
    rendered = _PLACEHOLDER_RE.sub(_render_placeholders, template)
    if "{{" in rendered:
        print("ERROR: unrendered template expression remains after substitution", file=sys.stderr)
        print(rendered, file=sys.stderr)
        return 1
    out.write_text(rendered)
    print(f"Wrote rendered Alertmanager config to {out}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("rules", "alertmanager"):
        print(f"Usage: {argv[0]} <rules|alertmanager> <out>", file=sys.stderr)
        return 2
    out = Path(argv[2])
    if argv[1] == "rules":
        return extract_rules(out)
    return extract_alertmanager(out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
