#!/usr/bin/env python3
"""Unit tests for generate-molecule-pipeline.py.

Covers the affected-set computation (direct role selection, transitive role
dependencies, integration-test selection, global triggers, inventory/playbook
handling), the coverage-bug failure modes, and the child-pipeline rendering
(including the empty -> no-op child). Runs partly against the real repo tree
(like test_check_versions.py / test_check_molecule_matrix_coverage.py, which
assert on live matrix + role files) and partly with synthetic graphs through the
pure compute_affected().

Run with pytest (preferred):
    pytest scripts/test_generate_molecule_pipeline.py -v
"""

import importlib.util
import sys
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Import the hyphenated-name module via importlib (same shim as test_check_versions.py).
_script_path = Path(__file__).parent / "generate-molecule-pipeline.py"
_spec = importlib.util.spec_from_file_location("generate_molecule_pipeline", _script_path)
gmp = importlib.util.module_from_spec(_spec)
sys.modules["generate_molecule_pipeline"] = gmp
_spec.loader.exec_module(gmp)

REPO = gmp.REPO


def _sel(changed):
    """Selection for a changed-file list, computed against the real repo."""
    return gmp.select(changed, repo=REPO)


class TestMatrixParsing(unittest.TestCase):
    """The scenario universe is parsed from the real .gitlab-ci.yml matrix."""

    def setUp(self):
        self.matrix, self.integration = gmp.parse_molecule_matrix(REPO / ".gitlab-ci.yml")

    def test_known_role_scenarios_present(self):
        # Roles with multiple scenarios must expose all of them.
        self.assertIn("tls", self.matrix["adguard_home"])
        self.assertIn("default", self.matrix["adguard_home"])
        self.assertEqual(sorted(self.matrix["k3s"]), ["agent", "default"])
        self.assertEqual(sorted(self.matrix["resolv_conf"]), ["default", "empty-search"])

    def test_integration_stack_list(self):
        self.assertEqual(
            self.integration,
            ["base-infrastructure", "cert-distribution", "dns-stack", "mail-stack", "storage-stack"],
        )

    def test_unparseable_matrix_raises(self):
        # A CI file with no molecule-tests job must fail loudly, never silently
        # yield an under-selected pipeline.
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as tf:
            tf.write("stages: [test]\n")
            bad = Path(tf.name)
        try:
            with self.assertRaises(RuntimeError):
                gmp.parse_molecule_matrix(bad)
        finally:
            bad.unlink()


class TestDerivedGraph(unittest.TestCase):
    """The role dependency edges are derived from the real repo, not hardcoded."""

    def setUp(self):
        roles_dir = REPO / "ansible" / "roles"
        self.known = {p.name for p in roles_dir.iterdir() if p.is_dir()}
        self.deps = gmp.build_role_graph(roles_dir, known_roles=self.known)
        self.integration = gmp.build_integration_map(
            REPO / "ansible" / "integration-tests", known_roles=self.known
        )

    def test_shared_role_edges(self):
        # The documented shared/wrapper roles must be discovered.
        self.assertEqual(self.deps["zfs_exporter"], {"prometheus_exporter"})
        self.assertEqual(self.deps["unbound_exporter"], {"prometheus_exporter"})
        self.assertEqual(self.deps["adguard_sync"], {"prometheus_exporter"})
        self.assertEqual(self.deps["compose_app"], {"docker_engine"})
        self.assertTrue({"compose_app", "zvol_mount"}.issubset(self.deps["immich"]))
        self.assertIn("apt_signed_repo", self.deps["alloy_host"])
        self.assertIn("resolv_conf", self.deps["base"])
        self.assertIn("unbound", self.deps["adguard_home"])  # meta dependency

    def test_no_self_edges_and_only_known_roles(self):
        for consumer, providers in self.deps.items():
            self.assertNotIn(consumer, providers, f"{consumer} has a self-edge")
            self.assertTrue(providers.issubset(self.known), f"{consumer} -> unknown {providers}")

    def test_integration_role_map(self):
        self.assertEqual(self.integration["base-infrastructure"], {"base", "qol", "tailscale"})
        self.assertEqual(self.integration["cert-distribution"], {"acme_certs"})
        self.assertEqual(self.integration["dns-stack"], {"adguard_home", "adguard_sync", "unbound"})
        self.assertEqual(self.integration["mail-stack"], {"postfix_null_client", "smtp_relay"})
        self.assertEqual(self.integration["storage-stack"], {"nas_storage"})


