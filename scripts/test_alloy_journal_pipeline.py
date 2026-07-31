#!/usr/bin/env python3
"""Guards for the host journald -> Loki pipeline (alloy_host + Loki ingester).

Two invariants span the Ansible and Kubernetes halves of the repo, so neither
`ansible-lint` nor `flux:lint` can see them:

1. **The journal relabel rules must be applied exactly once.**
   `loki.source.journal` exposes the `__journal_*` metadata only inside its own
   relabel pass and drops every `__`-prefixed label before forwarding. Passing
   `relabel_rules = loki.relabel.X.rules` AND forwarding into
   `loki.relabel.X.receiver` therefore runs the same rules a second time with
   the source labels gone, and Prometheus relabel semantics turn
   `target_label: unit` into `unit=""` — which deletes the label. That shipped
   for months: 23 hosts had no `unit` label and the mail and unbound dashboards'
   `unit=` panels rendered empty.

2. **The senders' journal re-read horizon must fit Loki's accept window.**
   Loki accepts out-of-order pushes within `ingester.max_chunk_age / 2` of a
   stream's newest entry and rejects the rest as `too_far_behind` (1.93M lines
   in a 7-day sample). `alloy_host_journal_max_age` is what a restarted Alloy
   replays, so it must stay inside that window; raising either number alone
   silently re-opens the loss.

Run with pytest:
    pytest scripts/test_alloy_journal_pipeline.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CONFIG_J2 = REPO / "ansible" / "roles" / "alloy_host" / "templates" / "config.alloy.j2"
DEFAULTS = REPO / "ansible" / "roles" / "alloy_host" / "defaults" / "main.yml"
LOKI_RELEASE = (
    REPO / "kubernetes" / "infrastructure" / "observability" / "loki" / "release.yaml"
)

_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> int:
    """Seconds from a Go-style duration ("3h", "90m", "1h30m")."""
    parts = re.findall(r"(\d+)([smhd])", str(text))
    assert parts, f"unparseable duration: {text!r}"
    assert "".join(n + u for n, u in parts) == str(text).strip(), (
        f"unparseable duration: {text!r}"
    )
    return sum(int(n) * _DURATION_UNITS[u] for n, u in parts)


def alloy_blocks(config_text: str) -> list[tuple[str, str, str]]:
    """(component_type, label, body) for every top-level Alloy block.

    Bodies are matched by brace depth, so nested `rule { ... }` blocks stay with
    their parent.
    """
    blocks: list[tuple[str, str, str]] = []
    for match in re.finditer(r'^([\w.]+)\s+"([^"]+)"\s*\{', config_text, re.MULTILINE):
        depth, i = 0, match.end() - 1
        while i < len(config_text):
            if config_text[i] == "{":
                depth += 1
            elif config_text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        blocks.append((match.group(1), match.group(2), config_text[match.end() : i]))
    return blocks


def journal_sources(config_text: str) -> list[tuple[str, str]]:
    return [
        (label, body)
        for kind, label, body in alloy_blocks(config_text)
        if kind == "loki.source.journal"
    ]


def _argument(body: str, name: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+)$", body, re.MULTILINE)
    return match.group(1).strip() if match else None


def loki_max_chunk_age() -> str:
    release = yaml.safe_load(LOKI_RELEASE.read_text())
    ingester = release["spec"]["values"]["loki"].get("ingester") or {}
    max_chunk_age = ingester.get("max_chunk_age")
    assert max_chunk_age, (
        "loki/release.yaml no longer pins loki.ingester.max_chunk_age — the "
        "chart default (2h) gives a 1h out-of-order accept window, narrower "
        "than the alloy_host journal re-read horizon"
    )
    return str(max_chunk_age)


def journal_max_age_default() -> str:
    defaults = yaml.safe_load(DEFAULTS.read_text())
    value = defaults.get("alloy_host_journal_max_age")
    assert value, "alloy_host defaults no longer set alloy_host_journal_max_age"
    return str(value)


class TestRelabelRulesAppliedOnce:
    def test_source_does_not_forward_into_the_component_whose_rules_it_uses(self):
        config = CONFIG_J2.read_text()
        sources = journal_sources(config)
        assert sources, "no loki.source.journal block found in config.alloy.j2"
        for label, body in sources:
            rules = _argument(body, "relabel_rules")
            if not rules:
                continue
            component = re.match(r"(loki\.relabel\.[\w-]+)\.rules", rules)
            assert component, f"unexpected relabel_rules expression: {rules!r}"
            receiver = f"{component.group(1)}.receiver"
            forward_to = _argument(body, "forward_to") or ""
            assert receiver not in forward_to, (
                f'loki.source.journal "{label}" both passes '
                f"{component.group(1)}.rules as relabel_rules and forwards into "
                f"{receiver}: the rules run twice and the second pass deletes "
                f"the `unit` label (the __journal_* metadata is gone by then)"
            )

    def test_rules_holder_component_forwards_nowhere(self):
        config = CONFIG_J2.read_text()
        referenced = {
            re.match(r"(loki\.relabel\.[\w-]+)\.rules", _argument(body, "relabel_rules")).group(1)
            for _, body in journal_sources(config)
            if _argument(body, "relabel_rules")
        }
        for kind, label, body in alloy_blocks(config):
            if kind != "loki.relabel" or f"loki.relabel.{label}" not in referenced:
                continue
            forward_to = _argument(body, "forward_to")
            assert forward_to == "[]", (
                f'loki.relabel "{label}" is used as a rules holder but forwards '
                f"to {forward_to} — nothing sends entries into it, so that "
                f"receiver list is either dead config or the double-apply bug"
            )

    def test_every_relabel_rule_still_maps_a_journal_field(self):
        config = CONFIG_J2.read_text()
        rules = re.findall(r"source_labels\s*=\s*\[\"([^\"]+)\"\]", config)
        assert rules, "no relabel rules found in config.alloy.j2"
        assert all(name.startswith("__journal") for name in rules), (
            f"non-journal source_labels in the journal relabel rules: {rules}"
        )

    def test_unit_is_the_only_journal_stream_label(self):
        """Every rule here is a Loki STREAM label, so each one multiplies the
        open chunks the ingester holds for max_chunk_age. `priority` and
        `hostname` were measured and dropped (see the template comment);
        re-adding one is a cardinality decision against
        max_global_streams_per_user, not a tidy-up. Asserted on the rendered
        rules, not as a file substring — the template's rationale comment names
        both dropped keys and `//` is Alloy syntax, so they render.
        """
        config = CONFIG_J2.read_text()
        targets = re.findall(r"(?m)^\s*target_label\s*=\s*\"([^\"]+)\"", config)
        assert targets == ["unit"], (
            f"journal relabel rules emit stream labels {targets}, expected "
            f"['unit'] only"
        )


class TestJournalReplayFitsLokiAcceptWindow:
    def test_journal_max_age_within_loki_out_of_order_window(self):
        max_age = parse_duration(journal_max_age_default())
        accept_window = parse_duration(loki_max_chunk_age()) // 2
        assert max_age <= accept_window, (
            f"alloy_host_journal_max_age ({journal_max_age_default()}) exceeds "
            f"Loki's out-of-order accept window (max_chunk_age "
            f"{loki_max_chunk_age()} / 2). A restarted Alloy would replay "
            f"entries Loki rejects as too_far_behind — silent log loss."
        )

    def test_template_renders_the_pinned_max_age(self):
        body = dict(journal_sources(CONFIG_J2.read_text()))["system"]
        max_age = _argument(body, "max_age")
        assert max_age and "alloy_host_journal_max_age" in max_age, (
            "loki.source.journal does not render alloy_host_journal_max_age; "
            "Alloy's 7h default is wider than Loki's accept window"
        )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
