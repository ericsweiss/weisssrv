#!/usr/bin/env python3
"""Guard for gitlab.rb.j2's rb() escaping macro.

/etc/gitlab/gitlab.rb is EVALUATED AS RUBY by `gitlab-ctl reconfigure`, and its
credential-bearing values come from variables. Two injection classes follow, and
the `ruby -c` validate on the template task only catches one:

  1. Quote/backslash break-out — a rotated SMTP password containing `"` or a
     trailing `\\` renders a syntactically invalid file. `ruby -c` catches it,
     but only after the template has been written to a live GitLab.
  2. Ruby `#{...}` interpolation — `"#{`id`}"` is SYNTACTICALLY VALID, so
     `ruby -c` passes it and the value is EVALUATED at reconfigure time.

The macro must leave every value inert. Stdlib only (the CI python-tests job
installs pytest + PyYAML and nothing else), so rather than rendering the
template this reads the macro's filter chain out of it and replays that exact
chain through a Python model of each filter — an unmodelled or reordered filter
fails the test rather than silently passing. All values here are synthetic.

Run with pytest:
    pytest scripts/test_gitlab_rb_escaping.py -v
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ansible" / "roles" / "gitlab" / "templates" / "gitlab.rb.j2"

MACRO_RE = re.compile(
    r"\{%-?\s*macro\s+rb\(value\)\s*-?%\}(.*?)\{%-?\s*endmacro\s*-?%\}", re.S
)
BODY_RE = re.compile(r"^\{\{-?\s*value\s*(.*?)\s*-?\}\}$", re.S)
JINJA_SPAN_RE = re.compile(r"\{\{.*?\}\}")
INTERPOLATION_RE = re.compile(r"\{\{-?\s*(.*?)\s*-?\}\}")


def _jinja_replace(value: object, old: str, new: str) -> str:
    """Jinja's `replace` filter: soft_str first, so a list is stringified."""
    return (value if isinstance(value, str) else str(value)).replace(old, new)


# Keyed by the filter's literal source text in the template, so a filter the
# test does not model cannot slip through as "tested".
FILTERS = {
    "to_json": json.dumps,
    r"replace('#', '\\#')": lambda v: _jinja_replace(v, "#", "\\#"),
}

# Synthetic values only — never a real credential. Each carries at least one
# character that is load-bearing in a Ruby double-quoted string.
HOSTILE_VALUES = [
    'fake"quote',
    "fake\\backslash",
    "fake-trailing-backslash\\",
    "fake\\\\double-backslash",
    'fake#{`id`}-command',
    'fake#{ENV["HOME"]}-lookup',
    "fake#$stdout-global",
    "fake#@ivar-instance",
    'fake"#{1 + 1}\\-everything',
    "fake\nnewline\ttab",
    "fake#-bare-hash-is-inert-in-ruby",
    "",
]

# Several rb() call sites render Ruby ARRAY literals (SAML group lists,
# monitoring_whitelist). A Ruby array of double-quoted strings interpolates
# exactly the same way, so the same guarantees must hold element-wise.
HOSTILE_LISTS = [
    ['fake#{`id`}', 'fake"quote', "fake\\"],
    [],
]

# `{{ }}` sites that sit inside a hand-written Ruby DOUBLE-quoted string without
# rb(). Every one is an inventory constant (URLs, a TLS protocol list, SAML
# attribute names), never a rotated credential — but the quoting there gives no
# protection, so anything ADDED to this set must go through rb() instead. The
# test fails closed on a new one.
BARE_IN_DOUBLE_QUOTES = {
    "gitlab_external_url",
    "gitlab_nginx_ssl_protocols",
    "gitlab_saml_groups_attribute",
    "gitlab_saml_idp_sso_url",
    "gitlab_saml_label",
}


def filter_chain() -> list[str]:
    """The ordered filters rb() applies, read out of the template."""
    macro = MACRO_RE.search(TEMPLATE.read_text())
    assert macro, f"{TEMPLATE.name} no longer defines the rb() macro"
    body = macro.group(1).strip()
    match = BODY_RE.match(body)
    assert match, f"rb() is no longer a plain filter chain over `value`: {body!r}"
    chain = [f.strip() for f in match.group(1).split("|") if f.strip()]
    assert chain, "rb() applies no filters — values reach gitlab.rb raw"
    return chain


def rb(value: object) -> str:
    """Replay the template's OWN filter chain over `value`."""
    out: object = value
    for name in filter_chain():
        assert name in FILTERS, (
            f"rb() applies {name!r}, which this test does not model — teach it "
            f"what that filter does before trusting the result"
        )
        out = FILTERS[name](out)
    return str(out)