class TestDirectRoleSelection(unittest.TestCase):
    """A leaf role change selects only that role's scenarios."""

    def test_plex_leaf(self):
        sel = _sel(["ansible/roles/plex/tasks/install.yml"])
        self.assertEqual(sel.scenarios, {("plex", "default")})
        self.assertEqual(sel.integration, set())  # plex is in no integration stack
        self.assertFalse(sel.full)

    def test_multi_scenario_role_selects_all_scenarios(self):
        # A change anywhere under a role selects every scenario the role has.
        sel = _sel(["ansible/roles/adguard_home/tasks/api_base_config.yml"])
        self.assertIn(("adguard_home", "default"), sel.scenarios)
        self.assertIn(("adguard_home", "tls"), sel.scenarios)

    def test_roles_readme_is_not_a_role_change(self):
        # A file directly under ansible/roles/ (the roles table) selects nothing.
        sel = _sel(["ansible/roles/README.md"])
        self.assertTrue(sel.empty)


class TestTransitiveRoleDeps(unittest.TestCase):
    """A provider-role change selects every consumer's scenarios, transitively."""

    def test_prometheus_exporter_fan_out(self):
        sel = _sel(["ansible/roles/prometheus_exporter/tasks/main.yml"])
        roles = {r for r, _ in sel.scenarios}
        self.assertEqual(
            roles,
            {"prometheus_exporter", "adguard_sync", "unbound_exporter", "zfs_exporter"},
        )
        # adguard_sync is exercised by dns-stack, so the stack must be selected.
        self.assertIn("dns-stack", sel.integration)

    def test_apt_signed_repo_deep_fan_out(self):
        sel = _sel(["ansible/roles/apt_signed_repo/defaults/main.yml"])
        roles = {r for r, _ in sel.scenarios}
        # apt_signed_repo -> {alloy_host, docker_engine, gitlab, plex};
        # docker_engine -> compose_app -> {immich, immich_ml, nextcloud}.
        self.assertTrue(
            {
                "apt_signed_repo", "alloy_host", "docker_engine", "gitlab", "plex",
                "compose_app", "immich", "immich_ml", "nextcloud",
            }.issubset(roles)
        )

    def test_resolv_conf_selects_base_and_its_consumers(self):
        sel = _sel(["ansible/roles/resolv_conf/tasks/main.yml"])
        roles = {r for r, _ in sel.scenarios}
        # resolv_conf -> {adguard_home, base}; base -> {qol, nas_storage}.
        self.assertTrue({"resolv_conf", "adguard_home", "base", "qol", "nas_storage"}.issubset(roles))
        # base-infrastructure (base, qol) and storage-stack (nas_storage->base) and
        # dns-stack (adguard_home->resolv_conf) all depend on it.
        self.assertTrue(
            {"base-infrastructure", "storage-stack", "dns-stack"}.issubset(sel.integration)
        )


class TestIntegrationSelection(unittest.TestCase):
    """Integration stacks are selected by the roles they exercise + a direct dir change."""

    def test_role_in_stack_selects_stack(self):
        sel = _sel(["ansible/roles/tailscale/tasks/main.yml"])
        self.assertIn("base-infrastructure", sel.integration)  # exercises tailscale
        self.assertEqual({r for r, _ in sel.scenarios}, {"tailscale"})

    def test_integration_dir_change_selects_only_that_stack(self):
        sel = _sel(["ansible/integration-tests/dns-stack/molecule/default/converge.yml"])
        self.assertEqual(sel.integration, {"dns-stack"})
        self.assertEqual(sel.scenarios, set())  # no role scenarios from a stack edit

    def test_shared_prepare_selects_all_stacks(self):
        sel = _sel(["ansible/integration-tests/_shared/prepare.yml"])
        self.assertEqual(
            sel.integration,
            {"base-infrastructure", "cert-distribution", "dns-stack", "mail-stack", "storage-stack"},
        )
        self.assertEqual(sel.scenarios, set())

    def test_unknown_stack_dir_raises(self):
        with self.assertRaises(gmp.CoverageError):
            _sel(["ansible/integration-tests/no-such-stack/molecule/default/converge.yml"])


