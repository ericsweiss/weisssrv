"""Shared fixtures for the scripts/ suites.

Outbound network is blocked in every test, so a fetch nobody patched fails
loudly here instead of reaching a live registry or API.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
K8S_SUFFIXES = ("*.yaml", "*.yml")

_LOOPBACK_HOSTS = {"localhost", "localhost.localdomain", "::1", "0.0.0.0", ""}
_INET_FAMILIES = (socket.AF_INET, socket.AF_INET6)


def require_tool(name: str, gate_name: str, install_hint: str = "") -> None:
    """Fail a binary-driven gate that has no binary under $CI; skip locally.

    Under $CI a missing tool means the job image never installed it, so the gate
    would certify a check it never ran. Call it from a body or autouse fixture.
    """
    if shutil.which(name):
        return
    if os.environ.get("CI"):
        pytest.fail(
            f"{name} is not on PATH — the {gate_name} gate cannot run. "
            + (install_hint or "Install it in the job image.")
        )
    pytest.skip(f"{name} not on PATH")


def reported(path: Path) -> str:
    """A path as a gate message should name it: repo-relative where it can be."""
    return str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)


def k8s_documents(
    root: Path, *, suffix: str | None = None
) -> tuple[list[tuple[Path, dict]], list[str]]:
    """(path, document) for every mapping document under `root`, plus the files
    that would not parse, because a dropped file shrinks the caller's corpus
    silently. `suffix` narrows the walk to one glob."""
    found: list[tuple[Path, dict]] = []
    unreadable: list[str] = []
    globs = (suffix,) if suffix else K8S_SUFFIXES
    for path in sorted({p for glob in globs for p in root.rglob(glob)}):
        if not path.is_file():
            continue
        try:
            docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            unreadable.append(f"{reported(path)}: {exc.__class__.__name__}")
            continue
        found += [(path, doc) for doc in docs if isinstance(doc, dict)]
    return found, unreadable


def assert_all_parsed(unreadable: list[str], kind: str) -> None:
    """The shared every-manifest-parsed assertion, named for what went unchecked."""
    assert not unreadable, (
        f"unparseable manifests — any {kind} they declare went unchecked:\n  "
        + "\n  ".join(unreadable)
    )


def source_and_run(lib: Path, func_call: str, stdin: str = "",
                   env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Source a shell library in a bash subprocess and run one function call.

    `func_call` is bash appended after sourcing, e.g. 'not_ready_node_names' or
    'deployment_replicas_ok 2 2'.
    """
    return subprocess.run(
        ["bash", "-c", f". {lib}\n{func_call}\n"],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


class NetworkBlocked(RuntimeError):
    """A test tried to open a real outbound connection.

    Deliberately not an OSError: urllib and http.client turn OSError into
    URLError, which a broad `except` in the code under test would swallow.
    """


def _is_loopback(address: object) -> bool:
    """True for a loopback or wildcard peer — a test's own listening socket."""
    host = address[0] if isinstance(address, tuple) and address else address
    if not isinstance(host, str):
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _guard(real):
    def blocked(self, address, *args, **kwargs):
        if self.family in _INET_FAMILIES and not _is_loopback(address):
            raise NetworkBlocked(
                f"a test tried to connect to {address!r}; patch the fetcher "
                "(monkeypatch urlopen or the script's own request helper) "
                "instead of letting the suite depend on the network"
            )
        return real(self, address, *args, **kwargs)

    return blocked


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch):
    """Block every non-loopback TCP connect for the duration of a test."""
    monkeypatch.setattr(socket.socket, "connect", _guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guard(socket.socket.connect_ex))
