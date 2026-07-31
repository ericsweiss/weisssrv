"""Gates for the doc inventories that keep silently drifting from the code.

1. The **Ansible Roles table in README.md** is declared the source of truth for
   the role list (CLAUDE.md, .cursorrules, the agent skill all defer to it), and
   it had fallen two roles behind `ansible/roles/`.
2. **`--tags` invocations in the docs.** Ansible exits 0 with every task skipped
   when a tag matches nothing, so a runbook that names a tag the playbook does
   not declare is a silent no-op — the failure mode that made a documented SMTP
   credential rotation deploy nothing. Every `ansible-playbook <playbook>
   --tags/--skip-tags <tag>` in the docs must name a tag that playbook really
   reaches.
3. **Namespaced `task ns:name` references** in the docs and the agent-facing
   files (which no gate covered — `lint:taskfile-smoke` checks the reverse
   direction, Taskfile → scripts). A renamed task leaves every runbook that
   named it pointing at nothing.

All run under `pytest scripts/` (`task scripts:test`, part of `task lint`, and
the CI `python-tests` job).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ROLES_DIR = REPO / "ansible" / "roles"
PLAYBOOK_DIR = REPO / "ansible" / "playbooks"

# Markdown files whose `ansible-playbook ... --tags` examples are operator-facing.
_DOC_GLOBS = ("docs/*.md", "README.md", "CLAUDE.md", "ansible/README.md")

# `ansible-playbook [-i inv] <playbook.yml> ... --tags foo[,bar]`
_INVOCATION_RE = re.compile(
    r"ansible-playbook\s+(?P<args>[^\n`]*?\.yml[^\n`]*)",
)
_TAGS_RE = re.compile(r"--(?:skip-)?tags[= ]+(?P<tags>[A-Za-z0-9_,.<>-]+)")
_PLAYBOOK_RE = re.compile(r"(?P<path>[\w./-]*ansible/playbooks/[\w./-]+\.yml|[\w./-]+\.yml)")


def _readme_role_rows() -> list[str]:
    """Role names from the `## Ansible Roles` table in README.md."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    start = text.index("## Ansible Roles")
    end = text.index("\n## ", start + 1)
    rows = []
    for line in text[start:end].splitlines():
        m = re.match(r"\|\s*([a-z0-9_]+)\s*\|", line)
        if m:
            rows.append(m.group(1))
    return rows


def test_readme_roles_table_matches_disk():
    documented = _readme_role_rows()
    actual = sorted(p.name for p in ROLES_DIR.iterdir() if p.is_dir())
    assert sorted(documented) == actual, (
        "README.md § Ansible Roles is out of sync with ansible/roles/. "
        f"Missing rows: {sorted(set(actual) - set(documented))}; "
        f"rows with no role: {sorted(set(documented) - set(actual))}"
    )


def test_readme_roles_table_has_no_duplicate_rows():
    documented = _readme_role_rows()
    dupes = sorted({r for r in documented if documented.count(r) > 1})
    assert not dupes, f"duplicate rows in the README roles table: {dupes}"


def _tags_reachable_from(playbook: Path) -> set[str]:
    """Every tag a `--tags` selection could match in this playbook.

    Play-level `tags:`, role-entry `tags:`, and task-level `tags:` anywhere in
    the roles the playbook loads (tags propagate down, and a role-level tag ADDS
    to rather than replaces the task-level ones).
    """
    tags: set[str] = set()
    roles: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "tags":
                    if isinstance(value, str):
                        tags.add(value)
                    elif isinstance(value, list):
                        tags.update(str(v) for v in value)
                elif key == "role" and isinstance(value, str):
                    roles.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    plays = yaml.safe_load(playbook.read_text(encoding="utf-8")) or []
    walk(plays)
    for play in plays if isinstance(plays, list) else []:
        for entry in (play or {}).get("roles", []) or []:
            if isinstance(entry, str):
                roles.add(entry)

    for role in roles:
        role_dir = ROLES_DIR / role
        if not role_dir.is_dir():
            continue
        for task_file in role_dir.rglob("tasks/*.yml"):
            try:
                walk(yaml.safe_load(task_file.read_text(encoding="utf-8")))
            except yaml.YAMLError:  # pragma: no cover - a broken role file is ansible-lint's job
                continue
    return tags


