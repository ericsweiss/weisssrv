"""Tests that check-dockerconfigjson.py fails on an unparseable payload.

Each mutation of the good payload must FAIL, and the repo's own registry-pull
ExternalSecrets are checked with their ${cluster_*} placeholders stood in for.
"""
from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from script_loader import load_path

SCRIPT = Path(__file__).resolve().parent / "check-dockerconfigjson.py"
REPO = SCRIPT.parent.parent


@pytest.fixture(scope="module")
def gate():
    return load_path(SCRIPT)


def _run(corpus: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=textwrap.dedent(corpus), capture_output=True, text=True, cwd=REPO,
    )


GOOD_PAYLOAD = (
    '{"auths":{"registry.git.example.test":{"username":"app",'
    '"password":{{ .token | toJson }},'
    '"auth":"{{ printf "app:%s" .token | b64enc }}"}}}'
)


def corpus(payload: str) -> str:
    return (
        "apiVersion: external-secrets.io/v1\n"
        "kind: ExternalSecret\n"
        "metadata:\n"
        "  name: app-registry-pull\n"
        "  namespace: app\n"
        "spec:\n"
        "  target:\n"
        "    name: app-registry-pull\n"
        "    template:\n"
        "      engineVersion: v2\n"
        "      type: kubernetes.io/dockerconfigjson\n"
        "      data:\n"
        f"        .dockerconfigjson: |\n          {payload}\n"
    )


def test_a_well_formed_payload_passes() -> None:
    result = _run(corpus(GOOD_PAYLOAD))
    assert result.returncode == 0, result.stderr
    assert "1 payload(s)" in result.stdout


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # A lost closing brace, the exact defect a block scalar hides.
        (GOOD_PAYLOAD[:-1], "not valid JSON"),
        # A lost comma between two credential fields.
        (GOOD_PAYLOAD.replace('}},"auth"', '}} "auth"'), "not valid JSON"),
        # The kubelet reads credentials only from `auths`.
        (GOOD_PAYLOAD.replace('"auths"', '"registries"'), "no non-empty `auths`"),
        ('{"auths":{}}', "no non-empty `auths`"),
        # A credential short of a field authenticates nothing.
        (GOOD_PAYLOAD.replace('"username":"app",', ""), "omit username"),
        (GOOD_PAYLOAD.replace('"password":{{ .token | toJson }},', ""), "omit password"),
        # A host the kubelet can never match against an image ref.
        (
            GOOD_PAYLOAD.replace("registry.git.example.test", "registry git"),
            "is not a registry host",
        ),
        # Flux substitution never reached the payload.
        (
            GOOD_PAYLOAD.replace("registry.git.example.test", "registry.${cluster_internal_domain}"),
            "placeholder",
        ),
    ],
)
def test_a_broken_payload_fails(payload: str, expected: str) -> None:
    result = _run(corpus(payload))
    assert result.returncode == 1, result.stdout + result.stderr
    assert expected in result.stderr


def test_a_secret_carrying_the_payload_directly_is_checked() -> None:
    body = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: app-registry-pull\n"
        "  namespace: app\n"
        "type: kubernetes.io/dockerconfigjson\n"
        "stringData:\n"
        '  .dockerconfigjson: \'{"auths":{"registry.example.test":{}}}\'\n'
    )
    result = _run(body)
    assert result.returncode == 1, result.stdout
    assert "omit username" in result.stderr


def test_an_empty_corpus_is_an_operator_error() -> None:
    result = _run("")
    assert result.returncode == 2
    assert "empty corpus" in result.stderr


def test_a_corpus_with_no_payload_is_an_operator_error() -> None:
    """Mutation case: a render loop that missed the apps must not pass."""
    result = _run("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n")
    assert result.returncode == 2
    assert "inspected 0" in result.stderr


def _repo_payload_docs() -> list[tuple[Path, dict]]:
    out = []
    for path in sorted((REPO / "kubernetes").rglob("*.yaml")):
        text = path.read_text()
        if "kubernetes.io/dockerconfigjson" not in text:
            continue
        try:
            docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
        except yaml.YAMLError:
            continue
        out.extend((path, d) for d in docs)
    return out


def test_every_repo_payload_parses(gate) -> None:
    """The live payloads, with ${cluster_*} stood in for as Flux would resolve."""
    docs = [d for _, d in _repo_payload_docs()]
    payloads = gate._payloads(docs)
    assert payloads, "no .dockerconfigjson payload found under kubernetes/"
    for label, raw in payloads:
        resolved = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", "example.test", raw)
        assert gate.payload_violations(label, resolved) == []
