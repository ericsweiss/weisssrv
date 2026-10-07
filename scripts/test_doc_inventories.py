"""Gates holding the doc inventories to the code: `--tags` invocations, `task
ns:name` references, the docs/13 CI job tables and docs/15's 1Password item
inventory. Runs under `pytest scripts/`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

import taskfile_tree
from test_vendored_byte_identity import load_ci_doc

REPO = Path(__file__).resolve().parent.parent
PLAYBOOK_DIR = REPO / "ansible" / "playbooks"

# Roles ship from the weisssrv.infra collection, so their task-level tags are
# readable from an installed collection or from the weisssrv-lib checkout the
# vendored-copy gate already requires.
_LIB_CHECKOUT = Path(os.environ.get("WEISSSRV_LIB_PATH") or REPO.parent / "weisssrv-lib")
_ROLE_DIR_CANDIDATES = (
    os.environ.get("WEISSSRV_INFRA_ROLES"),
    # `task ansible:lint` installs the pinned collection here.
    REPO / ".ansible-home/collections/ansible_collections/weisssrv/infra/roles",
    Path.home() / ".ansible/collections/ansible_collections/weisssrv/infra/roles",
    _LIB_CHECKOUT / "ansible_collections/weisssrv/infra/roles",
)

# Markdown files whose `ansible-playbook ... --tags` examples are operator-facing.
_DOC_GLOBS = ("docs/*.md", "README.md", "CLAUDE.md", "ansible/README.md")

# `ansible-playbook [-i inv] <playbook.yml> ... --tags foo[,bar]`
_INVOCATION_RE = re.compile(
    r"ansible-playbook\s+(?P<args>[^\n`]*?\.yml[^\n`]*)",
)
_TAGS_RE = re.compile(r"--(?:skip-)?tags[= ]+(?P<tags>[A-Za-z0-9_,.<>-]+)")
_PLAYBOOK_RE = re.compile(r"(?P<path>[\w./-]*ansible/playbooks/[\w./-]+\.yml|[\w./-]+\.yml)")


def _roles_dir() -> Path:
    for candidate in _ROLE_DIR_CANDIDATES:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    raise AssertionError(
        "no weisssrv.infra roles found: install the collection "
        "(`task ansible:lint`, or `task ansible:install-collections`) or "
        "provide a weisssrv-lib checkout (set $WEISSSRV_LIB_PATH, or place one at "
        f"{REPO.parent / 'weisssrv-lib'}). This gate never skips."
    )


def _tags_reachable_from(playbook: Path, roles_dir: Path) -> set[str]:
    """Every tag a `--tags` selection could match in this playbook."""
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


# `task ns:name` inside backticks. Colon-less tokens are skipped here: prose
# like "a task with …" is indistinguishable from a bare task name.
_TASK_RE = re.compile(r"`+\s*task ([a-zA-Z][a-zA-Z0-9:_-]*)")

# Second pass for the bare names (`task lint`, `task collect-state`): a whole
# inline code span that starts with `task `, which prose never is.
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_SPAN_TASK = re.compile(r"^task\s+([a-zA-Z][a-zA-Z0-9:_-]*)")

_AGENT_FILES = ("README.md", "CLAUDE.md", "AGENTS.md", ".cursorrules",
                "ansible/README.md", "ansible/TESTING.md")


def _taskfile_names() -> set[str]:
    """Every task in the includes: tree, not just the root file's own."""
    return set(taskfile_tree.load_tasks(REPO))


def _task_tokens_in(text: str) -> list[str]:
    """Task names one document names: namespaced anywhere, bare in a whole span."""
    tokens = []
    for m in _TASK_RE.finditer(text):
        token = m.group(1)
        # `task immich:*` — a glob, not a task name.
        if ":" not in token or token.endswith(":"):
            continue
        tokens.append(token)
    for span in _CODE_SPAN.finditer(text):
        m = _SPAN_TASK.match(span.group(1).strip())
        if m and ":" not in m.group(1):
            tokens.append(m.group(1))
    return tokens


