#!/usr/bin/env bash
# Guard against the molecule / integration-test CI matrix silently drifting
# from the scenario directories on disk.
#
# Two parallel:matrix blocks in .gitlab-ci.yml enumerate which tests run:
#
#   molecule-tests    — ROLE/SCENARIO pairs, one per ansible/roles/*/molecule/*/
#   integration-tests — a TEST list, one per ansible/integration-tests/*/
#
# A new molecule scenario dir (or a new integration-tests dir) that nobody
# adds to the matrix runs in NO CI job — a brand-new role's tests would
# silently never execute, and the gap is invisible at review time. This check
# fails loudly when a scenario/test exists on disk with no matching matrix
# entry, naming the missing entries and where to add them. It ALSO fails when
# a role under ansible/roles/ has no molecule scenario at all (a role committed
# without molecule/ would otherwise ship permanently untested), unless the role
# is named in the UNTESTED_ROLES allowlist below with a rationale.
#
# Direction is deliberately one-way: we flag on-disk scenarios MISSING from
# the matrix (the dangerous drift — untested code). A matrix entry pointing at
# a now-deleted scenario dir is caught at runtime (molecule errors on a missing
# scenario), so we don't duplicate that here.
#
# Delegates the actual parsing to a small embedded Python program (PyYAML) so
# the matrix is read structurally — a `grep` over job rules would mis-match
# ROLE:/SCENARIO: strings that appear in comments or unrelated blocks.

set -euo pipefail

# Resolve repo root from this script's location so it works from any CWD
# (CI runs from $CI_PROJECT_DIR; local invocations may run from anywhere).
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

cd "$REPO_ROOT"

python3 - <<'PYEOF'
import sys
from pathlib import Path

import yaml


def tag_passthrough(loader, tag_suffix, node):
    # Custom YAML tags (e.g. !reference) appear in .gitlab-ci.yml. Preserve the
    # underlying scalar/sequence/mapping so a tagged node inside (or near) a
    # matrix block keeps its structure instead of collapsing to None and
    # producing a false "missing scenario" failure. We don't resolve GitLab's
    # !reference semantics — only keep the parse structurally intact.
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


yaml.SafeLoader.add_multi_constructor("!", tag_passthrough)

repo = Path(".")
ci_path = repo / ".gitlab-ci.yml"
with ci_path.open() as f:
    ci = yaml.safe_load(f)


def matrix_entries(job_name):
    """Return the list of dicts under <job>.parallel.matrix, or []."""
    job = ci.get(job_name)
    if not isinstance(job, dict):
        return []
    parallel = job.get("parallel", {})
    if not isinstance(parallel, dict):
        return []
    matrix = parallel.get("matrix", [])
    return matrix if isinstance(matrix, list) else []


# molecule-tests: (ROLE, SCENARIO) pairs
# Matrix shape: a list of {ROLE: <name>, SCENARIO: <name>} dicts.
ci_molecule = set()
for entry in matrix_entries("molecule-tests"):
    if not isinstance(entry, dict):
        continue
    role = entry.get("ROLE")
    scenario = entry.get("SCENARIO")
    if isinstance(role, str) and isinstance(scenario, str):
        ci_molecule.add((role, scenario))

# On disk: ansible/roles/<role>/molecule/<scenario>/ (a dir containing a
# molecule.yml is the authoritative marker of a runnable scenario).
disk_molecule = set()
roles_dir = repo / "ansible" / "roles"
if roles_dir.is_dir():
    for scenario_dir in sorted(roles_dir.glob("*/molecule/*")):
        if not scenario_dir.is_dir():
            continue
        if not (scenario_dir / "molecule.yml").is_file():
            continue
        role = scenario_dir.parent.parent.name
        scenario = scenario_dir.name
        disk_molecule.add((role, scenario))

# Roles intentionally shipped without molecule coverage. Empty by design; add a
# role name here only with an inline rationale (mirrors the
# INTENTIONALLY_UNMAPPED_* pattern in check-deploy-coverage.sh).
UNTESTED_ROLES: set[str] = set()

# Roles with NO runnable molecule scenario at all: a new role committed without
# molecule/ would never appear in disk_molecule, so the matrix diff alone can't
# catch it.
untested_roles = []
if roles_dir.is_dir():
    tested_roles = {role for role, _scenario in disk_molecule}
    for role_dir in sorted(roles_dir.iterdir()):
        if not role_dir.is_dir():
            continue
        if role_dir.name in UNTESTED_ROLES:
            continue
        if role_dir.name not in tested_roles:
            untested_roles.append(role_dir.name)

# integration-tests: TEST list
# Matrix shape: a single {TEST: [a, b, ...]} entry (a list of test names).
ci_integration = set()
for entry in matrix_entries("integration-tests"):
    if not isinstance(entry, dict):
        continue
    tests = entry.get("TEST")
    if isinstance(tests, list):
        ci_integration.update(t for t in tests if isinstance(t, str))
    elif isinstance(tests, str):
        ci_integration.add(tests)

# On disk: ansible/integration-tests/<name>/ where <name> contains a
# molecule/ subdir with at least one scenario molecule.yml. The CI job does
# `cd ansible/integration-tests/$TEST && molecule test` (default scenario), so
# the test identifier is the directory name <name>, not the scenario.
disk_integration = set()
it_dir = repo / "ansible" / "integration-tests"
if it_dir.is_dir():
    for d in sorted(it_dir.iterdir()):
        if d.is_dir() and any((d / "molecule").glob("*/molecule.yml")):
            disk_integration.add(d.name)

failed = False

if untested_roles:
    failed = True
    sys.stderr.write(
        "ERROR: role(s) with no molecule scenario (would ship permanently untested):\n\n"
    )
    for role in untested_roles:
        sys.stderr.write(f"  - ansible/roles/{role}/ (no molecule/*/molecule.yml)\n")
    sys.stderr.write(
        "\n  Add a molecule scenario for the role (plus its molecule-tests\n"
        "  matrix entry in .gitlab-ci.yml), or — only with a rationale — name\n"
        "  it in UNTESTED_ROLES in scripts/check-molecule-matrix-coverage.sh.\n\n"
    )

missing_molecule = sorted(disk_molecule - ci_molecule)
if missing_molecule:
    failed = True
    sys.stderr.write(
        "ERROR: molecule scenario(s) on disk with no molecule-tests matrix entry:\n\n"
    )
    for role, scenario in missing_molecule:
        sys.stderr.write(f"  - ansible/roles/{role}/molecule/{scenario}/\n")
    sys.stderr.write(
        "\n  Add a matching entry to the molecule-tests parallel:matrix in\n"
        "  .gitlab-ci.yml:\n"
        "      - ROLE: <role>\n"
        "        SCENARIO: <scenario>\n\n"
    )

missing_integration = sorted(disk_integration - ci_integration)
if missing_integration:
    failed = True
    sys.stderr.write(
        "ERROR: integration-test(s) on disk with no integration-tests matrix entry:\n\n"
    )
    for name in missing_integration:
        sys.stderr.write(f"  - ansible/integration-tests/{name}/\n")
    sys.stderr.write(
        "\n  Add the name under the integration-tests parallel:matrix TEST list\n"
        "  in .gitlab-ci.yml.\n\n"
    )

if failed:
    sys.exit(1)

print(
    f"Molecule matrix covers all {len(disk_molecule)} scenario dir(s); "
    f"integration matrix covers all {len(disk_integration)} test dir(s); "
    f"every role has at least one scenario."
)
PYEOF
