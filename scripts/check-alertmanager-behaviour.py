#!/usr/bin/env python3
"""Assert what the Alertmanager config DOES, not just that it parses.

`amtool check-config` is a syntax gate: a route reorder that silences the
Watchdog dead-man's switch, a matcher that misroutes a critical, a redundant
`equal:` label that makes an inhibit pair dedup nothing, and a one-sided
alertname rename between an inhibit source and target all pass it green.

Extracts the config + rules with the consumer's extract-prometheus-config.py,
then:
  * resolves each declared route case with `amtool config routes test` and
    compares the receiver actually reached;
  * checks every inhibit rule for parseable matchers, a redundant `equal:`
    label, and alertnames that no longer exist. EVERY member of a regex
    alternation is checked, not just "at least one survives", so a long target
    list cannot hide a typo. Chart-shipped alerts are invisible to the
    extractor, so they are declared explicitly.

The routing table, the receivers and the upstream alert set are site data and
come from `--config` (see examples/alertmanager-behaviour.example.yaml):

    route_cases:                 # required, non-empty
      - receiver: watchdog-heartbeat
        labels: [alertname=Watchdog, severity=none]
    synthetic_route_alerts: []   # route-case alertnames that name no rule
    upstream_alerts: []          # alertnames shipped by a chart's own rules

Requires amtool on PATH. Exit 0 clean, 1 on a finding, 2 on an operator error.

  check-alertmanager-behaviour.py --config FILE [--repo-root DIR]
                                  [--extract-script PATH]
"""
from __future__ import annotations

import argparse
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

MATCHER_RE = re.compile(r'^\s*(\w+)\s*(=~|!~|!=|=)\s*"?(.*?)"?\s*$')


class Config:
    """The consumer data from --config. A value, not module state."""

    def __init__(self, route_cases, synthetic, upstream):
        self.route_cases = route_cases
        self.synthetic_route_alerts = synthetic
        self.upstream_alerts = upstream


