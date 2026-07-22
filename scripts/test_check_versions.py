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

import re
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
read_pinned_image_versions = check_versions.read_pinned_image_versions
update_version_in_file = check_versions.update_version_in_file
SERVICE_REGISTRY = check_versions.SERVICE_REGISTRY


class TestPinnedImageVersionTracking(unittest.TestCase):
    """Digest-locked image pins tracked from .gitlab-ci.yml and kubernetes/."""

    def test_pr_agent_in_registry(self):
        entry = next(
            (s for s in SERVICE_REGISTRY if s.get("var_name") == "pr_agent_version"),
            None,
        )
        self.assertIsNotNone(entry, "pr-agent must be in SERVICE_REGISTRY")
        self.assertEqual(entry["version_file"], "ci")
        self.assertEqual(entry["docker_image"], "codiumai/pr-agent")

    def test_reads_pr_agent_tag_from_gitlab_ci(self):
        # Reads the real .gitlab-ci.yml pin so check-versions can flag staleness.
        versions = read_pinned_image_versions()
        self.assertIn("pr_agent_version", versions)
        self.assertRegex(versions["pr_agent_version"], r"^\d+\.\d+")

    def test_reads_manifest_pins_from_kubernetes(self):
        # Reads the real kubernetes/ manifest pins (gluetun-exporter sidecar,
        # the shared python CronJob base image, the digest-pinned plex-exporter)
        # so pins that live outside all.yml stay visible to check-versions.
        versions = read_pinned_image_versions()
        self.assertRegex(
            versions.get("gluetun_exporter_version", ""),
            r"^\d+\.\d+\.\d+-standalone$",
        )
        self.assertRegex(
            versions.get("python_cronjob_version", ""), r"^3\.\d+-slim$"
        )
        self.assertEqual(versions.get("plex_exporter_version"), "latest")

    def test_divergent_multifile_pin_is_flagged(self):
        # The python base image is pinned in two CronJob manifests that must
        # share one tag. Patch one path to a different tag and assert the
        # divergence raises (fails CI loudly) rather than silently selecting one.
        real_read_text = Path.read_text

        def fake_read_text(self, *args, **kwargs):
            content = real_read_text(self, *args, **kwargs)
            if self.name == "cronjob.yaml" and "gitlab-runner-reaper" in str(self):
                content = re.sub(
                    r"(image:\s*python:)3\.\d+-slim",
                    r"\g<1>3.0-slim",
                    content,
                )
            return content

        with patch.object(Path, "read_text", fake_read_text):
            with self.assertRaises(RuntimeError) as ctx:
                read_pinned_image_versions()
        self.assertIn("python_cronjob_version", str(ctx.exception))
        self.assertIn("diverge", str(ctx.exception))

    def test_pinned_image_update_is_manual(self):
        # A digest-pinned image is flagged for manual update, never written.
        self.assertFalse(update_version_in_file("pr_agent_version", "9.9.9"))
        self.assertFalse(
            update_version_in_file("gluetun_exporter_version", "9.9.9-standalone")
        )
        self.assertFalse(update_version_in_file("python_cronjob_version", "9.9-slim"))


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
        # Every helm_chart_versions.* variable (the dotted registry var_name
        # spelling) must route through Flux — the old
        # maintenance:update-helm-charts task was removed when platform
        # controllers became Flux HelmReleases.
        for var in (
            "helm_chart_versions.metallb",
            "helm_chart_versions.traefik",
            "helm_chart_versions.cert_manager",
            "helm_chart_versions.external_dns",
            "helm_chart_versions.external_secrets",
            # Observability Helm charts
            "helm_chart_versions.kube_prometheus_stack",
            "helm_chart_versions.loki",
            "helm_chart_versions.alloy",
            "helm_chart_versions.prometheus_blackbox_exporter",
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
                         "helm_chart_versions.external_secrets"]:
            result = self._mk(var_name=var_name, category="dockerhub")
            cmd = check_versions.get_deploy_command(result)
            self.assertIn("flux:sync-versions", cmd,
                          f"{var_name} should route to Flux sync")

    def test_registry_cache_in_registry_and_routes_to_flux(self):
        """The CI registry pull-through cache (kubernetes/apps/registry-cache) is
        a Flux-managed Docker Hub image: it must be a `library/registry`
        dockerhub entry AND route through flux:sync-versions (not the
        infra:deploy fallback)."""
        entry = next(
            (s for s in check_versions.SERVICE_REGISTRY
             if s.get("var_name") == "registry_cache_version"),
            None,
        )
        self.assertIsNotNone(entry, "registry_cache_version must be in SERVICE_REGISTRY")
        self.assertEqual(entry["category"], "dockerhub")
        self.assertEqual(entry["docker_image"], "library/registry")
        # Bare X.Y.Z only — never the floating 3/3.1 or a 3.0.0-rc.N pre-release.
        self.assertRegex("3.1.1", entry["tag_regex"])
        self.assertNotRegex("3.1", entry["tag_regex"])
        self.assertNotRegex("3.0.0-rc.4", entry["tag_regex"])
        cmd = check_versions.get_deploy_command(
            self._mk("registry_cache_version", category="dockerhub")
        )
        self.assertIn("flux:sync-versions", cmd)
        self.assertNotIn("infra:deploy", cmd)

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

    def test_every_helm_chart_pin_has_registry_entry(self):
        """Every helm_chart_versions.* key pinned in all.yml must have a
        SERVICE_REGISTRY entry, or its updates are silently never reported
        (how kured and reloader went missing)."""
        current = check_versions.read_current_versions()
        registry_vars = {s["var_name"] for s in check_versions.SERVICE_REGISTRY}
        missing = [
            v for v in current
            if v.startswith("helm_chart_versions.") and v not in registry_vars
        ]
        self.assertEqual(
            missing, [],
            f"helm_chart_versions pins with no SERVICE_REGISTRY entry: {missing}",
        )

    def test_every_top_level_version_pin_has_registry_entry(self):
        """Every top-level `*_version` pin in all.yml must have a
        SERVICE_REGISTRY entry, or its updates are silently never reported.

        The sibling helm-chart test only guards `helm_chart_versions.*`; this is
        how `alloy_host_version` slipped through untracked. Non-service pins that
        legitimately have no upstream to check are allow-listed explicitly."""
        # Pins that are intentionally not tracked by SERVICE_REGISTRY: the base
        # OS release is set by the distro, not a per-service upstream.
        allowed_untracked = {
            "debian_version",
            # Immich's Postgres + Valkey images are release-coupled — the tags +
            # digests come from the pinned immich_version's own docker-compose.yml
            # (vectorchord/pgvectors build). Auto-bumping them independently would
            # break Immich. Bumped alongside immich_version per docs/36.
            "immich_postgres_version",
            "immich_valkey_version",
            # Docker Engine apt pins for the Debian/trixie VM compose stacks. The
            # apt version format (5:29.6.1-1~debian.13~trixie) has no simple
            # GitHub/DockerHub tracker; bumped manually from the download.docker.com
            # Packages index (see the all.yml comment + docs/36 Upgrades).
            "docker_ce_version",
            "containerd_version",
            "docker_buildx_plugin_version",
            "docker_compose_plugin_version",
            # restic/rclone install from Debian apt (restic_offsite role) and the
            # pins are deliberately empty ("" = distro version) — a non-empty
            # value becomes an exact apt pin that upstream release strings don't
            # match (see the all.yml comment). Nothing to track until the role
            # moves to checksum-verified binary downloads.
            "restic_version",
            "rclone_version",
            # Local image-revision tag (<hermes_version>-rN) for the patched
            # hermes-agent build — derived from hermes_version + the local
            # patch set, no independent upstream to track.
            "hermes_image_version",
        }
        current = check_versions.read_current_versions()
        registry_vars = {s["var_name"] for s in check_versions.SERVICE_REGISTRY}
        missing = [
            v for v in current
            if v.endswith("_version")
            and not v.startswith("helm_chart_versions.")
            and v not in registry_vars
            and v not in allowed_untracked
        ]
        self.assertEqual(
            missing, [],
            f"top-level *_version pins with no SERVICE_REGISTRY entry: {missing} "
            "(add a registry entry, or allow-list it if it has no upstream to track)",
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

    # A chart entry with a dependencies: sub-block whose dependency version
    # outranks the chart's own version. The parser must NOT collect the
    # dependency version (real index.yaml shape: e.g. authentik's postgresql dep).
    INDEX_WITH_DEPENDENCY = (
        "apiVersion: v1\n"
        "entries:\n"
        "  mychart:\n"
        "  - apiVersion: v2\n"
        "    appVersion: 9.9.9\n"
        "    dependencies:\n"
        "    - name: postgresql\n"
        "      repository: https://example.com\n"
        "      version: 99.0.0\n"
        "    version: 1.2.3\n"
        "  - apiVersion: v2\n"
        "    version: 1.2.2\n"
    )

    def test_extra_spaces_and_appversion_excluded(self):
        svc = {"helm_repo": "https://example.com", "helm_chart": "mychart"}
        with patch.object(check_versions, "_make_request", return_value=self.INDEX):
            latest = check_versions.fetch_helm_version(svc)
        # 1.2.3 (highest version:), not 9.9.9 (appVersion), and the 3-space line parsed
        self.assertEqual(latest, "1.2.3")

    def test_dependency_version_not_captured(self):
        svc = {"helm_repo": "https://example.com", "helm_chart": "mychart"}
        with patch.object(check_versions, "_make_request", return_value=self.INDEX_WITH_DEPENDENCY):
            latest = check_versions.fetch_helm_version(svc)
        # 1.2.3 (chart's own version), NOT 99.0.0 (postgresql dependency version)
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


class TestDockerhubMajorPinAndPrefix(unittest.TestCase):
    """fetch_dockerhub_version confines results when pinning a major or a series."""

    @staticmethod
    def _payload(*tags):
        return {"results": [{"name": t} for t in tags]}

    def test_pin_major_confines_to_current_major(self):
        # postgres pins its major so a DB-breaking major bump is never proposed.
        svc = {
            "docker_image": "library/postgres",
            "tag_regex": r"^(\d+(?:\.\d+)?)-trixie$",
            "pin_major_version": True,
            "_current_version": "17-trixie",
        }
        payload = self._payload("18-trixie", "17.2-trixie", "17-trixie", "16.9-trixie")
        with patch.object(check_versions, "_make_request", return_value=payload):
            # Highest tag within major 17 — never 18-trixie.
            self.assertEqual(check_versions.fetch_dockerhub_version(svc), "17.2-trixie")

    def test_version_prefix_confines_to_series(self):
        # Meilisearch is held to its 1.15.x series via version_prefix.
        svc = {
            "docker_image": "getmeili/meilisearch",
            "tag_regex": r"^v(\d+\.\d+\.\d+)$",
            "version_prefix": "v1.15.",
        }
        payload = self._payload("v1.16.0", "v1.15.2", "v1.15.0")
        with patch.object(check_versions, "_make_request", return_value=payload):
            self.assertEqual(check_versions.fetch_dockerhub_version(svc), "v1.15.2")

    def test_pin_major_handles_v_prefixed_versions(self):
        # Regression: a leading "v" (gluetun, k3s, redis-exporter, ...) must not
        # make the major pin a no-op. Previously `^(\d+)` failed to match "v3.x",
        # left major_filter unset, and let a v4 major bump leak through.
        svc = {
            "docker_image": "qmcgaw/gluetun",
            "tag_regex": r"^(v\d+\.\d+\.\d+)$",
            "pin_major_version": True,
            "_current_version": "v3.41.1",
        }
        payload = self._payload("v4.0.0", "v3.42.0", "v3.41.1", "v2.9.0")
        with patch.object(check_versions, "_make_request", return_value=payload):
            # Highest tag within major 3 — never v4.0.0.
            self.assertEqual(check_versions.fetch_dockerhub_version(svc), "v3.42.0")

    def test_name_filter_has_no_startswith_constraint(self):
        # The python `-slim` CronJob image: name_filter narrows only the API
        # query (a suffix like "-slim" never matches a tag's startswith, unlike
        # version_prefix). The regex is what selects the tag. A regression that
        # applied startswith to name_filter would reject every "3.x-slim" tag.
        svc = {
            "docker_image": "library/python",
            "tag_regex": r"^(3\.\d+)-slim$",
            "dockerhub_name_filter": "-slim",
        }
        captured = {}

        def fake_request(url, headers=None):
            captured["url"] = url
            return self._payload("3.13-slim", "3.12-slim", "3.13")

        with patch.object(check_versions, "_make_request", side_effect=fake_request):
            self.assertEqual(check_versions.fetch_dockerhub_version(svc), "3.13-slim")
        # name_filter feeds the Docker Hub `name=` query, not a tag startswith.
        self.assertIn("name=-slim", captured["url"])


class TestVersionTupleGreaterRules(unittest.TestCase):
    """version_tuple_greater unequal-length and release-vs-prerelease ordering."""

    def setUp(self):
        self.gt = check_versions.version_greater

    def test_longer_numeric_is_newer(self):
        self.assertTrue(self.gt("17.1", "17"))
        self.assertFalse(self.gt("17", "17.1"))

    def test_release_beats_prerelease(self):
        self.assertTrue(self.gt("1.0.0", "1.0.0-rc1"))
        self.assertFalse(self.gt("1.0.0-rc1", "1.0.0"))
        self.assertTrue(self.gt("17", "17-alpha"))
        self.assertFalse(self.gt("17-alpha", "17"))

    def test_numeric_minor_beats_string_suffix(self):
        self.assertTrue(self.gt("17.1-trixie", "17-trixie"))
        self.assertTrue(self.gt("18-trixie", "17.1-trixie"))


class TestFetchAptRepoVersion(unittest.TestCase):
    """fetch_apt_repo_version parses plain and gzip Packages payloads."""

    PACKAGES = (
        "Package: tailscale\nVersion: 1.80.0\nArchitecture: amd64\n\n"
        "Package: tailscale\nVersion: 1.82.3\nArchitecture: amd64\n\n"
        "Package: other\nVersion: 9.9.9\nArchitecture: amd64\n"
    )

    def test_plain_packages(self):
        svc = {"apt_index_url": "https://example.com/Packages", "apt_package": "tailscale"}
        with patch.object(check_versions, "_urlopen_with_retry", return_value=self.PACKAGES.encode()):
            self.assertEqual(check_versions.fetch_apt_repo_version(svc), "1.82.3")

    def test_gzip_packages(self):
        import gzip as _gz
        svc = {"apt_index_url": "https://example.com/Packages.gz", "apt_package": "tailscale"}
        with patch.object(check_versions, "_urlopen_with_retry",
                          return_value=_gz.compress(self.PACKAGES.encode())):
            self.assertEqual(check_versions.fetch_apt_repo_version(svc), "1.82.3")

    def test_missing_package_raises(self):
        svc = {"apt_index_url": "https://example.com/Packages", "apt_package": "nope"}
        with patch.object(check_versions, "_urlopen_with_retry", return_value=self.PACKAGES.encode()):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_apt_repo_version(svc)


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
            out = path.read_text()
            self.assertIn('traefik: "40.4.0"', out)
            # The nested branch must also rewrite the trailing comment (the
            # top-level test asserts this; the nested one was the blind spot).
            self.assertIn("# Currently deployed 40.4.0", out)
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

    def test_retries_on_incomplete_read_then_succeeds(self):
        """http.client.IncompleteRead (mid-body truncation — GitHub cutting a
        large release payload) is transient and is retried."""
        import http.client
        calls = [0]

        def side_effect(req, timeout=None):
            calls[0] += 1
            if calls[0] == 1:
                raise http.client.IncompleteRead(b"partial", expected=1000)
            return self._ok_response(b'{"tag_name": "v9.9.9"}')

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):
            result = check_versions._make_request("https://example.com/api")

        assert result == {"tag_name": "v9.9.9"}
        assert calls[0] == 2

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


