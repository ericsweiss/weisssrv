"""Gates for the doc inventories that keep silently drifting from the code.

1. **`--tags` invocations in the docs.** Ansible exits 0 with every task skipped
   when a tag matches nothing, so a runbook that names a tag the playbook does
   not declare is a silent no-op — the failure mode that made a documented SMTP
   credential rotation deploy nothing. Every `ansible-playbook <playbook>
   --tags/--skip-tags <tag>` in the docs must name a tag that playbook really
   reaches.
2. **Namespaced `task ns:name` references** in the docs and the agent-facing
   files (which no gate covered — `lint:taskfile-smoke` checks the reverse
   direction, Taskfile → scripts). A renamed task leaves every runbook that
   named it pointing at nothing.
3. **The docs/13 job tables vs the pipeline.** Those tables are the operator's
   map of CI, and a job missing from them is invisible to anyone reading the doc
   rather than the YAML. Only the locally-defined jobs can be enumerated from
   `.gitlab-ci.yml` — the library-included ones are documented there too but
   come from `weisssrv-lib` — so those are what this gate holds.

All run under `pytest scripts/` (`task scripts:test`, part of `task lint`, and
the CI `python-tests` job).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
PLAYBOOK_DIR = REPO / "ansible" / "playbooks"

# Roles ship from the weisssrv.infra collection, so their task-level tags are
# only readable where the collection is installed.
_ROLE_DIR_CANDIDATES = (
    os.environ.get("WEISSSRV_INFRA_ROLES"),
    REPO / ".tmp/collections/ansible_collections/weisssrv/infra/roles",
    Path.home() / ".ansible/collections/ansible_collections/weisssrv/infra/roles",
)

# Markdown files whose `ansible-playbook ... --tags` examples are operator-facing.
_DOC_GLOBS = ("docs/*.md", "README.md", "CLAUDE.md", "ansible/README.md")

# `ansible-playbook [-i inv] <playbook.yml> ... --tags foo[,bar]`
_INVOCATION_RE = re.compile(
    r"ansible-playbook\s+(?P<args>[^\n`]*?\.yml[^\n`]*)",
)
_TAGS_RE = re.compile(r"--(?:skip-)?tags[= ]+(?P<tags>[A-Za-z0-9_,.<>-]+)")
_PLAYBOOK_RE = re.compile(r"(?P<path>[\w./-]*ansible/playbooks/[\w./-]+\.yml|[\w./-]+\.yml)")


def _roles_dir() -> Path | None:
    for candidate in _ROLE_DIR_CANDIDATES:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return None


def _tags_reachable_from(playbook: Path, roles_dir: Path) -> set[str]:
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
        role_dir = roles_dir / role.rsplit(".", 1)[-1]
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
    roles_dir = _roles_dir()
    if roles_dir is None:
        pytest.skip(
            "weisssrv.infra roles are not installed, so role task tags cannot be "
            "resolved; run `ansible-galaxy collection install -r "
            "ansible/requirements.yml -p .tmp/collections` first"
        )
    problems = []
    for doc, playbook_ref, tag in _doc_tag_invocations():
        playbook = REPO / playbook_ref
        if not playbook.is_file():
            playbook = PLAYBOOK_DIR / Path(playbook_ref).name
        if not playbook.is_file():
            problems.append(f"{doc.relative_to(REPO)}: unknown playbook {playbook_ref}")
            continue
        reachable = _tags_reachable_from(playbook, roles_dir)
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


# --- docs/13 job tables vs the pipeline -------------------------------------

CI_FILE = REPO / ".gitlab-ci.yml"
CI_DOC = REPO / "docs" / "13-ci-cd.md"

# Top-level keys of a pipeline that are not jobs. GitLab also allows the
# job-keyword globals (`image`, `services`, ...) at the top level, and one of
# those read as a job name would demand a documentation row for it.
_CI_RESERVED = {
    "stages", "default", "workflow", "variables", "include",
    "image", "services", "cache", "before_script", "after_script",
}


class _CILoader(yaml.SafeLoader):
    """SafeLoader that survives GitLab's `!reference` tag.

    `safe_load` raises on it and .gitlab-ci.yml uses it freely; the values are
    irrelevant here, only the top-level job NAMES are read.
    """


_CILoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _local_ci_jobs() -> set[str]:
    """Every job `.gitlab-ci.yml` defines itself — hidden fragments excluded."""
    ci = yaml.load(CI_FILE.read_text(encoding="utf-8"), Loader=_CILoader) or {}
    return {
        name
        for name, job in ci.items()
        if isinstance(job, dict) and name not in _CI_RESERVED and not name.startswith(".")
    }


def _documented_ci_jobs() -> set[str]:
    """First-column backticked names of every `| Job | ... |` table row in docs/13.

    Scoped to those tables rather than every backtick in the file: a job name
    that happens to appear in prose is not the operator-facing row this gate is
    about, and counting it would let a table lose a row without failing.
    """
    documented: set[str] = set()
    in_job_table = False
    for line in CI_DOC.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_job_table = False
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if cells and cells[0].lower() == "job":
            in_job_table = True
            continue
        if in_job_table:
            match = re.match(r"`([^`]+)`", cells[0])
            if match:
                documented.add(match.group(1))
    return documented


def test_ci_doc_job_tables_name_every_local_job():
    documented = _documented_ci_jobs()
    assert documented, "docs/13-ci-cd.md has no `| Job |` table rows — this gate examined nothing"
    missing = sorted(_local_ci_jobs() - documented)
    assert not missing, (
        "docs/13-ci-cd.md's job tables do not name these jobs .gitlab-ci.yml defines:\n  "
        + "\n  ".join(missing)
    )


def test_ci_job_scan_finds_the_real_pipeline():
    """Guard both scanners: an empty side would make the gate above vacuous."""
    assert len(_local_ci_jobs()) >= 20
    assert len(_documented_ci_jobs()) >= 20
