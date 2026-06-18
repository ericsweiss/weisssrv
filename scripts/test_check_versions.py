#!/usr/bin/env python3
"""
Unit tests for check-versions.py

Tests the APT package parsing functions and version comparison logic.

Run with pytest (preferred):
    pytest scripts/test_check_versions.py -v
    cd scripts && pytest test_check_versions.py -v

Run without pytest (using unittest fallback):
    python3 scripts/test_check_versions.py -v

Note: pytest is preferred for better output formatting and fixtures, but
unittest fallback is provided for environments where pytest is not installed.
"""

import socket
import unittest
import urllib.error
from unittest.mock import patch, MagicMock
import importlib.util
import sys
from pathlib import Path

try:
    import pytest
    PYTEST_AVAILABLE = True
except ImportError:
    PYTEST_AVAILABLE = False

# Import the module with hyphenated name using importlib
script_path = Path(__file__).parent / "check-versions.py"
spec = importlib.util.spec_from_file_location("check_versions", script_path)
check_versions = importlib.util.module_from_spec(spec)
sys.modules["check_versions"] = check_versions
spec.loader.exec_module(check_versions)

# Now import the functions we need
parse_version_tuple = check_versions.parse_version_tuple
version_greater = check_versions.version_greater
fetch_plex_version = check_versions.fetch_plex_version
fetch_gitlab_version = check_versions.fetch_gitlab_version
fetch_apt_packages = check_versions.fetch_apt_packages


class TestVersionParsing(unittest.TestCase):
    """Tests for version string parsing and comparison."""

    def test_parse_simple_version(self):
        """Test parsing simple X.Y.Z versions.

        New format returns (type_rank, value) tuples where:
        - type_rank=0 for integers (sort before strings)
        - type_rank=1 for strings (sort after integers)
        """
        assert parse_version_tuple("1.2.3") == ((0, 1), (0, 2), (0, 3))
        assert parse_version_tuple("10.20.30") == ((0, 10), (0, 20), (0, 30))

    def test_parse_version_with_v_prefix(self):
        """Test parsing versions with 'v' prefix."""
        assert parse_version_tuple("v1.2.3") == ((0, 1), (0, 2), (0, 3))
        assert parse_version_tuple("v10.0.1") == ((0, 10), (0, 0), (0, 1))

    def test_parse_k3s_version(self):
        """Test parsing k3s-style versions with +k3sN suffix."""
        # v1.35.2+k3s1 -> ((0,1), (0,35), (0,2), (1,"k"), (0,3), (1,"s"), (0,1))
        result = parse_version_tuple("v1.35.2+k3s1")
        assert result[0:3] == ((0, 1), (0, 35), (0, 2))
        # k3s10 should sort after k3s9
        v1 = parse_version_tuple("v1.35.2+k3s10")
        v2 = parse_version_tuple("v1.35.2+k3s9")
        assert v1 > v2, "k3s10 should be greater than k3s9"

    def test_parse_four_part_version(self):
        """Test parsing four-part versions (like Sonarr/Radarr builds)."""
        assert parse_version_tuple("4.0.16.2944") == ((0, 4), (0, 0), (0, 16), (0, 2944))

    def test_parse_plex_version(self):
        """Test parsing Plex-style versions with hash suffix."""
        # Plex: 1.43.0.10492-121068a07
        result = parse_version_tuple("1.43.0.10492-121068a07")
        # Should parse numeric parts correctly (first 4 elements are the version numbers)
        assert result[0:4] == ((0, 1), (0, 43), (0, 0), (0, 10492))

    def test_parse_gitlab_version(self):
        """Test parsing GitLab-style versions."""
        # GitLab: 18.9.1-ee.0
        result = parse_version_tuple("18.9.1-ee.0")
        assert result[0:3] == ((0, 18), (0, 9), (0, 1))

    def test_version_greater_simple(self):
        """Test version comparison with simple versions."""
        assert version_greater("2.0.0", "1.9.9")
        assert version_greater("1.10.0", "1.9.0")
        assert not version_greater("1.0.0", "1.0.0")
        assert not version_greater("1.0.0", "2.0.0")

    def test_version_greater_with_prefix(self):
        """Test version comparison with v prefix."""
        assert version_greater("v2.0.0", "v1.9.9")
        assert version_greater("v1.10.0", "v1.9.0")

    def test_version_greater_k3s(self):
        """Test version comparison with k3s-style versions."""
        assert version_greater("v1.35.2+k3s10", "v1.35.2+k3s9")
        assert version_greater("v1.35.2+k3s1", "v1.35.1+k3s1")
        assert not version_greater("v1.35.2+k3s1", "v1.35.2+k3s2")


