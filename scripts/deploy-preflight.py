#!/usr/bin/env python3
"""Offline dry run of what the Ansible deploy jobs will do.

Resolves every `ansible-playbook` call in the deploy jobs and asserts the
playbook exists, its `--tags` reach a real task and its `--limit` hits a play.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

# PYTHONSAFEPATH, and any invocation that is not a direct script run, keeps this
# directory off sys.path, so the companion module is placed there explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from ci_playbook_invocations import load_ci, parse_invocations, script_lines  # noqa: E402
except ImportError:  # pragma: no cover - environment guard
    sys.exit("ci_playbook_invocations.py must sit next to this script")

# Jobs built on one of these are the deploy invocations this gate inspects.
DEPLOY_BASES = {".deploy-base", ".maintenance-base"}

# `--list-tasks` prints one `TAGS: [a, b]` per selected task. `always` tasks are
# printed for ANY selection, so output alone proves nothing -- the requested tag
# must appear in a task's own tag list.
TASK_TAGS = re.compile(r"TAGS: \[([^\]]*)\]")
# `--list-hosts` prints one `hosts (N):` per play. All zero means the limit and
# the plays' own `hosts:` patterns do not intersect.
PLAY_HOSTS = re.compile(r"hosts \((\d+)\):")


def deploy_jobs(pipeline: dict) -> dict[str, dict]:
    """Jobs whose `extends` names a deploy base. `extends` is a string OR a list;
    a job naming several parents must not slip the preflight."""
    jobs = {}
    for name, job in pipeline.items():
        if not isinstance(job, dict) or name.startswith("."):
            continue
        parents = job.get("extends") or []
        if isinstance(parents, str):
            parents = [parents]
        if DEPLOY_BASES.intersection(parents):
            jobs[name] = job
    return jobs


def check(pipeline: dict, ansible_dir: Path) -> list[str]:
    """Return one message per finding; empty means clean."""
    failures: list[str] = []
    playbooks: set[str] = set()
    selections = 0
    host_checks = 0

    for name, job in deploy_jobs(pipeline).items():
        # A `!reference [.anchor, script]` block is expanded, so a referenced
        # invocation is inspected rather than read as an empty script.
        unresolved: list[list] = []
        script = "\n".join(script_lines(job, pipeline, unresolved=unresolved))
        # A script inherited through `extends` is not on the job, so the parser
        # sees nothing to count and the job would skip the preflight in silence.
        if not script.strip():
            failures.append(
                f"{name}: has no `script` of its own, so its commands come from "
                "a deploy base through `extends` and are invisible here. Spell "
                "the invocation in the job, or in a `!reference` to an anchor "
                "inside .gitlab-ci.yml."
            )
        if unresolved:
            failures.append(
                f"{name}: script `!reference` {unresolved} points outside "
                ".gitlab-ci.yml, so those lines were not inspected"
            )
        parsed = parse_invocations(script)
        # Refuse to shrink silently. A shape the parser cannot read costs
        # coverage with no output at all, while the summary line still prints a
        # count -- the failure this gate exists to prevent.
        written = script.count("ansible-playbook")
        if len(parsed) < written:
            failures.append(
                f"{name}: parsed {len(parsed)} of {written} `ansible-playbook` "
                "invocation(s) -- this job is written in a shape the preflight "
                "parser does not understand, so its playbook/tag checks were "
                "skipped. Fix ci_playbook_invocations.py rather than the job."
            )
        for call in parsed:
            playbook = call["playbook"]
            if not (ansible_dir / playbook).is_file():
                failures.append(f"{name}: playbook {ansible_dir}/{playbook} does not exist")
                continue
            playbooks.add(playbook)
            # `--skip-tags` excludes rather than selects, so the shared parser
            # keeps it out of `tags`: an inert value there is not a no-op step.
            tags = sorted(call["tags"] or ())
            inventory_argv = ["-i", call["inventory"]] if call["inventory"] else []
            limit = call["limit"]
            limit_argv = ["--limit", limit] if limit else []
            scope = f"--limit {limit}" if limit else "its own hosts: patterns"
            host_checks += 1
            hosts = subprocess.run(
                ["ansible-playbook", *inventory_argv, playbook,
                 *limit_argv, "--list-hosts"],
                cwd=ansible_dir, capture_output=True, text=True,
            )
            counts = [int(n) for n in PLAY_HOSTS.findall(hosts.stdout)]
            if hosts.returncode != 0:
                failures.append(
                    f"{name}: --list-hosts failed for {playbook} ({scope})\n"
                    f"{hosts.stderr.strip()}"
                )
            elif not counts:
                failures.append(
                    f"{name}: --list-hosts for {playbook} ({scope}) printed no "
                    "`hosts (N):` line -- the preflight could not read the play "
                    "list, so the limit check was skipped. Fix PLAY_HOSTS rather "
                    "than the job."
                )
            elif not any(counts):
                failures.append(
                    f"{name}: {scope} on {playbook} matches NO host in any play "
                    "-- that deploy step is a silent no-op"
                )
            for tag in tags:
                selections += 1
                proc = subprocess.run(
                    ["ansible-playbook", *inventory_argv, playbook,
                     *limit_argv, "--list-tasks", "--tags", tag],
                    cwd=ansible_dir, capture_output=True, text=True,
                )
                if proc.returncode != 0:
                    failures.append(
                        f"{name}: --list-tasks failed for {playbook} --tags {tag}\n"
                        f"{proc.stderr.strip()}"
                    )
                    continue
                selected = any(
                    tag in [t.strip() for t in m.group(1).split(",")]
                    for m in TASK_TAGS.finditer(proc.stdout)
                )
                if selected:
                    print(f"OK   {name}: {playbook} --tags {tag}")
                else:
                    failures.append(
                        f"{name}: `--tags {tag}` on {playbook} selects NO task -- "
                        "that deploy step is a silent no-op"
                    )

    if not playbooks:
        failures.append(
            "preflight resolved 0 playbooks -- candidate-job selection or "
            "invocation parsing is no longer inspecting the deploy jobs"
        )
    if selections == 0:
        failures.append(
            "preflight resolved 0 tag selections -- the silent-no-op tag "
            "guard inspected nothing"
        )
    if host_checks == 0:
        failures.append(
            "preflight resolved 0 host checks -- the empty-limit guard "
            "inspected nothing"
        )
    print(
        f"\n{len(playbooks)} playbook(s), {selections} tag selection(s), "
        f"{host_checks} host check(s)"
    )
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=Path(argv[0]).name, description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pipeline", type=Path, default=Path(".gitlab-ci.yml"),
                        help="pipeline file holding the deploy jobs")
    parser.add_argument("--ansible-dir", type=Path, default=Path("ansible"),
                        help="directory the playbook paths are relative to")
    args = parser.parse_args(argv[1:])

    try:
        pipeline = load_ci(args.pipeline)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        # An unreadable input is an operator error, not a finding.
        print(f"ERROR: could not read {args.pipeline}: {exc}", file=sys.stderr)
        return 2

    # Every finding below comes from an `ansible-playbook` probe. Without the
    # binary the probes raise, and exit 1 would read as a silent-no-op finding.
    if shutil.which("ansible-playbook") is None:
        print("ERROR: ansible-playbook is not on PATH — the preflight could not run",
              file=sys.stderr)
        return 2

    try:
        failures = check(pipeline, args.ansible_dir)
    except OSError as exc:
        print(f"ERROR: could not run ansible-playbook: {exc}", file=sys.stderr)
        return 2
    if failures:
        print("", file=sys.stderr)
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
