#!/usr/bin/env python3
"""Assert KubeletImageGCIneffective fires at the kubelet's own image-gc high
watermark, set in `k3s_kubelet_args` and restated in the alert's expr and prose.
Exit 0 clean, 1 drifted, 2 the gate could not inspect its subject.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

REPO = Path(__file__).resolve().parent.parent

K3S_VARS = Path("ansible/inventories/prod/group_vars/k3s.yml")
RULES = Path("kubernetes/infrastructure/observability/rules/storage.yaml")
ALERTMANAGER = Path(
    "kubernetes/infrastructure/observability/kube-prometheus-stack/alertmanager-config.yaml"
)
ALERT = "KubeletImageGCIneffective"

# `image-gc-high-threshold=70` in a k3s_kubelet_args entry.
_KUBELET_ARG = re.compile(r"^image-gc-high-threshold=(\d+)$")
# The trailing comparison of the alert expr, e.g. `... > 70`.
_EXPR_THRESHOLD = re.compile(r">\s*(\d+(?:\.\d+)?)\s*$")
# A percentage attached to an `image-gc-high-threshold` mention, in both
# spellings the rules file uses: `70% image-gc-high-threshold` in the comment and
# `image-gc-high-threshold (70%)` in the description.
_PROSE_THRESHOLD = re.compile(
    r"(?:(\d+)\s*%\s*image-gc-high-threshold"
    r"|image-gc-high-threshold\s*\(\s*(\d+)\s*%\s*\))"
)
# Both prose sites must keep their number, so a half-done bump is still caught.
_MIN_PROSE_MENTIONS = 2
# `alertname="DiskUsageWarning"` in an inhibit rule's matcher list.
_MATCHER_ALERTNAME = re.compile(r'alertname\s*=\s*"([^"]+)"')
# The ExternalSecret body is a Go template; a value stands in so it parses.
_GO_TEMPLATE = re.compile(r"\{\{.*?\}\}", re.DOTALL)


class GateError(Exception):
    """The gate could not inspect its subject — exit 2, never a violation."""


def _load_yaml(path: Path):
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except OSError as exc:
        raise GateError(f"{path}: unreadable: {exc}") from exc
    except yaml.YAMLError as exc:
        raise GateError(f"{path}: unparseable YAML: {exc}") from exc


def kubelet_threshold(k3s_vars: Path) -> int:
    """The image-gc-high-threshold percentage the kubelet is started with."""
    doc = _load_yaml(k3s_vars) or {}
    args = doc.get("k3s_kubelet_args")
    if not args:
        raise GateError(f"{k3s_vars}: no k3s_kubelet_args entries")
    for arg in args:
        match = _KUBELET_ARG.match(str(arg).strip())
        if match:
            return int(match.group(1))
    raise GateError(f"{k3s_vars}: k3s_kubelet_args sets no image-gc-high-threshold")


def alert_expr_threshold(rules: Path, alert: str = ALERT) -> float:
    """The right-hand side of one alert's expr comparison."""
    doc = _load_yaml(rules) or {}
    for group in (doc.get("spec") or {}).get("groups") or []:
        for rule in group.get("rules") or []:
            if rule.get("alert") != alert:
                continue
            match = _EXPR_THRESHOLD.search(str(rule.get("expr", "")).strip())
            if not match:
                raise GateError(f"{rules}: {alert} expr has no trailing `> <number>` comparison")
            return float(match.group(1))
    raise GateError(f"{rules}: no {alert} alert")


def inhibitors(config: Path) -> list[str]:
    """Alertnames whose inhibit rule mutes this alert, source side.

    An inhibitor firing at or below this alert's own threshold mutes it for
    good: the generic alert is always already firing when the specific one does.
    """
    doc = _load_yaml(config) or {}
    body = (
        (((doc.get("spec") or {}).get("target") or {}).get("template") or {}).get("data") or {}
    ).get("alertmanager.yaml")
    if not body:
        raise GateError(f"{config}: holds no alertmanager.yaml in its ExternalSecret template")
    try:
        rendered = yaml.safe_load(_GO_TEMPLATE.sub('"x"', body)) or {}
    except yaml.YAMLError as exc:
        raise GateError(f"{config}: alertmanager.yaml is unparseable: {exc}") from exc
    rules = rendered.get("inhibit_rules") or []
    if not isinstance(rules, list) or not rules:
        raise GateError(f"{config}: its alertmanager.yaml declares no inhibit_rules")
    found = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        targets = {
            name
            for matcher in rule.get("target_matchers") or []
            for name in _MATCHER_ALERTNAME.findall(str(matcher))
        }
        if ALERT not in targets:
            continue
        found += [
            name
            for matcher in rule.get("source_matchers") or []
            for name in _MATCHER_ALERTNAME.findall(str(matcher))
        ]
    return sorted(set(found))


def prose_thresholds(rules: Path) -> list[int]:
    """Every percentage spelled next to an `image-gc-high-threshold` mention."""
    try:
        text = rules.read_text(encoding="utf-8")
    except OSError as exc:
        raise GateError(f"{rules}: unreadable: {exc}") from exc
    found = [int(high or low) for high, low in _PROSE_THRESHOLD.findall(text)]
    if len(found) < _MIN_PROSE_MENTIONS:
        raise GateError(
            f"{rules}: found {len(found)} percentage(s) next to an "
            f"image-gc-high-threshold mention, expected at least {_MIN_PROSE_MENTIONS} "
            f"(the comment above {ALERT} and its description)"
        )
    return found


def check(root: Path) -> list[str]:
    expected = kubelet_threshold(root / K3S_VARS)
    problems = []
    expr = alert_expr_threshold(root / RULES)
    if expr != expected:
        problems.append(
            f"{ALERT} fires above {expr:g}% but the kubelet prunes from "
            f"{expected}% ({K3S_VARS}): the alert would fire while image GC is "
            f"still working, or stay silent while it cannot reach its low watermark. "
            f"Update the expr in {RULES}."
        )
    for value in sorted(set(prose_thresholds(root / RULES))):
        if value != expected:
            problems.append(
                f"{RULES} names {value}% as the image-gc-high-threshold, but "
                f"{K3S_VARS} sets {expected}%."
            )
    for source in inhibitors(root / ALERTMANAGER):
        try:
            muting = alert_expr_threshold(root / RULES, source)
        except GateError:
            continue
        if muting <= expr:
            problems.append(
                f"{source} fires above {muting:g}% and inhibits {ALERT} at "
                f"{expr:g}%, so {ALERT} can never notify: the inhibitor is "
                f"already firing whenever it is. Raise {source}'s threshold, "
                f"lower image-gc-high-threshold in {K3S_VARS}, or drop the "
                f"pair in {ALERTMANAGER}."
            )
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="KubeletImageGCIneffective must track the kubelet image-gc high watermark.",
    )
    parser.add_argument("--repo-root", default=REPO, type=Path)
    args = parser.parse_args(argv)
    try:
        problems = check(args.repo_root)
    except GateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if problems:
        print(f"ERROR: {ALERT} has drifted from the kubelet image-gc high watermark:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"{ALERT} tracks the kubelet image-gc-high-threshold in {K3S_VARS}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
