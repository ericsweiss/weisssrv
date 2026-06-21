"""Guard the AdGuard Home API config bodies against re-stringified numbers.

AdGuard's ``/control/dns_config`` and ``/control/tls/configure`` endpoints
expect JSON numbers/booleans (Go ``uint32``/``bool`` struct fields). With
``jinja2_native`` disabled — ansible-core's default — a *filtered* quoted
template such as ``"{{ adguard_ratelimit | int }}"`` renders to a JSON string
(``"20"``), which AdGuard rejects with HTTP 400::

    decoding request: json: cannot unmarshal string into Go struct field
    jsonDNSConfig.ratelimit of type uint32

Bare variable references (``"{{ adguard_ratelimit }}"``) preserve the source
type, so the JSON body serializes as a native number/boolean. This test fails
if a future edit re-introduces an ``| int`` / ``| bool`` filter inside those two
POST bodies — the molecule scenario can't catch it because it runs with
``skip_adguard_api_config: true`` (no live AdGuard in the container).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

TASKS_FILE = (
    Path(__file__).resolve().parent.parent
    / "ansible/roles/adguard_home/tasks/api_base_config.yml"
)

# Endpoints whose POST bodies must serialize native numbers/booleans. Listed
# explicitly as the canonical set the molecule scenario can't guard; the test
# additionally asserts (test_all_post_dict_bodies_are_covered) that this tuple
# stays in sync with EVERY POST uri task in api_base_config.yml that sends a
# dict body, so a future endpoint with a templated numeric field can't slip in
# uncovered. /control/dhcp/set_config's body is `enabled: false` today (a bare
# boolean, no stringification risk yet) but is included so it is guarded if a
# numeric field (lease/range count) is ever added.
TYPED_BODY_URLS = (
    "/control/dns_config",
    "/control/tls/configure",
    "/control/dhcp/set_config",
)

# A coercion filter that, under jinja2_native=off, renders to a JSON string
# instead of a native number/boolean (| int is the confirmed culprit; the
# others are flagged for completeness since they coerce the same way).
STRINGIFYING_FILTER = re.compile(r"\|\s*(int|float|bool|string)\b")

URI_KEYS = ("ansible.builtin.uri", "uri")


def _post_dict_bodies() -> list[tuple[str, dict]]:
    """(url, body) for EVERY uri task POSTing a dict body, regardless of
    whether the URL is in TYPED_BODY_URLS. Used to detect a new POST endpoint
    that escapes the coverage list."""
    tasks = yaml.safe_load(TASKS_FILE.read_text())
    found = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        args = next((task[k] for k in URI_KEYS if k in task), None)
        if not isinstance(args, dict):
            continue
        method = str(args.get("method", "GET")).upper()
        url = args.get("url", "")
        body = args.get("body")
        if method == "POST" and isinstance(body, dict):
            found.append((url, body))
    return found


def _typed_bodies() -> list[tuple[str, dict]]:
    """(url, body) for every uri task POSTing to a typed-body endpoint."""
    return [
        (url, body)
        for url, body in _post_dict_bodies()
        if any(u in url for u in TYPED_BODY_URLS)
    ]


def test_typed_bodies_are_present():
    """Sanity: we actually found each typed-body POST endpoint."""
    urls = " ".join(url for url, _ in _typed_bodies())
    assert "/control/dns_config" in urls, "dns_config POST body not found"
    assert "/control/tls/configure" in urls, "tls/configure POST body not found"
    assert "/control/dhcp/set_config" in urls, "dhcp/set_config POST body not found"


def test_all_post_dict_bodies_are_covered():
    """Every POST endpoint with a dict body must be in TYPED_BODY_URLS, so a
    new endpoint added to api_base_config.yml can't silently escape the
    stringification guard (the original gap: dhcp/set_config was excluded)."""
    uncovered = [
        url
        for url, _ in _post_dict_bodies()
        if not any(u in url for u in TYPED_BODY_URLS)
    ]
    assert not uncovered, (
        "These POST endpoints with dict bodies are not in TYPED_BODY_URLS and "
        "are therefore unguarded against | int / | bool stringification — add "
        "them to TYPED_BODY_URLS:\n  " + "\n  ".join(uncovered)
    )


def test_no_stringifying_filters_in_typed_bodies():
    """No body field may use | int / | bool — it would serialize as a string."""
    offenders = []
    for url, body in _typed_bodies():
        for field, value in body.items():
            if isinstance(value, str) and STRINGIFYING_FILTER.search(value):
                offenders.append(f"{url} -> {field}: {value!r}")
    assert not offenders, (
        "AdGuard JSON body fields must use bare refs (jinja2_native is off, so "
        "| int / | bool would render to a string and AdGuard would return HTTP "
        "400). Offending fields:\n  " + "\n  ".join(offenders)
    )


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
