"""Import the hyphenated gates under scripts/ by path, from a test or a gate.

Suites bind `gate = load_script(...)` at module level because hyphenated names
are not importable; vendored test_check_lib_pins.py keeps its own loader.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent


def load_path(path: Path | str, *, register: bool = False):
    """Import any file by path, under its stem with `-` and `.` stripped.

    `register` puts the module in sys.modules, which a script needs when it
    pickles or re-imports itself.
    """
    path = Path(path)
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
