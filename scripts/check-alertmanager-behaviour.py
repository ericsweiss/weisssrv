#!/usr/bin/env python3
"""Assert what the Alertmanager config DOES, not just that it parses.

`amtool check-config` (run by scripts/lint-prometheus-config.sh) is a syntax
gate: a route reorder that silences the Watchdog dead-man's switch, a matcher
that misroutes a critical, a redundant `equal:` label that makes an inhibit pair
dedup nothing, and a one-sided alertname rename between an inhibit source and
target all pass it green.

Extracts the config + rules with scripts/extract-prometheus-config.py, then:
  * resolves each ROUTE_CASE with `amtool config routes test` and compares the
    receiver actually reached;
  * checks every inhibit rule for parseable matchers, a redundant `equal:`
    label, and alertnames that no longer exist. EVERY member of a regex
    alternation is checked, not just "at least one survives": the storm-control
    rule pins 14 target alertnames, so 13 could be typos while the pair still
    looked bound. Upstream chart alerts are invisible to the extractor, so they
    are named explicitly in UPSTREAM_ALERTS.

Requires amtool on PATH. Run from the repo root; exit 0 clean, 1 on a finding,
2 on an operator error.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

REPO = Path(__file__).resolve().parent.parent
EXTRACT = REPO / "scripts" / "extract-prometheus-config.py"

# (expected receiver, alert labels). amtool prints every matching receiver in
# tree order and only the first delivers, so the first line is what is asserted.
# One case per receiver, plus the ordering the config itself calls load-bearing:
# Watchdog carries severity=none and MUST beat the severity=none null route.
ROUTE_CASES = [
    ("watchdog-heartbeat", ["alertname=Watchdog", "severity=none"]),
    ("null", ["alertname=InfoInhibitor", "severity=none"]),
    ("email-and-discord", ["alertname=EtcdQuorumAtRisk", "severity=critical"]),
    ("discord-default", ["alertname=DiskUsageWarning", "severity=warning"]),
    # severity=info matches no child route and falls through to the root.
    ("discord-default", ["alertname=SwapCleanStoppedGuests", "severity=info"]),
    ("discord-default", ["alertname=SomeUnlabelledAlert"]),
]

# ROUTE_CASE alertnames that deliberately name no rule. Only one: the
# fall-through case, whose whole point is that an alert matching no child route
# still reaches the root receiver.
SYNTHETIC_ROUTE_ALERTS = {"SomeUnlabelledAlert"}

MATCHER_RE = re.compile(r'^\s*(\w+)\s*(=~|!~|!=|=)\s*"?(.*?)"?\s*$')

# Alerts shipped by the kube-prometheus-stack chart's own rule groups. The
# extractor only sees this repo's PrometheusRules, so a chart alertname named in
# an inhibit rule would otherwise read as dead. Entries here are a claim that the
# name exists upstream AND is not in defaultRules.disabled — verify against the
# chart before adding one, because a typo'd entry recreates the blind spot.
UPSTREAM_ALERTS = {
    "InfoInhibitor",
    "KubeAggregatedAPIDown",
    "KubeContainerWaiting",
    "KubeDaemonSetMisScheduled",
    "KubeDaemonSetRolloutStuck",
    "KubeDeploymentReplicasMismatch",
    "KubePodNotReady",
    "KubeStatefulSetReplicasMismatch",
    "NodeSystemSaturation",
    "Watchdog",
}


def _extract(work: Path) -> tuple[Path, Path]:
    rules, am = work / "rules.yaml", work / "alertmanager.yaml"
    for args in (["rules", str(rules)], ["alertmanager", str(am)]):
        run = subprocess.run(
            [sys.executable, str(EXTRACT), *args], capture_output=True, text=True
        )
        if run.returncode:
            sys.exit(f"ERROR: extraction failed:\n{run.stdout}{run.stderr}")
    return am, rules


def check_routes(am_config: Path) -> list[str]:
    problems = []
    for want, labels in ROUTE_CASES:
        run = subprocess.run(
            ["amtool", "config", "routes", "test", f"--config.file={am_config}", *labels],
            capture_output=True,
            text=True,
        )
        got = " ".join((run.stdout + run.stderr).split())
        if not got.startswith(want):
            problems.append(f"[{' '.join(labels)}] expected receiver {want!r}, resolved {got!r}")
    return problems


def _parse_matchers(matchers, index: int, side: str, problems: list[str]) -> dict:
    out = {}
    for raw in matchers or []:
        m = MATCHER_RE.match(raw)
        if not m:
            problems.append(f"rule {index}: unparseable {side} matcher {raw!r}")
            continue
        out[m.group(1)] = (m.group(2), m.group(3))
    return out


def _exact_alertnames(parsed: dict) -> tuple[list[str], str | None]:
    """(alertnames a matcher set pins exactly, why it could not be validated).

    Exactly one of the two is meaningful. `=` pins one name; `=~` pins a set
    only when the regex is a plain alternation of names — the shape every rule
    here uses. Any OTHER regex returns a REASON, not an empty list: silently
    returning [] meant zero names to check, zero problems reported, and the gate
    still printing "N inhibit rule(s) well-formed" — the exact blind spot the
    per-member check was hardened to close. Anchors are a no-op in Alertmanager
    (matchers are already fully anchored), so `^(A|B)$` reads as validatable to
    a human while this parser rejected it.
    """
    op, val = parsed.get("alertname", (None, None))
    if op == "=":
        return [val], None
    if op == "=~":
        if re.fullmatch(r"[A-Za-z0-9_|]+", val or ""):
            return val.split("|"), None
        return [], (
            f'alertname regex "{val}" is not a plain alternation of names, so '
            f"its members cannot be checked against the rules corpus. Write it "
            f'as "AlertOne|AlertTwo" (Alertmanager anchors matchers itself, so '
            f"^…$ is redundant), or split the rule."
        )
    return [], None


def _known_alertnames(rules_file: Path) -> set[str]:
    rules_doc = yaml.safe_load(rules_file.read_text()) or {}
    return {
        r["alert"]
        for g in rules_doc.get("groups") or []
        for r in g.get("rules") or []
        if "alert" in r
    }


def check_route_case_alertnames(known: set[str]) -> list[str]:
    """Every ROUTE_CASE alertname must still name a real rule.

    amtool resolves a route from LABELS alone: it never asks whether the alert
    exists, so renaming a rule leaves its ROUTE_CASE resolving the same
    receiver, green, and testing nothing. This is what makes the claim in
    prometheus-rule-tests/availability.test.yaml true — that a rename of e.g.
    EtcdQuorumAtRisk has to break something loudly.
    """
    problems = []
    for _want, labels in ROUTE_CASES:
        for label in labels:
            key, _, value = label.partition("=")
            if key != "alertname" or value in SYNTHETIC_ROUTE_ALERTS:
                continue
            if value not in known and value not in UPSTREAM_ALERTS:
                problems.append(
                    f"ROUTE_CASE alertname {value!r} matches no rule in "
                    f"observability/rules/ and is not in UPSTREAM_ALERTS. The "
                    f"route case still passes (amtool resolves labels, not "
                    f"rules), so it is now asserting nothing — renamed, deleted, "
                    f"or a typo?"
                )
    return problems


def check_inhibits(am_config: Path, known: set[str]) -> list[str]:
    cfg = yaml.safe_load(am_config.read_text()) or {}
    inhibits = cfg.get("inhibit_rules") or []
    if not inhibits:
        return ["no inhibit_rules found in the Alertmanager config"]

    problems: list[str] = []
    for i, rule in enumerate(inhibits):
        src = _parse_matchers(rule.get("source_matchers"), i, "source", problems)
        tgt = _parse_matchers(rule.get("target_matchers"), i, "target", problems)
        if not src or not tgt:
            problems.append(f"rule {i}: both source_matchers and target_matchers are required")
            continue
        for label in rule.get("equal") or []:
            s, t = src.get(label), tgt.get(label)
            if s and t and s[0] == "=" and t[0] == "=" and s[1] == t[1]:
                problems.append(
                    f"rule {i}: equal:[{label}] is redundant — both matcher sets already "
                    f'pin it to "{s[1]}", so the pair dedups nothing'
                )
        # Every alertname a matcher pins must resolve — to one of ours or to a
        # declared upstream alert. Checked per alternation MEMBER: requiring only
        # that one member survives lets the rest rot into inert matchers, which
        # is precisely how a 14-name target list hides a typo.
        defined = known | UPSTREAM_ALERTS
        for side, (names, unvalidatable) in (
            ("source", _exact_alertnames(src)),
            ("target", _exact_alertnames(tgt)),
        ):
            if unvalidatable:
                problems.append(f"rule {i}: {side} {unvalidatable}")
                continue
            dead = [n for n in names if n not in defined]
            if dead:
                problems.append(
                    f"rule {i}: {side} alertname(s) {dead} match no rule in "
                    f"observability/rules/ and are not in UPSTREAM_ALERTS — "
                    f"renamed, deleted, or a typo? An unmatched name in an "
                    f"alternation is an inert matcher, not an error at runtime."
                )
    return problems


def main() -> int:
    if shutil.which("amtool") is None:
        print("ERROR: amtool not found on PATH", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        am_config, rules_file = _extract(Path(tmp))
        known = _known_alertnames(rules_file)
        route_problems = check_routes(am_config) + check_route_case_alertnames(known)
        inhibit_problems = check_inhibits(am_config, known)
        inhibit_count = len(yaml.safe_load(am_config.read_text()).get("inhibit_rules") or [])

    if route_problems:
        print("ERROR: Alertmanager routing does not match the expected receivers:", file=sys.stderr)
        for p in route_problems:
            print(f"  - {p}", file=sys.stderr)
    if inhibit_problems:
        print("ERROR: inhibit rule problems:", file=sys.stderr)
        for p in inhibit_problems:
            print(f"  - {p}", file=sys.stderr)
    if route_problems or inhibit_problems:
        return 1

    print(
        f"Alertmanager behaviour OK: {len(ROUTE_CASES)} route case(s) resolve as expected, "
        f"{inhibit_count} inhibit rule(s) well-formed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