class TestFetchGithubReleaseLatest(unittest.TestCase):
    """The latest-release (no tag_filter) path must fail loud on a missing
    tag_name, matching the tag_filter branch — otherwise the service silently
    reports up-to-date with a blank Latest column."""

    def test_missing_tag_name_raises(self):
        svc = {"github_repo": "owner/repo"}  # no tag_filter -> latest endpoint
        with patch.object(check_versions, "github_api", return_value={}):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions.fetch_github_release(svc)
        self.assertIn("no tag_name", str(ctx.exception))

    def test_empty_tag_name_raises(self):
        svc = {"github_repo": "owner/repo"}
        with patch.object(check_versions, "github_api", return_value={"tag_name": ""}):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_github_release(svc)

    def test_present_tag_name_returned(self):
        svc = {"github_repo": "owner/repo"}
        with patch.object(check_versions, "github_api",
                          return_value={"tag_name": "v2.3.4"}):
            self.assertEqual(check_versions.fetch_github_release(svc), "v2.3.4")

    def test_present_tag_name_strip_prefix(self):
        svc = {"github_repo": "owner/repo", "version_prefix": "v", "strip_prefix": True}
        with patch.object(check_versions, "github_api",
                          return_value={"tag_name": "v2.3.4"}):
            self.assertEqual(check_versions.fetch_github_release(svc), "2.3.4")


