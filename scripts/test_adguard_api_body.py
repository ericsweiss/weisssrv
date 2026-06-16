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

# The two endpoints whose bodies carry numeric/boolean fields.
TYPED_BODY_URLS = ("/control/dns_config", "/control/tls/configure")

# A coercion filter that, under jinja2_native=off, renders to a JSON string
# instead of a native number/boolean (| int is the confirmed culprit; the
# others are flagged for completeness since they coerce the same way).
STRINGIFYING_FILTER = re.compile(r"\|\s*(int|float|bool|string)\b")

URI_KEYS = ("ansible.builtin.uri", "uri")


def _typed_bodies() -> list[tuple[str, dict]]:
    """(url, body) for every uri task POSTing to a typed-body endpoint."""
    tasks = yaml.safe_load(TASKS_FILE.read_text())
    found = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        args = next((task[k] for k in URI_KEYS if k in task), None)
        if not isinstance(args, dict):
            continue
        url = args.get("url", "")
        body = args.get("body")
        if isinstance(body, dict) and any(u in url for u in TYPED_BODY_URLS):
            found.append((url, body))
    return found


def test_typed_bodies_are_present():
    """Sanity: we actually found the dns_config and tls/configure POST bodies."""
    urls = " ".join(url for url, _ in _typed_bodies())
    assert "/control/dns_config" in urls, "dns_config POST body not found"
    assert "/control/tls/configure" in urls, "tls/configure POST body not found"


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