def _doc_task_tokens() -> list[tuple[Path, str]]:
    files = sorted((REPO / "docs").glob("*.md"))
    files += [REPO / name for name in _AGENT_FILES]
    files += sorted((REPO / ".claude").rglob("*.md"))
    return [
        (f, token)
        for f in files
        if f.is_file()
        for token in _task_tokens_in(f.read_text(encoding="utf-8"))
    ]


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


def test_the_scan_resolves_bare_task_names():
    """`task lint` carries no namespace, so a colon-requiring scan skips it."""
    bare = {token for _, token in _doc_task_tokens() if ":" not in token}
    assert "lint" in bare, "the bare-name pass found none of the docs' `task lint` uses"


def test_prose_about_a_task_is_not_read_as_a_task_name():
    """Mutation case: a bare name counts only as a whole code span."""
    assert _task_tokens_in("a task with the same name, and `task` on its own") == []
    assert _task_tokens_in("run `task lint` before the MR") == ["lint"]
    assert _task_tokens_in("`task flux:reconcile -- --with-source`") == ["flux:reconcile"]


# --- docs/15's 1Password inventory vs the items the repo really references ----

CRED_DOC = REPO / "docs" / "15-credential-rotation.md"

# The vaults this deployment reads. Anything else in an `op://` URI is a doc
# placeholder, not a reference.
_OP_VAULTS = ("Homelab", "Homelab-Boot")

# An item TITLE. Tight enough to reject the `op://Homelab/[^/]+/...` grep
# patterns the Taskfile's own credential-scan tasks carry.
_OP_ITEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._'-]*$")
_OP_URI_RE = re.compile(
    r"op://(?:" + "|".join(_OP_VAULTS) + r")/([^/\n\"'`]+)/"
)

# Documented items nothing in the repo can reference, each with the reason.
# They are real vault items: entered in an app's own UI, read from a CI variable
# rather than an `op://` URI, or kept for an alternate provider.
_DOC_ONLY_ITEMS = {
    "Flux Webhook Token": "optional Flux Receiver path; the GitLab agent drives reconcile",
    "GitLab Version Bump Bot Token": "read as the VERSION_BUMP_BOT_TOKEN CI variable",
    "Homarr Integrations": "DR record of credentials entered in the Homarr UI",
    "Homarr Proxmox Token": "entered in the Homarr UI",
    "OpenAI API Key": "entered in Mealie's UI",
    "Service Account Auth Token weisssrv": "read as OP_SERVICE_ACCOUNT_TOKEN in CI",
    "VPN Unlimited Credentials": "alternate Gluetun provider, not the configured one",
}


def _documented_1p_items() -> set[str]:
    """First-column item titles of every table in docs/15 § Inventory."""
    text = CRED_DOC.read_text(encoding="utf-8")
    block = text[text.index("### Inventory"):text.index("### Item detail")]
    items: set[str] = set()
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        first = cells[0]
        # Header row and the `|---|` separator.
        if first == "Item" or not first or set(first) <= set("-: "):
            continue
        # One row can list several items (the four WiFi SSIDs).
        for part in first.split(","):
            part = part.strip().strip("`")
            if part:
                items.add(part)
    return items