class TestFetchAptPackagesRetry(unittest.TestCase):
    """fetch_apt_packages now routes through the bounded retry helper (Plex /
    GitLab fetch via it), so a transient 5xx on the uncompressed URL retries
    instead of immediately falling back / failing."""

    def _ok_response(self, body: bytes, content_type: str = "text/plain"):
        resp = MagicMock()
        resp.read.return_value = body
        resp.headers = {"Content-Type": content_type}
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_transient_5xx_then_success_on_uncompressed(self):
        calls = [0]
        body = b"Package: test\nVersion: 1.0.0\n"

        def side_effect(req, timeout=None):
            calls[0] += 1
            if calls[0] == 1:
                raise urllib.error.HTTPError(
                    "https://example.com/Packages", 503, "boom", {}, None
                )
            return self._ok_response(body)

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):
            result = check_versions.fetch_apt_packages("https://example.com/Packages")

        self.assertEqual(result, body.decode("utf-8"))
        self.assertEqual(calls[0], 2, "a 5xx on the uncompressed URL must be retried")


class TestMakeRequestErrorTagging(unittest.TestCase):
    """_make_request's catch-all must include the exception TYPE in the message
    so check_service (which catches RuntimeError before its typed fallback)
    still surfaces the diagnostic type tag."""

    def test_unexpected_error_includes_type_name(self):
        # A non-network error inside the request path (e.g. a decode bug)
        # should be wrapped with its type name, not a bare "Request failed".
        def side_effect(req, timeout=None):
            raise ValueError("surprise")

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions._make_request("https://example.com/api")
        self.assertIn("ValueError", str(ctx.exception))


