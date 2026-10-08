"""Failure-path tests for scripts/check-ansible-service-names.py.

A role FQCN used as a systemd unit name must fail the gate, and the legitimate
unit spellings the playbooks use must not.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from script_loader import load_path

SCRIPT = Path(__file__).resolve().parent / "check-ansible-service-names.py"
REPO = SCRIPT.parent.parent


def _load():
    return load_path(SCRIPT)


@pytest.fixture(scope="module")
def gate():
    return _load()


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True, text=True, cwd=REPO,
    )


def _playbook(tmp_path: Path, body: str) -> Path:
    (tmp_path / "play.yml").write_text(textwrap.dedent(body))
    return tmp_path


def test_the_real_tree_is_clean():
    result = _run(REPO / "ansible")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_role_fqcn_as_a_unit_name_fails(tmp_path):
    root = _playbook(tmp_path, """\
        - hosts: all
          tasks:
            - name: Restart k3s service
              ansible.builtin.systemd:
                name: weisssrv.infra.k3s
                state: restarted
        """)
    result = _run(root)
    assert result.returncode == 1
    assert "weisssrv.infra.k3s" in result.stdout


def test_a_masked_no_op_still_fails(tmp_path):
    """failed_when: false must not mask the violation."""
    root = _playbook(tmp_path, """\
        - hosts: all
          tasks:
            - name: Stop k3s.service if present
              ansible.builtin.systemd:
                name: weisssrv.infra.k3s
                state: stopped
              failed_when: false
        """)
    assert _run(root).returncode == 1


def test_the_legitimate_spellings_pass(tmp_path):
    root = _playbook(tmp_path, """\
        - hosts: all
          tasks:
            - name: Bare unit name
              ansible.builtin.systemd:
                name: k3s-agent
                state: restarted
            - name: Explicit unit suffix
              ansible.builtin.service:
                name: nfs-server.service
                state: started
            - name: Timer
              ansible.builtin.systemd:
                name: zfs-scrub.timer
                enabled: true
            - name: Templated
              ansible.builtin.systemd:
                name: "{{ 'k3s' if k3s_role == 'server' else 'k3s-agent' }}"
                state: restarted
            - name: A role include is not a unit
              ansible.builtin.include_role:
                name: weisssrv.infra.k3s
        """)
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_list_of_units_is_inspected(tmp_path):
    root = _playbook(tmp_path, """\
        - hosts: all
          tasks:
            - name: Several units
              ansible.builtin.systemd:
                name:
                  - unbound.service
                  - weisssrv.infra.adguard
                state: restarted
        """)
    assert _run(root).returncode == 1


def test_free_form_args_are_inspected(tmp_path):
    root = _playbook(tmp_path, """\
        - hosts: all
          tasks:
            - name: Free-form
              systemd: name=weisssrv.infra.k3s state=restarted
        """)
    assert _run(root).returncode == 1


def test_an_unparseable_file_is_an_error_not_a_silent_skip(tmp_path):
    """An unreadable playbook drops out of the walk; the gate must say so
    instead of printing OK over a file it never scanned."""
    root = _playbook(tmp_path, """\
        - name: broken
          hosts: all
          tasks:
            - name: Start a unit
              ansible.builtin.service: {name: k3s, state: started
        """)
    result = _run(root)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "could not parse" in result.stdout
    assert "play.yml" in result.stdout


def test_an_empty_tree_is_an_error_not_a_pass(tmp_path):
    """A collection rule that stopped matching would exempt everything."""
    assert _run(tmp_path).returncode == 2
    assert _run(tmp_path / "nope").returncode == 2


@pytest.mark.parametrize("name,bad", [
    ("weisssrv.infra.k3s", True),
    ("k3s", False),
    ("k3s-agent", False),
    ("nfs-server.service", False),
    ("zfs-scrub.timer", False),
    ("systemd-networkd.socket", False),
    ("{{ svc_name }}", False),
    ("home-eric.mount", False),
])
def test_is_bad_unit(gate, name, bad):
    assert gate.is_bad_unit(name) is bad
