#!/usr/bin/env python3
"""Read a .gitlab-ci.yml job script the way the deploy gates need it.

One argv walk over the `ansible-playbook` calls, shared so every gate sees the
same set, plus the pipeline YAML loader that expands GitLab's `!reference`.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

INVENTORY_FLAGS = frozenset({"-i", "--inventory", "--inventory-file"})
LIMIT_FLAGS = frozenset({"-l", "--limit"})
TAGS_FLAGS = frozenset({"-t", "--tags"})
SKIP_TAGS_FLAGS = frozenset({"--skip-tags"})
# Consumed so their value is never mistaken for the playbook argument.
OTHER_VALUE_FLAGS = frozenset({"-e", "--extra-vars"})
VALUE_FLAGS = INVENTORY_FLAGS | LIMIT_FLAGS | TAGS_FLAGS | SKIP_TAGS_FLAGS | OTHER_VALUE_FLAGS
# A short flag takes its value attached too (`-iansible/inventories/prod`).
SHORT_VALUE_FLAGS = frozenset(f for f in VALUE_FLAGS if len(f) == 2 and f[1] != "-")

PLAYBOOK_SUFFIXES = (".yml", ".yaml")

_MISSING = object()


def _flag(token: str) -> tuple[str, str | None]:
    """Split `-ivalue` or `--flag=value` into its parts; other tokens keep a
    None value. The attached short form comes first: `-ekey=value` is `-e` with
    `key=value`, not a flag named `-ekey`."""
    if len(token) > 2 and token[0] == "-" and token[1] != "-" and token[:2] in SHORT_VALUE_FLAGS:
        return token[:2], token[2:]
    if token.startswith("-") and "=" in token:
        name, _, value = token.partition("=")
        return name, value
    return token, None


def parse_invocations(text: str) -> list[dict]:
    """Every `ansible-playbook` call in `text`.

    One dict per call: inventory, playbook, limit, tags and skip_tags (each a
    set or None) and the normalised argv the tag regexes read.
    """
    calls: list[dict] = []
    for segment in text.split("ansible-playbook")[1:]:
        # A continued line is still this argv; an unescaped newline ends it.
        segment = segment.replace("\\\n", " ")
        segment = re.split(r"[;&|\n]", segment, maxsplit=1)[0]
        try:
            tokens = shlex.split(segment)
        except ValueError:
            # An unbalanced quote is the tail of the enclosing `bash -c '...'`.
            tokens = segment.replace('"', " ").replace("'", " ").split()
        call: dict = {
            "inventory": None,
            "playbook": None,
            "limit": None,
            "tags": None,
            "skip_tags": None,
            "argv": " ".join(tokens),
        }
        index = 0
        while index < len(tokens):
            token = tokens[index]
            name, value = _flag(token)
            if name in VALUE_FLAGS:
                if value is None:
                    value = tokens[index + 1] if index + 1 < len(tokens) else None
                    index += 1
                if name in INVENTORY_FLAGS:
                    call["inventory"] = value
                elif name in LIMIT_FLAGS:
                    call["limit"] = value
                elif name in TAGS_FLAGS and value:
                    selected = {tag for tag in value.split(",") if tag}
                    call["tags"] = (call["tags"] or set()) | selected
                elif name in SKIP_TAGS_FLAGS and value:
                    skipped = {tag for tag in value.split(",") if tag}
                    call["skip_tags"] = (call["skip_tags"] or set()) | skipped
                index += 1
                continue
            if (
                call["playbook"] is None
                and not token.startswith("-")
                and token.endswith(PLAYBOOK_SUFFIXES)
            ):
                call["playbook"] = token
            index += 1
        if call["playbook"]:
            calls.append(call)
    return calls


class Reference(list):
    """A `!reference [.anchor, script]` node, resolved against the pipeline doc.

    Subclasses list so a caller that never resolves still sees the key path.
    """

    def resolve(self, doc, default=None):
        node = doc
        for key in self:
            if isinstance(node, dict) and key in node:
                node = node[key]
            elif isinstance(node, list) and isinstance(key, int) and key < len(node):
                node = node[key]
            else:
                return default
        return node


def _passthrough(loader, suffix, node):
    """Keep a tagged node's scalar/sequence/mapping shape instead of nulling it."""
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return None


def _reference(loader, suffix, node):
    if isinstance(node, yaml.SequenceNode):
        return Reference(loader.construct_sequence(node, deep=True))
    return _passthrough(loader, suffix, node)


class CILoader(yaml.SafeLoader):
    """SafeLoader for pipeline YAML: `!reference` becomes a resolvable
    `Reference`, any other `!` tag keeps its shape."""


# CRITICAL: PyYAML takes the FIRST matching tag prefix, so the "!reference"
# registration must precede the catch-all "!". Reordered, every reference nulls
# out and a job whose script block is referenced parses as having no script.
CILoader.add_multi_constructor("!reference", _reference)
CILoader.add_multi_constructor("!", _passthrough)


def parse_ci(text: str) -> dict:
    """The jobs mapping of pipeline YAML in memory, `!reference` nodes preserved.

    GitLab's inputs syntax makes a pipeline file two documents, `spec:` then the
    jobs, so the last mapping document is the one a caller wants.
    """
    docs = [d for d in yaml.load_all(text, Loader=CILoader) if isinstance(d, dict)]
    return docs[-1] if docs else {}


def load_ci(path) -> dict:
    """The jobs mapping of a pipeline file, `!reference` nodes preserved."""
    return parse_ci(Path(path).read_text(encoding="utf-8"))


def script_lines(job: dict, doc: dict, key: str = "script",
                 unresolved: list | None = None) -> list[str]:
    """A job's `key` block flattened to strings, `!reference` nodes expanded.

    A reference into an included file contributes nothing; pass `unresolved` to
    collect those key paths rather than lose them.
    """
    out: list[str] = []

    def walk(value) -> None:
        if isinstance(value, Reference):
            target = value.resolve(doc, _MISSING)
            if target is _MISSING:
                if unresolved is not None:
                    unresolved.append(list(value))
                return
            walk(target)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif value is not None:
            out.append(str(value))

    walk(job.get(key) or [])
    return out