class TestAptParsing(unittest.TestCase):
    """Tests for APT Packages file parsing."""

    # Sample Plex Packages file content
    PLEX_PACKAGES_CONTENT = """Package: plexmediaserver
Version: 1.42.0.10000-abc123
Architecture: amd64
Maintainer: Plex Inc.
Installed-Size: 123456
Depends: libc6
Section: video
Description: Plex Media Server
 Stream your media.

Package: plexmediaserver
Version: 1.43.0.10492-121068a07
Architecture: amd64
Maintainer: Plex Inc.
Installed-Size: 123456
Depends: libc6
Section: video
Description: Plex Media Server
 Stream your media.

Package: plexmediaserver
Version: 1.41.0.9000-xyz789
Architecture: amd64
Maintainer: Plex Inc.
Installed-Size: 123456
Depends: libc6
Section: video
Description: Plex Media Server
 Stream your media.
"""

    # Sample GitLab Packages file content
    GITLAB_PACKAGES_CONTENT = """Package: gitlab-ee
Version: 18.9.1-ee.0
Architecture: amd64
Maintainer: GitLab Inc.
Installed-Size: 1234567890
Section: devel
Description: GitLab Enterprise Edition

Package: gitlab-ee
Version: 18.8.2-ee.0
Architecture: amd64
Maintainer: GitLab Inc.
Installed-Size: 1234567890
Section: devel
Description: GitLab Enterprise Edition

Package: gitlab-ee
Version: 18.10.0~rc1-ee.0
Architecture: amd64
Maintainer: GitLab Inc.
Installed-Size: 1234567890
Section: devel
Description: GitLab Enterprise Edition (RC)

Package: gitlab-runner
Version: 16.11.0
Architecture: amd64
Maintainer: GitLab Inc.
Section: devel
Description: GitLab Runner
"""

    def test_plex_version_parsing(self):
        """Test that Plex version parsing finds the highest version."""
        with patch('check_versions.fetch_apt_packages') as mock_fetch:
            mock_fetch.return_value = self.PLEX_PACKAGES_CONTENT

            svc = {
                "name": "Plex Media Server",
                "var_name": "plex_version",
                "category": "plex",
            }
            version = fetch_plex_version(svc)

            # Should return the highest version (1.43.0.10492-121068a07)
            assert version == "1.43.0.10492-121068a07"

    def test_plex_version_missing_package(self):
        """Test error handling when plexmediaserver package is not found."""
        with patch('check_versions.fetch_apt_packages') as mock_fetch:
            mock_fetch.return_value = "Package: someother\nVersion: 1.0.0\n"

            svc = {
                "name": "Plex Media Server",
                "var_name": "plex_version",
                "category": "plex",
            }
            with self.assertRaises(RuntimeError) as ctx:
                fetch_plex_version(svc)
            self.assertIn("Could not find plexmediaserver", str(ctx.exception))

    def test_gitlab_version_parsing(self):
        """Test that GitLab version parsing finds the highest non-RC version."""
        with patch('check_versions.fetch_apt_packages') as mock_fetch:
            mock_fetch.return_value = self.GITLAB_PACKAGES_CONTENT

            svc = {
                "name": "GitLab EE",
                "var_name": "gitlab_version",
                "category": "gitlab",
            }
            version = fetch_gitlab_version(svc)

            # Should return 18.9.1-ee.0 (highest non-RC version)
            # NOT 18.10.0~rc1-ee.0 (RC versions are skipped)
            assert version == "18.9.1-ee.0"

    def test_gitlab_version_skips_rc(self):
        """Test that GitLab version parsing skips RC/beta/alpha versions."""
        packages_with_rc = """Package: gitlab-ee
Version: 18.10.0~rc1-ee.0
Architecture: amd64

Package: gitlab-ee
Version: 18.9.0~beta1-ee.0
Architecture: amd64

Package: gitlab-ee
Version: 18.8.0-ee.0
Architecture: amd64
"""
        with patch('check_versions.fetch_apt_packages') as mock_fetch:
            mock_fetch.return_value = packages_with_rc

            svc = {
                "name": "GitLab EE",
                "var_name": "gitlab_version",
                "category": "gitlab",
            }
            version = fetch_gitlab_version(svc)

            # Should return 18.8.0-ee.0 (only stable version)
            assert version == "18.8.0-ee.0"

    def test_gitlab_version_missing_package(self):
        """Test error handling when gitlab-ee package is not found."""
        with patch('check_versions.fetch_apt_packages') as mock_fetch:
            mock_fetch.return_value = "Package: someother\nVersion: 1.0.0\n"

            svc = {
                "name": "GitLab EE",
                "var_name": "gitlab_version",
                "category": "gitlab",
            }
            with self.assertRaises(RuntimeError) as ctx:
                fetch_gitlab_version(svc)
            self.assertIn("Could not find gitlab-ee", str(ctx.exception))


