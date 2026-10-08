"""An extra var a pipeline passes with `-e` arrives as a string.

ansible-core refuses a str-derived `when:`, so a conditional naming such a var
ends its filter chain in `| bool` or compares it.
"""
from __future__ import annotations

import os
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

EXTRA_VAR_FLAGS = frozenset({"-e", "--extra-vars"})
# One `name=value` pair, the only `-e` form carrying a readable variable name.
ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)=")

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

ROLE_KEYS = frozenset(
    {
        "ansible.builtin.include_role",
        "ansible.builtin.import_role",
        "include_role",
        "import_role",
    }
)

COLLECTION = "weisssrv.infra"
COLLECTION_ROLES = Path("ansible_collections/weisssrv/infra/roles")
# ansible honours either spelling, and ansible.cfg points a scratch install at it.
COLLECTIONS_PATH_ENV = ("ANSIBLE_COLLECTIONS_PATH", "ANSIBLE_COLLECTIONS_PATHS")
ROLE_SUBDIRS = ("tasks", "handlers")
ROLE_META = Path("meta/main.yml")

# A reference that yields a real boolean on its own, so `| bool` is redundant.
COMPARISON = re.compile(r"^\s*(==|!=|<=|>=|<|>|\bin\b|\bis\b|\bnot\s+in\b)")


class Uninspectable(RuntimeError):
    """A reachable file or role the walk cannot read, so a pass would be a lie."""


def _invocations(text: str) -> list[dict]:
    """The `ansible-playbook` calls in one caller file, via the shared parser."""
    parser = load_path(SCRIPTS / "ci_playbook_invocations.py", register=True)
    return parser.parse_invocations(text)


def _extra_vars_value(tokens: list[str], index: int) -> tuple[str | None, int]:
    """The first value token of an extra-vars flag, and where to resume."""
    token = tokens[index]
    flag, sep, attached = token.partition("=")
    if sep and flag in EXTRA_VAR_FLAGS:
        return attached, index + 1
    if token in EXTRA_VAR_FLAGS:
        if index + 1 >= len(tokens):
            return None, index + 1
        return tokens[index + 1], index + 2
    if token.startswith("-e") and len(token) > 2:
        return token[2:], index + 1
    return None, index + 1


def extra_var_names(argv: str) -> tuple[set[str], list[str]]:
    """Every `-e` name in one argv, plus the values carrying no name at all."""
    names: set[str] = set()
    unreadable: list[str] = []
    # The shared parser rejoins shlex tokens, so a quoted multi-assignment value
    # arrives as one assignment-shaped token per pair.
    tokens = argv.split()
    index = 0
    while index < len(tokens):
        value, index = _extra_vars_value(tokens, index)
        if value is None:
            continue
        first = ASSIGNMENT.match(value)
        if first is None:
            unreadable.append(value)
            continue
        names.add(first.group(1))
        while index < len(tokens) and (more := ASSIGNMENT.match(tokens[index])):
            names.add(more.group(1))
            index += 1
    return names, unreadable


def _scan_callers(repo: Path) -> tuple[dict[str, set[str]], list[tuple[str, str]]]:
    """playbook -> its callers' `-e` names, plus every value with no name in it."""
    found: dict[str, set[str]] = {}
    unreadable: list[tuple[str, str]] = []
    for glob in CALLER_GLOBS:
        for caller in sorted(repo.glob(glob)):
            if not caller.is_file():
                continue
            rel = caller.relative_to(repo).as_posix()
            for call in _invocations(caller.read_text(encoding="utf-8")):
                names, opaque = extra_var_names(call["argv"])
                if names:
                    found.setdefault(call["playbook"], set()).update(names)
                unreadable += [(rel, value) for value in opaque]
    return found, unreadable


def extra_vars_by_playbook(repo: Path = REPO) -> dict[str, set[str]]:
    """playbook path (as written) -> the `-e` names its callers pass it."""
    return _scan_callers(repo)[0]