def _eso_item_keys() -> set[str]:
    """`remoteRef.key` / `dataFrom.extract.key` = the 1Password item TITLE."""
    keys: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("remoteRef", "extract") and isinstance(value, dict) and "key" in value:
                    keys.add(str(value["key"]))
                walk(value)
        elif isinstance(node, list):
            for entry in node:
                walk(entry)

    for path in sorted((REPO / "kubernetes").rglob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if "remoteRef" not in text and "extract" not in text:
            continue
        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError:
            continue
        for doc in docs:
            walk(doc)
    return keys


def _op_uri_items() -> set[str]:
    """Item titles in the `op://vault/Item/field` URIs the host-side tooling reads."""
    items: set[str] = set()
    sources = [REPO / ".gitlab-ci.yml", REPO / "Taskfile.yml"]
    sources += sorted(taskfile_tree.include_paths(REPO).values())
    for path in sources:
        for match in _OP_URI_RE.finditer(path.read_text(encoding="utf-8")):
            title = match.group(1).strip()
            if _OP_ITEM_RE.match(title):
                items.add(title)
    return items


def _zfs_passphrase_items() -> set[str]:
    """`zfs_encryption_pools[].item` — the boot-unlock passphrases."""
    items: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "zfs_encryption_pools" and isinstance(value, list):
                    for pool in value:
                        if isinstance(pool, dict) and pool.get("item"):
                            items.add(str(pool["item"]))
                walk(value)
        elif isinstance(node, list):
            for entry in node:
                walk(entry)

    for path in sorted((REPO / "ansible" / "inventories" / "prod").rglob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if "zfs_encryption_pools" not in text:
            continue
        try:
            walk(yaml.safe_load(text))
        except yaml.YAMLError:
            continue
    return items


def _referenced_1p_items() -> set[str]:
    return _eso_item_keys() | _op_uri_items() | _zfs_passphrase_items()


def test_every_referenced_1p_item_is_documented():
    documented = _documented_1p_items()
    referenced = _referenced_1p_items()
    assert documented, "docs/15 § Inventory parsed to nothing"
    assert referenced, "no 1Password item reference found anywhere in the repo"
    missing = sorted(referenced - documented)
    assert not missing, (
        "1Password items the repo consumes but docs/15 § Inventory does not list:\n  "
        + "\n  ".join(missing)
    )


def test_every_documented_1p_item_is_referenced_or_declared_doc_only():
    unused = sorted(_documented_1p_items() - _referenced_1p_items() - set(_DOC_ONLY_ITEMS))
    assert not unused, (
        "docs/15 § Inventory lists items nothing references. Remove the row, or add "
        "it to _DOC_ONLY_ITEMS with the reason it cannot be referenced:\n  "
        + "\n  ".join(unused)
    )


def test_the_1p_reference_scan_sees_each_source():
    """Guard the three scanners: each really does find references."""
    assert len(_eso_item_keys()) >= 20
    assert len(_op_uri_items()) >= 20
    assert _zfs_passphrase_items()


def test_every_doc_only_item_carries_a_reason_and_is_documented():
    documented = _documented_1p_items()
    for item, reason in _DOC_ONLY_ITEMS.items():
        assert item in documented, f"{item} is declared doc-only but docs/15 does not list it"
        assert len(reason.split()) >= 4, f"{item} needs a real reason, not {reason!r}"

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


def _local_ci_jobs() -> set[str]:
    """Every job `.gitlab-ci.yml` defines itself — hidden fragments excluded."""
    ci = load_ci_doc(CI_FILE)
    return {
        name
        for name, job in ci.items()
        if isinstance(job, dict) and name not in _CI_RESERVED and not name.startswith(".")
    }


def _documented_ci_jobs() -> set[str]:
    """First-column backticked names of every `| Job | ... |` row in docs/13."""
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


# --- README's docs/ index vs the directory ----------------------------------

README_DOC = REPO / "README.md"


def _readme_documentation_section() -> str:
    """README.md from the `## Documentation` heading to the next `## ` heading.

    Scoped so that a `docs/NN` link anywhere else in the README (the Applications
    table, for one) cannot satisfy the assertion below.
    """
    text = README_DOC.read_text(encoding="utf-8")
    start = text.index("\n## Documentation\n")
    rest = text[start + 1 :]
    end = rest.index("\n## ", 1)
    return rest[:end]


def test_readme_indexes_every_doc():
    section = _readme_documentation_section()
    missing = sorted(p.name for p in (REPO / "docs").glob("*.md") if f"](docs/{p.name})" not in section)
    assert not missing, (
        "docs/ files with no row in README.md § Documentation:\n  " + "\n  ".join(missing)
    )


def test_readme_index_scan_finds_the_real_docs():
    """Guard the slice: an empty or mis-sliced section would pass vacuously."""
    section = _readme_documentation_section()
    assert "](docs/01-overview.md)" in section
    assert section.count("](docs/") >= 40
