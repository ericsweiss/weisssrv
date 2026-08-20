"""Tests for authentik-add-user.py — the users.tf scaffolder."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "authentik-add-user.py"

spec = importlib.util.spec_from_file_location("authentik_add_user", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

EMPTY = """locals {
  users = {}
}
"""

POPULATED = """locals {
  users = {
    "amy" = {
      name  = "Amy"
      email = "amy@example.com"
    }
  }
}
"""


def test_insert_into_empty_map():
    out = mod.add_user(EMPTY, "amy", "Amy", "amy@example.com")
    assert '"amy" = {' in out
    assert 'email = "amy@example.com"' in out
    # still one locals block, closed properly
    assert out.count("locals {") == 1
    assert out.rstrip().endswith("}")


def test_insert_into_populated_map_appends_after_existing():
    out = mod.add_user(POPULATED, "bob", "Bob", "bob@example.com")
    assert out.index('"amy"') < out.index('"bob"')
    assert out.count("name  =") == 2


def test_duplicate_username_is_refused():
    with pytest.raises(SystemExit):
        mod.add_user(POPULATED, "amy", "Amy Again", "amy2@example.com")


def test_unrecognised_file_shape_is_refused_not_guessed():
    with pytest.raises(SystemExit):
        mod.add_user("locals {\n  other = {}\n}\n", "amy", "Amy", "amy@example.com")


def _run(args, users_tf):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--users-tf", str(users_tf)],
        capture_output=True,
        text=True,
    )


def test_cli_end_to_end(tmp_path):
    f = tmp_path / "users.tf"
    f.write_text(EMPTY)
    r = _run(["amy", "--name", "Amy", "--email", "amy@example.com", "--groups", "app-grafana"], f)
    assert r.returncode == 0, r.stderr
    assert '"amy"' in f.read_text()
    assert "app-grafana" in r.stdout
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


def test_hcl_metacharacters_are_encoded_not_interpolated():
    out = mod.add_user(EMPTY, "amy", 'A"my ${file("/etc/shadow")} %{ if x }', "amy@example.com")
    assert '$${file' in out and "${file" not in out.replace("$${file", "")
    assert '%%{ if' in out
    assert '\\"my' in out


def test_control_characters_are_refused():
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        mod.add_user(EMPTY, "amy", "A\nmy", "amy@example.com")


def test_cli_quotes_in_name_are_escaped(tmp_path):
    f = tmp_path / "users.tf"
    f.write_text(EMPTY)
    r = _run(["amy", "--name", 'A"my', "--email", "amy@example.com"], f)
    assert r.returncode == 0, r.stderr
    assert '\\"my' in f.read_text()


def test_repo_users_tf_is_scaffoldable():
    """The real users.tf keeps the shape the scaffolder understands."""
    real = (SCRIPT.parent.parent / "terraform" / "authentik" / "users.tf").read_text()
    out = mod.add_user(real, "zz-shape-probe", "Probe", "probe@example.com")
    assert '"zz-shape-probe"' in out