def unreadable_extra_vars(repo: Path = REPO) -> list[tuple[str, str]]:
    """(caller, value) for every `-e @file` or JSON blob hiding its names."""
    return _scan_callers(repo)[1]


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


def _collection_roots(repo: Path) -> list[Path]:
    """Every roles directory `weisssrv.infra` may be read from, ansible's order first."""
    bases = [
        Path(entry).expanduser()
        for var in COLLECTIONS_PATH_ENV
        for entry in os.environ.get(var, "").split(os.pathsep)
        if entry
    ]
    bases += [
        repo / ".ansible-home/collections",
        Path.home() / ".ansible/collections",
        Path(os.environ.get("WEISSSRV_LIB_PATH") or repo.parent / "weisssrv-lib"),
    ]
    return [base / COLLECTION_ROLES for base in bases]


def resolve_role(name: str, repo: Path = REPO) -> Path:
    """A role name to its directory; unreadable is a failure, never a skip."""
    candidates = [repo / ANSIBLE / "roles" / name]
    if name.startswith(f"{COLLECTION}."):
        short = name.split(".")[-1]
        candidates += [root / short for root in _collection_roots(repo)]
    for candidate in candidates:
        if any((candidate / part).is_dir() for part in ROLE_SUBDIRS):
            return candidate
        if (candidate / ROLE_META).is_file():
            return candidate
    raise Uninspectable(
        f"role {name} is not readable, so its conditionals go unchecked. Install "
        "the pinned collection: `ansible-galaxy install -r ansible/requirements.yml`"
    )


def _role_files(role: Path) -> list[Path]:
    """Every task, handler and meta file in a role — all of it runs."""
    files = sorted(
        path
        for part in ROLE_SUBDIRS
        for pattern in ("*.yml", "*.yaml")
        for path in (role / part).rglob(pattern)
        if path.is_file()
    )
    meta = role / ROLE_META
    return files + ([meta] if meta.is_file() else [])


def _load(path: Path):
    """One YAML file, or a failure: a skipped file is coverage lost in silence."""
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        raise Uninspectable(
            f"{path} does not parse, so its conditionals go unchecked: {exc}"
        ) from exc


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


def _include_targets(doc) -> list[str]:
    """Every task file or playbook an include or import names, as written."""
    targets = []
    for key in INCLUDE_KEYS:
        for value in _walk(doc, key):
            target = value.get("file") if isinstance(value, dict) else value
            if isinstance(target, str) and "{{" not in target:
                targets.append(target)
    return targets


def _entry_role(entry) -> str | None:
    """The role name in one `roles:` or `dependencies:` entry, string or dict."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        name = entry.get("role") or entry.get("name")
        return name if isinstance(name, str) else None
    return None


def _role_names(doc, meta: bool = False) -> list[str]:
    """Every role a file names: `roles:`, include_role, or a meta dependency."""
    names = []
    keys = ["roles", "dependencies"] if meta else ["roles"]
    for key in keys:
        for value in _walk(doc, key):
            names += [_entry_role(e) for e in value] if isinstance(value, list) else []
    for key in ROLE_KEYS:
        for value in _walk(doc, key):
            names.append(_entry_role(value))
    return [n for n in names if isinstance(n, str) and "{{" not in n]


def reachable_files(playbook: str, repo: Path = REPO) -> list[Path]:
    """The playbook plus every task file, playbook and role it transitively runs."""
    start = _resolve(playbook, repo)
    if start is None:
        return []
    seen: dict[Path, None] = {start: None}
    queue = [start]
    roles_seen: set[str] = set()
    while queue:
        current = queue.pop()
        doc = _load(current)
        targets = [
            _resolve(target, repo, base=current.parent)
            for target in _include_targets(doc)
        ]
        for name in _role_names(doc, meta=current.match(str(ROLE_META))):
            if name in roles_seen:
                continue
            roles_seen.add(name)
            targets += _role_files(resolve_role(name, repo))
        for target in targets:
            if target is not None and target not in seen:
                seen[target] = None
                queue.append(target)
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
            for clause in _conditions(_load(path)):
                for name in sorted(names):
                    if re.search(r"\b" + re.escape(name) + r"\b", clause):
                        if not _is_booled(clause, name):
                            offenders.append((_label(path, repo), name, clause))
    return offenders


def _label(path: Path, repo: Path) -> str:
    """A path for a failure message: repo-relative, or the role's own tail."""
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return "/".join(path.parts[-4:])


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

    def test_a_collection_role_is_reached(self):
        """`roles:` is a reachability edge too: an uncoerced role conditional
        fails the same play."""
        reached = reachable_files("playbooks/home-assistant.yml")
        tails = {p.parts[-3:] for p in reached}
        assert ("home_assistant", "tasks", "main.yml") in tails, tails


