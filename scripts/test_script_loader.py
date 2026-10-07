"""Coverage for scripts/script_loader.py, the suites' importlib helper.

The guards matter more than the happy path: without them a typo imports None
and every assertion in the calling suite fails as an AttributeError.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from script_loader import SCRIPTS, load_path, load_script

GATE = "check-secret-rotation-coverage.py"


def test_a_hyphenated_script_imports_under_an_underscored_name() -> None:
    module = load_script(GATE)
    assert module.__name__ == "check_secret_rotation_coverage"
    assert callable(module.check)


def test_a_module_is_not_registered_unless_asked() -> None:
    load_script(GATE)
    assert "check_secret_rotation_coverage" not in sys.modules


def test_register_puts_the_module_in_sys_modules(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "check_secret_rotation_coverage", raising=False)
    module = load_script(GATE, register=True)
    assert sys.modules["check_secret_rotation_coverage"] is module


def test_a_missing_script_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_path(tmp_path / "absent.py")


def test_a_file_python_cannot_import_raises_importerror(tmp_path: Path) -> None:
    """Mutation case: without the `spec is None` guard this returned a module
    whose every attribute lookup failed somewhere else entirely."""
    unloadable = tmp_path / "gate.txt"
    unloadable.write_text("x = 1\n")
    with pytest.raises(ImportError):
        load_path(unloadable)


def test_the_loader_sits_beside_the_gates_it_imports() -> None:
    assert SCRIPTS == Path(__file__).resolve().parent
    assert (SCRIPTS / "script_loader.py").is_file()
