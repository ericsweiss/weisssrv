"""Invariants of the terraform/ roots that `terraform validate` cannot see.

README counts matching the maps they describe, UniFi reservation MAC case,
`import.sh` parsing `imports.tf`, and state-backend parity across the roots.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
AUTHENTIK = REPO / "terraform" / "authentik"
UNIFI = REPO / "terraform" / "unifi"
TERRAFORM_TASKFILE = REPO / "taskfiles" / "terraform.yml"

IMPORT_SH = "import.sh"

# A UniFi reservation the PROVIDER created carries its config spelling in state,
# so re-casing one is a replacement of a live object for no gain. These are
# grandfathered; the set only ever shrinks, by re-importing an entry.
GRANDFATHERED_UPPERCASE_MACS = frozenset(
    {
        "6C:4C:BC:AF:E9:08",
        "6C:4C:BC:AF:ED:23",
        "6C:4C:BC:AF:F0:AD",
        "6C:4C:BC:AF:F0:DB",
        "6C:4C:BC:AF:F9:03",
        "6C:4C:BC:B0:01:C8",
        "6C:4C:BC:B0:0D:DD",
        "6C:4C:BC:B0:0D:FE",
        "74:F9:2C:A6:A2:57",
        "90:41:B2:C8:86:65",
        "9C:9C:1F:45:6B:5E",
        "9C:9C:1F:45:76:FE",
        "9C:9C:1F:45:CF:F9",
        "B8:27:EB:A8:93:27",
    }
)

_MAC_RE = re.compile(r'mac\s*=\s*"([0-9A-Fa-f:]{17})"')
_PAIR_RE = re.compile(r'^module\.sso\.[a-z0-9_]+\.(?:this\["[^"]+"\]|embedded\[0\])\|\S+$')


def _run_import_sh(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", IMPORT_SH, "--check"],
        cwd=directory,
        capture_output=True,
        text=True,
    )


def _top_level_keys(body: str, local_name: str) -> list[str]:
    """Keys declared directly inside `<local_name> = { ... }`.

    A brace-depth walk, because quoted keys and nested objects both defeat an
    indentation regex and a miscount would enshrine a wrong README number.
    """
    match = re.search(rf"^\s*{re.escape(local_name)}\s*=\s*\{{", body, re.M)
    assert match, f"{local_name} not found"
    depth = 1
    keys: list[str] = []
    for line in body[match.end():].split("\n"):
        stripped = line.strip()
        if depth == 1 and stripped and not stripped.startswith("#"):
            key = re.match(r'^("?)([A-Za-z0-9_.-]+)\1\s*=', stripped)
            if key:
                keys.append(key.group(2))
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            break
    return keys


def _readme_counts(readme: Path) -> dict[str, int]:
    """`| Kind | Count | …` rows of a root README's "What is managed" table."""
    counts: dict[str, int] = {}
    for line in readme.read_text().split("\n"):
        row = re.match(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|", line)
        if row:
            counts[row.group(1)] = int(row.group(2))
    return counts


# --------------------------------------------------------------------------
# terraform/authentik/import.sh
# --------------------------------------------------------------------------


def test_import_sh_check_derives_the_import_pairs():
    result = _run_import_sh(AUTHENTIK)
    assert result.returncode == 0, (
        f"terraform/authentik/{IMPORT_SH} --check failed:\n{result.stderr}"
    )
    pairs = result.stdout.split()
    assert len(pairs) >= 40, f"only {len(pairs)} import pairs derived from imports.tf"
    bad = [p for p in pairs if not _PAIR_RE.match(p)]
    assert not bad, f"malformed address|id pairs: {bad}"
    addresses = [p.split("|", 1)[0] for p in pairs]
    duplicates = sorted({a for a in addresses if addresses.count(a) > 1})
    assert not duplicates, f"imports.tf binds these addresses twice: {duplicates}"


def test_import_sh_check_fails_on_an_import_block_it_cannot_parse(tmp_path):
    """Mutation proof: an `import {}` shape the extractor does not know must
    abort, not silently under-report — an under-report is a partial DR import."""
    work = tmp_path / "authentik"
    shutil.copytree(AUTHENTIK, work)
    imports = work / "imports.tf"
    imports.write_text(
        imports.read_text().replace(
            'import {\n  to = module.sso.authentik_user.this["eric"]\n  id = "7"\n}',
            'import {\n  for_each = local.something_new\n'
            "  to       = module.sso.authentik_user.this[each.key]\n"
            "  id       = each.value\n}",
        )
    )
    result = _run_import_sh(work)
    assert result.returncode != 0, "an unparseable import block must fail the check"
    assert "for_each" in result.stderr or "extractor" in result.stderr


def test_import_sh_check_fails_when_the_extractor_finds_almost_nothing(tmp_path):
    """Mutation proof for the floor: a gutted imports.tf must abort rather than
    import a handful of objects and leave the rest planning as creates."""
    work = tmp_path / "authentik"
    shutil.copytree(AUTHENTIK, work)
    body = (work / "imports.tf").read_text()
    head, _, _ = body.partition("# Proxy providers")
    (work / "imports.tf").write_text(head)
    result = _run_import_sh(work)
    assert result.returncode != 0
    assert "fewer than 40" in result.stderr


# --------------------------------------------------------------------------
# terraform/unifi
# --------------------------------------------------------------------------


def test_unifi_client_macs_are_lowercase_unless_grandfathered():
    """A new reservation must spell its MAC in lowercase.

    `mac` is ForceNew and matched case-sensitively, so an uppercase entry plans a
    replacement on every apply (networks.tf, `local.clients`).
    """
    macs = _MAC_RE.findall((UNIFI / "networks.tf").read_text())
    assert len(macs) >= 20, f"only {len(macs)} client MACs parsed — regex drifted"
    offenders = sorted(
        m for m in macs if m != m.lower() and m not in GRANDFATHERED_UPPERCASE_MACS
    )
    assert not offenders, (
        f"new unifi client reservations must spell the MAC in lowercase: {offenders}. "
        "networks.tf explains why at `local.clients` (ForceNew plus a "
        "case-sensitive GetClientByMAC); an uppercase entry plans a replacement "
        "and breaks adoption."
    )


def test_the_lowercase_mac_rule_rejects_a_new_uppercase_entry():
    """Mutation proof: the rule above must not pass on any uppercase MAC."""
    macs = ["aa:bb:cc:dd:ee:ff", "AA:BB:CC:DD:EE:00"]
    offenders = [
        m for m in macs if m != m.lower() and m not in GRANDFATHERED_UPPERCASE_MACS
    ]
    assert offenders == ["AA:BB:CC:DD:EE:00"]


def test_every_grandfathered_mac_is_still_declared():
    macs = set(_MAC_RE.findall((UNIFI / "networks.tf").read_text()))
    stale = sorted(GRANDFATHERED_UPPERCASE_MACS - macs)
    assert not stale, (
        f"these MACs are exempt here but no longer in networks.tf: {stale} — "
        "drop them from GRANDFATHERED_UPPERCASE_MACS"
    )


def test_unifi_readme_zone_policy_count_matches_the_map():
    body = (UNIFI / "networks.tf").read_text()
    policies = body[body.index("  policies = ["):body.index("\n  ]\n")]
    live = len(re.findall(r'^\s*name\s*=\s*"', policies, re.M))
    assert live >= 20, f"only {live} policies parsed — the map shape moved"
    counts = _readme_counts(UNIFI / "README.md")
    assert counts.get("Zone policies") == live, (
        f"terraform/unifi/README.md says {counts.get('Zone policies')} zone "
        f"policies; local.policies has {live}"
    )


# --------------------------------------------------------------------------
# terraform/authentik README inventory
# --------------------------------------------------------------------------

_AUTHENTIK_ROWS = {
    "Applications": ("applications.tf", "application_data"),
    "Proxy providers (forward_single)": ("providers_proxy.tf", "proxy_provider_data"),
    "OAuth2/OIDC providers": ("providers_oauth2.tf", "oauth2_provider_data"),
    "SAML provider (GitLab)": ("providers_saml.tf", "saml_providers"),
    "Policy bindings (group → application)": ("policy_bindings.tf", "policy_bindings"),
}


@pytest.mark.parametrize("kind", sorted(_AUTHENTIK_ROWS))
def test_authentik_readme_counts_match_the_maps(kind):
    filename, local_name = _AUTHENTIK_ROWS[kind]
    live = len(_top_level_keys((AUTHENTIK / filename).read_text(), local_name))
    counts = _readme_counts(AUTHENTIK / "README.md")
    assert len(counts) >= 6, (
        f"only {len(counts)} rows parsed from the What is managed table — the "
        "table shape moved and this gate has degraded to a no-op"
    )
    assert counts.get(kind) == live, (
        f"terraform/authentik/README.md says {counts.get(kind)} for {kind!r}; "
        f"local.{local_name} has {live}"
    )


def _list_entries(body: str, local_name: str) -> list[str]:
    """Quoted entries of a `<local_name> = [ ... ]` list literal."""
    match = re.search(rf"^\s*{re.escape(local_name)}\s*=\s*\[", body, re.M)
    assert match, f"{local_name} not found"
    tail = body[match.end():]
    return re.findall(r'"([^"]+)"', tail[: tail.index("]")])


def test_authentik_readme_user_and_group_counts_match_the_maps():
    users = _list_entries((AUTHENTIK / "users.tf").read_text(), "managed_usernames")
    member_groups = _list_entries((AUTHENTIK / "groups.tf").read_text(), "member_groups")
    counts = _readme_counts(AUTHENTIK / "README.md")
    assert counts.get("Users") == len(users), (
        f"README says {counts.get('Users')} users; local.managed_usernames has "
        f"{len(users)}"
    )
    # local.groups is a merge() of the member_groups for-expression plus the one
    # explicit `authentik-admins` entry, so it is counted rather than parsed.
    assert counts.get("Groups + memberships") == len(member_groups) + 1, (
        f"README says {counts.get('Groups + memberships')} groups; "
        f"local.member_groups has {len(member_groups)} plus authentik-admins"
    )


def test_the_readme_count_gate_notices_a_wrong_number(tmp_path):
    """Mutation proof: a stale count in the table must fail the parse-compare."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "| Kind | Count |\n|---|---|\n| Applications | 19 |\n"
    )
    assert _readme_counts(readme)["Applications"] == 19
    live = len(
        _top_level_keys((AUTHENTIK / "applications.tf").read_text(), "application_data")
    )
    assert _readme_counts(readme)["Applications"] != live


# --------------------------------------------------------------------------
# taskfiles/terraform.yml state-backend parity
# --------------------------------------------------------------------------

# One env anchor per root, each repeating the backend auth. A rotation that
# updates three of them leaves the fourth reaching for a stale 1Password field.
_TF_ROOTS = ("cloudflare", "tailscale", "authentik", "unifi")
_TF_SHARED_HTTP_KEYS = (
    "TF_HTTP_USERNAME",
    "TF_HTTP_PASSWORD",
    "TF_HTTP_LOCK_METHOD",
    "TF_HTTP_UNLOCK_METHOD",
)


def _tf_root_envs(taskfile: Path) -> dict[str, dict]:
    """The `env:` map of each `<root>-init` task, one per anchor."""
    tasks = yaml.safe_load(taskfile.read_text())["tasks"]
    return {root: tasks[f"{root}-init"]["env"] for root in _TF_ROOTS}


def _tf_state_parity_problems(envs: dict[str, dict]) -> list[str]:
    problems = []
    for key in _TF_SHARED_HTTP_KEYS:
        values = {root: env.get(key) for root, env in envs.items()}
        if len(set(values.values())) != 1:
            problems.append(f"{key} is not identical across the roots: {values}")
    for root, env in envs.items():
        address = str(env.get("TF_HTTP_ADDRESS"))
        if not address.endswith(f"/terraform/state/{root}"):
            problems.append(
                f"{root}: TF_HTTP_ADDRESS does not end in "
                f"/terraform/state/{root}: {address!r}"
            )
    return problems


def test_every_terraform_root_shares_the_state_backend_credential():
    envs = _tf_root_envs(TERRAFORM_TASKFILE)
    assert sorted(envs) == sorted(_TF_ROOTS)
    problems = _tf_state_parity_problems(envs)
    assert not problems, (
        "taskfiles/terraform.yml state-backend blocks have drifted: "
        f"{problems}"
    )


def test_the_state_backend_parity_gate_notices_one_rotated_root(tmp_path):
    """Mutation proof: changing one root's credential must fail the compare."""
    body = TERRAFORM_TASKFILE.read_text()
    stale = 'TF_HTTP_PASSWORD: "op://Homelab/GitLab Terraform State Token/credential"'
    assert body.count(stale) == len(_TF_ROOTS)
    head, _, tail = body.rpartition(stale)
    mutant = tmp_path / "terraform.yml"
    mutant.write_text(head + 'TF_HTTP_PASSWORD: "op://Homelab/Stale Item/credential"' + tail)
    problems = _tf_state_parity_problems(_tf_root_envs(mutant))
    assert any("TF_HTTP_PASSWORD" in p for p in problems), problems


# --- validation regexes stay one spelling per shape --------------------------

# Every `regex("…")` literal the roots' variables.tf blocks use, as written. One
# shape spelled two ways drifts silently, so a new literal must land here too.
EXPECTED_VALIDATION_REGEXES = frozenset(
    {
        r"^[0-9a-f]{32}$",
        r"^[a-z0-9-]+(\\.[a-z0-9-]+)*\\.[a-z]{2,}$",
        r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$",
        r"^[\\x20-\\x7e]{8,63}$",
    }
)

_REGEX_LITERAL_RE = re.compile(r'regex\(\s*"((?:[^"\\]|\\.)*)"')


def _validation_regexes(terraform_dir: Path) -> set[str]:
    """Regex literals in every root's variables.tf, exactly as spelled there."""
    found: set[str] = set()
    for path in sorted(terraform_dir.glob("*/variables.tf")):
        found.update(_REGEX_LITERAL_RE.findall(path.read_text(encoding="utf-8")))
    return found


def _regex_parity_problems(terraform_dir: Path) -> list[str]:
    found = _validation_regexes(terraform_dir)
    problems = []
    if not found:
        problems.append(f"no regex literal found under {terraform_dir}")
    if extra := sorted(found - EXPECTED_VALIDATION_REGEXES):
        problems.append(f"regex literals not in the frozen set: {extra}")
    if missing := sorted(EXPECTED_VALIDATION_REGEXES - found):
        problems.append(f"frozen regex literals no longer used: {missing}")
    return problems


def test_validation_regexes_are_one_spelling_per_shape():
    problems = _regex_parity_problems(REPO / "terraform")
    assert not problems, (
        f"{problems}\n\nReuse the existing literal for an existing shape; update "
        "EXPECTED_VALIDATION_REGEXES when the shape itself changes."
    )


def test_a_respelled_regex_is_detected(tmp_path):
    """Mutation proof: one altered character must fail the compare."""
    for path in sorted((REPO / "terraform").glob("*/variables.tf")):
        root = tmp_path / path.parent.name
        root.mkdir()
        (root / "variables.tf").write_text(
            path.read_text(encoding="utf-8").replace(
                r"^[\\x20-\\x7e]{8,63}$", r"^[\\x20-\\x7f]{8,63}$"
            ),
            encoding="utf-8",
        )
    problems = _regex_parity_problems(tmp_path)
    assert any("x7f" in p for p in problems), problems
