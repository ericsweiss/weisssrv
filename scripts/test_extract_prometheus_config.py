"""Tests the local fork of scripts/extract-prometheus-config.py.

Covers the fork's own behaviour: rules are the UNION of the HelmRelease and the
standalone PrometheusRule manifests, and a null-valued key hits the error path.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml
from script_loader import load_script

ext = load_script("extract-prometheus-config.py")


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

    def test_rendered_config_still_parses_as_yaml(self, tmp_path: Path):
        out = tmp_path / "am.yaml"
        ext.extract_alertmanager(out)
        assert isinstance(yaml.safe_load(out.read_text()), dict)

    def test_url_placeholders_render_as_parseable_urls(self):
        """amtool parses webhook/API targets as URLs, so a placeholder whose
        name ends in `url` must not render as a bare scalar."""
        assert ext.dummy_for("discordWebhookUrl").startswith("https://")
        assert ext.dummy_for("smtpPassword") == "dummy"


class TestNullTolerance:
    """A key present with an explicit YAML null must hit the script's own error
    path, not a TypeError traceback — `.get(k, {})` returns None for those."""

    def test_load_returns_a_mapping_for_empty_and_null_docs(self, tmp_path: Path):
        for body in ("", "null\n", "# only a comment\n"):
            f = tmp_path / "doc.yaml"
            f.write_text(body)
            assert ext._load(f) == {}

    def test_null_alertmanager_template_returns_1(self, tmp_path: Path):
        bad = tmp_path / "am.yaml"
        bad.write_text("spec:\n  target:\n    template:\n      data:\n")
        assert ext.extract_alertmanager(tmp_path / "out.yaml", bad) == 1

    def test_null_release_values_returns_1(self, tmp_path: Path):
        bad = tmp_path / "release.yaml"
        bad.write_text("spec:\n  values:\n")
        assert ext.extract_rules(tmp_path / "out.yaml", bad, tmp_path / "absent") == 1


class TestPrometheusRuleSource:
    """Alert groups may live in the HelmRelease values, in standalone
    PrometheusRule manifests under observability/rules/, or in both."""

    RULE_DOC = (
        "apiVersion: monitoring.coreos.com/v1\n"
        "kind: PrometheusRule\n"
        "metadata:\n"
        "  name: zfs\n"
        "spec:\n"
        "  groups:\n"
        "    - name: zfs\n"
        "      rules:\n"
        "        - alert: ZFSPoolNotOnline\n"
        "          expr: zfs_pool_health != 0\n"
    )

    def test_rules_dir_groups_are_collected(self, tmp_path: Path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "zfs.yaml").write_text(self.RULE_DOC)
        empty_release = tmp_path / "release.yaml"
        empty_release.write_text("spec:\n  values: {}\n")
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, empty_release, rules_dir) == 0
        doc = yaml.safe_load(out.read_text())
        assert [g["name"] for g in doc["groups"]] == ["zfs"]

    def test_non_prometheusrule_docs_are_ignored(self, tmp_path: Path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "mixed.yaml").write_text(
            "kind: ConfigMap\nmetadata:\n  name: x\n---\n" + self.RULE_DOC
        )
        empty_release = tmp_path / "release.yaml"
        empty_release.write_text("spec:\n  values: {}\n")
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, empty_release, rules_dir) == 0
        assert len(yaml.safe_load(out.read_text())["groups"]) == 1

    def test_a_subdirectory_and_a_yml_suffix_are_both_scanned(self, tmp_path: Path):
        """A rule file promtool never sees is an unlinted expr; the walk is
        recursive and covers both YAML suffixes."""
        rules_dir = tmp_path / "rules"
        (rules_dir / "nested").mkdir(parents=True)
        (rules_dir / "nested" / "zfs.yaml").write_text(self.RULE_DOC)
        (rules_dir / "mail.yml").write_text(
            self.RULE_DOC.replace("zfs", "mail").replace(
                "ZFSPoolNotOnline", "MailQueueGrowing"
            )
        )
        empty_release = tmp_path / "release.yaml"
        empty_release.write_text("spec:\n  values: {}\n")
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, empty_release, rules_dir) == 0
        doc = yaml.safe_load(out.read_text())
        assert sorted(g["name"] for g in doc["groups"]) == ["mail", "zfs"]

    def test_live_tree_yields_every_group_from_both_sources(self, tmp_path: Path):
        # Guards the split itself: whichever home the groups move to, the
        # extractor must still see all of them.
        combined = len(ext._release_groups(ext.DEFAULT_RELEASE)) + len(
            ext._prometheusrule_groups(ext.DEFAULT_RULES_DIR)
        )
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out) == 0
        assert len(yaml.safe_load(out.read_text())["groups"]) == combined


class TestRulesDirIsFullyShipped:
    """Every file in observability/rules/ is listed in its kustomization.yaml."""

    RULES_DIR = Path(__file__).resolve().parent.parent / ext.DEFAULT_RULES_DIR
    KUSTOMIZATION = RULES_DIR / "kustomization.yaml"

    def _resources(self) -> set[str]:
        doc = yaml.safe_load(self.KUSTOMIZATION.read_text()) or {}
        return set(doc.get("resources") or [])

    def _rule_files(self) -> set[str]:
        return {
            p.name
            for p in self.RULES_DIR.glob("*.yaml")
            if p.name != self.KUSTOMIZATION.name
        }

    def test_every_rule_file_is_listed_and_every_listing_exists(self):
        on_disk, listed = self._rule_files(), self._resources()
        assert on_disk, "no PrometheusRule files found — wrong directory?"
        unlisted = sorted(on_disk - listed)
        assert not unlisted, (
            f"linted + unit-tested but never deployed (add to "
            f"{self.KUSTOMIZATION.relative_to(self.RULES_DIR.parents[3])} "
            f"resources:): {unlisted}"
        )
        orphaned = sorted(listed - on_disk)
        assert not orphaned, f"listed in resources: but not on disk: {orphaned}"

    def test_every_listed_file_is_a_prometheusrule(self):
        """A non-PrometheusRule in resources: would ship but never be linted."""
        for name in sorted(self._resources()):
            docs = [
                d
                for d in yaml.safe_load_all((self.RULES_DIR / name).read_text())
                if isinstance(d, dict)
            ]
            assert docs, f"{name} holds no YAML document"
            assert all(d.get("kind") == "PrometheusRule" for d in docs), (
                f"{name} holds a non-PrometheusRule document; "
                f"extract-prometheus-config.py would skip it"
            )


class TestRequireFlags:
    """Each rule source can be declared mandatory, so an emptied or renamed one
    reds the gate instead of linting the subset that is left.
    """

    def _release(self, tmp_path: Path, groups: list | None) -> Path:
        values: dict = {}
        if groups is not None:
            values["additionalPrometheusRulesMap"] = {"custom": {"groups": groups}}
        path = tmp_path / "release.yaml"
        path.write_text(yaml.safe_dump({"spec": {"values": values}}))
        return path

    def _rules_dir(self, tmp_path: Path) -> Path:
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "a.yaml").write_text(
            yaml.safe_dump(
                {
                    "kind": "PrometheusRule",
                    "spec": {"groups": [{"name": "dir", "rules": [{"record": "r", "expr": "1"}]}]},
                }
            )
        )
        return rules_dir

    def test_require_release_rules_fails_on_an_emptied_map(self, tmp_path: Path):
        release = self._release(tmp_path, None)
        rules_dir = self._rules_dir(tmp_path)
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, release, rules_dir) == 0
        assert ext.extract_rules(out, release, rules_dir, True) == 1

    def test_require_release_rules_passes_when_the_map_has_groups(self, tmp_path: Path):
        release = self._release(
            tmp_path, [{"name": "inline", "rules": [{"record": "r", "expr": "1"}]}]
        )
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, release, self._rules_dir(tmp_path), True) == 0

    def test_require_rules_dir_fails_on_an_absent_tree(self, tmp_path: Path):
        release = self._release(
            tmp_path, [{"name": "inline", "rules": [{"record": "r", "expr": "1"}]}]
        )
        out = tmp_path / "rules.yaml"
        argv = [
            "extract-prometheus-config.py",
            "rules",
            str(out),
            "--release",
            str(release),
            "--rules-dir",
            str(tmp_path / "gone"),
        ]
        assert ext.main(argv + ["--require-rules-dir"]) == 2
        # Without the flag the named-but-absent tree is still an operator error.
        assert ext.main(argv) == 2

    def test_require_rules_dir_passes_when_the_tree_exists(self, tmp_path: Path):
        release = self._release(tmp_path, None)
        out = tmp_path / "rules.yaml"
        assert ext.main([
            "extract-prometheus-config.py",
            "rules",
            str(out),
            "--release",
            str(release),
            "--rules-dir",
            str(self._rules_dir(tmp_path)),
            "--require-rules-dir",
        ]) == 0


class TestCli:
    """argparse exits 2 on a usage error; assert that through the process so the
    exit code CI reads is the one asserted."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(Path(__file__).parent / "extract-prometheus-config.py"), *args],
            capture_output=True, text=True,
        ).returncode

    def test_bad_subcommand_returns_2(self):
        assert self._run("bogus", "/tmp/x") == 2

    def test_missing_args_returns_2(self):
        assert self._run("rules") == 2