class TestUpdateVersionInFileUnquoted(unittest.TestCase):
    """The unquoted-value write branch (uses_quotes=False) was untested."""

    def _write_tmp(self, content):
        import tempfile
        tf = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        tf.write(content)
        tf.close()
        return Path(tf.name)

    def test_unquoted_value_stays_unquoted(self):
        import os
        path = self._write_tmp("foo_version: 1.2.3\nbar_version: 4.5.6\n")
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertTrue(
                    check_versions.update_version_in_file("foo_version", "1.2.4")
                )
            out = path.read_text()
            self.assertIn("foo_version: 1.2.4", out)
            self.assertNotIn('foo_version: "1.2.4"', out)  # must stay unquoted
            self.assertIn("bar_version: 4.5.6", out)  # untouched
        finally:
            os.unlink(path)

    def test_prefix_collision_not_matched(self):
        """`redis_version` must not match `redis_exporter_version` (the trailing
        ':' in the startswith guard prevents the prefix collision)."""
        import os
        path = self._write_tmp(
            'redis_version: "1.0"\nredis_exporter_version: "2.0"\n'
        )
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertTrue(
                    check_versions.update_version_in_file("redis_version", "1.1")
                )
            out = path.read_text()
            self.assertIn('redis_version: "1.1"', out)
            self.assertIn('redis_exporter_version: "2.0"', out)  # untouched
        finally:
            os.unlink(path)