def interpolation_sites(rendered: str) -> list[str]:
    """Every `#{`/`#$`/`#@` a Ruby double-quoted string would EVALUATE.

    Walks the literal the way Ruby's lexer does: a backslash consumes the next
    character, so `\\#{` is an escaped hash and not an interpolation site.
    """
    sites: list[str] = []
    i = 0
    while i < len(rendered):
        if rendered[i] == "\\":
            i += 2
            continue
        if rendered[i] == "#" and i + 1 < len(rendered) and rendered[i + 1] in "{$@":
            sites.append(rendered[i : i + 8])
        i += 1
    return sites


def ruby_value(rendered: str) -> object:
    """Decode the rendered literal the way Ruby would read it.

    Ruby's `\\#` is a plain `#`; JSON has no such escape, so undo it first. Every
    other escape to_json can emit (`\\"`, `\\\\`, `\\n`, `\\uXXXX`) means the same
    thing in a Ruby double-quoted string, and Ruby array literals of
    double-quoted strings are spelled exactly like JSON arrays. to_json never
    emits a bare `#`, so every `#` in the rendered text is one the macro
    escaped.
    """
    return json.loads(rendered.replace("\\#", "#"))


def bare_double_quoted_interpolations() -> set[str]:
    """Template vars interpolated into a Ruby double-quoted string without rb().

    A `{{ }}` is inside such a string when an odd number of `"` precedes it on
    its line, counting only the Ruby text (other `{{ }}` spans are stripped so a
    quote inside a Jinja expression cannot flip the parity).
    """
    bare: set[str] = set()
    for line in TEMPLATE.read_text().splitlines():
        if line.lstrip().startswith("#"):  # gitlab.rb comment, not Ruby
            continue
        for match in INTERPOLATION_RE.finditer(line):
            prefix = JINJA_SPAN_RE.sub("", line[: match.start()])
            if prefix.count('"') % 2 == 0:
                continue
            expression = match.group(1)
            if expression.startswith("rb("):
                continue
            bare.add(expression.split("|")[0].strip())
    return bare


@pytest.mark.parametrize("value", HOSTILE_VALUES + HOSTILE_LISTS)
def test_rendered_value_evaluates_nothing(value):
    rendered = rb(value)
    sites = interpolation_sites(rendered)
    assert not sites, (
        f"rb({value!r}) renders {rendered!r}, which Ruby would INTERPOLATE at "
        f"{sites} — `gitlab-ctl reconfigure` evaluates gitlab.rb, and `ruby -c` "
        f"cannot catch this because the result is syntactically valid"
    )


@pytest.mark.parametrize("value", HOSTILE_VALUES + HOSTILE_LISTS)
def test_rendered_value_round_trips_exactly(value):
    rendered = rb(value)
    try:
        decoded = ruby_value(rendered)
    except json.JSONDecodeError as exc:  # pragma: no cover - failure path
        pytest.fail(
            f"rb({value!r}) renders {rendered!r}, which is not a well-formed "
            f"literal ({exc}) — a quote or backslash broke out of its string and "
            f"the next reconfigure on a live GitLab would fail to parse gitlab.rb"
        )
    assert decoded == value, (
        f"rb({value!r}) renders {rendered!r}, which denotes {decoded!r} — the "
        f"escaping is lossy, so the credential GitLab uses is not the one in "
        f"1Password"
    )


def test_escaping_is_applied_after_json_encoding():
    """`\\#` must pair with the backslash to_json emitted, not precede it.

    Escaping `#` BEFORE to_json turns the added backslash into `\\\\#` — an
    escaped backslash followed by a LIVE interpolation, i.e. the same bug with
    an extra step.
    """
    rendered = rb("fake\\#{1}")
    assert not interpolation_sites(rendered)
    assert ruby_value(rendered) == "fake\\#{1}"


def test_credential_bearing_values_still_use_the_macro():
    """The macro is only worth testing if the call sites still call it."""
    text = TEMPLATE.read_text()
    for key in ("smtp_password", "smtp_user_name", "idp_cert_fingerprint"):
        line = next((ln for ln in text.splitlines() if key in ln and "{{" in ln), None)
        assert line and "rb(" in line, (
            f"{key} is no longer rendered through rb() — it is interpolated "
            f"straight into a Ruby string"
        )


def test_no_new_unescaped_double_quoted_interpolation():
    """Ruby's `"` quoting is exactly where `#{}` lives, so a new bare value
    there is an evaluation site the macro was written to close."""
    bare = bare_double_quoted_interpolations()
    assert bare == BARE_IN_DOUBLE_QUOTES, (
        f"double-quoted Ruby strings interpolate {sorted(bare)} without rb(); "
        f"expected exactly {sorted(BARE_IN_DOUBLE_QUOTES)}. A value added here "
        f"is evaluated at reconfigure time if it contains `#{{` — render it "
        f"through rb() (which emits its own quotes) instead"
    )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
