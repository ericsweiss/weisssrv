"""Failure-path tests for scripts/check-ansible-service-names.py.

The gate reports success by printing a sentence, so what needs proving is that
it can still FAIL — on exactly the shape that shipped (a role FQCN passed as a
systemd unit name), and that it does not fire on the legitimate spellings the
playbooks use.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-ansible-service-names.py"
REPO = SCRIPT.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("check_ansible_service_names", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    """failed_when: false is exactly what made the second occurrence invisible."""
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