class TestReadCurrentVersions(unittest.TestCase):
    """read_current_versions parses all.yml by hand (no PyYAML); feed it a
    representative snippet — mix of quoted/unquoted top-level *_version keys,
    inline comments, and a helm_chart_versions block."""

    def _write_tmp(self, content):
        import tempfile
        tf = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        tf.write(content)
        tf.close()
        return Path(tf.name)

    SNIPPET = (
        "# header comment\n"
        '\n'
        'authentik_version: "2026.2.2"  # Currently deployed\n'
        "debian_version: 13\n"
        "plex_version: 1.43.1.5266  # inline note\n"
        "unrelated_key: ignored\n"
        "helm_chart_versions:\n"
        '  traefik: "40.0.0"  # comment\n'
        "  cert_manager: v1.20.2\n"
        "some_other_top: value\n"
    )

    def test_parses_mixed_snippet(self):
        import os
        path = self._write_tmp(self.SNIPPET)
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                versions = check_versions.read_current_versions()
        finally:
            os.unlink(path)
        self.assertEqual(versions.get("authentik_version"), "2026.2.2")
        self.assertEqual(versions.get("debian_version"), "13")
        self.assertEqual(versions.get("plex_version"), "1.43.1.5266")
        self.assertEqual(versions.get("helm_chart_versions.traefik"), "40.0.0")
        self.assertEqual(versions.get("helm_chart_versions.cert_manager"), "v1.20.2")
        # Keys whose name has no '_version' substring are not collected.
        self.assertNotIn("unrelated_key", versions)
        self.assertNotIn("some_other_top", versions)

    def test_substring_heuristic_collects_non_pin_keys(self):
        """Documents the known fragility (finding scripts-python read_current_versions):
        the `'_version' in key` substring test collects ANY key containing the
        substring, e.g. a hypothetical `min_version_note` — not just true
        `*_version` pins. Locked in so a future regex-anchor fix updates this
        test deliberately."""
        import os
        path = self._write_tmp("min_version_note: hello\n")
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                versions = check_versions.read_current_versions()
        finally:
            os.unlink(path)
        # Current behavior: substring match collects it.
        self.assertIn("min_version_note", versions)

    def test_top_level_after_helm_block_still_parsed(self):
        """A *_version key appearing AFTER the helm block (block exited) is
        still collected — guards the in_helm fall-through."""
        import os
        path = self._write_tmp(
            "helm_chart_versions:\n"
            '  traefik: "40.0.0"\n'
            'gitlab_version: "17.0.0"\n'
        )
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                versions = check_versions.read_current_versions()
        finally:
            os.unlink(path)
        self.assertEqual(versions.get("helm_chart_versions.traefik"), "40.0.0")
        self.assertEqual(versions.get("gitlab_version"), "17.0.0")


class TestAnnotateLatestResolution(unittest.TestCase):
    """_annotate_latest_resolution surfaces the resolved version in notes only
    for services that track 'latest', so the table shows what 'latest' maps to
    (on both the cache-hit and live-fetch paths)."""

    def _mk(self, latest_version=None, notes=""):
        return check_versions.ServiceVersion(
            name="Some Service",
            category="dockerhub",
            current_version="latest",
            latest_version=latest_version,
            var_name="some_version",
            notes=notes,
        )

    def test_latest_with_resolution_appends_suffix(self):
        # current == 'latest' + a resolved latest_version => suffix added.
        result = self._mk(latest_version="1.2.3")
        check_versions._annotate_latest_resolution(result, "latest")
        self.assertEqual(result.notes, "'latest' resolves to 1.2.3")

    def test_latest_preserves_existing_notes(self):
        # Existing notes are kept and the suffix appended after a space.
        result = self._mk(latest_version="1.2.3", notes="pre-existing note")
        check_versions._annotate_latest_resolution(result, "latest")
        self.assertEqual(result.notes, "pre-existing note 'latest' resolves to 1.2.3")

    def test_non_latest_current_left_untouched(self):
        # A pinned (non-'latest') service is never annotated.
        result = check_versions.ServiceVersion(
            name="Some Service",
            category="dockerhub",
            current_version="1.2.0",
            latest_version="1.2.3",
            var_name="some_version",
            notes="keep me",
        )
        check_versions._annotate_latest_resolution(result, "1.2.0")
        self.assertEqual(result.notes, "keep me")

    def test_latest_without_resolution_left_untouched(self):
        # 'latest' but no resolved version yet => nothing to surface.
        result = self._mk(latest_version=None, notes="")
        check_versions._annotate_latest_resolution(result, "latest")
        self.assertEqual(result.notes, "")