def load_config(path) -> Config:
    """Read and validate a --config file. Raises ValueError on a bad file."""
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: top-level must be a mapping")
    raw_cases = doc.get("route_cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError(f"{path}: `route_cases` must be a non-empty list")
    cases = []
    for case in raw_cases:
        if not isinstance(case, dict) or not case.get("receiver") or not case.get("labels"):
            raise ValueError(f"{path}: route case needs receiver + labels: {case!r}")
        cases.append((str(case["receiver"]), [str(label) for label in case["labels"]]))
    return Config(
        cases,
        {str(a) for a in doc.get("synthetic_route_alerts") or []},
        {str(a) for a in doc.get("upstream_alerts") or []},
    )


class ExtractionError(RuntimeError):
    """The consumer's extractor failed — an operator error (exit 2)."""


def _extract(work: Path, extract_script: Path, repo_root: Path) -> tuple[Path, Path]:
    """Run the consumer's extractor, FROM the consumer's repo root.

    The extractor resolves its manifest defaults against the process cwd, so
    without `cwd=` the `--repo-root` seam only locates the script and the run
    dies anywhere but the consumer root.
    """
    rules, am = work / "rules.yaml", work / "alertmanager.yaml"
    for args in (["rules", str(rules)], ["alertmanager", str(am)]):
        run = subprocess.run(
            [sys.executable, str(extract_script), *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        if run.returncode:
            raise ExtractionError(f"extraction failed:\n{run.stdout}{run.stderr}")
    return am, rules


def check_routes(am_config: Path, route_cases) -> list[str]:
    problems = []
    for want, labels in route_cases:
        run = subprocess.run(
            ["amtool", "config", "routes", "test", f"--config.file={am_config}", *labels],
            capture_output=True,
            text=True,
        )
        got = " ".join((run.stdout + run.stderr).split())
        # amtool can print several matching receivers in tree order, so only the
        # FIRST token is the resolution. Compare it exactly: a prefix test passes
        # `critical-page` for an expected `critical`, which is the misroute the
        # gate exists to catch.
        tokens = got.split()
        first = tokens[0] if tokens else ""
        if first != want:
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
    only when the regex is a plain alternation of names. Any OTHER regex returns
    a REASON, not an empty list — an empty list is zero names to check, zero
    problems reported, and the gate still printing "N inhibit rule(s)
    well-formed". Anchors are a no-op in Alertmanager (matchers are already
    fully anchored), so `^(A|B)$` reads as validatable to a human but is
    rejected here.
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


def _load_extracted(path: Path, what: str) -> dict:
    """Parse one extracted file, or raise ExtractionError (exit 2).

    The extractor copies the alertmanager.yaml block scalar out of the
    ExternalSecret verbatim without parsing it, so a YAML typo INSIDE that block
    leaves the outer manifest valid and the extractor green, and the malformed
    body arrives here. Unparsed, it reaches yaml.safe_load mid-check as an
    uncaught traceback on exit 1 — the code that means "routing drifted".
    """
    try:
        # The handle, not the text, so PyYAML's mark names the file.
        with path.open() as fh:
            doc = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        raise ExtractionError(f"extracted {what} ({path}) is not parseable YAML: {exc}") from exc
    if doc is None:
        raise ExtractionError(f"extracted {what} ({path}) is empty")
    if not isinstance(doc, dict):
        raise ExtractionError(
            f"extracted {what} ({path}) is a {type(doc).__name__}, not a YAML mapping"
        )
    return doc


def _known_alertnames(rules_doc: dict) -> set[str]:
    return {
        r["alert"]
        for g in rules_doc.get("groups") or []
        for r in g.get("rules") or []
        if "alert" in r
    }


def check_route_case_alertnames(known: set[str], config: Config) -> list[str]:
    """Every route-case alertname must still name a real rule.

    amtool resolves a route from LABELS alone: it never asks whether the alert
    exists, so renaming a rule leaves its route case resolving the same
    receiver, green, and testing nothing.
    """
    problems = []
    for _want, labels in config.route_cases:
        for label in labels:
            key, _, value = label.partition("=")
            if key != "alertname" or value in config.synthetic_route_alerts:
                continue
            if value not in known and value not in config.upstream_alerts:
                problems.append(
                    f"route case alertname {value!r} matches no extracted rule and is "
                    f"not in upstream_alerts. The route case still passes (amtool "
                    f"resolves labels, not rules), so it is asserting nothing — "
                    f"renamed, deleted, or a typo?"
                )
    return problems


def check_inhibits(am_doc: dict, known: set[str], upstream: set[str]) -> list[str]:
    inhibits = am_doc.get("inhibit_rules") or []
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
        defined = known | upstream
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
                    f"rule {i}: {side} alertname(s) {dead} match no extracted rule and "
                    f"are not in upstream_alerts — renamed, deleted, or a typo? "
                    f"An unmatched name in an alternation is an inert matcher, "
                    f"not an error at runtime."
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assert what the Alertmanager config does, not just that it parses.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", required=True, help="route cases + alert sets (see docstring)")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--extract-script",
        type=Path,
        help="extract-prometheus-config.py (default: <repo-root>/scripts/)",
    )
    args = parser.parse_args(argv)

    # Resolved ONCE, against the caller's cwd, before anything is validated: the
    # extractor runs with `cwd=repo_root`, so a relative path handed straight
    # through would be re-resolved against the CHILD's cwd and double its prefix.
    repo_root = args.repo_root.resolve()
    extract = (
        args.extract_script or repo_root / "scripts" / "extract-prometheus-config.py"
    ).resolve()
    if not repo_root.is_dir():
        print(f"ERROR: --repo-root {repo_root} is not a directory", file=sys.stderr)
        return 2
    if not extract.is_file():
        print(f"ERROR: {extract} not found (pass --extract-script)", file=sys.stderr)
        return 2
    if shutil.which("amtool") is None:
        print("ERROR: amtool not found on PATH", file=sys.stderr)
        return 2
    try:
        config = load_config(args.config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        try:
            am_config, rules_file = _extract(Path(tmp), extract, repo_root)
            # Parsed ONCE here so a malformed body is an operator error (exit 2)
            # rather than a traceback from mid-check on exit 1.
            am_doc = _load_extracted(am_config, "Alertmanager config")
            rules_doc = _load_extracted(rules_file, "rules")
        except ExtractionError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        known = _known_alertnames(rules_doc)
        route_problems = check_routes(am_config, config.route_cases) + check_route_case_alertnames(
            known, config
        )
        inhibit_problems = check_inhibits(am_doc, known, config.upstream_alerts)
        inhibit_count = len(am_doc.get("inhibit_rules") or [])

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
        f"Alertmanager behaviour OK: {len(config.route_cases)} route case(s) resolve as "
        f"expected, {inhibit_count} inhibit rule(s) well-formed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