def test_every_extra_var_value_names_its_variables():
    unreadable = unreadable_extra_vars()
    assert not unreadable, (
        "an `-e` value carries no `name=value` pair, so the names it sets go "
        "unchecked — teach this gate to read the form or pass them inline: "
        + "; ".join(f"{caller}: `{value}`" for caller, value in unreadable)
    )


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

ROLE_PLAYBOOK = """---
- name: Probe
  hosts: all
  roles:
    - role: probe_role
"""

CI_CALL = (
    "verify:\n"
    "  script:\n"
    "    - ansible-playbook -i inventories/prod playbooks/probe.yml {args}\n"
)


def _fixture_repo(
    tmp_path: Path, playbook_body: str, args: str = "-e probe_exercise=false"
) -> Path:
    (tmp_path / "ansible/playbooks").mkdir(parents=True)
    (tmp_path / "ansible/playbooks/probe.yml").write_text(playbook_body)
    (tmp_path / ".gitlab-ci.yml").write_text(CI_CALL.format(args=args))
    return tmp_path


def _fixture_role(repo: Path, body: str, part: str = "tasks") -> Path:
    """A `probe_role` whose one task file carries the conditional under test."""
    role = repo / "ansible/roles/probe_role" / part
    role.mkdir(parents=True)
    (role / "main.yml").write_text(body)
    return role / "main.yml"


ROLE_TASKS = """---
- name: Exercise something
  ansible.builtin.command: /bin/true
  when: probe_exercise | default(true){suffix}
"""


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


class TestExtraVarParsing:
    """Every assignment in an `-e` argument, in each spelling a caller uses."""

    @pytest.mark.parametrize(
        "args",
        [
            '-e "probe_exercise=false probe_other=false"',
            "-e probe_exercise=false -e probe_other=false",
            "--extra-vars=probe_exercise=false --extra-vars probe_other=false",
            "-eprobe_exercise=false -e probe_other=false",
        ],
    )
    def test_every_assignment_in_the_argument_is_read(self, tmp_path, args):
        found = extra_vars_by_playbook(_fixture_repo(tmp_path, BUGGY_PLAYBOOK, args))
        assert found == {"playbooks/probe.yml": {"probe_exercise", "probe_other"}}

    def test_a_second_assignment_in_a_quoted_value_is_checked(self, tmp_path):
        """A quoted value carrying two assignments yields both names."""
        body = BUGGY_PLAYBOOK.replace("probe_exercise", "probe_other")
        repo = _fixture_repo(tmp_path, body, '-e "probe_exercise=false probe_other=false"')
        assert [name for _, name, _ in unbooled_conditionals(repo)] == ["probe_other"]

    def test_a_value_with_a_space_keeps_its_name(self, tmp_path):
        found = extra_vars_by_playbook(
            _fixture_repo(tmp_path, BUGGY_PLAYBOOK, '-e "probe_exercise=not here"')
        )
        assert found == {"playbooks/probe.yml": {"probe_exercise"}}

    @pytest.mark.parametrize("args", ["-e @extra.json", "--extra-vars=@extra.json"])
    def test_a_file_valued_extra_var_is_reported_unreadable(self, tmp_path, args):
        repo = _fixture_repo(tmp_path, BUGGY_PLAYBOOK, args)
        assert unreadable_extra_vars(repo) == [(".gitlab-ci.yml", "@extra.json")]
        assert extra_vars_by_playbook(repo) == {}