class TestCliArgumentValidation(unittest.TestCase):
    """Unknown CLI flags must fail loudly (exit 2), not silently run the full
    unfiltered check (e.g. a typo'd `--catagory helm`)."""

    def _run(self, *args):
        import subprocess
        return subprocess.run(
            [sys.executable, str(script_path), *args],
            capture_output=True,
            text=True,
        )

    def test_unknown_flag_exits_2(self):
        res = self._run("--catagory", "helm")
        self.assertEqual(res.returncode, 2)
        self.assertIn("unknown argument", res.stderr)

    def test_unknown_flag_after_valid_flag_exits_2(self):
        res = self._run("--service", "k3s", "--bogus")
        self.assertEqual(res.returncode, 2)
        self.assertIn("--bogus", res.stderr)

    def test_list_still_works(self):
        res = self._run("--list")
        self.assertEqual(res.returncode, 0)
        self.assertIn("Tracked services", res.stdout)


class TestFetchGithubReleaseTagFilter(unittest.TestCase):
    """The tag_filter branch of fetch_github_release — the version-selection
    logic behind k3s/gluetun/mealie. It paginates, SKIPS drafts/prereleases,
    and returns the HIGHEST version (not the newest by date). None of this is
    covered by the latest-endpoint (no-tag_filter) tests."""

    # Mirrors the real k3s registry entry.
    K3S_SVC = {
        "github_repo": "k3s-io/k3s",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d+\.\d+\.\d+\+k3s\d+$",
    }

    @staticmethod
    def _page_dispatch(pages):
        """github_api side_effect: return the release list for the ?page=N."""
        def api(path):
            m = re.search(r"[?&]page=(\d+)", path)
            return pages.get(int(m.group(1)), [])
        return api

    def test_highest_not_newest_and_skip_is_load_bearing(self):
        # Page 1 (exactly 100 items -> pagination continues to page 2) leads
        # with a STABLE older-branch patch, simulating newest-by-date. Page 2
        # (<100 -> loop stops) holds the true winner plus a prerelease AND a
        # draft that both carry HIGHER versions. The winner must be the highest
        # STABLE version across pages, proving both "highest not newest" and
        # that the draft/prerelease skip is load-bearing (drop the skip and the
        # 1.37 draft would win instead).
        page1 = [{"tag_name": "v1.34.9+k3s2"}] + [
            {"tag_name": f"nightly-{i}"} for i in range(99)  # non-matching filler
        ]
        page2 = [
            {"tag_name": "v1.35.3+k3s1"},
            {"tag_name": "v1.36.0+k3s1", "prerelease": True},
            {"tag_name": "v1.37.0+k3s1", "draft": True},
        ]
        pages = {1: page1, 2: page2}
        with patch.object(check_versions, "github_api",
                          side_effect=self._page_dispatch(pages)) as mock_api:
            result = check_versions.fetch_github_release(self.K3S_SVC)
        self.assertEqual(result, "v1.35.3+k3s1")  # prefix retained (strip_prefix False)
        # page 1 was full (100) -> page 2 fetched; page 2 <100 -> stop.
        self.assertEqual(mock_api.call_count, 2)

    def test_no_matching_release_raises(self):
        # A single page with only draft/prerelease/non-matching entries.
        page1 = [
            {"tag_name": "v1.36.0+k3s1", "prerelease": True},
            {"tag_name": "v1.37.0+k3s1", "draft": True},
            {"tag_name": "not-a-release"},
        ]
        with patch.object(check_versions, "github_api",
                          side_effect=self._page_dispatch({1: page1})):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions.fetch_github_release(self.K3S_SVC)
        self.assertIn("No release matching", str(ctx.exception))

    def test_strip_prefix_applied_in_tag_filter_branch(self):
        # strip_prefix=True in the tag_filter branch must strip the version_prefix
        # from the winning tag (gluetun/mealie-style).
        svc = {
            "github_repo": "owner/repo",
            "version_prefix": "v",
            "strip_prefix": True,
            "tag_filter": r"^v\d+\.\d+\.\d+$",
        }
        page1 = [{"tag_name": "v1.2.3"}, {"tag_name": "v1.2.2"}]
        with patch.object(check_versions, "github_api",
                          side_effect=self._page_dispatch({1: page1})):
            self.assertEqual(check_versions.fetch_github_release(svc), "1.2.3")