class TestFetchAptPackages(unittest.TestCase):
    """Tests for the fetch_apt_packages function.

    Note: fetch_apt_packages uses urllib.request.urlopen directly (not _make_request)
    because it needs access to response headers and handles compression specially.
    Tests must mock urllib.request.urlopen to properly exercise the code path.
    """

    def _create_mock_response(self, content: bytes, content_type: str = "text/plain"):
        """Helper to create a mock urllib response with proper context manager support."""
        mock_response = MagicMock()
        mock_response.read.return_value = content
        mock_response.headers = {"Content-Type": content_type}
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        return mock_response

    def test_fetch_uncompressed(self):
        """Test fetching uncompressed Packages file."""
        mock_content = b"Package: test\nVersion: 1.0.0\n"

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = self._create_mock_response(mock_content)

            result = fetch_apt_packages("https://example.com/Packages")

            assert result == mock_content.decode("utf-8")
            # Verify urlopen was called (the uncompressed URL succeeded on first try)
            assert mock_urlopen.call_count == 1
            # Verify the Request object has the correct URL
            call_args = mock_urlopen.call_args
            request_obj = call_args[0][0]  # First positional arg
            assert request_obj.full_url == "https://example.com/Packages"

    def test_fetch_compressed_fallback(self):
        """Test fallback to .gz compressed file when uncompressed fails."""
        import gzip
        import io
        import urllib.error

        mock_content = b"Package: test\nVersion: 1.0.0\n"
        compressed = io.BytesIO()
        with gzip.GzipFile(fileobj=compressed, mode='wb') as gz:
            gz.write(mock_content)
        compressed_bytes = compressed.getvalue()

        call_count = [0]

        def mock_urlopen_side_effect(req, timeout=None):
            call_count[0] += 1
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            if call_count[0] == 1:
                # First call (uncompressed) fails with 404
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            else:
                # Second call (.gz) succeeds
                return self._create_mock_response(compressed_bytes, "application/gzip")

        with patch('urllib.request.urlopen', side_effect=mock_urlopen_side_effect):
            result = fetch_apt_packages("https://example.com/Packages")

            assert result == mock_content.decode("utf-8")
            assert call_count[0] == 2  # Verify both calls were made

    def test_fetch_empty_content_falls_back(self):
        """Test that empty response triggers fallback to .gz."""
        import gzip
        import io

        mock_content = b"Package: test\nVersion: 1.0.0\n"
        compressed = io.BytesIO()
        with gzip.GzipFile(fileobj=compressed, mode='wb') as gz:
            gz.write(mock_content)
        compressed_bytes = compressed.getvalue()

        call_count = [0]

        def mock_urlopen_side_effect(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call returns empty content (invalid - no "Package:" line)
                return self._create_mock_response(b"")
            else:
                # Second call (.gz) succeeds
                return self._create_mock_response(compressed_bytes, "application/gzip")

        with patch('urllib.request.urlopen', side_effect=mock_urlopen_side_effect):
            result = fetch_apt_packages("https://example.com/Packages")

            assert result == mock_content.decode("utf-8")
            assert call_count[0] == 2  # Verify fallback was triggered

    def test_fetch_html_error_page_falls_back(self):
        """Test that HTML error pages trigger fallback to .gz."""
        import gzip
        import io

        mock_content = b"Package: test\nVersion: 1.0.0\n"
        compressed = io.BytesIO()
        with gzip.GzipFile(fileobj=compressed, mode='wb') as gz:
            gz.write(mock_content)
        compressed_bytes = compressed.getvalue()

        call_count = [0]

        def mock_urlopen_side_effect(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call returns HTML error page
                return self._create_mock_response(
                    b"<!DOCTYPE html><html><body>Error</body></html>",
                    "text/html"
                )
            else:
                # Second call (.gz) succeeds
                return self._create_mock_response(compressed_bytes, "application/gzip")

        with patch('urllib.request.urlopen', side_effect=mock_urlopen_side_effect):
            result = fetch_apt_packages("https://example.com/Packages")

            assert result == mock_content.decode("utf-8")
            assert call_count[0] == 2  # Verify fallback was triggered


class TestGetDeployCommand(unittest.TestCase):
    """Verify every managed service routes to the right deploy command.

    The Flux migration collapsed a fleet of per-app deploy tasks into
    `task flux:sync-versions && git push`. A silent regression here
    (typo in the flux_managed tuple, an unreachable branch, etc.) would
    recommend `task infra:deploy` as a fallback — which does nothing useful
    for k8s workloads and could mislead operators into treating a bogus
    message as success.
    """

    def _mk(self, var_name: str, category: str = ""):
        """Build a ServiceVersion-shaped object for get_deploy_command()."""
        result = MagicMock()
        result.var_name = var_name
        result.category = category
        return result

    def test_flux_managed_image_versions_route_to_flux_sync(self):
        for var in (
            "gluetun_version", "nzbget_version", "qbittorrent_version",
            "prowlarr_version", "sonarr_version", "radarr_version",
            "lidarr_version", "pulsarr_version",
            "mealie_version", "mealie_postgresql_version",
            "bar_assistant_version", "salt_rim_version",
            "meilisearch_version", "redis_version", "busybox_version",
            "authentik_version", "postgresql_version",
            "gitlab_runner_helm_version", "gitlab_agent_helm_version",
            # Observability exporter container images
            "exportarr_version", "proxmox_exporter_version",
            "zfs_exporter_version", "adguard_exporter_version",
            "unbound_exporter_version",
        ):
            cmd = check_versions.get_deploy_command(self._mk(var))
            self.assertIn(
                "flux:sync-versions", cmd,
                f"{var} should route to Flux (got: {cmd})",
            )
            self.assertNotIn(
                "infra:deploy", cmd,
                f"{var} fell through to the infra:deploy fallback",
            )

    def test_helm_chart_prefix_routes_to_flux(self):
        # Every helm_chart_* variable must route through Flux — the old
        # maintenance:update-helm-charts task was removed when platform
        # controllers became Flux HelmReleases.
        for var in (
            "helm_chart_versions_metallb",
            "helm_chart_versions_traefik",
            "helm_chart_versions_cert_manager",
            "helm_chart_versions_external_dns",
            "helm_chart_versions_external_secrets",
            # Observability Helm charts
            "helm_chart_versions_kube_prometheus_stack",
            "helm_chart_versions_loki",
            "helm_chart_versions_alloy",
            "helm_chart_versions_prometheus_blackbox_exporter",
        ):
            cmd = check_versions.get_deploy_command(self._mk(var, category="helm"))
            self.assertIn("flux:sync-versions", cmd, f"{var}: {cmd}")
            self.assertNotIn(
                "update-helm-charts", cmd,
                f"{var} still references the removed task",
            )

    def test_category_helm_also_routes_to_flux(self):
        # category: "helm" in SERVICES should also route to Flux (belt-and-
        # suspenders — the prefix check catches most cases, but category
        # is an independent signal used by some services).
        cmd = check_versions.get_deploy_command(self._mk("anything", category="helm"))
        self.assertIn("flux:sync-versions", cmd)

    def test_gitlab_vm_version_stays_ansible(self):
        # GitLab EE is installed on a VM via Ansible, NOT in k8s. It must
        # not accidentally route to Flux.
        cmd = check_versions.get_deploy_command(self._mk("gitlab_version"))
        self.assertIn("gitlab:deploy", cmd)
        self.assertNotIn("flux:", cmd)

    def test_k3s_infrastructure_stays_ansible(self):
        # k3s + kube-vip are Ansible-managed.
        self.assertIn(
            "maintenance:update-k3s-nodes",
            check_versions.get_deploy_command(self._mk("k3s_version")),
        )
        self.assertIn(
            "k3s:deploy",
            check_versions.get_deploy_command(self._mk("kube_vip_version")),
        )

    def test_adguard_stays_ansible(self):
        # AdGuard runs in LXC, Ansible-managed.
        cmd = check_versions.get_deploy_command(self._mk("adguard_home_version"))
        self.assertIn("maintenance:update-applications", cmd)

    def test_new_flux_services_route_correctly(self):
        """Verify busybox, meilisearch, redis, and external-secrets route to Flux."""
        for var_name in ["busybox_version", "meilisearch_version", "redis_version",
                         "helm_chart_versions_external_secrets"]:
            result = self._mk(var_name=var_name, category="dockerhub")
            cmd = check_versions.get_deploy_command(result)
            self.assertIn("flux:sync-versions", cmd,
                          f"{var_name} should route to Flux sync")

    def test_every_registry_service_has_specific_deploy_command(self):
        """Every SERVICE_REGISTRY entry should route to a specific deploy
        command, not the generic 'infra:deploy' fallback."""
        for svc in check_versions.SERVICE_REGISTRY:
            result = self._mk(svc["var_name"], category=svc.get("category", ""))
            cmd = check_versions.get_deploy_command(result)
            self.assertNotIn(
                "infra:deploy", cmd,
                f"{svc['name']} ({svc['var_name']}) fell through to infra:deploy fallback. "
                f"Add it to flux_managed tuple or a specific handler in get_deploy_command().",
            )

    def test_service_registry_no_duplicates(self):
        """var_name and name must be unique across SERVICE_REGISTRY."""
        var_names = [s["var_name"] for s in check_versions.SERVICE_REGISTRY]
        names = [s["name"] for s in check_versions.SERVICE_REGISTRY]
        self.assertEqual(
            len(var_names), len(set(var_names)),
            f"Duplicate var_names: {[v for v in var_names if var_names.count(v) > 1]}",
        )
        self.assertEqual(
            len(names), len(set(names)),
            f"Duplicate names: {[n for n in names if names.count(n) > 1]}",
        )

    def test_service_registry_field_completeness(self):
        """Every SERVICE_REGISTRY entry must have required fields for its category."""
        required_fields = {
            "github": ("github_repo", "version_prefix"),
            "helm": ("helm_repo", "helm_chart"),
            "dockerhub": ("docker_image",),
            "lsio": ("docker_image",),
            "ghcr": ("ghcr_image",),
        }
        for svc in check_versions.SERVICE_REGISTRY:
            cat = svc.get("category", "")
            if cat in required_fields:
                for field in required_fields[cat]:
                    self.assertIn(
                        field, svc,
                        f"{svc['name']} ({cat}) is missing required field '{field}'",
                    )


class TestDebianVersionCompare(unittest.TestCase):
    """debian_version_compare ordering rules (referenced by its docstring)."""

    def setUp(self):
        self.cmp = check_versions.debian_version_compare

    def test_plain_patch(self):
        self.assertEqual(self.cmp("1.98.4", "1.98.5"), -1)
        self.assertEqual(self.cmp("1.98.5", "1.98.4"), 1)
        self.assertEqual(self.cmp("1.98.4", "1.98.4"), 0)

    def test_epoch_wins(self):
        # 1:0.4.6-1 outranks 0.4.6 regardless of upstream version
        self.assertEqual(self.cmp("1:0.4.6-1", "0.4.6"), 1)
        # explicit zero epoch is the default
        self.assertEqual(self.cmp("0:1.98.4", "1.98.5"), -1)

    def test_tilde_is_prerelease(self):
        self.assertEqual(self.cmp("0.5.0~rc1-1", "0.5.0-1"), -1)

    def test_revision_tail(self):
        self.assertEqual(self.cmp("0.4.6-1ubuntu1", "0.4.6-1"), 1)

    def test_malformed_epoch_raises(self):
        with self.assertRaises(ValueError):
            self.cmp("x:1.0", "1.0")


class TestParseVersionTupleEpoch(unittest.TestCase):
    """parse_version_tuple must drop a Debian epoch prefix."""

    def test_epoch_stripped(self):
        self.assertEqual(
            check_versions.parse_version_tuple("1:1.80.0"),
            check_versions.parse_version_tuple("1.80.0"),
        )

    def test_no_epoch_unchanged(self):
        self.assertEqual(
            check_versions.parse_version_tuple("1.80.0"),
            ((0, 1), (0, 80), (0, 0)),
        )


class TestHelmIndexParser(unittest.TestCase):
    """fetch_helm_version tolerates extra post-colon whitespace and skips appVersion."""

    INDEX = (
        "apiVersion: v1\n"
        "entries:\n"
        "  mychart:\n"
        "  - apiVersion: v2\n"
        "    appVersion: 9.9.9\n"
        "    version:   1.2.3\n"  # three spaces after the colon
        "  - apiVersion: v2\n"
        "    appVersion: 9.9.9\n"
        "    version: 1.2.2\n"
    )

    def test_extra_spaces_and_appversion_excluded(self):
        svc = {"helm_repo": "https://example.com", "helm_chart": "mychart"}
        with patch.object(check_versions, "_make_request", return_value=self.INDEX):
            latest = check_versions.fetch_helm_version(svc)
        # 1.2.3 (highest version:), not 9.9.9 (appVersion), and the 3-space line parsed
        self.assertEqual(latest, "1.2.3")


class TestContainerFetcherGuards(unittest.TestCase):
    """Docker Hub / LSIO fetchers raise a clear error on a non-JSON body."""

    def test_dockerhub_non_json_raises(self):
        svc = {"docker_image": "library/foo", "tag_regex": r"^(\d+\.\d+\.\d+)$"}
        with patch.object(check_versions, "_make_request", return_value="<html>maintenance</html>"):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_dockerhub_version(svc)

    def test_lsio_non_json_raises(self):
        svc = {"docker_image": "linuxserver/foo", "lsio_version_regex": r"^version-(\d+\.\d+\.\d+)$"}
        with patch.object(check_versions, "_make_request", return_value="not json"):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_lsio_version(svc)


class TestUpdateVersionInFile(unittest.TestCase):
    """update_version_in_file round-trips on a temp file, preserving format."""

    def _write_tmp(self, content):
        import tempfile
        tf = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        tf.write(content)
        tf.close()
        return Path(tf.name)

    def test_top_level_roundtrip_preserves_quotes_and_comment(self):
        import os
        path = self._write_tmp(
            'gluetun_version: "v3.40.0"  # Currently deployed v3.40.0\n'
            "other_version: 1.2.3\n"
        )
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertTrue(
                    check_versions.update_version_in_file("gluetun_version", "v3.41.0")
                )
            out = path.read_text()
            self.assertIn('gluetun_version: "v3.41.0"', out)
            self.assertIn("# Currently deployed v3.41.0", out)
            self.assertIn("other_version: 1.2.3", out)  # untouched
        finally:
            os.unlink(path)

    def test_missing_var_returns_false(self):
        import os
        path = self._write_tmp("foo_version: 1.0.0\n")
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertFalse(
                    check_versions.update_version_in_file("nonexistent_version", "9.9.9")
                )
        finally:
            os.unlink(path)

    def test_helm_nested_key(self):
        import os
        path = self._write_tmp(
            "helm_chart_versions:\n"
            '  traefik: "40.3.0"  # Currently deployed 40.3.0\n'
        )
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertTrue(
                    check_versions.update_version_in_file("helm_chart_versions.traefik", "40.4.0")
                )
            self.assertIn('traefik: "40.4.0"', path.read_text())
        finally:
            os.unlink(path)


class TestHeldUpdateGuard(unittest.TestCase):
    """A held service must never be written by --update-all (the MetalLB hold)."""

    def test_metallb_registry_entry_is_held(self):
        metallb = [s for s in check_versions.SERVICE_REGISTRY
                   if s.get("var_name") == "helm_chart_versions.metallb"]
        self.assertTrue(metallb, "MetalLB must be in the registry")
        self.assertTrue(metallb[0].get("held"), "MetalLB must carry held=True (documented hold)")

    def test_update_all_skips_held(self):
        SV = check_versions.ServiceVersion
        held = SV(name="MetalLB", category="helm", current_version="0.15.3",
                  latest_version="0.16.0", update_available=True,
                  var_name="helm_chart_versions.metallb", held=True)
        normal = SV(name="Foo", category="helm", current_version="1.0",
                    latest_version="1.1", update_available=True, var_name="foo_version")
        with patch.object(check_versions, "check_all", return_value=[held, normal]), \
             patch.object(check_versions, "read_current_versions", return_value={}), \
             patch.object(check_versions, "get_deploy_command", return_value="deploy"), \
             patch.object(check_versions, "update_version_in_file", return_value=True) as muf, \
             patch.object(check_versions.sys, "argv", ["check-versions.py", "--update-all"]):
            with self.assertRaises(SystemExit):
                check_versions.main()
        written_vars = [call.args[0] for call in muf.call_args_list]
        self.assertIn("foo_version", written_vars)
        self.assertNotIn("helm_chart_versions.metallb", written_vars)

    def test_single_update_skips_held(self):
        """`--update <service>` must also refuse to write a held version.

        This is an independent code path from --update-all (it calls
        check_service, not check_all), so it needs its own guard test — a
        regression here would let `check-versions.py --update metallb` write a
        held version into all.yml even while --update-all stayed green.
        """
        SV = check_versions.ServiceVersion
        held = SV(name="MetalLB", category="helm", current_version="0.15.3",
                  latest_version="0.16.0", update_available=True,
                  var_name="helm_chart_versions.metallb", held=True)
        with patch.object(check_versions, "check_service", return_value=held), \
             patch.object(check_versions, "read_current_versions", return_value={}), \
             patch.object(check_versions, "update_version_in_file", return_value=True) as muf, \
             patch.object(check_versions.sys, "argv",
                          ["check-versions.py", "--update", "metallb"]):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        muf.assert_not_called()                  # held => never written
        self.assertEqual(cm.exception.code, 0)   # a held hold is a clean exit


class TestMakeRequestRetry(unittest.TestCase):
    """_make_request retries on transient failures but never on 4xx.

    The checker does dozens of sequential external fetches; without a bounded
    retry a single flaky endpoint (DNS blip, connection reset, upstream 5xx)
    fails the whole CI version check. These tests pin the retry semantics:
    retry on URLError/socket.timeout/HTTP 5xx, never on 4xx (incl. 403
    rate-limit), and re-raise the last exception after exhausting attempts.
    """

    def _ok_response(self, body: bytes):
        """A urlopen-style success response usable as a context manager."""
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def _http_error(self, code: int):
        return urllib.error.HTTPError(
            "https://example.com", code, "boom", {}, None
        )

    def test_retries_then_succeeds_on_urlerror(self):
        """Two transient URLErrors then success → returns the success body."""
        calls = [0]

        def side_effect(req, timeout=None):
            calls[0] += 1
            if calls[0] <= 2:
                raise urllib.error.URLError("temporary failure in name resolution")
            return self._ok_response(b'{"tag_name": "v1.2.3"}')

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):  # don't actually back off
            result = check_versions._make_request("https://example.com/api")

        assert result == {"tag_name": "v1.2.3"}
        assert calls[0] == 3, "should have retried twice before succeeding"

    def test_retries_on_socket_timeout_then_succeeds(self):
        """socket.timeout is transient and is retried."""
        calls = [0]

        def side_effect(req, timeout=None):
            calls[0] += 1
            if calls[0] == 1:
                raise socket.timeout("timed out")
            return self._ok_response(b"plain text body")

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):
            result = check_versions._make_request("https://example.com/Packages")

        assert result == "plain text body"
        assert calls[0] == 2

    def test_4xx_does_not_retry(self):
        """HTTP 4xx (here 404) raises immediately with no retry."""
        calls = [0]

        def side_effect(req, timeout=None):
            calls[0] += 1
            raise self._http_error(404)

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep") as mock_sleep:
            with self.assertRaises(RuntimeError):  # _make_request wraps HTTPError
                check_versions._make_request("https://example.com/missing")

        assert calls[0] == 1, "4xx must not be retried"
        mock_sleep.assert_not_called()

    def test_403_rate_limit_does_not_retry(self):
        """A 403 rate-limit is surfaced as-is, not retried as a transient blip."""
        calls = [0]

        def side_effect(req, timeout=None):
            calls[0] += 1
            raise self._http_error(403)

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions._make_request("https://api.github.com/rate")

        assert calls[0] == 1, "403 must not be retried"
        # _make_request maps 403 to its rate-limit message
        self.assertIn("403", str(ctx.exception))

    def test_persistent_5xx_exhausts_retries(self):
        """Persistent HTTP 5xx retries RETRY_ATTEMPTS times, then raises."""
        calls = [0]

        def side_effect(req, timeout=None):
            calls[0] += 1
            raise self._http_error(503)

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):
            with self.assertRaises(RuntimeError):  # last HTTPError wrapped by _make_request
                check_versions._make_request("https://example.com/flaky")

        assert calls[0] == check_versions.RETRY_ATTEMPTS, (
            f"should attempt exactly RETRY_ATTEMPTS={check_versions.RETRY_ATTEMPTS} times"
        )

    def test_helper_reraises_last_exception_after_exhaustion(self):
        """_urlopen_with_retry re-raises the original exception type unchanged
        so existing callers behave identically after retries are exhausted."""
        def side_effect(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):
            req = check_versions.urllib.request.Request("https://example.com")
            with self.assertRaises(urllib.error.URLError):
                check_versions._urlopen_with_retry(req)


if __name__ == "__main__":
    if PYTEST_AVAILABLE:
        # Use pytest for better output formatting when available
        pytest.main([__file__, "-v"])
    else:
        # Fall back to unittest when pytest is not installed
        unittest.main(verbosity=2)
