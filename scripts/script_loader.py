"""Loader for the hyphenated scripts under scripts/.

Not a conftest: cli/tests ships its own and the two would shadow each other.
tests/test_check_lib_pins.py keeps its own copy, being vendored elsewhere.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def load_path(path: Path, *, register: bool = False):
    """Import any file by path, under its stem with `-` and `.` stripped.

    `register` puts the module in sys.modules, which a script needs when it
    pickles or re-imports itself.
    """
    module_name = path.stem.replace("-", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_script(name: str, *, register: bool = False):
    """Import scripts/<name> by path."""
    return load_path(SCRIPTS / name, register=register)
