"""An extra var a pipeline passes with `-e` arrives as a string.

ansible-core refuses a str-derived `when:`, so a conditional naming such a var
ends its filter chain in `| bool` or compares it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from script_loader import load_path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
ANSIBLE = "ansible"

# Every file that hands an `ansible-playbook` call an `-e name=value` pair.
CALLER_GLOBS = (
    ".gitlab-ci.yml",
    ".gitlab/ci/*.yml",
    "Taskfile.yml",
    "taskfiles/*.yml",
    "scripts/*.sh",
)

# `-e name=value`, in the spaced, joined and attached spellings. A `-e` whose
# value is a file or a JSON blob carries no bare name and is ignored.
EXTRA_VAR = re.compile(r"(?:^|\s)(?:-e|--extra-vars)[=\s]?['\"]?([A-Za-z_]\w*)=")

INCLUDE_KEYS = frozenset(
    {
        "ansible.builtin.include_tasks",
        "ansible.builtin.import_tasks",
        "ansible.builtin.include_playbook",
        "ansible.builtin.import_playbook",
        "include_tasks",
        "import_tasks",
        "import_playbook",
    }
)

# A reference that yields a real boolean on its own, so `| bool` is redundant.
COMPARISON = re.compile(r"^\s*(==|!=|<=|>=|<|>|\bin\b|\bis\b|\bnot\s+in\b)")


def _invocations(text: str) -> list[dict]:
    """The `ansible-playbook` calls in one caller file, via the shared parser."""
    parser = load_path(SCRIPTS / "ci_playbook_invocations.py", register=True)
    return parser.parse_invocations(text)


def extra_vars_by_playbook(repo: Path = REPO) -> dict[str, set[str]]:
    """playbook path (as written) -> the `-e` names its callers pass it."""
    found: dict[str, set[str]] = {}
    for glob in CALLER_GLOBS:
        for caller in sorted(repo.glob(glob)):
            if not caller.is_file():
                continue
            for call in _invocations(caller.read_text(encoding="utf-8")):
                names = set(EXTRA_VAR.findall(call["argv"]))
                if names:
                    found.setdefault(call["playbook"], set()).update(names)
    return found


def _resolve(playbook: str, repo: Path, base: Path | None = None) -> Path | None:
    """A playbook or include path as written, against its own dir then ansible/."""
    candidates = []
    if base is not None:
        candidates.append(base / playbook)
    candidates += [repo / ANSIBLE / playbook, repo / playbook]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _walk(node, key_wanted: str):
    """Every value stored under `key_wanted` anywhere in a parsed YAML tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == key_wanted:
                yield value
            yield from _walk(value, key_wanted)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key_wanted)


def reachable_files(playbook: str, repo: Path = REPO) -> list[Path]:
    """The playbook plus every task file or playbook it transitively pulls in."""
    start = _resolve(playbook, repo)
    if start is None:
        return []
    seen: dict[Path, None] = {start: None}
    queue = [start]
    while queue:
        current = queue.pop()
        try:
            doc = yaml.safe_load(current.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        for key in INCLUDE_KEYS:
            for value in _walk(doc, key):
                target = value.get("file") if isinstance(value, dict) else value
                if not isinstance(target, str) or "{{" in target:
                    continue
                resolved = _resolve(target, repo, base=current.parent)
                if resolved is not None and resolved not in seen:
                    seen[resolved] = None
                    queue.append(resolved)
    return list(seen)


def _conditions(doc) -> list[str]:
    """Every `when:` expression in a file, one string per clause."""
    clauses = []
    for value in _walk(doc, "when"):
        for clause in value if isinstance(value, list) else [value]:
            if isinstance(clause, str):
                clauses.append(clause)
    return clauses


def _is_booled(expression: str, name: str) -> bool:
    """Does every reference to `name` in one clause yield a real boolean?"""
    pattern = re.compile(
        r"\b" + re.escape(name) + r"\b((?:\s*\|\s*\w+(?:\([^()]*\))?)*)"
    )
    for match in pattern.finditer(expression):
        chain = [f.strip() for f in match.group(1).split("|") if f.strip()]
        filters = {f.split("(")[0] for f in chain}
        if "bool" in filters:
            continue
        if COMPARISON.match(expression[match.end():]):
            continue
        return False
    return True


def unbooled_conditionals(repo: Path = REPO) -> list[tuple[str, str, str]]:
    """(file, extra-var, clause) for every conditional missing its `| bool`."""
    offenders = []
    for playbook, names in sorted(extra_vars_by_playbook(repo).items()):
        for path in reachable_files(playbook, repo):
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError:
                continue
            for clause in _conditions(doc):
                for name in sorted(names):
                    if re.search(r"\b" + re.escape(name) + r"\b", clause):
                        if not _is_booled(clause, name):
                            rel = path.relative_to(repo).as_posix()
                            offenders.append((rel, name, clause))
    return offenders


class TestDiscovery:
    """Guards against a vacuous pass: the walk must find real work to check."""

    def test_the_pipeline_extra_vars_are_found(self):
        found = extra_vars_by_playbook()
        names = {n for names in found.values() for n in names}
        assert {"postflight_exercise_sync", "auto_reboot"} <= names, found

    def test_an_included_task_file_is_reached(self):
        reached = {
            p.relative_to(REPO).as_posix()
            for p in reachable_files("playbooks/maintenance/update-packages.yml")
        }
        assert "ansible/playbooks/maintenance/_reboot-if-needed.yml" in reached, reached


def test_every_extra_var_conditional_is_booled():
    offenders = unbooled_conditionals()
    assert not offenders, (
        "a `when:` reads a `-e` extra var without `| bool`, so ansible-core "
        "refuses the str-derived result: "
        + "; ".join(f"{f}: {name} in `{clause}`" for f, name, clause in offenders)
    )


BUGGY_PLAYBOOK = """---
- name: Probe
  hosts: all
  tasks:
    - name: Exercise something
      ansible.builtin.command: /bin/true
      when:
        - probe_exercise | default(true)
"""


def _fixture_repo(tmp_path: Path, playbook_body: str) -> Path:
    (tmp_path / "ansible/playbooks").mkdir(parents=True)
    (tmp_path / "ansible/playbooks/probe.yml").write_text(playbook_body)
    (tmp_path / ".gitlab-ci.yml").write_text(
        "verify:\n"
        "  script:\n"
        "    - ansible-playbook -i inventories/prod playbooks/probe.yml"
        " -e probe_exercise=false\n"
    )
    return tmp_path


def test_a_string_typed_conditional_is_reported(tmp_path):
    """Mutation case: the shape that failed deploy-verify-hosts must be caught."""
    offenders = unbooled_conditionals(_fixture_repo(tmp_path, BUGGY_PLAYBOOK))
    assert offenders == [
        (
            "ansible/playbooks/probe.yml",
            "probe_exercise",
            "probe_exercise | default(true)",
        )
    ]


@pytest.mark.parametrize(
    "clause",
    [
        "probe_exercise | default(true) | bool",
        "not (probe_exercise | default(true) | bool)",
        "probe_exercise | default('') == 'yes'",
    ],
)
def test_a_boolean_valued_conditional_passes(tmp_path, clause):
    body = BUGGY_PLAYBOOK.replace("probe_exercise | default(true)", clause)
    assert unbooled_conditionals(_fixture_repo(tmp_path, body)) == []
