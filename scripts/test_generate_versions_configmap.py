"""Tests for scripts/generate-versions-configmap.py.

Run via `task scripts:test` (pytest).
"""
from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "gen_versions_configmap",
    Path(__file__).parent / "generate-versions-configmap.py",
)
gen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen)  # type: ignore[union-attr]


class TestFlatten:
    """Core behavior of flatten() — the single function that determines what
    ends up in the ConfigMap. Regressions here silently break every Flux
    postBuild substitution downstream."""

    def test_top_level_version_suffix_kept(self):
        assert gen.flatten({"authentik_version": "2026.2.2", "unrelated": "x"}) == {
            "authentik_version": "2026.2.2"
        }

    def test_non_version_suffix_dropped(self):
        assert gen.flatten({"some_random_key": "foo"}) == {}

    def test_integer_version_coerced_to_str(self):
        # Matches the actual debian_version: 13 case in all.yml.
        assert gen.flatten({"debian_version": 13}) == {"debian_version": "13"}

    def test_nested_helm_chart_versions_flattened(self):
        result = gen.flatten(
            {"helm_chart_versions": {"traefik": "40.0.0", "cert_manager": "v1.20.2"}}
        )
        assert result == {
            "helm_chart_versions_traefik": "40.0.0",
            "helm_chart_versions_cert_manager": "v1.20.2",
        }

    def test_empty_input_returns_empty(self):
        assert gen.flatten({}) == {}

    def test_nested_non_registered_key_ignored(self):
        # Only keys in NESTED_KEYS get nested flattening.
        assert gen.flatten({"not_nested": {"foo": "1"}}) == {}


class TestTypeSafety:
    """flatten() must reject surprise types that would silently corrupt the
    ConfigMap. bool is the obvious one (bool is int subclass in Python)."""

    def test_bool_at_top_level_silently_skipped(self):
        # We intentionally skip top-level bools instead of raising so a
        # mis-quoted value like `some_version: yes` doesn't block the
        # entire sync — but it gets dropped, which the "no flat keys"
        # check surfaces if every key drops.
        assert gen.flatten({"flag_version": True}) == {}

    def test_bool_inside_nested_helm_chart_raises(self):
        with pytest.raises(ValueError, match="bool"):
            gen.flatten({"helm_chart_versions": {"traefik": True}})

    def test_dict_inside_nested_helm_chart_raises(self):
        with pytest.raises(ValueError, match="non-scalar"):
            gen.flatten({"helm_chart_versions": {"traefik": {"nested": "bad"}}})

    def test_list_inside_nested_helm_chart_raises(self):
        with pytest.raises(ValueError, match="non-scalar"):
            gen.flatten({"helm_chart_versions": {"traefik": ["1.0", "2.0"]}})

    def test_hyphen_in_nested_key_raises(self):
        # Flux postBuild var names require [A-Za-z_][A-Za-z0-9_]*.
        # A nested key with a hyphen would produce an invalid var name;
        # catch it at generation time instead of silently shipping it.
        with pytest.raises(ValueError, match="Flux postBuild"):
            gen.flatten({"helm_chart_versions": {"external-dns": "1.0"}})


class TestDeterministic:
    """The generated output is diffed by CI, so flatten must be order-stable."""

    def test_twice_same_result(self):
        data = {
            "authentik_version": "1.0",
            "mealie_version": "2.0",
            "helm_chart_versions": {"traefik": "3.0", "cert_manager": "4.0"},
        }
        assert gen.flatten(data) == gen.flatten(data)


class TestMain:
    """End-to-end main() coverage — the CI out-of-sync contract (this script's
    reason to exist) was previously verified only via flatten(). These tests
    redirect gen.ALL_YML / gen.OUT to temp paths."""

    @staticmethod
    def _set_paths(monkeypatch, tmp_path, all_yml_text: str | None):
        out = tmp_path / "versions-configmap.yaml"
        all_yml = tmp_path / "all.yml"
        if all_yml_text is not None:
            all_yml.write_text(all_yml_text)
        monkeypatch.setattr(gen, "ALL_YML", all_yml)
        monkeypatch.setattr(gen, "OUT", out)
        # main() prints OUT.relative_to(REPO); point REPO at tmp_path so the
        # relative_to() call doesn't raise for our temp OUT.
        monkeypatch.setattr(gen, "REPO", tmp_path)
        return out

    def test_golden_output_with_header(self, monkeypatch, tmp_path):
        out = self._set_paths(
            monkeypatch,
            tmp_path,
            textwrap.dedent(
                """\
                authentik_version: "2026.2.2"
                debian_version: 13
                some_unrelated_key: "ignored"
                helm_chart_versions:
                  traefik: "40.0.0"
                  cert_manager: "v1.20.2"
                """
            ),
        )
        rc = gen.main()
        assert rc == 0
        produced = out.read_text()
        # Exact AUTO-GENERATED header.
        assert produced.startswith(
            "---\n"
            "# AUTO-GENERATED by scripts/generate-versions-configmap.py from\n"
            "# ansible/inventories/prod/group_vars/all.yml. Do NOT edit by hand.\n"
            "# Run `task flux:sync-versions` to regenerate. CI fails if out of sync.\n"
        )
        # ConfigMap shape + flattened keys present; unrelated key dropped.
        import yaml

        body = produced.split("\n", 4)[-1]  # strip the 4-line header comment
        cm = yaml.safe_load(body)
        assert cm["kind"] == "ConfigMap"
        assert cm["metadata"] == {"name": "cluster-versions", "namespace": "flux-system"}
        assert cm["data"]["authentik_version"] == "2026.2.2"
        assert cm["data"]["debian_version"] == "13"
        assert cm["data"]["helm_chart_versions_traefik"] == "40.0.0"
        assert cm["data"]["helm_chart_versions_cert_manager"] == "v1.20.2"
        assert "some_unrelated_key" not in cm["data"]

    def test_output_is_byte_identical_across_runs(self, monkeypatch, tmp_path):
        text = 'authentik_version: "1.0"\nmealie_version: "2.0"\n'
        out = self._set_paths(monkeypatch, tmp_path, text)
        assert gen.main() == 0
        first = out.read_text()
        assert gen.main() == 0
        second = out.read_text()
        assert first == second

    def test_missing_file_exits_one(self, monkeypatch, tmp_path):
        # all_yml_text=None -> file never created.
        self._set_paths(monkeypatch, tmp_path, None)
        assert gen.main() == 1

    def test_empty_file_exits_one(self, monkeypatch, tmp_path):
        self._set_paths(monkeypatch, tmp_path, "")
        assert gen.main() == 1

    def test_non_mapping_top_level_exits_one(self, monkeypatch, tmp_path):
        self._set_paths(monkeypatch, tmp_path, "- just\n- a\n- list\n")
        assert gen.main() == 1

    def test_no_version_keys_exits_one(self, monkeypatch, tmp_path):
        # Valid mapping but nothing matches the _version suffix / nested keys.
        self._set_paths(monkeypatch, tmp_path, "foo: bar\nbaz: qux\n")
        assert gen.main() == 1

    def test_invalid_yaml_exits_one(self, monkeypatch, tmp_path):
        self._set_paths(monkeypatch, tmp_path, "foo: [unterminated\n")
        assert gen.main() == 1