class TestFetchLsioVersion(unittest.TestCase):
    """fetch_lsio_version returns the CAPTURED version group (return_full_tag
    False), i.e. it strips the `version-` prefix. Previously only the non-JSON
    guard was tested, never the capture/strip return path."""

    @staticmethod
    def _payload(*tags):
        return {"results": [{"name": t} for t in tags]}

    def test_version_prefix_stripped_from_capture(self):
        svc = {
            "docker_image": "linuxserver/sonarr",
            "lsio_version_regex": r"^version-(\d+\.\d+\.\d+\.\d+)$",
        }
        payload = self._payload("version-4.0.15.2941", "version-4.0.16.2944", "latest")
        with patch.object(check_versions, "_make_request", return_value=payload):
            # Captured version, prefix stripped — not the full "version-..." tag.
            self.assertEqual(check_versions.fetch_lsio_version(svc), "4.0.16.2944")

    def test_bare_tag_regex_empty_prefix(self):
        # qBittorrent-style bare tags (no version- prefix).
        svc = {
            "docker_image": "linuxserver/qbittorrent",
            "lsio_version_regex": r"^(\d+\.\d+\.\d+)$",
        }
        payload = self._payload("5.1.3", "5.1.4", "latest")
        with patch.object(check_versions, "_make_request", return_value=payload):
            self.assertEqual(check_versions.fetch_lsio_version(svc), "5.1.4")


class TestFetchGhcrVersion(unittest.TestCase):
    """fetch_ghcr_version (the gluetun-exporter tracker): anonymous pull-token
    flow, tag_filter matching, and highest-version selection — untested until now."""

    @staticmethod
    def _dispatch(token_resp, tags_resp):
        def request(url, headers=None):
            if "/token" in url:
                return token_resp
            return tags_resp
        return request

    def test_standalone_filter_selects_highest_standalone(self):
        # The -standalone constraint keeps a bare X.Y.Z from ranking above its
        # -standalone twin.
        svc = {"ghcr_image": "thecfu/gluetun-exporter",
               "tag_filter": r"^\d+\.\d+\.\d+-standalone$"}
        tags = {"tags": ["1.2.3", "1.2.3-standalone", "1.3.0-standalone"]}
        with patch.object(check_versions, "_make_request",
                          side_effect=self._dispatch({"token": "x"}, tags)):
            self.assertEqual(check_versions.fetch_ghcr_version(svc), "1.3.0-standalone")

    def test_missing_token_raises(self):
        svc = {"ghcr_image": "thecfu/gluetun-exporter"}
        with patch.object(check_versions, "_make_request",
                          side_effect=self._dispatch({}, {"tags": ["1.0.0"]})):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_ghcr_version(svc)

    def test_non_json_tags_raises(self):
        svc = {"ghcr_image": "thecfu/gluetun-exporter"}
        with patch.object(check_versions, "_make_request",
                          side_effect=self._dispatch({"token": "x"}, "<html>500</html>")):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_ghcr_version(svc)


