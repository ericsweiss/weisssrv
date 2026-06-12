#!/usr/bin/env python3
"""
check-versions.py - Automated version discovery for weisssrv homelab infrastructure.

Checks the latest available versions from official sources and compares them
against the pinned versions in ansible/inventories/prod/group_vars/all.yml.

Supports:
  - GitHub releases (binary tools, container images with GitHub releases)
  - Docker Hub / ghcr.io / LinuxServer.io container image tags
  - Helm chart versions from OCI/HTTP repositories
  - APT package versions (documented only)

Usage:
  ./scripts/check-versions.py                     # Check all services
  ./scripts/check-versions.py --service gluetun    # Check single service
  ./scripts/check-versions.py --category helm      # Check category
  ./scripts/check-versions.py --json               # JSON output
  ./scripts/check-versions.py --update gluetun     # Update version in all.yml
  ./scripts/check-versions.py --update-all         # Update all outdated versions

Environment:
  GITHUB_TOKEN - Optional GitHub personal access token for higher rate limits
                 (unauthenticated: 60 req/hr, authenticated: 5000 req/hr)
"""

import functools
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VARS_FILE = Path(__file__).resolve().parent.parent / "ansible" / "inventories" / "prod" / "group_vars" / "all.yml"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".version-cache"
CACHE_TTL = 3600  # 1 hour cache

# GitHub API rate limit handling
GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GH_API_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")

# Request timeout in seconds
REQUEST_TIMEOUT = 15


@dataclass
class ServiceVersion:
    """Represents a tracked service and its version information."""
    name: str
    category: str  # github, container, helm, apt, manual
    current_version: str
    latest_version: Optional[str] = None
    update_available: bool = False
    source_url: str = ""
    release_url: str = ""
    error: Optional[str] = None
    var_name: str = ""  # Variable name in all.yml
    notes: str = ""
    # A held update is reported but not actionable: it doesn't flip the
    # exit code or trigger MR comments (e.g. MetalLB 0.16.x blocked on an
    # open upstream regression). The registry entry documents why in notes.
    held: bool = False


