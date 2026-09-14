"""Tests for authentik-add-user.py — the users.tf username scaffolder.

The scaffolder appends a username to `managed_usernames`; personal data (name,
email) is emitted only as a 1Password JSON snippet and MUST NOT be written into
the public-mirrored users.tf.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "authentik-add-user.py"

spec = importlib.util.spec_from_file_location("authentik_add_user", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

EMPTY = """locals {
  managed_usernames = [
  ]
}
"""

POPULATED = """locals {
  managed_usernames = [
    "eric",
  ]
}
"""


def test_insert_into_empty_list():
    out = mod.add_username(EMPTY, "amy")
    assert '"amy",' in out
    assert out.count("locals {") == 1
    assert out.rstrip().endswith("}")


def test_insert_into_populated_list_appends_after_existing():
    out = mod.add_username(POPULATED, "amy")
    assert out.index('"eric"') < out.index('"amy"')
    assert out.count('",') == 2


def test_duplicate_username_is_refused():
    with pytest.raises(SystemExit):
        mod.add_username(POPULATED, "eric")


def test_unrecognised_file_shape_is_refused_not_guessed():
    with pytest.raises(SystemExit):
        mod.add_username("locals {\n  other = {}\n}\n", "amy")


def test_identity_snippet_is_valid_json_and_escapes():
    snippet = mod.identity_snippet("amy", 'A"my', "amy@example.com")
    # It is an object fragment (no braces); wrapping it reparses to the entry.
    parsed = json.loads("{" + snippet + "}")
    assert parsed == {"amy": {"name": 'A"my', "email": "amy@example.com"}}


def _run(args, users_tf):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--users-tf", str(users_tf)],
        capture_output=True,
        text=True,
    )


def test_cli_end_to_end_writes_username_and_prints_snippet(tmp_path):
    f = tmp_path / "users.tf"
    f.write_text(EMPTY)
    r = _run(["amy", "--name", "Amy Weiss", "--email", "amy@example.com",
              "--groups", "mealie-users"], f)
    assert r.returncode == 0, r.stderr
    written = f.read_text()
    assert '"amy",' in written
    # The invariant: personal data never lands in the public-mirrored file.
    assert "Amy Weiss" not in written
    assert "amy@example.com" not in written
    # ...but IS printed as the 1Password snippet + the groups/apply guidance.
    assert '"amy": {"name": "Amy Weiss", "email": "amy@example.com"}' in r.stdout
    assert "mealie-users" in r.stdout
    assert "supervised" in r.stdout


@pytest.mark.parametrize(
    "username",
    ["Bad Upper", "-leading-dash", "", "sp ace", "a" * 80],
)
def test_cli_rejects_bad_usernames(tmp_path, username):
    f = tmp_path / "users.tf"
    f.write_text(EMPTY)
    r = _run([username, "--name", "X", "--email", "x@example.com"], f)
    assert r.returncode != 0
    assert f.read_text() == EMPTY


def test_cli_rejects_bad_email(tmp_path):
    f = tmp_path / "users.tf"
    f.write_text(EMPTY)
    r = _run(["amy", "--name", "Amy", "--email", "not-an-email"], f)
    assert r.returncode != 0
    assert f.read_text() == EMPTY


def test_repo_users_tf_is_scaffoldable():
    """The real users.tf keeps the shape the scaffolder understands."""
    real = (SCRIPT.parent.parent / "terraform" / "authentik" / "users.tf").read_text()
    out = mod.add_username(real, "zz-shape-probe")
    assert '"zz-shape-probe",' in out