class TestRoleWalk:
    """A role is reachable code: its conditionals are checked like a playbook's."""

    def test_a_string_typed_role_conditional_is_reported(self, tmp_path):
        repo = _fixture_repo(tmp_path, ROLE_PLAYBOOK)
        _fixture_role(repo, ROLE_TASKS.format(suffix=""))
        assert unbooled_conditionals(repo) == [
            ("ansible/roles/probe_role/tasks/main.yml", "probe_exercise",
             "probe_exercise | default(true)"),
        ]

    def test_a_coerced_role_conditional_passes(self, tmp_path):
        repo = _fixture_repo(tmp_path, ROLE_PLAYBOOK)
        _fixture_role(repo, ROLE_TASKS.format(suffix=" | bool"))
        assert unbooled_conditionals(repo) == []

    def test_a_role_handler_is_walked(self, tmp_path):
        repo = _fixture_repo(tmp_path, ROLE_PLAYBOOK)
        _fixture_role(repo, ROLE_TASKS.format(suffix=" | bool"))
        _fixture_role(repo, ROLE_TASKS.format(suffix=""), part="handlers")
        assert [f for f, _, _ in unbooled_conditionals(repo)] == [
            "ansible/roles/probe_role/handlers/main.yml",
        ]

    def test_an_included_role_is_walked(self, tmp_path):
        body = """---
- name: Probe
  hosts: all
  tasks:
    - name: Apply the role
      ansible.builtin.include_role:
        name: probe_role
"""
        repo = _fixture_repo(tmp_path, body)
        _fixture_role(repo, ROLE_TASKS.format(suffix=""))
        assert [f for f, _, _ in unbooled_conditionals(repo)] == [
            "ansible/roles/probe_role/tasks/main.yml",
        ]

    def test_a_meta_dependency_is_walked(self, tmp_path):
        repo = _fixture_repo(tmp_path, ROLE_PLAYBOOK)
        _fixture_role(repo, ROLE_TASKS.format(suffix=" | bool"))
        meta = repo / "ansible/roles/probe_role/meta"
        meta.mkdir(parents=True)
        (meta / "main.yml").write_text("---\ndependencies:\n  - role: probe_dep\n")
        dependency = repo / "ansible/roles/probe_dep/tasks"
        dependency.mkdir(parents=True)
        (dependency / "main.yml").write_text(ROLE_TASKS.format(suffix=""))
        assert [f for f, _, _ in unbooled_conditionals(repo)] == [
            "ansible/roles/probe_dep/tasks/main.yml",
        ]

    def test_an_unresolvable_role_fails_the_gate(self, tmp_path):
        repo = _fixture_repo(
            tmp_path, ROLE_PLAYBOOK.replace("probe_role", f"{COLLECTION}.no_such_role")
        )
        with pytest.raises(Uninspectable, match="no_such_role"):
            unbooled_conditionals(repo)

    def test_a_role_directory_without_content_is_not_a_role(self, tmp_path):
        """`ansible/roles/<name>` holding only molecule scratch must not resolve."""
        repo = _fixture_repo(tmp_path, ROLE_PLAYBOOK)
        (repo / "ansible/roles/probe_role/.ansible/roles").mkdir(parents=True)
        with pytest.raises(Uninspectable, match="probe_role"):
            unbooled_conditionals(repo)

    def test_an_unparseable_reachable_file_fails_the_gate(self, tmp_path):
        repo = _fixture_repo(tmp_path, ROLE_PLAYBOOK)
        _fixture_role(repo, "---\n- name: broken\n  when: [\n")
        with pytest.raises(Uninspectable, match="does not parse"):
            unbooled_conditionals(repo)