def _doc_tag_invocations() -> list[tuple[Path, str, str]]:
    """(doc, playbook-path-as-written, tag) for every documented --tags usage."""
    found: list[tuple[Path, str, str]] = []
    for glob in _DOC_GLOBS:
        for doc in sorted(REPO.glob(glob)):
            for m in _INVOCATION_RE.finditer(doc.read_text(encoding="utf-8")):
                args = m.group("args")
                tag_match = _TAGS_RE.search(args)
                if not tag_match:
                    continue
                pb_match = _PLAYBOOK_RE.search(args)
                if not pb_match:
                    continue
                for tag in tag_match.group("tags").split(","):
                    tag = tag.strip()
                    # `<role-tag>` / `<host>` style placeholders are not tags.
                    if not tag or tag.startswith("<"):
                        continue
                    found.append((doc, pb_match.group("path"), tag))
    return found


def test_documented_tags_exist_in_their_playbook():
    problems = []
    for doc, playbook_ref, tag in _doc_tag_invocations():
        playbook = REPO / playbook_ref
        if not playbook.is_file():
            playbook = PLAYBOOK_DIR / Path(playbook_ref).name
        if not playbook.is_file():
            problems.append(f"{doc.relative_to(REPO)}: unknown playbook {playbook_ref}")
            continue
        reachable = _tags_reachable_from(playbook)
        if tag not in reachable and tag not in {"all", "always", "never", "tagged", "untagged"}:
            problems.append(
                f"{doc.relative_to(REPO)}: `{playbook_ref} --tags {tag}` matches no tag "
                f"(ansible would exit 0 having done nothing)"
            )
    assert not problems, "documented --tags that silently no-op:\n  " + "\n  ".join(problems)


def test_documented_tag_scan_finds_the_real_invocations():
    """Guard the regex itself: the docs really do carry --tags examples."""
    found = _doc_tag_invocations()
    assert len(found) >= 8, f"expected the docs to carry --tags examples, found {found}"


# `task ns:name` inside backticks. Colon-less tokens are skipped: prose like
# "a task with …" is indistinguishable from a bare task name, and namespaced
# names are where renames actually happen.
_TASK_RE = re.compile(r"`+\s*task ([a-zA-Z][a-zA-Z0-9:_-]*)")

_AGENT_FILES = ("README.md", "CLAUDE.md", "AGENTS.md", ".cursorrules",
                "ansible/README.md", "ansible/TESTING.md")


def _taskfile_names() -> set[str]:
    data = yaml.safe_load((REPO / "Taskfile.yml").read_text(encoding="utf-8"))
    return set(data.get("tasks", {}))


def _doc_task_tokens() -> list[tuple[Path, str]]:
    files = sorted((REPO / "docs").glob("*.md"))
    files += [REPO / name for name in _AGENT_FILES]
    files += sorted((REPO / ".claude").rglob("*.md"))
    found = []
    for f in files:
        if not f.is_file():
            continue
        for m in _TASK_RE.finditer(f.read_text(encoding="utf-8")):
            token = m.group(1)
            # `task immich:*` — a glob, not a task name.
            if ":" not in token or token.endswith(":"):
                continue
            found.append((f, token))
    return found


def test_documented_task_names_exist():
    names = _taskfile_names()
    missing = sorted(
        {f"{f.relative_to(REPO)}: task {tok}" for f, tok in _doc_task_tokens() if tok not in names}
    )
    assert not missing, "documented task names that do not exist:\n  " + "\n  ".join(missing)


def test_task_token_scan_finds_the_real_references():
    """Guard the regex: the docs really do reference namespaced tasks."""
    tokens = _doc_task_tokens()
    assert len(tokens) >= 50, f"expected many `task ns:name` references, found {len(tokens)}"