class TestGlobalTriggers(unittest.TestCase):
    """Any global-trigger path selects the FULL matrix + all integration tests."""

    def setUp(self):
        self.matrix, self.integration = gmp.parse_molecule_matrix(REPO / ".gitlab-ci.yml")
        self.full_scenarios = {(r, s) for r, scen in self.matrix.items() for s in scen}

    def _assert_full(self, path):
        sel = _sel([path])
        self.assertTrue(sel.full, f"{path} should trigger the full matrix")
        self.assertEqual(sel.scenarios, self.full_scenarios, path)
        self.assertEqual(sel.integration, set(self.integration), path)

    def test_each_global_trigger(self):
        for path in [
            "ansible/molecule/base.yml",
            "ansible/molecule/tasks/container-warmup.yml",
            "ansible/requirements.yml",
            "docker/molecule-ci/Dockerfile",
            "docker/molecule-test/Dockerfile",
            ".gitlab-ci.yml",
            "scripts/molecule-retry.sh",
            "scripts/generate-molecule-pipeline.py",
        ]:
            with self.subTest(path=path):
                self._assert_full(path)

    def test_malformed_yaml_raises(self):
        """A YAML parse error must fail loud, never silently drop graph edges."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "broken.yml"
            bad.write_text("key: [unclosed")
            with self.assertRaisesRegex(gmp.CoverageError, "YAML parse failed"):
                gmp._load_yaml(bad)

    def test_absent_yaml_is_none(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(gmp._load_yaml(Path(td) / "absent.yml"))

    def test_global_trigger_wins_over_unknown_role(self):
        # A global trigger short-circuits before role validation, so a bogus role
        # path alongside .gitlab-ci.yml still yields the full matrix (no raise).
        sel = _sel([".gitlab-ci.yml", "ansible/roles/bogus_role/tasks/main.yml"])
        self.assertTrue(sel.full)


class TestInventoryAndPlaybookPaths(unittest.TestCase):
    """Inventory / playbook / cfg changes select scenarios only where derived."""

    def test_group_vars_all_selects_integration_only(self):
        # The 5 integration converges vars_files all.yml; role scenarios do not.
        sel = _sel(["ansible/inventories/prod/group_vars/all.yml"])
        self.assertEqual(sel.scenarios, set())
        self.assertEqual(
            sel.integration,
            {"base-infrastructure", "cert-distribution", "dns-stack", "mail-stack", "storage-stack"},
        )

    def test_playbook_only_is_empty(self):
        self.assertTrue(_sel(["ansible/playbooks/site.yml"]).empty)

    def test_host_vars_hosts_and_cfg_are_empty(self):
        for path in [
            "ansible/inventories/prod/hosts.yml",
            "ansible/inventories/prod/host_vars/dns-01.yml",
            "ansible/ansible.cfg",
        ]:
            with self.subTest(path=path):
                self.assertTrue(_sel([path]).empty, path)

    def test_non_ansible_paths_are_empty(self):
        sel = _sel(["docs/16-next-steps.md", "kubernetes/apps/plex/release.yaml",
                    "README.md", "scripts/check-versions.py"])
        self.assertTrue(sel.empty)


class TestFailureModes(unittest.TestCase):
    """Coverage bugs fail loud; empty diffs are handled."""

    def test_unknown_role_raises(self):
        with self.assertRaises(gmp.CoverageError):
            _sel(["ansible/roles/does_not_exist/tasks/main.yml"])

    def test_empty_diff_is_empty_selection(self):
        self.assertTrue(_sel([]).empty)

    def test_selected_role_without_scenarios_raises(self):
        # A synthetic graph where a selected role has no matrix scenarios must
        # raise rather than silently drop it (never emit fewer than demanded).
        with self.assertRaises(gmp.CoverageError):
            gmp.compute_affected(
                ["ansible/roles/provider/tasks/main.yml"],
                matrix={"provider": ["default"]},
                integration_tests=[],
                # consumer depends on provider but has no matrix entry.
                role_deps={"consumer": {"provider"}},
                integration_map={},
                inventory_consumers={},
            )


class TestComputeAffectedSynthetic(unittest.TestCase):
    """Pure compute_affected() over synthetic graphs (transitivity + direction)."""

    MATRIX = {"a": ["default"], "b": ["default"], "c": ["default"], "leaf": ["default"]}
    # b consumes a; c consumes b  => a change fans out a->b->c.
    DEPS = {"b": {"a"}, "c": {"b"}}

    def _compute(self, changed, integration_map=None):
        return gmp.compute_affected(
            changed,
            matrix=self.MATRIX,
            integration_tests=list(integration_map or []),
            role_deps=self.DEPS,
            integration_map=integration_map or {},
            inventory_consumers={},
        )

    def test_provider_change_fans_out_to_consumers(self):
        sel = self._compute(["ansible/roles/a/tasks/main.yml"])
        self.assertEqual({r for r, _ in sel.scenarios}, {"a", "b", "c"})

    def test_consumer_change_does_not_pull_providers(self):
        # Changing c (a consumer) must NOT select its providers a/b.
        sel = self._compute(["ansible/roles/c/tasks/main.yml"])
        self.assertEqual({r for r, _ in sel.scenarios}, {"c"})

    def test_leaf_change_selects_only_leaf(self):
        sel = self._compute(["ansible/roles/leaf/tasks/main.yml"])
        self.assertEqual({r for r, _ in sel.scenarios}, {"leaf"})

    def test_integration_provider_coupling(self):
        # Stack exercises c; c depends (transitively) on a. Changing a selects the
        # stack because a is in c's provider closure.
        sel = self._compute(["ansible/roles/a/tasks/main.yml"], integration_map={"stack": {"c"}})
        self.assertIn("stack", sel.integration)


class TestRendering(unittest.TestCase):
    """The emitted child pipeline is valid, deterministic, and reuses the template."""

    def test_noop_child_when_empty(self):
        out = gmp.render_child_pipeline(gmp.Selection(set(), set()))
        self.assertIn(gmp.NOOP_JOB_NAME, out)
        doc = yaml.safe_load(out)
        # A single trivially-green job, self-contained (no include of the job file).
        self.assertNotIn("include", doc)
        self.assertIn(gmp.NOOP_JOB_NAME, doc)
        self.assertEqual(doc[gmp.NOOP_JOB_NAME]["stage"], "test")

    def test_child_reuses_template_and_matrix(self):
        sel = gmp.Selection({("plex", "default"), ("gitlab", "default")}, {"dns-stack"})
        out = gmp.render_child_pipeline(sel)
        doc = yaml.safe_load(out)
        self.assertEqual(doc["include"], [{"local": gmp.MOLECULE_JOBS_INCLUDE}])
        self.assertEqual(doc["molecule-tests"]["extends"], gmp.MOLECULE_JOB_EXTENDS)
        self.assertEqual(doc["integration-tests"]["extends"], gmp.INTEGRATION_JOB_EXTENDS)
        # Matrix entries match the selection, sorted deterministically.
        self.assertEqual(
            doc["molecule-tests"]["parallel"]["matrix"],
            [{"ROLE": "gitlab", "SCENARIO": "default"}, {"ROLE": "plex", "SCENARIO": "default"}],
        )
        self.assertEqual(
            doc["integration-tests"]["parallel"]["matrix"], [{"TEST": ["dns-stack"]}]
        )

    def test_render_is_deterministic(self):
        sel = gmp.Selection({("b", "x"), ("a", "y"), ("a", "x")}, {"s2", "s1"})
        self.assertEqual(gmp.render_child_pipeline(sel), gmp.render_child_pipeline(sel))

    def test_molecule_only_selection_omits_integration_job(self):
        out = gmp.render_child_pipeline(gmp.Selection({("plex", "default")}, set()))
        doc = yaml.safe_load(out)
        self.assertIn("molecule-tests", doc)
        self.assertNotIn("integration-tests", doc)

    def test_integration_only_selection_omits_molecule_job(self):
        out = gmp.render_child_pipeline(gmp.Selection(set(), {"dns-stack"}))
        doc = yaml.safe_load(out)
        self.assertNotIn("molecule-tests", doc)
        self.assertIn("integration-tests", doc)


class TestEndToEndRealRepo(unittest.TestCase):
    """select() -> render() produces valid YAML for real-repo changed sets."""

    def test_transitive_change_renders_valid_child(self):
        sel = _sel(["ansible/roles/prometheus_exporter/tasks/main.yml"])
        doc = yaml.safe_load(gmp.render_child_pipeline(sel))
        roles = {e["ROLE"] for e in doc["molecule-tests"]["parallel"]["matrix"]}
        self.assertEqual(roles, {"prometheus_exporter", "adguard_sync", "unbound_exporter", "zfs_exporter"})
        self.assertEqual(doc["integration-tests"]["parallel"]["matrix"], [{"TEST": ["dns-stack"]}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