class TestVersionCache(unittest.TestCase):
    """_read_cache / _write_cache: round-trip, TTL expiry, and the corrupted-
    cache self-heal unlink — all previously uncovered."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.patcher = patch.object(check_versions, "CACHE_DIR", self.tmp)
        self.patcher.start()

    def tearDown(self):
        import shutil
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_then_read_roundtrips(self):
        check_versions._write_cache("Some Service", "1.2.3")
        self.assertEqual(check_versions._read_cache("Some Service"), "1.2.3")

    def test_expired_entry_returns_none(self):
        import json
        import time
        cache_file = check_versions._cache_key("Old Service")
        cache_file.write_text(json.dumps({
            "version": "9.9.9",
            "timestamp": time.time() - check_versions.CACHE_TTL - 1,
            "service": "Old Service",
        }))
        self.assertIsNone(check_versions._read_cache("Old Service"))

    def test_corrupted_entry_is_removed(self):
        cache_file = check_versions._cache_key("Bad Service")
        cache_file.write_text("not json {{{")
        self.assertIsNone(check_versions._read_cache("Bad Service"))
        # Self-heal: the unreadable entry is unlinked so it can't wedge every run.
        self.assertFalse(cache_file.exists())


class TestFormatJson(unittest.TestCase):
    """format_json's summary contract: held updates are excluded from
    updates_available and counted separately in updates_held (version-check-ci.py
    keys its exit code off this distinction)."""

    def test_summary_counts_and_held_flag(self):
        import json
        SV = check_versions.ServiceVersion
        results = [
            SV(name="MetalLB", category="helm", current_version="0.15.3",
               latest_version="0.16.0", update_available=True,
               var_name="helm_chart_versions.metallb", held=True),
            SV(name="Foo", category="helm", current_version="1.0",
               latest_version="1.1", update_available=True, var_name="foo_version"),
            SV(name="Bar", category="github", current_version="1.0",
               var_name="bar_version", error="boom"),
            SV(name="Baz", category="github", current_version="2.0",
               latest_version="2.0", update_available=False, var_name="baz_version"),
        ]
        data = json.loads(check_versions.format_json(results))
        self.assertEqual(data["summary"], {
            "total": 4,
            "up_to_date": 1,
            "updates_available": 1,   # only Foo — the held MetalLB is excluded
            "updates_held": 1,        # MetalLB
            "errors": 1,
        })
        metallb = next(s for s in data["services"] if s["name"] == "MetalLB")
        self.assertIs(metallb.get("held"), True)


class TestCheckService(unittest.TestCase):
    """check_service — the function that decides update_available — exercised
    for real (never mocked): update detection on both the live-fetch and
    cache-hit paths, plus the RuntimeError-vs-typed-Exception error mapping."""

    SVC = {"name": "Fake GH", "var_name": "fake_version", "category": "github"}

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.patcher = patch.object(check_versions, "CACHE_DIR", self.tmp)
        self.patcher.start()

    def tearDown(self):
        import shutil
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_live_update_available_when_latest_greater(self):
        # use_cache=False so the patched fetcher is actually reached (check_service
        # reads the cache first and would short-circuit otherwise).
        with patch.object(check_versions, "fetch_github_release", return_value="2.0.0"):
            r = check_versions.check_service(self.SVC, {"fake_version": "1.0.0"}, use_cache=False)
        self.assertTrue(r.update_available)
        self.assertIsNone(r.error)

    def test_live_no_update_when_equal(self):
        with patch.object(check_versions, "fetch_github_release", return_value="1.0.0"):
            r = check_versions.check_service(self.SVC, {"fake_version": "1.0.0"}, use_cache=False)
        self.assertFalse(r.update_available)

    def test_current_latest_never_updates(self):
        with patch.object(check_versions, "fetch_github_release", return_value="2.0.0"):
            r = check_versions.check_service(self.SVC, {"fake_version": "latest"}, use_cache=False)
        self.assertFalse(r.update_available)

    def test_cache_hit_drives_update_available(self):
        check_versions._write_cache(self.SVC["name"], "3.0.0")
        # A cache hit must return before any fetch.
        with patch.object(check_versions, "fetch_github_release",
                          side_effect=AssertionError("must not fetch on cache hit")):
            r = check_versions.check_service(self.SVC, {"fake_version": "2.0.0"}, use_cache=True)
        self.assertEqual(r.latest_version, "3.0.0")
        self.assertTrue(r.update_available)

    def test_cache_hit_no_update_when_not_greater(self):
        check_versions._write_cache(self.SVC["name"], "2.0.0")
        r = check_versions.check_service(self.SVC, {"fake_version": "2.0.0"}, use_cache=True)
        self.assertFalse(r.update_available)

    def test_runtimeerror_surfaced_verbatim(self):
        with patch.object(check_versions, "fetch_github_release",
                          side_effect=RuntimeError("upstream 500")):
            r = check_versions.check_service(self.SVC, {"fake_version": "1.0.0"}, use_cache=False)
        self.assertEqual(r.error, "upstream 500")
        self.assertNotIn("Unexpected", r.error)

    def test_generic_exception_is_type_tagged(self):
        with patch.object(check_versions, "fetch_github_release",
                          side_effect=ValueError("boom")):
            r = check_versions.check_service(self.SVC, {"fake_version": "1.0.0"}, use_cache=False)
        self.assertTrue(r.error.startswith("Unexpected ValueError"))


class TestMainExitCodes(unittest.TestCase):
    """The default (no-arg) run's exit-code contract: errors->2,
    actionable-updates->1, clean/held-only->0. This is the code the CI job and
    version-check-ci.py reconcile against; only --update/--list/unknown-flag
    paths were covered before."""

    def _run_main_with(self, results):
        with patch.object(check_versions, "check_all", return_value=results), \
             patch.object(check_versions, "read_current_versions", return_value={}), \
             patch.object(check_versions.sys, "argv", ["check-versions.py"]):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        return cm.exception.code

    def test_held_only_and_clean_exits_0(self):
        SV = check_versions.ServiceVersion
        held = SV(name="MetalLB", category="helm", current_version="0.15.3",
                  latest_version="0.16.0", update_available=True,
                  var_name="helm_chart_versions.metallb", held=True)
        clean = SV(name="Foo", category="helm", current_version="1.0",
                   latest_version="1.0", var_name="foo_version")
        self.assertEqual(self._run_main_with([held, clean]), 0)

    def test_actionable_update_exits_1(self):
        SV = check_versions.ServiceVersion
        upd = SV(name="Foo", category="helm", current_version="1.0",
                 latest_version="1.1", update_available=True, var_name="foo_version")
        self.assertEqual(self._run_main_with([upd]), 1)

    def test_error_exits_2(self):
        SV = check_versions.ServiceVersion
        err = SV(name="Bar", category="github", current_version="1.0",
                 var_name="bar_version", error="boom")
        self.assertEqual(self._run_main_with([err]), 2)


if __name__ == "__main__":
    if PYTEST_AVAILABLE:
        # Use pytest for better output formatting when available
        pytest.main([__file__, "-v"])
    else:
        # Fall back to unittest when pytest is not installed
        unittest.main(verbosity=2)