# Service definitions - maps var_name to lookup configuration
SERVICE_REGISTRY: list[dict] = [
    # --- GitHub releases (binary tools) ---
    {
        "name": "AdGuard Home",
        "var_name": "adguard_home_version",
        "category": "github",
        "github_repo": "AdguardTeam/AdGuardHome",
        "version_prefix": "v",
        "strip_prefix": True,
    },
    {
        "name": "adguardhome-sync",
        "var_name": "adguardhome_sync_version",
        "category": "github",
        "github_repo": "bakito/adguardhome-sync",
        "version_prefix": "v",
        "strip_prefix": True,
    },
    {
        "name": "k3s",
        "var_name": "k3s_version",
        "category": "github",
        "github_repo": "k3s-io/k3s",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d+\.\d+\.\d+\+k3s\d+$",
    },
    {
        "name": "kube-vip",
        "var_name": "kube_vip_version",
        "category": "github",
        "github_repo": "kube-vip/kube-vip",
        "version_prefix": "v",
        "strip_prefix": False,
    },
    {
        "name": "Pulsarr",
        "var_name": "pulsarr_version",
        "category": "github",
        "github_repo": "jamcalli/Pulsarr",
        "version_prefix": "v",
        "strip_prefix": True,
    },
    # --- Container images ---
    {
        "name": "Gluetun",
        "var_name": "gluetun_version",
        "category": "github",
        "github_repo": "qdm12/gluetun",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        # Authentik is deployed via the goauthentik Helm chart, so the
        # version we pin in `authentik_version` is read as a chart tag
        # (e.g. `version: "{{ authentik_version }}"` in the HelmRelease).
        # The Helm chart publishes a few days after the GitHub release tag
        # — see incident 2026-06-10 where the checker reported 2026.5.3
        # from `goauthentik/authentik` GitHub releases, MR #76 picked it
        # up, Flux failed with "no 'authentik' chart with version matching
        # '2026.5.3' found", and MR #78 reverted to 2026.5.2 (the latest
        # actually present in the helm repo). Query the chart repo
        # directly to avoid the lag.
        "name": "Authentik",
        "var_name": "authentik_version",
        "category": "helm",
        "helm_repo": "https://charts.goauthentik.io",
        "helm_chart": "authentik",
        "source_url": "https://github.com/goauthentik/authentik/releases",
    },
    {
        "name": "PostgreSQL (Authentik)",
        "var_name": "postgresql_version",
        "category": "dockerhub",
        "docker_image": "library/postgres",
        "tag_regex": r"^(\d+(?:\.\d+)?)-trixie$",  # Matches 17-trixie, 17.1-trixie, etc.
        "notes": "Used by Authentik (bundled PostgreSQL). Only checks updates within current major version.",
        "pin_major_version": True,  # Only suggest updates within same major version
    },
    {
        "name": "PostgreSQL (Mealie)",
        "var_name": "mealie_postgresql_version",
        "category": "dockerhub",
        "docker_image": "library/postgres",
        "tag_regex": r"^(\d+(?:\.\d+)?)-alpine$",  # Matches 16-alpine, 16.1-alpine, etc.
        "notes": "Used by Mealie (standalone deployment). Only checks updates within current major version.",
        "pin_major_version": True,  # Only suggest updates within same major version
    },
    {
        "name": "Mealie",
        "var_name": "mealie_version",
        "category": "github",
        "github_repo": "mealie-recipes/mealie",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        "name": "Bar Assistant",
        "var_name": "bar_assistant_version",
        "category": "dockerhub",
        "docker_image": "barassistant/server",
        "tag_regex": r"^(\d+\.\d+(?:\.\d+)?)$",
    },
    {
        "name": "Salt Rim",
        "var_name": "salt_rim_version",
        "category": "dockerhub",
        "docker_image": "barassistant/salt-rim",
        "tag_regex": r"^(\d+\.\d+(?:\.\d+)?)$",
    },
    {
        "name": "BusyBox",
        "var_name": "busybox_version",
        "category": "dockerhub",
        "docker_image": "library/busybox",
        "tag_regex": r"^(\d+\.\d+)$",
    },
    {
        "name": "Meilisearch",
        "var_name": "meilisearch_version",
        "category": "dockerhub",
        "docker_image": "getmeili/meilisearch",
        "tag_regex": r"^v(\d+\.\d+\.\d+)$",
        # Bar Assistant requires Meilisearch 1.15.x — the database format is
        # version-locked and newer majors/minors refuse to open old data.
        # Only suggest patch updates within the 1.15 series.
        "version_prefix": "v1.15.",
    },
    {
        "name": "Redis",
        "var_name": "redis_version",
        "category": "dockerhub",
        "docker_image": "library/redis",
        "tag_regex": r"^(\d+\.\d+\.\d+-alpine)$",
    },
    # --- LinuxServer.io container images ---
    # LinuxServer.io tags follow these patterns:
    #   version-vX.Y.Z (nzbget), version-X.Y.Z-rN (qbittorrent),
    #   version-X.Y.Z.BUILD (*arr apps - stable branch)
    # We use the "version-" prefixed tags as the canonical source of truth
    {
        "name": "NZBGet",
        "var_name": "nzbget_version",
        "category": "lsio",
        "docker_image": "linuxserver/nzbget",
        "lsio_tag_prefix": "version-v",
        "lsio_version_regex": r"^version-v(\d+\.\d+(?:\.\d+)?)$",
    },
    {
        "name": "qBittorrent",
        "var_name": "qbittorrent_version",
        "category": "lsio",
        "docker_image": "linuxserver/qbittorrent",
        "lsio_tag_prefix": "",  # qBittorrent uses bare tags without version- prefix
        "lsio_version_regex": r"^(\d+\.\d+\.\d+)$",  # Match bare version tags like "5.1.4"
    },
    {
        "name": "Prowlarr",
        "var_name": "prowlarr_version",
        "category": "lsio",
        "docker_image": "linuxserver/prowlarr",
        "lsio_tag_prefix": "version-",
        "lsio_version_regex": r"^version-(\d+\.\d+\.\d+\.\d+)$",
        "notes": "LinuxServer stable branch",
    },
    {
        "name": "Sonarr",
        "var_name": "sonarr_version",
        "category": "lsio",
        "docker_image": "linuxserver/sonarr",
        "lsio_tag_prefix": "version-",
        "lsio_version_regex": r"^version-(\d+\.\d+\.\d+\.\d+)$",
        "notes": "LinuxServer stable branch",
    },
    {
        "name": "Radarr",
        "var_name": "radarr_version",
        "category": "lsio",
        "docker_image": "linuxserver/radarr",
        "lsio_tag_prefix": "version-",
        "lsio_version_regex": r"^version-(\d+\.\d+\.\d+\.\d+)$",
        "notes": "LinuxServer stable branch",
    },
    {
        "name": "Lidarr",
        "var_name": "lidarr_version",
        "category": "lsio",
        "docker_image": "linuxserver/lidarr",
        "lsio_tag_prefix": "version-",
        "lsio_version_regex": r"^version-(\d+\.\d+\.\d+\.\d+)$",
        "notes": "LinuxServer stable branch",
    },
    # --- Helm charts ---
    {
        "name": "MetalLB",
        "var_name": "helm_chart_versions.metallb",
        "category": "helm",
        "helm_repo": "https://metallb.github.io/metallb",
        "helm_chart": "metallb",
        "source_url": "https://artifacthub.io/packages/helm/metallb/metallb",
        "held": True,
        "notes": (
            "0.16.x intentionally held back: open apiserver-flooding "
            "regression (metallb#3063). Rationale in "
            "kubernetes/infrastructure/controllers/metallb/release.yaml; "
            "re-evaluate when the issue closes."
        ),
    },
    {
        "name": "Traefik",
        "var_name": "helm_chart_versions.traefik",
        "category": "helm",
        "helm_repo": "https://traefik.github.io/charts",
        "helm_chart": "traefik",
        "source_url": "https://github.com/traefik/traefik-helm-chart/releases",
    },
    {
        "name": "cert-manager",
        "var_name": "helm_chart_versions.cert_manager",
        "category": "helm",
        "helm_repo": "https://charts.jetstack.io",
        "helm_chart": "cert-manager",
        "source_url": "https://artifacthub.io/packages/helm/cert-manager/cert-manager",
    },
    {
        "name": "external-dns",
        "var_name": "helm_chart_versions.external_dns",
        "category": "helm",
        "helm_repo": "https://kubernetes-sigs.github.io/external-dns",
        "helm_chart": "external-dns",
        "source_url": "https://artifacthub.io/packages/helm/external-dns/external-dns",
    },
    {
        "name": "External Secrets Operator",
        "var_name": "helm_chart_versions.external_secrets",
        "category": "helm",
        "helm_repo": "https://charts.external-secrets.io",
        "helm_chart": "external-secrets",
        "source_url": "https://artifacthub.io/packages/helm/external-secrets-operator/external-secrets",
    },
    {
        "name": "Tailscale",
        "var_name": "tailscale_version",
        # Track the Tailscale apt repo instead of GitHub releases — the apt
        # publish cadence lags GitHub by days/weeks and we install via apt
        # (base role → tailscale_version pin → `apt install tailscale=...`).
        # Reporting the GitHub version repeatedly suggests bumps the apt
        # repo can't satisfy yet.
        #
        # The Packages index URL is hardcoded to trixie/amd64 because every
        # host that gets Tailscale installed is a Debian Trixie / amd64
        # Proxmox node (CLAUDE.md > "Current Infrastructure"). If we add a
        # different suite/arch to the fleet, also extend this to a list and
        # check every relevant index — otherwise we'd advertise a version
        # the actual `apt install` target can't satisfy (which is exactly
        # the bug this entry is meant to prevent).
        "category": "apt_repo",
        "apt_index_url": "https://pkgs.tailscale.com/stable/debian/dists/trixie/main/binary-amd64/Packages.gz",
        "apt_package": "tailscale",
        "source_url": "https://pkgs.tailscale.com/stable/debian/dists/trixie/main/binary-amd64/Packages.gz",
    },
    # --- GitLab ---
    {
        "name": "GitLab EE",
        "var_name": "gitlab_version",
        "category": "gitlab",
        "source_url": "https://packages.gitlab.com/gitlab/gitlab-ee",
        "notes": "GitLab EE (CE features). Check packages.gitlab.com for apt versions.",
    },
    {
        "name": "GitLab Runner",
        "var_name": "gitlab_runner_helm_version",
        "category": "helm",
        "helm_repo": "https://charts.gitlab.io",
        "helm_chart": "gitlab-runner",
        "source_url": "https://gitlab.com/gitlab-org/charts/gitlab-runner/tags",
    },
    {
        "name": "GitLab Agent (Helm)",
        "var_name": "gitlab_agent_helm_version",
        "category": "helm",
        "helm_repo": "https://charts.gitlab.io",
        "helm_chart": "gitlab-agent",
        "source_url": "https://gitlab.com/gitlab-org/charts/gitlab-agent/tags",
    },
    # --- Observability ---
    {
        "name": "kube-prometheus-stack",
        "var_name": "helm_chart_versions.kube_prometheus_stack",
        "category": "helm",
        "helm_repo": "https://prometheus-community.github.io/helm-charts",
        "helm_chart": "kube-prometheus-stack",
        "source_url": "https://artifacthub.io/packages/helm/prometheus-community/kube-prometheus-stack",
    },
    {
        "name": "Loki",
        "var_name": "helm_chart_versions.loki",
        "category": "helm",
        "helm_repo": "https://grafana-community.github.io/helm-charts",
        "helm_chart": "loki",
        "source_url": "https://artifacthub.io/packages/helm/grafana-community/loki",
    },
    {
        "name": "Alloy",
        "var_name": "helm_chart_versions.alloy",
        "category": "helm",
        "helm_repo": "https://grafana.github.io/helm-charts",
        "helm_chart": "alloy",
        "source_url": "https://artifacthub.io/packages/helm/grafana/alloy",
    },
    {
        "name": "Blackbox Exporter",
        "var_name": "helm_chart_versions.prometheus_blackbox_exporter",
        "category": "helm",
        "helm_repo": "https://prometheus-community.github.io/helm-charts",
        "helm_chart": "prometheus-blackbox-exporter",
        "source_url": "https://artifacthub.io/packages/helm/prometheus-community/prometheus-blackbox-exporter",
    },
    {
        "name": "1Password Connect",
        "var_name": "helm_chart_versions.onepassword_connect",
        "category": "helm",
        "helm_repo": "https://1password.github.io/connect-helm-charts",
        "helm_chart": "connect",
        "source_url": "https://artifacthub.io/packages/helm/1password/connect",
    },
    {
        "name": "VPA",
        "var_name": "helm_chart_versions.vpa",
        "category": "helm",
        "helm_repo": "https://charts.fairwinds.com/stable",
        "helm_chart": "vpa",
        "source_url": "https://artifacthub.io/packages/helm/fairwinds-stable/vpa",
    },
    {
        "name": "Flux CLI (CI verify)",
        "var_name": "flux_version",
        "category": "github",
        "github_repo": "fluxcd/flux2",
        "version_prefix": "v",
        "strip_prefix": True,
    },
    {
        "name": "Exportarr",
        "var_name": "exportarr_version",
        "category": "github",
        "github_repo": "onedr0p/exportarr",
        "version_prefix": "v",
        "strip_prefix": False,
    },
    {
        "name": "Proxmox VE Exporter",
        "var_name": "proxmox_exporter_version",
        "category": "github",
        "github_repo": "prometheus-pve/prometheus-pve-exporter",
        "version_prefix": "v",
        "strip_prefix": True,
    },
    {
        "name": "ZFS Exporter",
        "var_name": "zfs_exporter_version",
        "category": "github",
        "github_repo": "pdf/zfs_exporter",
        "version_prefix": "v",
        "strip_prefix": True,
    },
    {
        "name": "AdGuard Exporter",
        "var_name": "adguard_exporter_version",
        "category": "github",
        "github_repo": "henrywhitaker3/adguard-exporter",
        "version_prefix": "v",
        "strip_prefix": False,
    },
    {
        "name": "Unbound Exporter",
        "var_name": "unbound_exporter_version",
        "category": "github",
        "github_repo": "letsencrypt/unbound_exporter",
        "version_prefix": "v",
        "strip_prefix": True,
        "pin_version": True,
        "notes": "Pinned to v0.4.6 — v0.5.0 has no binary releases (.deb only for v0.4.6).",
    },
    {
        "name": "Redis Exporter",
        "var_name": "redis_exporter_version",
        "category": "dockerhub",
        "docker_image": "oliver006/redis_exporter",
        "tag_regex": r"^(v\d+\.\d+\.\d+)$",
    },
    # --- APT / Manual ---
    {
        "name": "Plex Media Server",
        "var_name": "plex_version",
        "category": "plex",
        "source_url": "https://www.plex.tv/media-server-downloads/",
    },
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _make_request(url: str, headers: Optional[dict] = None) -> dict | list | str:
    """Make an HTTP GET request and return parsed JSON or raw text."""
    req_headers = {"User-Agent": "weisssrv-version-checker/1.0"}
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = resp.read().decode("utf-8")
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return data
    except urllib.error.HTTPError as e:
        if e.code == 403:
            # Check for rate limiting
            remaining = e.headers.get("X-RateLimit-Remaining", "?")
            reset = e.headers.get("X-RateLimit-Reset", "?")
            raise RuntimeError(
                f"HTTP 403 (rate limited?) remaining={remaining} reset={reset}"
            ) from e
        raise RuntimeError(f"HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error: {e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Request failed: {e}") from e


def github_api(path: str) -> dict | list:
    """Make a GitHub API request with optional authentication."""
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return _make_request(f"{GITHUB_API}{path}", headers)


def fetch_apt_packages(base_url: str) -> str:
    """Fetch apt Packages file, trying uncompressed first then .gz fallback.

    Some apt repositories only provide compressed Packages.gz files.
    This function handles both cases for better reliability.

    Args:
        base_url: URL to the Packages file (without .gz extension)

    Returns:
        The contents of the Packages file as a string

    Raises:
        RuntimeError: If neither Packages nor Packages.gz can be fetched
    """
    req_headers = {"User-Agent": "weisssrv-version-checker/1.0"}

    def _is_valid_packages_response(resp, content: str) -> bool:
        """Check if response is a valid Packages file (not an HTML error page)."""
        content_type = resp.headers.get("Content-Type", "")
        # Packages files are text/plain or have no Content-Type
        # HTML error pages will have text/html
        if "text/html" in content_type.lower():
            return False
        # Also check content for HTML markers in case Content-Type is missing
        if content.strip().startswith("<!DOCTYPE") or content.strip().startswith("<html"):
            return False
        # Valid Packages files contain "Package:" lines
        if "Package:" not in content:
            return False
        return True

    # Try uncompressed first
    try:
        req = urllib.request.Request(base_url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            content = resp.read().decode("utf-8")
            if content.strip() and _is_valid_packages_response(resp, content):
                return content
    except (urllib.error.HTTPError, urllib.error.URLError, UnicodeDecodeError):
        pass

    # Fall back to .gz compressed version
    gz_url = f"{base_url}.gz"
    req = urllib.request.Request(gz_url, headers=req_headers)

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            # Check Content-Type before attempting decompression
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" in content_type.lower():
                raise RuntimeError(f"Received HTML error page instead of Packages.gz from {gz_url}")

            compressed_data = resp.read()
            with gzip.GzipFile(fileobj=BytesIO(compressed_data)) as gz:
                content = gz.read().decode("utf-8")
                # Validate the decompressed content
                if not content.strip() or "Package:" not in content:
                    raise RuntimeError(f"Invalid or empty Packages file from {gz_url}")
                return content
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Failed to fetch {base_url} or {gz_url}: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error fetching apt Packages: {e.reason}") from e
    except gzip.BadGzipFile as e:
        raise RuntimeError(f"Invalid gzip data from {gz_url}") from e


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(service_name: str) -> Path:
    """Generate a cache file path for a service."""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", service_name)
    return CACHE_DIR / f"{safe_name}.json"


def _read_cache(service_name: str) -> Optional[str]:
    """Read cached version if still valid."""
    cache_file = _cache_key(service_name)
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text())
        if time.time() - data.get("timestamp", 0) < CACHE_TTL:
            return data.get("version")
    except (json.JSONDecodeError, KeyError, OSError) as e:
        # Delete corrupted cache so _write_cache can overwrite cleanly and
        # we don't keep hitting the same broken entry on every run.
        print(
            f"Warning: corrupted cache {cache_file.name}, removing: {e}",
            file=sys.stderr,
        )
        try:
            cache_file.unlink()
        except OSError as e2:
            print(f"Warning: could not remove corrupted cache {cache_file.name}: {e2}", file=sys.stderr)
    return None


def _write_cache(service_name: str, version: str) -> None:
    """Write version to cache."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _cache_key(service_name)
        cache_file.write_text(json.dumps({
            "version": version,
            "timestamp": time.time(),
            "service": service_name,
        }))
    except OSError as e:
        print(f"Warning: failed to write cache for {service_name}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------

def parse_version_tuple(version_str: str) -> tuple:
    """Parse a version string into a comparable tuple.

    Handles formats like:
      1.2.3, v1.2.3, 2025.12.3, 1.2.3.4567, v1.33.7+k3s1, v1.35.2+k3s10

    Numeric suffixes (like k3s1, k3s10) are handled by extracting all numeric
    parts for proper ordering (so k3s10 > k3s9, not k3s10 < k3s9).

    Returns a tuple of (type_rank, value) pairs where type_rank is 0 for ints
    and 1 for strings. This ensures consistent comparison ordering: all ints
    sort before all strings, and within each type, values compare naturally.
    """
    # Remove leading 'v' for comparison
    v = version_str.lstrip("v")
    # Replace + with . for k3s-style versions
    v = v.replace("+", ".")
    # Split on . and - and try to convert to ints
    parts = re.split(r"[.\-]", v)
    result = []
    for part in parts:
        # Split part into alternating text/numeric segments for proper ordering
        # This handles both "123abc" and "abc123" patterns (e.g., "k3s1", "k3s10")
        segments = re.findall(r"(\d+|\D+)", part)
        for seg in segments:
            if seg.isdigit():
                # Tuple of (type_rank=0, int_value) - ints sort before strings
                result.append((0, int(seg)))
            else:
                # Tuple of (type_rank=1, str_value) - strings sort after ints
                result.append((1, seg))
    return tuple(result)


def version_tuple_greater(a: tuple, b: tuple) -> bool:
    """Compare two version tuples, handling different lengths correctly.

    This handles the case where versions have different segment counts:
    - "17.1" > "17" (17.1 is newer - more segments with matching prefix)
    - "17.1-trixie" > "17-trixie" (17.1 is newer)
    - "18-trixie" > "17.1-trixie" (18 is newer)

    The key insight: when comparing version segments at the same position,
    a numeric segment (like a minor version number) takes precedence over
    a string segment (like a suffix). This handles the case where:
    - "17.1-trixie" ((0,17),(0,1),(1,"trixie")) vs "17-trixie" ((0,17),(1,"trixie"))
    - At index 1: (0,1) vs (1,"trixie") - numeric vs string

    Comparison rules for (type_rank, value) tuples at same position:
    - Same type_rank: compare values normally
    - Different type_rank: numeric (0) beats string (1) for version purposes
      because a numeric segment represents a version number, not a suffix

    Returns True if tuple a represents a newer version than tuple b.
    """
    # Compare element by element up to the shorter length
    min_len = min(len(a), len(b))
    for i in range(min_len):
        a_elem, b_elem = a[i], b[i]
        a_type, a_val = a_elem
        b_type, b_val = b_elem

        # Same type: compare values
        if a_type == b_type:
            if a_val > b_val:
                return True
            if a_val < b_val:
                return False
            # Equal, continue to next element
        else:
            # Different types: numeric (0) beats string (1)
            # This handles "17.1-trixie" vs "17-trixie" at position 1:
            #   (0, 1) [numeric minor version] vs (1, "trixie") [string suffix]
            #   Numeric segment = more specific version = newer
            return a_type < b_type  # 0 < 1, so numeric wins

    # All compared elements are equal; now check remaining elements
    if len(a) == len(b):
        return False  # Identical versions

    # Versions have different lengths with matching prefix
    # The longer version is newer IF its next element is numeric (type_rank=0)
    # Examples:
    #   "17.1" ((0,17),(0,1)) > "17" ((0,17)) - extra numeric = newer
    #   "17-alpha" ((0,17),(1,"alpha")) < "17" ((0,17)) - extra string suffix = older (pre-release)
    if len(a) > len(b):
        # a has more segments - a is newer if next segment is numeric
        return a[min_len][0] == 0  # type_rank 0 = numeric
    else:
        # b has more segments - b is newer if next segment is numeric, so a is NOT newer
        return b[min_len][0] != 0  # a is newer only if b's extra is a string (pre-release)


def version_greater(a: str, b: str) -> bool:
    """Return True if version a is greater than version b.

    Uses version_tuple_greater for proper handling of versions with different
    segment counts (e.g., "17.1-trixie" > "17-trixie").
    """
    try:
        return version_tuple_greater(parse_version_tuple(a), parse_version_tuple(b))
    except (TypeError, ValueError):
        # Fallback to lexicographic — log so operators know comparison quality may be degraded
        print(f"Warning: falling back to string comparison for {a!r} vs {b!r}", file=sys.stderr)
        return a > b


def version_compare(a: str, b: str) -> int:
    """Compare two version strings for sorting.

    Returns:
        -1 if a < b, 0 if a == b, 1 if a > b

    This is a comparator function suitable for use with functools.cmp_to_key.
    Uses semantic comparison via parsed tuples, so "1.0.0" and "v1.0.0" are equal.
    """
    # Parse both versions to tuples for semantic comparison
    a_tuple = parse_version_tuple(a)
    b_tuple = parse_version_tuple(b)

    # If tuples are equal, versions are semantically identical
    if a_tuple == b_tuple:
        return 0
    if version_tuple_greater(a_tuple, b_tuple):
        return 1
    return -1


# ---------------------------------------------------------------------------
# Version fetchers
# ---------------------------------------------------------------------------

def _debian_version_part_compare(a: str, b: str) -> int:
    """Compare one Debian upstream_version or debian_revision part per
    debian-policy §5.6.12: alternate non-digit and digit chunks. Non-digit
    chunks compare lexically with the tweak that letters sort before all
    non-letters and `~` sorts before everything (including the empty
    string); digit chunks compare numerically.
    """
    def order(c: str) -> int:
        # Sort key inside a non-digit chunk:
        #   '~'  → -1   (sorts before end-of-string and before everything else)
        #   ''   →  0   (end of chunk, before any non-tilde char)
        #   letter → ord (a..z, A..Z) — sorts before non-letter non-tilde
        #   other → ord + 256  (sorts after letters)
        if c == "~":
            return -1
        if c == "":
            return 0
        if c.isalpha():
            return ord(c)
        return ord(c) + 256

    i = j = 0
    while i < len(a) or j < len(b):
        # Non-digit run
        sa = ""
        while i < len(a) and not a[i].isdigit():
            sa += a[i]
            i += 1
        sb = ""
        while j < len(b) and not b[j].isdigit():
            sb += b[j]
            j += 1
        # Compare character by character with Debian's ordering tweaks
        for k in range(max(len(sa), len(sb))):
            ca = sa[k] if k < len(sa) else ""
            cb = sb[k] if k < len(sb) else ""
            if order(ca) != order(cb):
                return -1 if order(ca) < order(cb) else 1

        # Digit run — compare numerically (skipping leading zeros)
        na = ""
        while i < len(a) and a[i].isdigit():
            na += a[i]
            i += 1
        nb = ""
        while j < len(b) and b[j].isdigit():
            nb += b[j]
            j += 1
        if (int(na) if na else 0) != (int(nb) if nb else 0):
            return -1 if (int(na) if na else 0) < (int(nb) if nb else 0) else 1
    return 0


def debian_version_compare(a: str, b: str) -> int:
    """Compare two Debian package version strings per debian-policy
    §5.6.12 (epoch:upstream_version[-debian_revision]). Returns -1, 0, +1.

    Reimplemented in pure Python so the controller doesn't need dpkg
    installed (e.g. when run from a macOS dev machine). Cross-checked
    against `dpkg --compare-versions` in CI via tests:
      0:1.98.4 < 1.98.5
      1:0.4.6-1 > 0.4.6 (epoch wins)
      0.5.0~rc1-1 < 0.5.0-1 (tilde is pre-release)
      0.4.6-1ubuntu1 > 0.4.6-1 (revision tail)
    """
    # Split epoch. Per debian-policy §5.6.12 the epoch is "a single
    # (generally small) unsigned integer". Anything else with a `:` in it
    # is malformed and we raise rather than silently dropping back to
    # epoch=0 (which would otherwise hide upstream metadata bugs as
    # "version unchanged" reports). The `:` itself is reserved as the
    # epoch separator so there's no legitimate non-epoch case to fall
    # back to.
    def split(v: str) -> tuple[int, str, str]:
        if ":" in v:
            ep_s, rest = v.split(":", 1)
            try:
                ep = int(ep_s)
            except ValueError as e:
                raise ValueError(
                    f"malformed Debian version {v!r}: epoch prefix "
                    f"{ep_s!r} before ':' must be an unsigned integer"
                ) from e
            if ep < 0:
                raise ValueError(
                    f"malformed Debian version {v!r}: epoch must be "
                    f"non-negative (got {ep})"
                )
        else:
            ep, rest = 0, v
        # Split upstream / debian_revision on LAST '-'
        if "-" in rest:
            up, rev = rest.rsplit("-", 1)
        else:
            up, rev = rest, ""
        return ep, up, rev

    ea, ua, ra = split(a)
    eb, ub, rb = split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    rc = _debian_version_part_compare(ua, ub)
    if rc != 0:
        return rc
    return _debian_version_part_compare(ra, rb)


def fetch_apt_repo_version(svc: dict) -> str:
    """Fetch latest version from a Debian apt repo's Packages index.

    Use for upstream-managed apt repos (e.g. pkgs.tailscale.com) where the
    GitHub release cadence runs ahead of the apt publish cadence. Tracking
    GitHub would advertise versions that `apt-get install` can't satisfy.

    Required keys in `svc`:
      apt_index_url: URL to the (typically gzipped) Packages file, e.g.
                     https://pkgs.tailscale.com/stable/debian/dists/trixie/main/binary-amd64/Packages.gz
                     Detect gzip from the response payload header rather
                     than the URL suffix — apt mirrors often serve the
                     index with a redirect and/or the `.gz` may be
                     stripped in the final URL.
      apt_package:   Binary package name (e.g. "tailscale").
    """
    url = svc["apt_index_url"]
    pkg = svc["apt_package"]
    req = urllib.request.Request(url, headers={"User-Agent": "weisssrv-version-check/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    # gzip magic bytes are 0x1f 0x8b. Sniff the payload rather than the
    # URL extension so an apt-mirror redirect that drops `.gz` from the
    # path (or one that serves un-gzipped content over a `.gz` URL)
    # parses correctly.
    text = (
        gzip.decompress(raw).decode("utf-8", errors="replace")
        if raw[:2] == b"\x1f\x8b"
        else raw.decode("utf-8", errors="replace")
    )

    # Packages files are stanzas separated by blank lines; each stanza has
    # `Package:` and `Version:` lines among others. Collect all Version
    # lines for stanzas whose Package matches our target, then return the
    # highest using debian-policy version ordering (epochs, revisions,
    # and `~` pre-release semantics — a plain string-tuple compare would
    # silently get these wrong).
    versions: list[str] = []
    in_pkg = False
    for line in text.splitlines():
        if line.startswith("Package:"):
            in_pkg = line.split(":", 1)[1].strip() == pkg
        elif in_pkg and line.startswith("Version:"):
            versions.append(line.split(":", 1)[1].strip())
    if not versions:
        raise RuntimeError(f"package '{pkg}' not found in {url}")
    latest = versions[0]
    for v in versions[1:]:
        if debian_version_compare(v, latest) > 0:
            latest = v
    return latest


def fetch_github_release(svc: dict) -> str:
    """Fetch latest release version from GitHub.

    When tag_filter is specified, collects all matching releases and returns
    the one with the highest version number (not the most recently published).
    This handles projects like Authentik that maintain multiple release branches
    and may publish patches to older branches after newer releases.

    Pagination: GitHub returns max 100 releases per page. For repos with many
    releases, we paginate up to 5 pages (500 releases) to ensure we find all
    matching versions.
    """
    repo = svc["github_repo"]
    tag_filter = svc.get("tag_filter")
    prefix = svc.get("version_prefix", "")
    strip_prefix = svc.get("strip_prefix", False)

    if tag_filter:
        # List releases and filter, then sort by version to get the highest
        # Use per_page=100 (GitHub API maximum) and paginate to avoid missing
        # versions in repos with many releases across multiple branches
        matching_versions = []
        max_pages = 5  # Limit pagination to avoid excessive API calls

        for page in range(1, max_pages + 1):
            releases = github_api(f"/repos/{repo}/releases?per_page=100&page={page}")

            # Empty page means we've exhausted all releases
            if not releases:
                break

            for release in releases:
                if release.get("draft") or release.get("prerelease"):
                    continue
                tag = release.get("tag_name", "")
                if re.match(tag_filter, tag):
                    version = tag
                    if strip_prefix and prefix and version.startswith(prefix):
                        version = version[len(prefix):]
                    matching_versions.append(version)

            # If we got fewer than 100 releases, this is the last page
            if len(releases) < 100:
                break

        if not matching_versions:
            raise RuntimeError(f"No release matching {tag_filter}")

        # Sort by semantic version comparison to get the highest version, not the most recent by date
        # Uses version_compare for proper handling of mixed numeric/string segments
        matching_versions.sort(key=functools.cmp_to_key(version_compare), reverse=True)
        return matching_versions[0]
    else:
        # Use latest release endpoint
        release = github_api(f"/repos/{repo}/releases/latest")
        version = release.get("tag_name", "")
        if strip_prefix and prefix and version.startswith(prefix):
            version = version[len(prefix):]
        return version


def fetch_dockerhub_version(svc: dict) -> str:
    """Fetch latest version from Docker Hub using tag_regex.

    The tag_regex should have a capture group for the version portion.
    The highest matching version (by version tuple comparison) is returned,
    prefixed with the non-captured portion of the tag.

    If pin_major_version is True, only returns versions matching the same major
    version as the current version.
    """
    image = svc["docker_image"]
    tag_regex = svc.get("tag_regex", r"^(v?\d+(?:\.\d+)*)$")
    pin_major = svc.get("pin_major_version", False)
    current_version = svc.get("_current_version", "")

    # Extract major version from current version if pinning
    major_version_filter = None
    if pin_major and current_version:
        # Extract major version (e.g., "17-trixie" -> "17", "17.2-trixie" -> "17")
        match = re.match(r"^(\d+)", current_version)
        if match:
            major_version_filter = match.group(1)

    # For postgres, use larger page size to find alpine/trixie tags.
    # For version_prefix-pinned services, use Docker Hub's name= filter so
    # old tags that have scrolled off the first page are still found.
    page_size = 100 if image == "library/postgres" else 50
    version_prefix = svc.get("version_prefix", "")
    url = f"https://hub.docker.com/v2/repositories/{image}/tags?page_size={page_size}&ordering=last_updated"
    if version_prefix:
        url += f"&name={version_prefix}"
    data = _make_request(url)

    best_tag = None
    best_tuple = None

    for result in data.get("results", []):
        tag_name = result.get("name", "")
        match = re.match(tag_regex, tag_name)
        if match:
            # version_prefix: only consider tags starting with this prefix
            # (e.g., "v1.15." restricts to patch updates within 1.15.x)
            version_prefix = svc.get("version_prefix")
            if version_prefix and not tag_name.startswith(version_prefix):
                continue

            # If pinning major version, check if this tag matches
            if major_version_filter:
                tag_major = re.match(r"^(\d+)", tag_name)
                if not tag_major or tag_major.group(1) != major_version_filter:
                    continue  # Skip tags from different major versions

            # Compare using the captured version portion (match.group(1)) to avoid
            # TypeError when comparing tuples with mixed int/str elements (e.g.,
            # "17-trixie" vs "17.1-trixie" produces (17, "trixie") vs (17, 1, "trixie")).
            # Still return the full tag name since that's what's stored in all.yml.
            # Use version_tuple_greater for proper semantic ordering with (type_rank, value) tuples.
            extracted_version = match.group(1)
            try:
                vtuple = parse_version_tuple(extracted_version)
                if best_tuple is None or version_tuple_greater(vtuple, best_tuple):
                    best_tuple = vtuple
                    best_tag = tag_name
            except (TypeError, ValueError):
                continue

    if best_tag is None:
        raise RuntimeError(f"No matching tags found for {image} (regex: {tag_regex})")

    return best_tag


def fetch_lsio_version(svc: dict) -> str:
    """Fetch latest version from LinuxServer.io Docker Hub images.

    LinuxServer.io images use canonical version tags with prefixes:
      version-vX.Y.Z (nzbget), version-X.Y.Z-rN (qbittorrent),
      version-X.Y.Z.BUILD (*arr apps - stable branch)

    The regex captures the version portion from the tag.
    """
    image = svc["docker_image"]
    version_regex = svc["lsio_version_regex"]

    # Docker Hub API v2 - list tags sorted by most recently updated
    # For postgres, use larger page size to find alpine/trixie tags.
    # For version_prefix-pinned services, use Docker Hub's name= filter so
    # old tags that have scrolled off the first page are still found.
    page_size = 100 if image == "library/postgres" else 50
    version_prefix = svc.get("version_prefix", "")
    url = f"https://hub.docker.com/v2/repositories/{image}/tags?page_size={page_size}&ordering=last_updated"
    if version_prefix:
        url += f"&name={version_prefix}"
    data = _make_request(url)

    best_version = None
    best_tuple = None

    for result in data.get("results", []):
        tag_name = result.get("name", "")
        match = re.match(version_regex, tag_name)
        if match:
            extracted = match.group(1)
            try:
                vtuple = parse_version_tuple(extracted)
                # Use version_tuple_greater for proper semantic ordering with (type_rank, value) tuples
                if best_tuple is None or version_tuple_greater(vtuple, best_tuple):
                    best_tuple = vtuple
                    best_version = extracted
            except (TypeError, ValueError):
                continue

    if best_version is None:
        raise RuntimeError(
            f"No matching tags found for {image} "
            f"(regex: {version_regex})"
        )

    return best_version


def fetch_ghcr_version(svc: dict) -> str:
    """Fetch latest version tag from GitHub Container Registry.

    Uses the GitHub API to list package versions since ghcr.io token
    auth requires a different flow.
    """
    image = svc["ghcr_image"]
    tag_filter = svc.get("tag_filter", r"^v?\d+\.\d+")

    # Use GitHub API for package versions
    # ghcr.io packages are accessible via the packages API
    owner = image.split("/")[0]
    package_name = image.split("/", 1)[1] if "/" in image else image

    url = f"/users/{owner}/packages/container/{package_name}/versions?per_page=50"
    try:
        versions = github_api(url)
    except RuntimeError:
        # Try org endpoint
        url = f"/orgs/{owner}/packages/container/{package_name}/versions?per_page=50"
        versions = github_api(url)

    best_version = None
    best_tuple = None

    for version in versions:
        tags = version.get("metadata", {}).get("container", {}).get("tags", [])
        for tag in tags:
            if re.match(tag_filter, tag):
                try:
                    vtuple = parse_version_tuple(tag)
                    if best_tuple is None or version_tuple_greater(vtuple, best_tuple):
                        best_tuple = vtuple
                        best_version = tag
                except (TypeError, ValueError):
                    continue

    if best_version is None:
        raise RuntimeError(f"No matching tags found for ghcr.io/{image}")

    return best_version


def fetch_helm_version(svc: dict) -> str:
    """Fetch latest chart version from a Helm repository index.

    Parses the index.yaml manually to avoid PyYAML dependency.
    The format is:
        entries:
          chartname:
          - apiVersion: v2
            version: X.Y.Z
          - apiVersion: v2
            version: X.Y.Z
          otherchartname:
          ...
    """
    repo_url = svc["helm_repo"]
    chart_name = svc["helm_chart"]

    index_url = f"{repo_url}/index.yaml"
    raw = _make_request(index_url)

    if not isinstance(raw, str):
        raise RuntimeError(f"Unexpected response type from {index_url}")

    # Find the chart section using a simple state machine
    lines = raw.split("\n")
    in_entries = False
    in_chart = False
    chart_indent = 0
    versions = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Track when we enter the entries: block
        if stripped == "entries:":
            in_entries = True
            continue

        if not in_entries:
            continue

        # Detect chart name entry - it appears as "  chartname:" under entries
        # The key thing is that chart names are at a consistent indentation level
        if not in_chart:
            # Chart names are indented exactly 2 spaces under entries:
            if line.rstrip().rstrip(":") and stripped.rstrip(":") == chart_name:
                in_chart = True
                chart_indent = len(line) - len(line.lstrip())
                continue
        else:
            # Calculate this line's indent
            if not line or line.isspace():
                continue
            line_indent = len(line) - len(line.lstrip())

            # If we hit something at the same indent as the chart name
            # (another chart entry), we've exited our chart section
            if line_indent <= chart_indent and not stripped.startswith("-"):
                break

            # Look for "version:" lines within chart entries
            # These are indented deeper than the chart name
            if stripped.startswith("version:") and not stripped.startswith("version:  "):
                ver = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                if not re.search(r"(alpha|beta|rc|dev|snapshot)", ver, re.IGNORECASE):
                    versions.append(ver)

    if not versions:
        raise RuntimeError(f"No versions found for chart {chart_name}")

    # Sort by semantic version comparison for proper handling of mixed numeric/string segments
    versions.sort(key=functools.cmp_to_key(version_compare), reverse=True)
    return versions[0]


def fetch_plex_version(svc: dict) -> str:
    """Fetch latest Plex Media Server version from Plex apt repository.

    Queries the actual apt repository Packages file to get the version
    that's available for installation, rather than the Plex downloads API
    which may advertise versions not yet available in apt.

    Collects all plexmediaserver versions and returns the highest one,
    since the Packages file may contain multiple versions.
    """
    # Fetch from the apt repository Packages file (where apt actually installs from)
    # v2 repository URL (as of Plex v1.43.0)
    # Uses fetch_apt_packages to handle both uncompressed and .gz formats
    packages_url = "https://repo.plex.tv/deb/dists/public/main/binary-amd64/Packages"
    raw = fetch_apt_packages(packages_url)

    # Parse the Packages file format (debian control file)
    # Looking for:
    #   Package: plexmediaserver
    #   Version: X.Y.Z.BUILD-hash
    versions = []
    in_plex_package = False

    for line in raw.split("\n"):
        if line.startswith("Package:"):
            package_name = line.split(":", 1)[1].strip()
            in_plex_package = package_name == "plexmediaserver"
        elif in_plex_package and line.startswith("Version:"):
            version = line.split(":", 1)[1].strip()
            versions.append(version)
            in_plex_package = False  # Reset for next package block
        elif line == "" and in_plex_package:
            # End of package block without finding version
            in_plex_package = False

    if not versions:
        raise RuntimeError("Could not find plexmediaserver version in apt repository")

    # Sort by semantic version comparison for proper handling of mixed numeric/string segments
    versions.sort(key=functools.cmp_to_key(version_compare), reverse=True)
    return versions[0]


def fetch_gitlab_version(svc: dict) -> str:
    """Fetch latest GitLab EE version from GitLab apt repository.

    Queries the actual apt repository Packages file to get the version
    that's available for installation. Uses fetch_apt_packages to handle
    both uncompressed and .gz formats.
    """
    # Fetch from the apt repository Packages file (Debian trixie/bookworm amd64)
    # Try trixie first (Debian 13), fall back to bookworm (Debian 12)
    packages_urls = [
        "https://packages.gitlab.com/gitlab/gitlab-ee/debian/dists/trixie/main/binary-amd64/Packages",
        "https://packages.gitlab.com/gitlab/gitlab-ee/debian/dists/bookworm/main/binary-amd64/Packages",
    ]

    raw = None
    errors = []
    for url in packages_urls:
        try:
            raw = fetch_apt_packages(url)
            if raw and raw.strip():
                break
        except RuntimeError as e:
            errors.append(f"{url}: {e}")
            continue

    if not raw:
        raise RuntimeError(
            ("Could not fetch GitLab apt repository Packages file; attempts: "
             + "; ".join(errors)) if errors else "Could not fetch GitLab apt Packages"
        )

    # Parse the Packages file format (debian control file)
    # Looking for:
    #   Package: gitlab-ee
    #   Version: X.Y.Z-ee.N
    best_version = None
    best_tuple = None
    in_gitlab_package = False

    for line in raw.split("\n"):
        if line.startswith("Package:"):
            package_name = line.split(":", 1)[1].strip()
            in_gitlab_package = package_name == "gitlab-ee"
        elif in_gitlab_package and line.startswith("Version:"):
            version = line.split(":", 1)[1].strip()
            # Skip RC/beta versions
            if re.search(r"(rc|beta|alpha)", version, re.IGNORECASE):
                in_gitlab_package = False
                continue
            try:
                vtuple = parse_version_tuple(version)
                # Use version_tuple_greater for proper semantic ordering with (type_rank, value) tuples
                if best_tuple is None or version_tuple_greater(vtuple, best_tuple):
                    best_tuple = vtuple
                    best_version = version
            except (TypeError, ValueError):
                pass
            in_gitlab_package = False
        elif line == "" and in_gitlab_package:
            in_gitlab_package = False

    if not best_version:
        raise RuntimeError("Could not find gitlab-ee version in apt repository")

    return best_version


# ---------------------------------------------------------------------------
# all.yml parser (simple YAML extraction without PyYAML)
# ---------------------------------------------------------------------------

def read_current_versions() -> dict[str, str]:
    """Read current versions from all.yml without a YAML parser.

    Returns a dict mapping var_name to current version string.
    """
    content = VARS_FILE.read_text()
    versions = {}

    # Track if we are inside helm_chart_versions block
    in_helm = False

    for line in content.split("\n"):
        stripped = line.strip()

        # Skip comments and empty lines
        if not stripped or stripped.startswith("#"):
            continue

        # Detect helm_chart_versions block
        if stripped == "helm_chart_versions:":
            in_helm = True
            continue

        if in_helm:
            # Indented entries under helm_chart_versions
            if line.startswith("  ") and ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                # Remove inline comments
                if "#" in val:
                    val = val[:val.index("#")].strip().strip('"').strip("'")
                versions[f"helm_chart_versions.{key}"] = val
            elif not line.startswith(" "):
                in_helm = False
                # Fall through to check this line as a regular entry

        if not in_helm and ":" in stripped and "_version" in stripped.split(":")[0]:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            # Remove inline comments
            if "#" in val:
                val = val[:val.index("#")].strip().strip('"').strip("'")
            versions[key] = val

    return versions


def update_version_in_file(var_name: str, new_version: str) -> bool:
    """Update a version in all.yml, preserving formatting and comments.

    Returns True if the file was modified.
    """
    content = VARS_FILE.read_text()
    lines = content.split("\n")
    modified = False

    if var_name.startswith("helm_chart_versions."):
        # Handle nested helm chart version
        chart_key = var_name.split(".", 1)[1]
        in_helm = False
        for i, line in enumerate(lines):
            if line.strip() == "helm_chart_versions:":
                in_helm = True
                continue
            if in_helm and line.startswith("  ") and line.strip().startswith(f"{chart_key}:"):
                # Preserve the comment portion
                comment = ""
                if "#" in line:
                    # Find comment after the value
                    parts = line.split("#", 1)
                    comment_text = parts[1]
                    # Update "Currently deployed" comment
                    comment_text = re.sub(
                        r"Currently deployed \S+",
                        f"Currently deployed {new_version}",
                        comment_text,
                    )
                    comment = f"# {comment_text.strip()}" if comment_text.strip() else ""

                indent = len(line) - len(line.lstrip())
                prefix = " " * indent + f'{chart_key}: "{new_version}"'
                if comment:
                    lines[i] = f"{prefix}  {comment}"
                else:
                    lines[i] = prefix
                modified = True
                break
            if in_helm and not line.startswith(" ") and line.strip() and not line.strip().startswith("#"):
                break
    else:
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{var_name}:"):
                # Preserve the comment portion
                comment = ""
                if "#" in line:
                    parts = line.split("#", 1)
                    comment_text = parts[1]
                    # Update "Currently deployed" comment
                    comment_text = re.sub(
                        r"Currently deployed \S+",
                        f"Currently deployed {new_version}",
                        comment_text,
                    )
                    comment = f"# {comment_text.strip()}" if comment_text.strip() else ""

                # Determine quoting style from original
                old_val_part = line.split(":", 1)[1]
                if "#" in old_val_part:
                    old_val_part = old_val_part[:old_val_part.index("#")]
                old_val_part = old_val_part.strip()

                uses_quotes = old_val_part.startswith('"') or old_val_part.startswith("'")

                if uses_quotes:
                    new_val = f'"{new_version}"'
                else:
                    new_val = new_version

                prefix = f"{var_name}: {new_val}"
                # Pad to align comment (rough alignment)
                if comment:
                    lines[i] = f"{prefix}  {comment}"
                else:
                    lines[i] = prefix
                modified = True
                break

    if modified:
        VARS_FILE.write_text("\n".join(lines))

    return modified


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def check_service(svc_def: dict, current_versions: dict[str, str], use_cache: bool = True) -> ServiceVersion:
    """Check a single service for available updates."""
    name = svc_def["name"]
    var_name = svc_def["var_name"]
    category = svc_def["category"]
    current = current_versions.get(var_name, "unknown")
    notes = svc_def.get("notes", "")

    result = ServiceVersion(
        name=name,
        category=category,
        current_version=current,
        var_name=var_name,
        notes=notes,
        held=bool(svc_def.get("held", False)),
    )

    # Set source URLs
    if "github_repo" in svc_def:
        result.source_url = f"https://github.com/{svc_def['github_repo']}/releases"
        result.release_url = result.source_url
    elif "docker_image" in svc_def:
        result.source_url = f"https://hub.docker.com/r/{svc_def['docker_image']}/tags"
        result.release_url = result.source_url
    elif "ghcr_image" in svc_def:
        result.source_url = f"https://github.com/orgs/{svc_def['ghcr_image'].split('/')[0]}/packages"
    if svc_def.get("source_url"):
        result.source_url = svc_def["source_url"]

    # Manual/apt services - no automated check
    if category == "manual":
        result.latest_version = current
        result.notes = notes or "Manual version management"
        return result

    # Check cache first
    if use_cache:
        cached = _read_cache(name)
        if cached:
            result.latest_version = cached
            result.update_available = (
                current != "latest"
                and not svc_def.get("pin_version")
                and cached != current
                and version_greater(cached, current)
            )
            return result

    # Fetch latest version
    try:
        # Add current version to svc_def for major version pinning
        svc_def_with_current = svc_def.copy()
        svc_def_with_current["_current_version"] = current

        if category == "github":
            latest = fetch_github_release(svc_def_with_current)
        elif category == "dockerhub":
            latest = fetch_dockerhub_version(svc_def_with_current)
        elif category == "lsio":
            latest = fetch_lsio_version(svc_def)
        elif category == "ghcr":
            latest = fetch_ghcr_version(svc_def)
        elif category == "helm":
            latest = fetch_helm_version(svc_def)
        elif category == "plex":
            latest = fetch_plex_version(svc_def)
        elif category == "gitlab":
            latest = fetch_gitlab_version(svc_def)
        elif category == "apt_repo":
            latest = fetch_apt_repo_version(svc_def)
        else:
            result.error = f"Unknown category: {category}"
            return result

        result.latest_version = latest
        _write_cache(name, latest)

        # Determine if update is available
        if current == "latest":
            result.notes = (result.notes + " " if result.notes else "") + f"'latest' resolves to {latest}"
            result.update_available = False
        elif svc_def.get("pin_version"):
            # Version is intentionally pinned (e.g., newer releases lack binary assets)
            result.update_available = False
        elif latest != current:
            result.update_available = version_greater(latest, current)

    except RuntimeError as e:
        result.error = str(e)
    except Exception as e:
        # Include the exception type so unknown failures ('NoneType' object
        # has no attribute 'foo') are diagnosable without re-running under
        # a debugger. Set DEBUG=1 in the environment to also print the
        # full traceback.
        result.error = f"Unexpected {type(e).__name__}: {e}"
        if os.environ.get("DEBUG"):
            import traceback
            traceback.print_exc(file=sys.stderr)

    return result


def check_all(
    services: Optional[list[dict]] = None,
    category_filter: Optional[str] = None,
    use_cache: bool = True,
) -> list[ServiceVersion]:
    """Check all services for available updates."""
    current_versions = read_current_versions()

    if services is None:
        services = SERVICE_REGISTRY

    if category_filter:
        services = [s for s in services if s["category"] == category_filter]

    results = []
    for svc_def in services:
        result = check_service(svc_def, current_versions, use_cache=use_cache)
        results.append(result)
        # Small delay between API calls to be nice to rate limits
        time.sleep(0.2)

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

# ANSI colors
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def should_use_color() -> bool:
    """Determine if terminal supports color output."""
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def format_table(results: list[ServiceVersion]) -> str:
    """Format results as a human-readable table."""
    use_color = should_use_color()

    def c(code: str, text: str) -> str:
        if use_color:
            return f"{code}{text}{RESET}"
        return text

    lines = []
    lines.append("")
    lines.append(c(BOLD, "Homelab Version Check Report"))
    lines.append(c(DIM, f"Source: {VARS_FILE}"))
    lines.append(c(DIM, f"Checked: {time.strftime('%Y-%m-%d %H:%M:%S')}"))
    lines.append("")

    # Group by category
    categories = {
        "github": "GitHub Releases",
        "dockerhub": "Container Images (Docker Hub)",
        "ghcr": "Container Images (GHCR)",
        "lsio": "Container Images (LinuxServer.io)",
        "helm": "Helm Charts",
        "gitlab": "GitLab (packages.gitlab.com)",
        "plex": "Plex Media Server",
        "apt_repo": "APT Repositories (upstream)",
        "manual": "Manual / APT Managed",
    }

    updates_available = 0
    errors = 0

    for cat_key, cat_name in categories.items():
        cat_results = [r for r in results if r.category == cat_key]
        if not cat_results:
            continue

        lines.append(c(BOLD + CYAN, f"--- {cat_name} ---"))
        lines.append("")

        # Column widths
        name_w = max(len(r.name) for r in cat_results)
        cur_w = max(len(r.current_version) for r in cat_results)
        lat_w = max(len(r.latest_version or "error") for r in cat_results)

        # Header
        header = f"  {'Service':<{name_w}}  {'Current':<{cur_w}}  {'Latest':<{lat_w}}  Status"
        lines.append(c(DIM, header))
        lines.append(c(DIM, "  " + "-" * (name_w + cur_w + lat_w + 20)))

        for r in cat_results:
            latest_str = r.latest_version or "error"

            if r.error:
                status = c(RED, "ERROR")
                latest_str = "?"
                errors += 1
            elif r.update_available and r.held:
                status = c(DIM, "HELD")
            elif r.update_available:
                status = c(YELLOW, "UPDATE AVAILABLE")
                updates_available += 1
            elif r.current_version == "latest":
                status = c(DIM, "tracking latest")
            else:
                status = c(GREEN, "up to date")

            line = f"  {r.name:<{name_w}}  {r.current_version:<{cur_w}}  {latest_str:<{lat_w}}  {status}"
            lines.append(line)

            if r.notes:
                lines.append(c(DIM, f"  {'':>{name_w}}  {r.notes}"))
            if r.error:
                lines.append(c(RED, f"  {'':>{name_w}}  Error: {r.error}"))

        lines.append("")

    # Summary
    lines.append(c(BOLD, "--- Summary ---"))
    total = len(results)
    held = sum(1 for r in results if r.update_available and r.held)
    up_to_date = total - updates_available - held - errors
    lines.append(f"  Total services: {total}")
    lines.append(f"  Up to date:     {c(GREEN, str(up_to_date))}")
    if updates_available > 0:
        lines.append(f"  Updates:        {c(YELLOW, str(updates_available))}")
    else:
        lines.append(f"  Updates:        {updates_available}")
    if held > 0:
        lines.append(f"  Held:           {c(DIM, str(held))} (documented holds, not actionable)")
    if errors > 0:
        lines.append(f"  Errors:         {c(RED, str(errors))}")
    else:
        lines.append(f"  Errors:         {errors}")
    lines.append("")

    if updates_available > 0:
        lines.append(c(DIM, "To update a specific service:"))
        lines.append(c(DIM, "  task maintenance:update-version SERVICE=<name>"))
        lines.append(c(DIM, ""))
        lines.append(c(DIM, "To update all outdated services:"))
        lines.append(c(DIM, "  task maintenance:update-all-versions"))
        lines.append("")

    return "\n".join(lines)


def format_json(results: list[ServiceVersion]) -> str:
    """Format results as JSON.

    Summary semantics: `updates_available` counts ACTIONABLE updates only;
    registry-held updates (held=True) are excluded and counted separately
    in `updates_held`. version-check-ci.py keys its exit code and MR
    comment off this distinction.
    """
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_file": str(VARS_FILE),
        "services": [],
        "summary": {
            "total": len(results),
            "up_to_date": sum(1 for r in results if not r.update_available and not r.error),
            "updates_available": sum(1 for r in results if r.update_available and not r.held),
            "updates_held": sum(1 for r in results if r.update_available and r.held),
            "errors": sum(1 for r in results if r.error),
        },
    }

    for r in results:
        entry = {
            "name": r.name,
            "category": r.category,
            "var_name": r.var_name,
            "current_version": r.current_version,
            "latest_version": r.latest_version,
            "update_available": r.update_available,
            "source_url": r.source_url,
        }
        if r.error:
            entry["error"] = r.error
        if r.held:
            entry["held"] = True
        if r.notes:
            entry["notes"] = r.notes
        if r.release_url:
            entry["release_url"] = r.release_url
        data["services"].append(entry)

    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def get_deploy_command(result: ServiceVersion) -> str:
    """Get the deployment command for a service."""
    var_name = result.var_name
    category = result.category

    # Flux-managed workloads: all Helm charts and app container images reach
    # the cluster via the cluster-versions ConfigMap + git push + Flux.
    # Keep this list in sync with versions tracked by the Flux ConfigMap.
    flux_managed = (
        # Helm charts (from helm_chart_versions.*)
        "helm_chart_versions_metallb", "helm_chart_versions_traefik",
        "helm_chart_versions_cert_manager", "helm_chart_versions_external_dns",
        "helm_chart_versions_external_secrets",
        # App container images / helm chart pins
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
        "unbound_exporter_version", "redis_exporter_version",
    )
    if var_name in flux_managed or var_name.startswith("helm_chart") or category == "helm":
        return "task flux:sync-versions && git commit -am '...' && git push  # Flux reconciles on push"

    # K3s infrastructure (Ansible-managed, not Flux)
    if var_name == "k3s_version":
        return "task maintenance:update-k3s-nodes"
    if var_name == "kube_vip_version":
        return "task k3s:deploy  # Re-run k3s deployment to update kube-vip"

    # Flux CLI used by CI deploy-verify (pin + sha256 live together)
    if var_name == "flux_version":
        return "edit FLUX_VERSION + sha256 in .gitlab-ci.yml deploy-verify, update all.yml, task flux:sync-versions, commit"

    # Plex (LXC, Ansible-managed)
    if var_name == "plex_version":
        return "task maintenance:update-plex"

    # Tailscale (apt, Ansible-managed)
    if var_name == "tailscale_version":
        return "task maintenance:update-applications"

    # AdGuard Home (LXC, Ansible-managed)
    if var_name == "adguard_home_version":
        return "task maintenance:update-applications --limit dns"
    if var_name == "adguardhome_sync_version":
        return "task maintenance:update-applications --limit dns-01"

    # GitLab VM (Ansible-managed — not Flux)
    if var_name == "gitlab_version":
        return "task gitlab:deploy"

    # Fallback when no specific deploy task mapping exists
    return "task infra:deploy"


def print_usage():
    """Print usage information."""
    print("""Usage: check-versions.py [OPTIONS]

Options:
  --help                Show this help message
  --service NAME        Check a specific service only
  --category CAT        Check a category only (github, dockerhub, ghcr, lsio, helm, gitlab, plex, manual)
  --json                Output as JSON
  --no-cache            Skip cache, force fresh lookups
  --clear-cache         Clear the version cache
  --update NAME         Update a specific service to latest version in all.yml
  --update-all          Update all outdated versions in all.yml
  --list                List all tracked services

Environment:
  GITHUB_TOKEN          GitHub personal access token for higher API rate limits
  NO_COLOR              Disable colored output""")


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print_usage()
        sys.exit(0)

    if "--clear-cache" in args:
        if CACHE_DIR.exists():
            for f in CACHE_DIR.iterdir():
                f.unlink()
            print(f"Cache cleared: {CACHE_DIR}")
        else:
            print("No cache to clear")
        sys.exit(0)

    if "--list" in args:
        print("\nTracked services:\n")
        for svc in SERVICE_REGISTRY:
            cat = svc["category"]
            var = svc["var_name"]
            print(f"  {svc['name']:<25} [{cat:<10}] var: {var}")
        print()
        sys.exit(0)

    use_cache = "--no-cache" not in args
    output_json = "--json" in args
    service_filter = None
    category_filter = None

    # Parse arguments
    i = 0
    while i < len(args):
        if args[i] == "--service" and i + 1 < len(args):
            service_filter = args[i + 1].lower()
            i += 2
        elif args[i] == "--category" and i + 1 < len(args):
            category_filter = args[i + 1].lower()
            i += 2
        elif args[i] == "--update" and i + 1 < len(args):
            service_name = args[i + 1].lower()
            # Find the service
            matched = [
                s for s in SERVICE_REGISTRY
                if s["name"].lower() == service_name
                or s["var_name"].lower() == service_name
                or s["var_name"].replace("_version", "").lower() == service_name
            ]
            if not matched:
                print(f"Error: Unknown service '{service_name}'")
                print("Run with --list to see available services")
                sys.exit(1)

            svc_def = matched[0]
            current_versions = read_current_versions()
            result = check_service(svc_def, current_versions, use_cache=False)

            if result.error:
                print(f"Error checking {result.name}: {result.error}")
                sys.exit(1)

            if not result.update_available:
                print(f"{result.name} is already at the latest version ({result.current_version})")
                sys.exit(0)

            print(f"Updating {result.name}: {result.current_version} -> {result.latest_version}")
            if update_version_in_file(result.var_name, result.latest_version):
                print(f"Updated {result.var_name} in {VARS_FILE.name}")
                print("\nNext steps:")
                print("  1. Review the change: git diff ansible/inventories/prod/group_vars/all.yml")
                print(f"  2. Deploy the update: {get_deploy_command(result)}")
                print("  3. Verify deployment: task k3s:status  # Or appropriate status check")
                sys.exit(0)
            else:
                # The file didn't change — var_name may have been renamed or
                # the file format changed. Fail loudly so CI / Taskfile can
                # catch it instead of silently reporting success.
                print(f"ERROR: Could not find {result.var_name} in {VARS_FILE.name}", file=sys.stderr)
                sys.exit(1)

        elif args[i] == "--update-all":
            current_versions = read_current_versions()
            results = check_all(use_cache=False)
            updated = []
            write_failed = []
            errored = [r for r in results if r.error]
            for r in results:
                if r.update_available and not r.error:
                    print(f"Updating {r.name}: {r.current_version} -> {r.latest_version}")
                    if update_version_in_file(r.var_name, r.latest_version):
                        updated.append(r)
                    else:
                        print(f"  ERROR: Could not find {r.var_name} in {VARS_FILE.name}")
                        write_failed.append(r)

            # Surface errors FIRST — an operator looking at a long successful
            # update list could easily miss that 5 other services failed their
            # version check. Previous behavior silently swallowed errors.
            if write_failed:
                print(f"\nERROR: {len(write_failed)} service(s) could not be updated in {VARS_FILE.name}:")
                for r in write_failed:
                    print(f"  - {r.var_name}")

            if errored:
                print(f"\nWARNING: {len(errored)} service(s) had errors and were NOT checked:")
                for r in errored:
                    print(f"  - {r.name}: {r.error}")

            if updated:
                print(f"\nUpdated {len(updated)} services in {VARS_FILE.name}")

                # Group updates by deployment command
                deploy_commands = {}
                for r in updated:
                    cmd = get_deploy_command(r)
                    if cmd not in deploy_commands:
                        deploy_commands[cmd] = []
                    deploy_commands[cmd].append(r.name)

                print("\nNext steps:")
                print("  1. Review changes:")
                repo_root = Path(__file__).resolve().parent.parent
                print(f"     git diff {VARS_FILE.relative_to(repo_root)}")
                print("\n  2. Deploy updates (in this order):")

                # Show deployment commands with the services they update
                for cmd, services in deploy_commands.items():
                    print(f"     {cmd}")
                    for svc in services:
                        print(f"       # Updates: {svc}")

                print("\n  3. Verify deployments:")
                print("     task k3s:status")
                print("     task infra:verify")

                print("\n  4. Commit changes:")
                print("     git add -A && git commit -m 'Update service versions'")
            else:
                if not errored:
                    print("\nAll services are up to date!")
            # Exit code convention:
            #   2 — at least one service errored or couldn't be written
            #   0 — all checks succeeded (whether or not we updated anything)
            sys.exit(2 if (errored or write_failed) else 0)
        else:
            i += 1

    # Filter services
    services = SERVICE_REGISTRY
    if service_filter:
        services = [
            s for s in services
            if service_filter in s["name"].lower()
            or service_filter in s["var_name"].lower()
        ]
        if not services:
            print(f"Error: No services matching '{service_filter}'")
            print("Run with --list to see available services")
            sys.exit(1)

    # Run checks
    results = check_all(services=services, category_filter=category_filter, use_cache=use_cache)

    # Output
    if output_json:
        print(format_json(results))
    else:
        print(format_table(results))

    # Exit code: 0 = all up to date, 1 = updates available, 2 = errors
    has_errors = any(r.error for r in results)
    has_updates = any(r.update_available and not r.held for r in results)
    if has_errors:
        sys.exit(2)
    elif has_updates:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
