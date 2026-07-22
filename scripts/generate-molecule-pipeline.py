#!/usr/bin/env python3
"""generate-molecule-pipeline.py - Emit a targeted molecule child pipeline.

On a merge-request pipeline the full molecule matrix (~30 role/scenario jobs +
5 integration stacks) is wasteful: an MR usually touches a handful of roles.
This script computes the set of molecule scenarios + integration tests actually
*affected* by the MR's changed files and emits a GitLab child-pipeline YAML that
runs only those. On `main` the parent pipeline still runs the full static matrix
(that wiring lives in .gitlab-ci.yml, not here).

Inputs (how the changed-file list is obtained, first match wins):
  1. --changed-files-from FILE   read newline-separated paths (FILE may be "-" = stdin)
  2. a base SHA (positional, or --diff-base): run
        git diff --name-only <base>...HEAD
     — matches CI_MERGE_REQUEST_DIFF_BASE_SHA semantics; three-dot compares
     HEAD against the merge-base.
  3. stdin, when it is not a TTY: read newline-separated paths.
CI usage:
    python3 scripts/generate-molecule-pipeline.py "$CI_MERGE_REQUEST_DIFF_BASE_SHA" -o molecule-child.yml
Local usage:
    printf '%s\n' ansible/roles/plex/tasks/install.yml | python3 scripts/generate-molecule-pipeline.py
    python3 scripts/generate-molecule-pipeline.py --print-graph   # inspect the derived deps

Single source of truth:
  * The scenario universe is the molecule-tests / integration-tests parallel:matrix
    in .gitlab-ci.yml (read-only) — the SAME matrix the
    molecule-matrix-coverage-check enforces. It is never re-hardcoded here.
  * The role dependency map is DERIVED from the repo, not guessed:
      - meta/main.yml `dependencies:` (consumer depends on each dep)
      - include_role / import_role in a role's PRODUCTION dirs (everything under
        the role except molecule/) — the wrapping relationships
        (e.g. zfs_exporter/unbound_exporter/adguard_sync wrap prometheus_exporter;
         immich/immich_ml/nextcloud include compose_app; compose_app includes
         docker_engine; alloy_host/docker_engine/gitlab/plex include apt_signed_repo;
         base/adguard_home include resolv_conf; gitlab/immich/k3s/nextcloud include
         zvol_mount; nas_storage/node_exporter_host/smtp_relay include
         textfile_collector).
    A change to a depended-on (provider) role selects EVERY dependent (consumer)
    role's scenarios, transitively.
  * The integration-test -> roles map is DERIVED by scanning each stack's
    molecule/ tree for include_role/import_role names. A stack is selected when
    a changed role is one it exercises directly OR via that role's providers.

Determinism + safety (a coverage bug must fail loud, never silently under-select):
  * A changed path under ansible/roles/<name>/ where <name> is not in the matrix
    raises CoverageError (same philosophy as molecule-matrix-coverage-check).
  * An unparseable / empty molecule matrix raises.
  * A selected role with no matrix scenarios raises.
  * When in doubt we select MORE, never fewer: any global-trigger file selects
    EVERYTHING; the direct role->scenario mapping is always a floor.

Inventory / non-role ansible paths:
  Verified against the tree (2026-07): NO role molecule scenario references
  ansible/inventories/prod — role scenarios pin their own values via molecule.yml
  inline group_vars, so a group_vars/all.yml override does NOT reach a role
  scenario (role defaults live in the role). The 5 integration-test converges DO
  `vars_files: ../../../../inventories/prod/group_vars/all.yml`, so they consume
  all.yml. The inventory-consumer map is derived by scanning scenarios for
  inventory-file references, so today a change to group_vars/all.yml selects the
  integration tests (only) and nothing else under ansible/ (playbooks/,
  host_vars/, hosts.yml, other group_vars, ansible.cfg) selects any scenario.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - dependency guard mirrors sibling scripts
    sys.exit("PyYAML required: pip install pyyaml (or brew install python && pip3 install pyyaml)")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
CI_FILE = REPO / ".gitlab-ci.yml"
ROLES_DIR = REPO / "ansible" / "roles"
INTEGRATION_DIR = REPO / "ansible" / "integration-tests"

# Path (repo-relative) the parent extracts .molecule-base + the molecule /
# integration job bodies into; the emitted child `include: local:`s it and the
# child jobs `extends:` the hidden templates below. Parameterized so a move is a
# one-line edit.
MOLECULE_JOBS_INCLUDE = ".gitlab/ci/molecule-jobs.gitlab-ci.yml"
# Hidden-template job names the child extends (provided by the include file).
# They carry the script + `extends: .molecule-base` (+ integration timeout) but
# NO parallel:matrix and NO rules, so the child supplies the narrowed matrix and
# runs unconditionally in the child pipeline.
MOLECULE_JOB_EXTENDS = ".molecule-test-job"
INTEGRATION_JOB_EXTENDS = ".integration-test-job"

# The no-op job emitted when nothing is affected (a GitLab trigger job fails on
# an empty child pipeline, so we always emit at least one trivially-green job).
NOOP_JOB_NAME = "molecule-none-affected"
# Pinned image for the no-op job (matches test-aggregate's alpine pin).
NOOP_IMAGE = "alpine:3.23"

# Files/prefixes that force the FULL matrix (any change -> EVERYTHING): the
# shared molecule base/prepare, the galaxy requirements both suites install, the
# two molecule CI image contexts, the CI file that defines the matrix, the shared
# in-job retry wrapper, and this generator itself (its selection logic changing
# means the narrowing can't be trusted — run everything).
GLOBAL_TRIGGER_FILES = frozenset({
    "ansible/requirements.yml",
    # Top-level pip pins bake into the molecule CI image both suites run in.
    "requirements.txt",
    ".gitlab-ci.yml",
    # The shared job templates every molecule/integration job extends — a
    # template-only change must re-run everything, not emit a no-op child.
    ".gitlab/ci/molecule-jobs.gitlab-ci.yml",
    "scripts/molecule-retry.sh",
    "scripts/generate-molecule-pipeline.py",
})
GLOBAL_TRIGGER_PREFIXES = (
    "ansible/molecule/",
    # Sole playbook input of any scenario (k3s/default verify include_tasks's
    # maintenance/_reboot-if-needed.yml). Global rather than a k3s-only map so
    # a future scenario consuming another maintenance playbook can't rot the
    # narrowing — over-select on rare maintenance-playbook edits instead.
    "ansible/playbooks/maintenance/",
    "docker/molecule-test/",
    "docker/molecule-ci/",
)

# include_role / import_role — matched on the final dotted component so
# ansible.builtin.include_role, ansible.legacy.import_role, etc. all count.
_INCLUDE_ROLE_KEYS = frozenset({"include_role", "import_role"})

# Sentinel: a change under ansible/integration-tests/_shared/ (the shared prepare
# every stack references) selects ALL integration tests.
_ALL_INTEGRATION = object()


class CoverageError(RuntimeError):
    """A changed role/test has no matrix entry — a coverage bug, fail loud."""


# ---------------------------------------------------------------------------
# YAML loading (tolerant of GitLab custom tags such as !reference)
# ---------------------------------------------------------------------------

def _tag_passthrough(loader, tag_suffix, node):
    """Preserve custom-tagged nodes structurally (mirrors
    check-molecule-matrix-coverage.sh) so a !reference near the matrix doesn't
    collapse the parse. Returns only scalars/sequences/mappings — it never
    constructs arbitrary Python types — so yaml.safe_load stays safe. We do not
    resolve !reference semantics."""
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


# Make the SafeLoader tolerant of GitLab custom tags (e.g. !reference), same
# pattern as scripts/check-molecule-matrix-coverage.sh. This only adds
# structural passthrough for `!`-tagged nodes; yaml.safe_load still refuses
# !!python/object and other arbitrary-type tags.
yaml.SafeLoader.add_multi_constructor("!", _tag_passthrough)


def _load_yaml(path: Path):
    """Parse a YAML file with yaml.safe_load; None when absent/unreadable.

    Malformed YAML RAISES (fail-loud): silently skipping a parse error could
    drop include_role/dependency edges from the derived graph and under-select
    tests — the exact silent-coverage-loss this script's safety rules forbid.
    """
    try:
        with path.open() as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise CoverageError(f"{path}: YAML parse failed: {e}") from e
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Matrix parsing (single source of truth = .gitlab-ci.yml)
# ---------------------------------------------------------------------------

def parse_molecule_matrix(ci_path: Path = CI_FILE) -> tuple[dict[str, list[str]], list[str]]:
    """Parse the molecule-tests / integration-tests parallel:matrix from the CI file.

    Returns (role_scenarios, integration_tests) where role_scenarios maps a role
    to its list of scenarios (sorted, unique) and integration_tests is the sorted
    list of stack names. Raises RuntimeError if either matrix is missing or
    malformed — a matrix we cannot parse must fail loudly, not silently emit an
    under-selected pipeline.
    """
    ci = _load_yaml(ci_path)
    if not isinstance(ci, dict):
        raise RuntimeError(f"could not parse {ci_path} as a YAML mapping")

    def _matrix(job_name: str) -> list:
        job = ci.get(job_name)
        if not isinstance(job, dict):
            raise RuntimeError(f"{ci_path}: job {job_name!r} missing or not a mapping")
        parallel = job.get("parallel")
        if not isinstance(parallel, dict):
            raise RuntimeError(f"{ci_path}: {job_name}.parallel missing or not a mapping")
        matrix = parallel.get("matrix")
        if not isinstance(matrix, list) or not matrix:
            raise RuntimeError(f"{ci_path}: {job_name}.parallel.matrix missing or empty")
        return matrix

    role_scenarios: dict[str, set[str]] = defaultdict(set)
    for entry in _matrix("molecule-tests"):
        if not isinstance(entry, dict):
            continue
        role = entry.get("ROLE")
        scenario = entry.get("SCENARIO")
        if isinstance(role, str) and isinstance(scenario, str):
            role_scenarios[role].add(scenario)
    if not role_scenarios:
        raise RuntimeError(f"{ci_path}: molecule-tests matrix yielded no ROLE/SCENARIO pairs")

    integration: set[str] = set()
    for entry in _matrix("integration-tests"):
        if not isinstance(entry, dict):
            continue
        tests = entry.get("TEST")
        if isinstance(tests, list):
            integration.update(t for t in tests if isinstance(t, str))
        elif isinstance(tests, str):
            integration.add(tests)
    if not integration:
        raise RuntimeError(f"{ci_path}: integration-tests matrix yielded no TEST names")

    return ({r: sorted(s) for r, s in role_scenarios.items()}, sorted(integration))


# ---------------------------------------------------------------------------
# Dependency-graph derivation (from the repo, not hardcoded)
# ---------------------------------------------------------------------------


def _yaml_files(root: Path):
    """Every YAML file under root — both extensions, so a dependency moved into
    a .yaml file can never silently drop out of the derived graph."""
    yield from root.rglob("*.yml")
    yield from root.rglob("*.yaml")

def _collect_include_role_names(node, out: set[str]) -> None:
    """Recursively collect literal include_role/import_role `name:` values.

    Walks the parsed YAML structure so a task's own `name:` (a sibling key, not
    the include's) is never mistaken for the included role — a plain grep -A
    conflates them. Templated names ({{ ... }}) are skipped: they are not a
    static role reference we can resolve.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if (
                isinstance(key, str)
                and key.split(".")[-1] in _INCLUDE_ROLE_KEYS
                and isinstance(value, dict)
            ):
                name = value.get("name")
                if isinstance(name, str) and "{{" not in name:
                    out.add(name.strip())
            _collect_include_role_names(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_include_role_names(item, out)


def _meta_dependencies(meta_path: Path) -> set[str]:
    """Role names from a role's meta/main.yml `dependencies:` (str or {role/name})."""
    deps: set[str] = set()
    data = _load_yaml(meta_path)
    if not isinstance(data, dict):
        return deps
    for item in data.get("dependencies") or []:
        if isinstance(item, str):
            deps.add(item)
        elif isinstance(item, dict):
            name = item.get("role") or item.get("name")
            if isinstance(name, str):
                deps.add(name)
    return deps


def build_role_graph(
    roles_dir: Path = ROLES_DIR, known_roles: set[str] | None = None
) -> dict[str, set[str]]:
    """Map consumer_role -> set(provider roles it depends on).

    Scans each role's meta dependencies + include_role/import_role usages in its
    PRODUCTION dirs (everything under the role except molecule/, which is test
    scaffolding). Providers are filtered to known on-disk roles so templated /
    non-role includes are ignored. Self-edges are dropped.
    """
    if known_roles is None:
        known_roles = {p.name for p in roles_dir.iterdir() if p.is_dir()}
    graph: dict[str, set[str]] = {}
    for role_dir in sorted(roles_dir.iterdir()):
        if not role_dir.is_dir():
            continue
        role = role_dir.name
        providers: set[str] = set()
        meta = role_dir / "meta" / "main.yml"
        if meta.is_file():
            providers |= _meta_dependencies(meta)
        for yml in _yaml_files(role_dir):
            if "/molecule/" in yml.as_posix():
                continue
            _collect_include_role_names(_load_yaml(yml), providers)
        providers = {p for p in providers if p in known_roles and p != role}
        if providers:
            graph[role] = providers
    return graph


def build_integration_map(
    it_dir: Path = INTEGRATION_DIR, known_roles: set[str] | None = None
) -> dict[str, set[str]]:
    """Map integration-test stack -> set(roles it exercises directly).

    Derived by scanning every YAML under the stack's molecule/ tree for
    include_role/import_role names (the converge applies them). Filtered to known
    roles. The shared `_shared/` dir is not a stack.
    """
    if known_roles is None and ROLES_DIR.is_dir():
        known_roles = {p.name for p in ROLES_DIR.iterdir() if p.is_dir()}
    known_roles = known_roles or set()
    mapping: dict[str, set[str]] = {}
    if not it_dir.is_dir():
        return mapping
    for stack_dir in sorted(it_dir.iterdir()):
        if not stack_dir.is_dir() or stack_dir.name == "_shared":
            continue
        molecule = stack_dir / "molecule"
        if not molecule.is_dir():
            continue
        roles: set[str] = set()
        for yml in _yaml_files(molecule):
            _collect_include_role_names(_load_yaml(yml), roles)
        mapping[stack_dir.name] = {r for r in roles if r in known_roles}
    return mapping


# Match an inventory-file reference anywhere in a scenario file (e.g. a
# vars_files entry "../../../../inventories/prod/group_vars/all.yml").
_INVENTORY_REF_RE = re.compile(r"[A-Za-z0-9_./-]*inventories/[A-Za-z0-9_./-]+\.ya?ml")


def build_inventory_consumers(
    repo: Path = REPO,
) -> dict[str, set[tuple[str, str]]]:
    """Map repo-relative inventory file -> set of selectors that consume it.

    A selector is ("role", <role>) or ("integration", <stack>). Derived by
    scanning every molecule scenario file (role + integration) for references to
    files under inventories/, resolved relative to the referencing file. Today
    only the integration converges reference inventories/prod/group_vars/all.yml;
    role scenarios reference none (verified) — so this map is what makes a
    group_vars/all.yml change select the integration tests and nothing else.
    """
    consumers: dict[str, set[tuple[str, str]]] = defaultdict(set)

    def _scan(scenario_file: Path, selector: tuple[str, str]) -> None:
        try:
            text = scenario_file.read_text()
        except OSError:
            return
        base = scenario_file.parent
        for token in _INVENTORY_REF_RE.findall(text):
            if token.startswith("ansible/") or token.startswith("/"):
                resolved = Path(os.path.normpath(token))
            else:
                resolved = Path(os.path.normpath(base / token))
            try:
                rel = resolved.resolve().relative_to(repo.resolve()).as_posix()
            except ValueError:
                continue
            consumers[rel].add(selector)

    roles_dir = repo / "ansible" / "roles"
    if roles_dir.is_dir():
        for scenario in roles_dir.glob("*/molecule/*"):
            if not scenario.is_dir():
                continue
            role = scenario.parent.parent.name
            for yml in _yaml_files(scenario):
                _scan(yml, ("role", role))

    it_dir = repo / "ansible" / "integration-tests"
    if it_dir.is_dir():
        for stack in it_dir.iterdir():
            if not stack.is_dir() or stack.name == "_shared":
                continue
            for yml in _yaml_files(stack / "molecule"):
                _scan(yml, ("integration", stack.name))

    return dict(consumers)


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------

def is_global_trigger(path: str) -> bool:
    """True if a change to `path` forces the full matrix."""
    if path in GLOBAL_TRIGGER_FILES:
        return True
    return any(path.startswith(prefix) for prefix in GLOBAL_TRIGGER_PREFIXES)


def classify_role_path(path: str, matrix_roles: set[str]) -> str | None:
    """Return the role a changed path belongs to, or None if it isn't a role path.

    Raises CoverageError for ansible/roles/<name>/... where <name> is not in the
    molecule matrix (a role missing from the matrix is a coverage bug).
    """
    parts = path.split("/")
    if len(parts) < 4 or parts[0] != "ansible" or parts[1] != "roles":
        # len<4 covers a file directly under ansible/roles/ (e.g. README.md) —
        # not scoped to any role.
        return None
    name = parts[2]
    if name in matrix_roles:
        return name
    raise CoverageError(
        f"changed path {path!r} is under ansible/roles/{name}/, but {name!r} has "
        "no entry in the molecule-tests matrix in .gitlab-ci.yml. Add its "
        "ROLE/SCENARIO entry (and a molecule scenario) — a role missing from the "
        "matrix would ship untested."
    )


def classify_integration_path(path: str, integration_tests: set[str]):
    """Classify a change under ansible/integration-tests/.

    Returns the _ALL_INTEGRATION sentinel for a change under _shared/ (the shared
    prepare every stack references), the stack name for a change under a known
    stack, or None if the path isn't under integration-tests. Raises CoverageError
    for an unknown stack dir.
    """
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "ansible" or parts[1] != "integration-tests":
        return None
    name = parts[2]
    if name == "_shared":
        return _ALL_INTEGRATION
    if len(parts) < 4:
        # a file directly under integration-tests/ — not a stack
        return None
    if name in integration_tests:
        return name
    raise CoverageError(
        f"changed path {path!r} is under ansible/integration-tests/{name}/, but "
        f"{name!r} has no entry in the integration-tests matrix in .gitlab-ci.yml."
    )


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

class Selection:
    """Result of computing the affected set."""

    def __init__(self, scenarios: set[tuple[str, str]], integration: set[str], full: bool = False):
        self.scenarios = scenarios          # {(role, scenario)}
        self.integration = integration      # {stack}
        self.full = full                    # a global trigger selected everything

    @property
    def empty(self) -> bool:
        return not self.scenarios and not self.integration


def _closure(seed: set[str], adjacency: dict[str, set[str]]) -> set[str]:
    """Transitive closure of `seed` under `adjacency` (includes the seed)."""
    result = set(seed)
    stack = list(seed)
    while stack:
        for nxt in adjacency.get(stack.pop(), ()):  # noqa: PLW2901
            if nxt not in result:
                result.add(nxt)
                stack.append(nxt)
    return result


def compute_affected(
    changed_files: list[str],
    *,
    matrix: dict[str, list[str]],
    integration_tests: list[str],
    role_deps: dict[str, set[str]],
    integration_map: dict[str, set[str]],
    inventory_consumers: dict[str, set[tuple[str, str]]],
) -> Selection:
    """Compute the affected scenarios + integration tests for a changed-file set.

    Pure over its inputs (the derived data structures), so it is unit-testable
    with synthetic graphs as well as against the real repo.
    """
    matrix_roles = set(matrix)
    all_scenarios = {(r, s) for r, scen in matrix.items() for s in scen}
    integration_set = set(integration_tests)

    # Global trigger -> everything (first, so it short-circuits role/coverage checks).
    if any(is_global_trigger(f) for f in changed_files):
        return Selection(set(all_scenarios), set(integration_set), full=True)

    # consumer graph -> reverse (provider -> direct consumers) for molecule fan-out.
    consumers_of: dict[str, set[str]] = defaultdict(set)
    for consumer, providers in role_deps.items():
        for provider in providers:
            consumers_of[provider].add(consumer)

    changed_roles: set[str] = set()
    integration_selected: set[str] = set()

    for path in changed_files:
        role = classify_role_path(path, matrix_roles)
        if role is not None:
            changed_roles.add(role)
            continue
        stack = classify_integration_path(path, integration_set)
        if stack is _ALL_INTEGRATION:
            integration_selected |= integration_set
            continue
        if isinstance(stack, str):
            integration_selected.add(stack)
            continue
        for selector_kind, selector_name in inventory_consumers.get(path, ()):
            if selector_kind == "role":
                changed_roles.add(selector_name)
            else:
                integration_selected.add(selector_name)

    # Molecule scenarios: every role that transitively CONSUMES a changed role
    # (provider) must run — walk the reverse graph. The changed roles themselves
    # are the floor.
    selected_roles = _closure(changed_roles, consumers_of)
    scenarios: set[tuple[str, str]] = set()
    for role in selected_roles:
        scen = matrix.get(role)
        if not scen:
            raise CoverageError(
                f"role {role!r} was selected but has no molecule-tests scenarios in "
                ".gitlab-ci.yml (coverage bug: refusing to silently drop it)."
            )
        for scenario in scen:
            scenarios.add((role, scenario))

    # Integration tests: a stack runs when a changed role is one it exercises
    # directly OR via that role's providers (its converge applies the role, which
    # pulls in its dependencies). Closing each stack's role set under the provider
    # graph captures the transitive coupling.
    for stack in integration_set:
        exercised = _closure(integration_map.get(stack, set()), role_deps)
        if exercised & changed_roles:
            integration_selected.add(stack)

    return Selection(scenarios, integration_selected)


def select(changed_files: list[str], *, repo: Path = REPO) -> Selection:
    """Build the derived data from `repo` and compute the affected set."""
    matrix, integration_tests = parse_molecule_matrix(repo / ".gitlab-ci.yml")
    roles_dir = repo / "ansible" / "roles"
    known_roles = {p.name for p in roles_dir.iterdir() if p.is_dir()} if roles_dir.is_dir() else set()
    role_deps = build_role_graph(roles_dir, known_roles=known_roles)
    integration_map = build_integration_map(repo / "ansible" / "integration-tests", known_roles=known_roles)
    inventory_consumers = build_inventory_consumers(repo)
    return compute_affected(
        changed_files,
        matrix=matrix,
        integration_tests=integration_tests,
        role_deps=role_deps,
        integration_map=integration_map,
        inventory_consumers=inventory_consumers,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_HEADER = (
    "---\n"
    "# AUTO-GENERATED by scripts/generate-molecule-pipeline.py — targeted molecule\n"
    "# child pipeline for this MR's diff. Do NOT edit by hand.\n"
)


# Every emitted job carries explicit `rules: [when: always]`. Without ANY
# rules/only/except, GitLab applies the LEGACY implicit `only: branches, tags`
# — and a child of a merge-request pipeline runs on the MR ref (neither a
# branch nor a tag), so every rule-less job is filtered out and the trigger
# fails with "the resulting pipeline would have been empty" (observed on
# pipeline 775). Explicit rules disable that legacy default.
_ALWAYS_RULES = [{"when": "always"}]


def render_child_pipeline(selection: Selection) -> str:
    """Render the GitLab child-pipeline YAML for a Selection."""
    if selection.empty:
        # A GitLab trigger job fails on an empty pipeline, so emit one
        # trivially-green job. Self-contained (no include needed).
        doc = {
            "stages": ["test"],
            NOOP_JOB_NAME: {
                "stage": "test",
                "image": NOOP_IMAGE,
                "script": [
                    "echo 'No molecule scenarios or integration tests affected by "
                    "this MR diff; nothing to run.'"
                ],
                "rules": [dict(r) for r in _ALWAYS_RULES],
            },
        }
        return _HEADER + yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)

    doc: dict = {
        "include": [{"local": MOLECULE_JOBS_INCLUDE}],
        "stages": ["test"],
    }
    if selection.scenarios:
        doc["molecule-tests"] = {
            "extends": MOLECULE_JOB_EXTENDS,
            "rules": [dict(r) for r in _ALWAYS_RULES],
            "parallel": {
                "matrix": [
                    {"ROLE": role, "SCENARIO": scenario}
                    for role, scenario in sorted(selection.scenarios)
                ]
            },
        }
    if selection.integration:
        doc["integration-tests"] = {
            "extends": INTEGRATION_JOB_EXTENDS,
            "rules": [dict(r) for r in _ALWAYS_RULES],
            "parallel": {"matrix": [{"TEST": sorted(selection.integration)}]},
        }
    return _HEADER + yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _git_changed_files(base: str, repo: Path = REPO) -> list[str]:
    """`git diff --name-only <base>...HEAD` (three-dot: HEAD vs the merge-base)."""
    out = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", f"{base}...HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _read_paths(stream) -> list[str]:
    return [line.strip() for line in stream if line.strip()]


def _print_graph(repo: Path = REPO) -> int:
    """Diagnostic: print the derived matrix + dependency graph + integration map."""
    matrix, integration_tests = parse_molecule_matrix(repo / ".gitlab-ci.yml")
    roles_dir = repo / "ansible" / "roles"
    known_roles = {p.name for p in roles_dir.iterdir() if p.is_dir()}
    role_deps = build_role_graph(roles_dir, known_roles=known_roles)
    integration_map = build_integration_map(repo / "ansible" / "integration-tests", known_roles=known_roles)
    inv = build_inventory_consumers(repo)

    print(f"# molecule matrix: {sum(len(v) for v in matrix.values())} scenarios across {len(matrix)} roles")
    print(f"# integration stacks: {', '.join(integration_tests)}\n")
    print("# consumer -> providers (a change to a provider selects the consumer):")
    for consumer in sorted(role_deps):
        print(f"  {consumer} -> {sorted(role_deps[consumer])}")
    consumers_of: dict[str, set[str]] = defaultdict(set)
    for consumer, providers in role_deps.items():
        for provider in providers:
            consumers_of[provider].add(consumer)
    print("\n# provider -> direct consumers (reverse edges that drive fan-out):")
    for provider in sorted(consumers_of):
        print(f"  {provider} -> {sorted(consumers_of[provider])}")
    print("\n# integration stack -> roles exercised (direct):")
    for stack in sorted(integration_map):
        print(f"  {stack} -> {sorted(integration_map[stack])}")
    print("\n# inventory file -> consumers:")
    for path in sorted(inv):
        print(f"  {path} -> {sorted(inv[path])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base", nargs="?", help="base SHA/ref; runs `git diff --name-only <base>...HEAD`")
    parser.add_argument("--diff-base", help="base SHA/ref (alternative to the positional arg)")
    parser.add_argument(
        "--changed-files-from",
        metavar="FILE",
        help='read newline-separated changed paths from FILE ("-" = stdin)',
    )
    parser.add_argument("-o", "--output", help="write the child pipeline YAML here (default: stdout)")
    parser.add_argument("--print-graph", action="store_true", help="print the derived dependency graph and exit")
    args = parser.parse_args(argv)

    if args.print_graph:
        return _print_graph()

    base = args.diff_base or args.base
    if args.changed_files_from:
        if args.changed_files_from == "-":
            changed = _read_paths(sys.stdin)
        else:
            with open(args.changed_files_from) as f:
                changed = _read_paths(f)
    elif base:
        try:
            changed = _git_changed_files(base)
        except subprocess.CalledProcessError as e:
            print(f"ERROR: git diff against {base!r} failed: {e.stderr.strip()}", file=sys.stderr)
            return 1
    elif not sys.stdin.isatty():
        changed = _read_paths(sys.stdin)
    else:
        parser.error("no changed files: pass a base SHA, --changed-files-from, or pipe paths on stdin")

    try:
        selection = select(changed)
    except CoverageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    rendered = render_child_pipeline(selection)
    if args.output:
        Path(args.output).write_text(rendered)
        n_scen, n_int = len(selection.scenarios), len(selection.integration)
        tag = "FULL matrix" if selection.full else ("none affected" if selection.empty else f"{n_scen} scenario(s) + {n_int} integration test(s)")
        print(f"Wrote {args.output} ({tag})", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
