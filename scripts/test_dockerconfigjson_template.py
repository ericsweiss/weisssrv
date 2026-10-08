"""Every dockerconfigjson ExternalSecret renders parseable JSON.

The body is one line of hand-written JSON holding Go template actions, so a lost
comma or brace ships a Secret the kubelet reports only as ImagePullBackOff.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
KUBERNETES = REPO / "kubernetes"
DOCKERCONFIG_TYPE = "kubernetes.io/dockerconfigjson"
KEY = ".dockerconfigjson"
REQUIRED_AUTH_FIELDS = ("username", "password", "auth")

# A Go template action. `{{ .token | toJson }}` emits its own quotes while
# `"{{ printf ... }}"` is already quoted, so the stand-in matches the position.
_ACTION = re.compile(r"\{\{.*?\}\}")


def neutralise(text: str) -> str:
    """The body with every template action replaced by a JSON-valid stand-in."""

    def repl(match: re.Match) -> str:
        before = text[: match.start()].rstrip()
        after = text[match.end() :].lstrip()
        already_quoted = before.endswith('"') and after.startswith('"')
        return "X" if already_quoted else '"X"'

    return _ACTION.sub(repl, text)


def dockerconfig_bodies() -> list[tuple[str, str]]:
    """(manifest path, the .dockerconfigjson template) for every such secret."""
    found = []
    for path in sorted(KUBERNETES.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if DOCKERCONFIG_TYPE not in text or "kind: ExternalSecret" not in text:
            continue
        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError:
            continue
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") != "ExternalSecret":
                continue
            template = (((doc.get("spec") or {}).get("target") or {}).get("template")) or {}
            if template.get("type") != DOCKERCONFIG_TYPE:
                continue
            body = (template.get("data") or {}).get(KEY)
            assert body, f"{path}: {DOCKERCONFIG_TYPE} target has no {KEY} data"
            found.append((str(path.relative_to(REPO)), body))
    return found


BODIES = dockerconfig_bodies()


def test_there_is_something_to_check():
    """An empty parametrisation would pass every case below."""
    assert BODIES, (
        f"no ExternalSecret with a {DOCKERCONFIG_TYPE} template found under "
        "kubernetes/ — the walk is broken and this gate inspects nothing"
    )


@pytest.mark.parametrize(("rel", "body"), BODIES, ids=[rel for rel, _ in BODIES])
def test_the_body_is_valid_json_with_complete_auths(rel: str, body: str):
    try:
        parsed = json.loads(neutralise(body))
    except json.JSONDecodeError as exc:
        pytest.fail(f"{rel}: {KEY} is not valid JSON once rendered ({exc})")
    auths = parsed.get("auths")
    assert isinstance(auths, dict) and auths, f"{rel}: {KEY} has no auths registry"
    for registry, entry in auths.items():
        for field in REQUIRED_AUTH_FIELDS:
            assert entry.get(field), (
                f"{rel}: auths[{registry}] has no {field} — the kubelet cannot "
                "authenticate with a partial entry"
            )


def test_a_malformed_body_is_reported():
    """Mutation case: the lost brace this gate exists to catch."""
    good = '{"auths":{"r":{"username":"u","password":{{ .token }},"auth":"{{ .a }}"}}}'
    json.loads(neutralise(good))
    with pytest.raises(json.JSONDecodeError):
        json.loads(neutralise(good.replace('"auth"', '"auth')))
