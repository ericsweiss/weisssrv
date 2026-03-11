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

import unittest
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
        import urllib.error

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


if __name__ == "__main__":
    if PYTEST_AVAILABLE:
        # Use pytest for better output formatting when available
        pytest.main([__file__, "-v"])
    else:
        # Fall back to unittest when pytest is not installed
        unittest.main(verbosity=2)
