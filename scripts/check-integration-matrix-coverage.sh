#!/usr/bin/env bash
# Fail when an integration-test directory exists with no `parallel:matrix` entry
# in the CI job that runs them — an added suite that silently never runs.
#
# Roles (and their molecule scenarios) ship from the weisssrv.infra collection,
# which gates its own matrix; this covers the half that stays here. The library's
# check-molecule-matrix-coverage.sh does both halves but requires a roles
# directory, which this repo no longer has.
#
# Usage: scripts/check-integration-matrix-coverage.sh
# Env:   CI_FILE (.gitlab-ci.yml), INTEGRATION_DIR (ansible/integration-tests),
#        INTEGRATION_JOB (integration-tests)
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$(cd "$SCRIPT_DIR/.." && pwd)"

CI_FILE="${CI_FILE:-.gitlab-ci.yml}"
INTEGRATION_DIR="${INTEGRATION_DIR:-ansible/integration-tests}"
INTEGRATION_JOB="${INTEGRATION_JOB:-integration-tests}"
export CI_FILE INTEGRATION_DIR INTEGRATION_JOB

python3 - <<'PYEOF'
import os
import sys
from pathlib import Path

import yaml

CI_FILE = os.environ["CI_FILE"]
INTEGRATION_DIR = os.environ["INTEGRATION_DIR"]
INTEGRATION_JOB = os.environ["INTEGRATION_JOB"]


class _CILoader(yaml.SafeLoader):
    """SafeLoader tolerating GitLab's `!reference` tags, subclassed so the
    constructor is not registered on the global SafeLoader."""


def _tag_passthrough(loader, suffix, node):
    # Keep the node's structure: a tagged node near the matrix collapsing to
    # None would read as a missing entry.
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


_CILoader.add_multi_constructor("!", _tag_passthrough)

ci = yaml.load(Path(CI_FILE).read_text(), Loader=_CILoader) or {}
job = ci.get(INTEGRATION_JOB)
if not isinstance(job, dict):
    sys.exit(f"ERROR: {CI_FILE} has no job {INTEGRATION_JOB!r}")

in_ci = set()
for entry in ((job.get("parallel") or {}).get("matrix") or []):
    if not isinstance(entry, dict):
        continue
    tests = entry.get("TEST")
    if isinstance(tests, list):
        in_ci.update(t for t in tests if isinstance(t, str))
    elif isinstance(tests, str):
        in_ci.add(tests)

# A dir holding at least one molecule/<scenario>/molecule.yml is a runnable
# suite; the job does `cd <dir>/$TEST && molecule test`, so the identifier is
# the directory name.
it_dir = Path(INTEGRATION_DIR)
if not it_dir.is_dir():
    sys.exit(f"ERROR: integration directory {INTEGRATION_DIR!r} does not exist")
on_disk = {
    d.name for d in it_dir.iterdir()
    if d.is_dir() and any((d / "molecule").glob("*/molecule.yml"))
}

missing = sorted(on_disk - in_ci)
if missing:
    sys.stderr.write(
        f"ERROR: integration test(s) with no {INTEGRATION_JOB} matrix entry:\n\n"
    )
    for name in missing:
        sys.stderr.write(f"  - {INTEGRATION_DIR}/{name}/\n")
    sys.stderr.write(f"\n  Add the name to the TEST list in {CI_FILE}.\n\n")
    sys.exit(1)

print(f"Integration matrix covers all {len(on_disk)} test dir(s).")
PYEOF
