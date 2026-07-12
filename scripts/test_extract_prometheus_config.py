"""Tests for scripts/extract-prometheus-config.py.

Validates the extraction/rendering against the live kube-prometheus-stack files
so a structural regression (or a template placeholder the renderer misses) fails
here, before the promtool/amtool CI job downloads its binaries.

Run via `task scripts:test` (pytest).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

_SPEC = importlib.util.spec_from_file_location(
    "extract_prometheus_config",
    Path(__file__).parent / "extract-prometheus-config.py",
)
ext = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ext)  # type: ignore[union-attr]


class TestExtractRules:
    def test_produces_promtool_shaped_groups(self, tmp_path: Path):
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out) == 0
        doc = yaml.safe_load(out.read_text())
        assert "groups" in doc and isinstance(doc["groups"], list)
        assert doc["groups"], "expected at least one rule group"
        # Every group has a name and a rules list.
        for group in doc["groups"]:
            assert "name" in group
            assert isinstance(group.get("rules"), list)

    def test_every_rule_has_alert_and_expr(self, tmp_path: Path):
        out = tmp_path / "rules.yaml"
        ext.extract_rules(out)
        doc = yaml.safe_load(out.read_text())
        for group in doc["groups"]:
            for rule in group["rules"]:
                # recording rules use `record`; alerts use `alert` — either way
                # an `expr` is mandatory (promtool would reject a missing one).
                assert "expr" in rule
                assert "alert" in rule or "record" in rule


class TestExtractAlertmanager:
    def test_no_unrendered_template_remains(self, tmp_path: Path):
        out = tmp_path / "am.yaml"
        assert ext.extract_alertmanager(out) == 0
        rendered = out.read_text()
        assert "{{" not in rendered and "}}" not in rendered

    def test_secret_placeholders_rendered_as_urls(self, tmp_path: Path):
        out = tmp_path / "am.yaml"
        ext.extract_alertmanager(out)
        doc = yaml.safe_load(out.read_text())
        # Rendered config must parse as YAML and carry the dummy webhook URL.
        blob = yaml.safe_dump(doc)
        assert "discord.example" in blob
        assert "hc-ping.example" in blob

    def test_placeholder_regex_maps_known_vars(self):
        import re

        m = ext._PLACEHOLDER_RE.match("{{ .discordWebhookUrl | quote }}")
        assert m and m.group(1) == "discordWebhookUrl"
        assert ext._render_placeholders(m) == '"https://discord.example/api/webhooks/1/abc"'
        m2 = ext._PLACEHOLDER_RE.match("{{ .unknownVar | quote }}")
        assert ext._render_placeholders(m2) == '"dummy"'


class TestCli:
    def test_bad_subcommand_returns_2(self):
        assert ext.main(["prog", "bogus", "/tmp/x"]) == 2

    def test_missing_args_returns_2(self):
        assert ext.main(["prog", "rules"]) == 2
