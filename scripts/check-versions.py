#!/usr/bin/env python3
"""
check-versions.py - Automated version discovery for weisssrv homelab infrastructure.

Checks the latest available versions from official sources and compares them
against the pinned versions in ansible/inventories/prod/group_vars/all.yml.

Supports:
  - GitHub releases (binary tools, container images with GitHub releases)
  - Docker Hub / ghcr.io / LinuxServer.io container image tags
  - Helm chart versions from OCI/HTTP repositories
  - APT package versions from live repo indexes (Tailscale, Plex, GitLab EE)

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
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

# Configuration

VARS_FILE = Path(__file__).resolve().parent.parent / "ansible" / "inventories" / "prod" / "group_vars" / "all.yml"
# CI-pinned container images (e.g. the pr-agent reviewer) live in .gitlab-ci.yml
# as digest-locked `image:` pins, not as vars in all.yml.
CI_FILE = Path(__file__).resolve().parent.parent / ".gitlab-ci.yml"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".version-cache"
CACHE_TTL = 3600  # 1 hour cache

# GitHub API rate limit handling
GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GH_API_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")

# Request timeout in seconds
REQUEST_TIMEOUT = 15

# Bounded retry for transient network failures. The checker does dozens of
# sequential external fetches (GitHub, Docker Hub, LSIO, Helm, apt); without a
# retry a single flaky endpoint (DNS blip, connection reset, upstream 5xx) makes
# the whole CI version check fail intermittently. We retry only on transient
# failures (URLError, socket.timeout, HTTP 5xx) — never on 4xx (including a 403
# rate-limit, which is surfaced as-is so it isn't masked as a transient blip).
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 0.5  # seconds; multiplied by attempt number for linear backoff


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
    # True only when this check performed a live network fetch (not a cache
    # hit or a manual/no-check service). Lets check_all skip the rate-limit
    # sleep on cache hits.
    fetched_live: bool = False


# Service definitions - maps var_name to lookup configuration
SERVICE_REGISTRY: list[dict] = [
    # GitHub releases (binary tools)
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
    {
        "name": "wg-easy",
        "var_name": "wg_easy_version",
        "category": "github",
        "github_repo": "wg-easy/wg-easy",
        "version_prefix": "v",
        "strip_prefix": True,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        # Docker tag is v-prefixed (ghcr.io/homarr-labs/homarr:v1.71.0), so the
        # pin keeps the "v" (strip_prefix False, like immich).
        "name": "Homarr",
        "var_name": "homarr_version",
        "category": "github",
        "github_repo": "homarr-labs/homarr",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        # Immich app (immich-server + immich-machine-learning images share this
        # tag). The coupled DB/Valkey pins (immich_postgres_version,
        # immich_valkey_version) are NOT tracked here — they must be taken from
        # the SAME release's docker-compose.yml (vectorchord/pgvectors coupling),
        # so they are allow-listed in the check-versions test, not auto-bumped.
        "name": "Immich",
        "var_name": "immich_version",
        "category": "github",
        "github_repo": "immich-app/immich",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        # CalVer release tags (vYYYY.M.D[.N]); the pin keeps the leading "v"
        # because the CI image build checks out the upstream tag verbatim.
        "name": "Hermes Agent",
        "var_name": "hermes_version",
        "category": "github",
        "github_repo": "NousResearch/hermes-agent",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d{4}\.\d+\.\d+(\.\d+)?$",
    },
    {
        # OpenAI Codex CLI, baked into the hermes-agent image (npm @openai/codex)
        # so Hermes' Codex app-server runtime can delegate OpenAI/Codex turns to
        # it. Upstream tags stable releases `rust-vX.Y.Z`; the pin is the bare npm
        # version (0.144.5), so strip the "rust-v" prefix. The tag_filter excludes
        # the per-platform alpha tags (rust-vX.Y.Z-alpha.N). Requires >=0.130.0.
        "name": "Codex CLI (Hermes)",
        "var_name": "hermes_codex_version",
        "category": "github",
        "github_repo": "openai/codex",
        "version_prefix": "rust-v",
        "strip_prefix": True,
        "tag_filter": r"^rust-v\d+\.\d+\.\d+$",
    },
    {
        # Claude Code CLI, baked into the hermes-agent image alongside Codex
        # (npm @anthropic-ai/claude-code) so Hermes can delegate coding tasks to
        # headless `claude -p` runs on the Claude Max subscription. Upstream tags
        # stable releases `vX.Y.Z`; the pin is the bare npm version, so strip
        # the "v" prefix.
        "name": "Claude Code CLI (Hermes)",
        "var_name": "hermes_claude_version",
        "category": "github",
        "github_repo": "anthropics/claude-code",
        "version_prefix": "v",
        "strip_prefix": True,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        # 1Password CLI (op), baked into the hermes-agent image so Hermes' 1Password
        # skill can drive `op` against the isolated Agent vault. 1Password ships it
        # via its own signed apt repo (no GitHub release feed), and the pin is the
        # full DEB version string — hence manual: check `apt-cache madison
        # 1password-cli` against the repo (or the CLI2 release history) and bump.
        "name": "1Password CLI (Hermes)",
        "var_name": "hermes_op_version",
        "category": "manual",
        "source_url": "https://app-updates.agilebits.com/product_history/CLI2",
        "notes": "op CLI baked into the hermes image (docker/hermes-agent). Full DEB version pin — bump via `apt-cache madison 1password-cli` against 1Password's signed apt repo, sync-versions, commit; CI rebuilds the wrapper.",
    },
    {
        # Camofox anti-detection browser server, built from source by the
        # build-camofox-browser CI job (upstream publishes no image). The pin is
        # the bare semver used as the built image tag; upstream tags releases
        # `vX.Y.Z`, so strip the prefix. hermes_camofox_git_sha (the tag's
        # commit) moves in lockstep — same supply-chain pattern as
        # hermes_version/hermes_git_sha.
        "name": "Camofox browser (Hermes)",
        "var_name": "hermes_camofox_version",
        "category": "github",
        "github_repo": "jo-inc/camofox-browser",
        "version_prefix": "v",
        "strip_prefix": True,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        # Hindsight agent-memory server (Hermes' memory backend). The pin is the
        # ghcr.io/vectorize-io/hindsight image tag — bare semver, matching the
        # GitHub release tag with the "v" stripped (verified: release vX.Y.Z
        # publishes image tag X.Y.Z).
        "name": "Hindsight (Hermes memory)",
        "var_name": "hindsight_version",
        "category": "github",
        "github_repo": "vectorize-io/hindsight",
        "version_prefix": "v",
        "strip_prefix": True,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        # llama.cpp server sidecar for Hindsight's local LLM
        # (ghcr.io/ggml-org/llama.cpp:server-<pin>). Upstream tags a build
        # (bNNNN) many times per day and only some builds publish a server
        # image, so auto-nagging on "latest release" would be pure churn —
        # bumped opportunistically when touching the hindsight app (verify the
        # server-bNNNN tag exists on ghcr before pinning), hence manual.
        "name": "llama.cpp server (Hindsight)",
        "var_name": "hindsight_llamacpp_version",
        "category": "manual",
        "source_url": "https://github.com/ggml-org/llama.cpp/releases",
        "notes": "Pin bNNNN whose ghcr server-cuda-bNNNN (CUDA) image tag exists; any recent build serves the pinned GGUF.",
    },
    {
        # nvidia-open from NVIDIA's CUDA apt repo (debian13), exact apt version
        # installed on the GPU k3s agent (k3s role tasks/gpu.yml). Manual: the apt
        # version string (X.Y.Z-N) tracks the CUDA repo package — bump when it
        # moves. See docs/43 for why >=570 (CUDA 12.8) is required on GeForce.
        "name": "NVIDIA driver (nvidia-open, CUDA repo)",
        "var_name": "nvidia_driver_version",
        "category": "manual",
        "source_url": "https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/",
        "notes": "Exact nvidia-open apt version (X.Y.Z-N) from the NVIDIA CUDA debian13 repo; >=570 required for the CUDA-12.8 llama image on GeForce (docs/43).",
    },
    {
        # nvidia-container-toolkit apt pin (NVIDIA libnvidia-container repo).
        # Manual: the apt version carries a -N Debian revision the GitHub release
        # tag lacks, so auto-diffing would churn.
        "name": "NVIDIA container toolkit",
        "var_name": "nvidia_container_toolkit_version",
        "category": "manual",
        "source_url": "https://github.com/NVIDIA/nvidia-container-toolkit/releases",
        "notes": "Apt version X.Y.Z-N from nvidia.github.io/libnvidia-container/stable/deb.",
    },
    {
        # cuda-keyring apt package — ships the debian13 CUDA repo signing key +
        # the matching sources file (k3s role tasks/gpu.yml, SHA256-verified
        # before install). Manual: NVIDIA versions the .deb; bump it and the
        # nvidia_cuda_keyring_sha256 pin in all.yml in lockstep.
        "name": "NVIDIA cuda-keyring",
        "var_name": "nvidia_cuda_keyring_version",
        "category": "manual",
        "source_url": "https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/",
        "notes": "cuda-keyring_<version>_all.deb; SHA256-pinned in all.yml (nvidia_cuda_keyring_sha256).",
    },
    {
        # DCGM exporter image tag (nvcr.io/nvidia/k8s/dcgm-exporter). Manual: the
        # tag is a compound <DCGM>-<exporter>-<variant> string, not a plain
        # semver an auto-tracker can compare.
        "name": "NVIDIA DCGM exporter",
        "var_name": "dcgm_exporter_version",
        "category": "manual",
        "source_url": "https://github.com/NVIDIA/dcgm-exporter/releases",
        "notes": "nvcr.io tag <DCGM>-<exporter>-<variant>, e.g. 4.6.0-4.8.3-distroless.",
    },
    # Container images
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
        # Authentik is deployed via the goauthentik Helm chart, so the version
        # pinned in `authentik_version` is read as a chart tag (e.g.
        # `version: "{{ authentik_version }}"` in the HelmRelease). The chart
        # publishes a few days after the matching GitHub release tag, so query
        # the chart repo directly — pinning a GitHub tag Flux can't yet resolve
        # fails reconciliation with "no 'authentik' chart with version ... found".
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
    # LinuxServer.io container images
    # LinuxServer.io tags follow these patterns:
    #   version-vX.Y.Z (nzbget), version-X.Y.Z-rN (qbittorrent),
    #   version-X.Y.Z.BUILD (*arr apps - stable branch)
    # lsio_version_regex is authoritative: it both selects the tag and captures
    # the bare version (group 1) that gets pinned in all.yml.
    # Stable tags get buried under daily develop/nightly pushes (3 arch variants
    # each), so the *arr/NZBGet entries below set lsio_name_filter="version-"
    # (server-side filter) + lsio_max_pages to page deeper than the default.
    {
        "name": "NZBGet",
        "var_name": "nzbget_version",
        "category": "lsio",
        "docker_image": "linuxserver/nzbget",
        "lsio_version_regex": r"^version-v(\d+\.\d+(?:\.\d+)?)$",
        "lsio_name_filter": "version-",
        "lsio_max_pages": 4,
    },
    {
        "name": "qBittorrent",
        "var_name": "qbittorrent_version",
        "category": "lsio",
        "docker_image": "linuxserver/qbittorrent",
        # qBittorrent uses bare tags without the version- prefix.
        "lsio_version_regex": r"^(\d+\.\d+\.\d+)$",  # Match bare version tags like "5.1.4"
    },
    {
        "name": "Prowlarr",
        "var_name": "prowlarr_version",
        "category": "lsio",
        "docker_image": "linuxserver/prowlarr",
        "lsio_version_regex": r"^version-(\d+\.\d+\.\d+\.\d+)$",
        "lsio_name_filter": "version-",
        "lsio_max_pages": 4,
        "notes": "LinuxServer stable branch",
    },
    {
        "name": "Sonarr",
        "var_name": "sonarr_version",
        "category": "lsio",
        "docker_image": "linuxserver/sonarr",
        "lsio_version_regex": r"^version-(\d+\.\d+\.\d+\.\d+)$",
        "lsio_name_filter": "version-",
        "lsio_max_pages": 4,
        "notes": "LinuxServer stable branch",
    },
    {
        "name": "Radarr",
        "var_name": "radarr_version",
        "category": "lsio",
        "docker_image": "linuxserver/radarr",
        "lsio_version_regex": r"^version-(\d+\.\d+\.\d+\.\d+)$",
        "lsio_name_filter": "version-",
        "lsio_max_pages": 4,
        "notes": "LinuxServer stable branch",
    },
    {
        "name": "Lidarr",
        "var_name": "lidarr_version",
        "category": "lsio",
        "docker_image": "linuxserver/lidarr",
        "lsio_version_regex": r"^version-(\d+\.\d+\.\d+\.\d+)$",
        "lsio_name_filter": "version-",
        "lsio_max_pages": 4,
        "notes": "LinuxServer stable branch",
    },
    # Helm charts
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
        "name": "NVIDIA device plugin",
        "var_name": "helm_chart_versions.nvidia_device_plugin",
        "category": "helm",
        "helm_repo": "https://nvidia.github.io/k8s-device-plugin",
        "helm_chart": "nvidia-device-plugin",
        "source_url": "https://github.com/NVIDIA/k8s-device-plugin/releases",
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
    {
        "name": "Grafana Alloy (host)",
        "var_name": "alloy_host_version",
        # Host-side Alloy apt package (alloy_host role: `apt install alloy=...`
        # from the Grafana repo, then dpkg-hold). Distinct from the in-cluster
        # helm_chart_versions.alloy chart entry above — that one only tracks the
        # Helm chart, leaving this fleet-wide agent otherwise unchecked. The repo
        # is arch-agnostic `stable main` (alloy_host role repo line), so the
        # binary-amd64 index is the right one for our amd64 fleet.
        "category": "apt_repo",
        "apt_index_url": "https://apt.grafana.com/dists/stable/main/binary-amd64/Packages.gz",
        "apt_package": "alloy",
        "source_url": "https://apt.grafana.com/dists/stable/main/binary-amd64/Packages.gz",
    },
    # GitLab
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
    {
        # In-cluster pull-through registry cache for CI (kubernetes/apps/
        # registry-cache, docs/27). The CNCF distribution image, Docker Hub
        # `library/registry`. Bare X.Y.Z tags only — the regex excludes the
        # floating majors/minors (3, 3.1) and pre-releases (3.0.0-rc.4) that
        # share the repo. Flux-managed image (registry_cache_version pin ->
        # cluster-versions ConfigMap), so it routes through flux:sync-versions.
        "name": "Registry Cache (distribution)",
        "var_name": "registry_cache_version",
        "category": "dockerhub",
        "docker_image": "library/registry",
        "tag_regex": r"^(\d+\.\d+\.\d+)$",
        "source_url": "https://hub.docker.com/_/registry/tags",
    },
    # Observability
    {
        "name": "kube-prometheus-stack",
        "var_name": "helm_chart_versions.kube_prometheus_stack",
        "category": "helm",
        "helm_repo": "https://prometheus-community.github.io/helm-charts",
        "helm_chart": "kube-prometheus-stack",
        "source_url": "https://artifacthub.io/packages/helm/prometheus-community/kube-prometheus-stack",
    },
    {
        "name": "prometheus-operator-crds",
        "var_name": "helm_chart_versions.prometheus_operator_crds",
        "category": "helm",
        "helm_repo": "https://prometheus-community.github.io/helm-charts",
        "helm_chart": "prometheus-operator-crds",
        "source_url": "https://artifacthub.io/packages/helm/prometheus-community/prometheus-operator-crds",
        "notes": (
            "CRD stage ahead of the controllers (kubernetes/infrastructure/"
            "crds/). Bump so its appVersion stays in lockstep with the "
            "prometheus-operator appVersion of the pinned kube-prometheus-stack "
            "(kps carries the same CRDs but is configured NOT to manage them — "
            "the Flux HelmRelease sets crds: Skip on install+upgrade AND "
            "crds.enabled: false in values, so this stage is their sole owner) "
            "— check both charts' appVersion before bumping either."
        ),
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
        "name": "kured",
        "var_name": "helm_chart_versions.kured",
        "category": "helm",
        "helm_repo": "https://kubereboot.github.io/charts",
        "helm_chart": "kured",
        "source_url": "https://artifacthub.io/packages/helm/kured/kured",
    },
    {
        "name": "Reloader",
        "var_name": "helm_chart_versions.reloader",
        "category": "helm",
        "helm_repo": "https://stakater.github.io/stakater-charts",
        "helm_chart": "reloader",
        "source_url": "https://artifacthub.io/packages/helm/stakater/reloader",
    },
    {
        "name": "Tailscale Operator",
        "var_name": "helm_chart_versions.tailscale_operator",
        "category": "helm",
        "helm_repo": "https://pkgs.tailscale.com/helmcharts",
        "helm_chart": "tailscale-operator",
        "source_url": "https://github.com/tailscale/tailscale/releases",
        "notes": (
            "Tracks the host tailscale_version; exposes the internal Traefik "
            "ingress + the tailnet-dns resolver to the tailnet (docs/05)."
        ),
    },
    {
        "name": "CoreDNS (tailnet-dns resolver)",
        "var_name": "coredns_tailnet_version",
        "category": "dockerhub",
        "docker_image": "rancher/mirrored-coredns-coredns",
        "tag_regex": r"^(\d+\.\d+\.\d+)$",
        "notes": "CoreDNS image for the tailnet-dns split-horizon resolver (rancher mirror k3s caches).",
    },
    {
        "name": "Flux CLI (CI verify)",
        "var_name": "flux_version",
        "category": "github",
        "github_repo": "fluxcd/flux2",
        "version_prefix": "v",
        "strip_prefix": True,
        "held": True,
        "notes": (
            "Held at 2.9.0: this pin is not a plain version bump. It gates the "
            "CI deploy-verify (flux CLI download + sha256) AND must stay in lock-"
            "step with kubernetes/clusters/weisssrv/flux-system/gotk-components.yaml, "
            "which is regenerated by `flux install --export` from a matching CLI "
            "and only truly validated by a bootstrap. Bumping the GitOps control "
            "plane blind is the highest-blast-radius change in the repo. "
            "Re-evaluate as a dedicated, bootstrap-tested Flux upgrade."
        ),
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
        "notes": "On bump also update zfs_exporter_checksum in all.yml (sha256 of the release .tar.gz).",
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
        "notes": "On bump also update unbound_exporter_checksum in all.yml (upstream ships no checksum file).",
    },
    {
        "name": "Redis Exporter",
        "var_name": "redis_exporter_version",
        "category": "dockerhub",
        "docker_image": "oliver006/redis_exporter",
        "tag_regex": r"^(v\d+\.\d+\.\d+)$",
    },
    # Plex (apt repo, auto-checked via fetch_plex_version)
    {
        "name": "Plex Media Server",
        "var_name": "plex_version",
        "category": "plex",
        "source_url": "https://www.plex.tv/media-server-downloads/",
    },
    # Nextcloud (Docker Compose stack on the NAS-pinned VM)
    {
        "name": "Nextcloud",
        "var_name": "nextcloud_version",
        "category": "dockerhub",
        "docker_image": "library/nextcloud",
        # Bare X.Y.Z tags only (the compose file appends -apache). Only suggest
        # patches within the current major — Nextcloud must be upgraded one major
        # at a time, so a jump to N+1 is a deliberate, documented step.
        "tag_regex": r"^(\d+\.\d+\.\d+)$",
        "pin_major_version": True,
        "source_url": "https://github.com/nextcloud/docker/blob/master/versions.json",
    },
    {
        "name": "PostgreSQL (Nextcloud)",
        "var_name": "nextcloud_postgres_version",
        "category": "dockerhub",
        "docker_image": "library/postgres",
        "tag_regex": r"^(\d+(?:\.\d+)?)-trixie$",
        "pin_major_version": True,
        "notes": "Used by Nextcloud (standalone container). Only checks updates within the current major.",
    },
    # Nextcloud's Redis reuses the shared `redis_version` pin (the "Redis" entry
    # above), so it needs no separate registry entry here.
    {
        "name": "Nextcloud Exporter",
        "var_name": "nextcloud_exporter_version",
        "category": "ghcr",
        "ghcr_image": "xperimental/nextcloud-exporter",
        "image_ref": "ghcr.io/xperimental/nextcloud-exporter",
        "tag_filter": r"^\d+\.\d+\.\d+$",
        "source_url": "https://github.com/xperimental/nextcloud-exporter/releases",
    },
    # CI tooling images (pinned in .gitlab-ci.yml, not all.yml)
    {
        # The pr-agent AI reviewer image, pinned by tag+digest in the
        # pr-agent-review job. Tracked here so `check-versions` flags a stale
        # reviewer (the kind of version/model drift that prompted adding this).
        # Its update is a guided manual step — see update_version_in_file: the
        # @sha256 supply-chain pin is not auto-rewritten.
        "name": "pr-agent (CI reviewer)",
        "var_name": "pr_agent_version",
        "category": "dockerhub",
        "docker_image": "codiumai/pr-agent",
        "tag_regex": r"^(\d+\.\d+(?:\.\d+)?)$",
        "version_file": "ci",
        "source_url": "https://github.com/qodo-ai/pr-agent/releases",
    },
    # Manifest-pinned container images (kubernetes/, not all.yml)
    # Tag+digest `image:` pins that live directly in kubernetes/ manifests with
    # no ${...} substitution from all.yml. version_file names the manifest(s)
    # the current tag is read from; like the CI pins above, updates are manual
    # tag+digest edits there (update_version_in_file refuses to auto-rewrite).
    {
        "name": "gluetun-exporter",
        "var_name": "gluetun_exporter_version",
        "category": "ghcr",
        "ghcr_image": "thecfu/gluetun-exporter",
        "image_ref": "ghcr.io/thecfu/gluetun-exporter",
        # Standalone-flavor tags only: the manifest pins X.Y.Z-standalone, and a
        # bare X.Y.Z would falsely compare as newer than its -standalone twin.
        "tag_filter": r"^\d+\.\d+\.\d+-standalone$",
        "version_file": "kubernetes/apps/download-clients/qbittorrent/resources.yaml",
        "source_url": "https://github.com/TheCfu/gluetun-exporter",
    },
    {
        "name": "python (CronJob base image)",
        "var_name": "python_cronjob_version",
        "category": "dockerhub",
        "docker_image": "library/python",
        "image_ref": "python",
        "tag_regex": r"^(3\.\d+)-slim$",
        # Substring API filter: plain last_updated paging floods with
        # non-slim variants and misses the current X.Y-slim tags entirely.
        "dockerhub_name_filter": "-slim",
        "version_file": [
            "kubernetes/infrastructure/configs/cloudflare-ddns/cronjob.yaml",
            "kubernetes/apps/gitlab-runner-reaper/cronjob.yaml",
        ],
        "source_url": "https://hub.docker.com/_/python",
        "notes": "Both CronJobs share one tag+digest pin; bump them together.",
    },
    {
        # Upstream publishes no versioned tags (see the manifest header): the
        # pin is an immutable @sha256 of `latest`, re-resolved manually when
        # updating — nothing to compare against, hence category manual.
        "name": "prometheus-plex-exporter",
        "var_name": "plex_exporter_version",
        "category": "manual",
        "image_ref": "ghcr.io/jsclayton/prometheus-plex-exporter",
        "version_file": "kubernetes/infrastructure/observability/exporters/plex-exporter.yaml",
        "source_url": "https://github.com/jsclayton/prometheus-plex-exporter",
        "notes": "Digest-pinned `latest` (no upstream version tags); re-resolve the digest manually to update.",
    },
    {
        # virtio-win publishes no GitHub releases/tags (the virtio-win-pkg-scripts
        # repo has neither) — versions are cut only to the Fedora ISO archive, so
        # this is a manual check. On bump: pick the newest virtio-win-X.Y.NNN at
        # the source_url, update virtio_win_version in all.yml, and recompute the
        # ISO sha256 (virtio_win_checksum) since Fedora ships no ISO checksum.
        # Current tag is read from all.yml (no version_file). See docs/39.
        "name": "virtio-win",
        "var_name": "virtio_win_version",
        "category": "manual",
        "source_url": "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/",
        "notes": "VirtIO driver ISO for the Windows 11 VM; no GitHub releases — check the Fedora stable-virtio dir and recompute virtio_win_checksum on bump.",
    },
    {
        # Debian LXC root template (pveam appliance). Proxmox silently rotates
        # the point build out of its index (13.1-2 vanished 2026-07 and broke a
        # cached-template recreate — proxmox_lxc role + !160). No release feed to
        # poll; check the pveam index on a Proxmox host. Current value read from
        # all.yml (authoritative pin; mirrored as the proxmox_lxc role default).
        "name": "Debian LXC template (pveam)",
        "var_name": "lxc_template",
        "category": "manual",
        "source_url": "http://download.proxmox.com/images/system/",
        "notes": "Debian LXC root template; no release feed — run `pveam update && pveam available --section system | grep debian-13-standard` on a Proxmox host, then bump lxc_template in all.yml + the proxmox_lxc role default together.",
    },
]


# HTTP helpers

def _urlopen_with_retry_full(req, timeout: int = REQUEST_TIMEOUT) -> tuple[str, bytes]:
    """urlopen with a bounded retry on transient failures; return (content_type, body).

    Retries on URLError, socket.timeout, and HTTP 5xx (transient upstream
    errors). HTTP 4xx — including a 403 rate-limit — is re-raised immediately
    so callers can surface it as-is rather than masking it as a transient blip.
    After RETRY_ATTEMPTS, the last exception is re-raised unchanged, so callers
    behave identically to the no-retry version once retries are exhausted.

    The Content-Type header is returned alongside the body so callers that
    must distinguish a real payload from an HTML error page (fetch_apt_packages)
    keep that sniffing logic while still going through the retry helper.
    """
    last_exc: Exception
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content_type = resp.headers.get("Content-Type", "")
                return content_type, resp.read()
        except urllib.error.HTTPError as e:
            # 4xx (incl. 403 rate-limit) is not transient — don't retry.
            if e.code < 500:
                raise
            last_exc = e
        except (urllib.error.URLError, socket.timeout) as e:
            last_exc = e
        except http.client.IncompleteRead as e:
            # Mid-body truncation (GitHub intermittently cuts large release
            # payloads — observed as IncompleteRead on the ~25MB Codex asset
            # list). Transient: the next attempt re-reads the full body.
            last_exc = e
        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF * attempt)
    raise last_exc


def _urlopen_with_retry(req, timeout: int = REQUEST_TIMEOUT) -> bytes:
    """urlopen with a bounded retry on transient failures; return the body.

    Thin wrapper over _urlopen_with_retry_full for callers that only need the
    response body. See that function for retry semantics.
    """
    _content_type, body = _urlopen_with_retry_full(req, timeout=timeout)
    return body


def _make_request(url: str, headers: Optional[dict] = None) -> dict | list | str:
    """Make an HTTP GET request and return parsed JSON or raw text."""
    req_headers = {"User-Agent": "weisssrv-version-checker/1.0"}
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, headers=req_headers)
    try:
        data = _urlopen_with_retry(req, timeout=REQUEST_TIMEOUT).decode("utf-8")
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
        # check_service catches RuntimeError before its typed fallback, so the
        # type tag must be in the message here or it's lost from the diagnostic.
        raise RuntimeError(f"Request failed ({type(e).__name__}): {e}") from e


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

    def _is_valid_packages_response(content_type: str, content: str) -> bool:
        """Check if response is a valid Packages file (not an HTML error page)."""
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

    # Try uncompressed first. Route through the bounded retry helper so a
    # transient 5xx/timeout retries (same protection Tailscale's fetcher gets);
    # the helper returns the Content-Type so the HTML-vs-payload sniff below is
    # preserved.
    try:
        req = urllib.request.Request(base_url, headers=req_headers)
        content_type, raw = _urlopen_with_retry_full(req, timeout=REQUEST_TIMEOUT)
        content = raw.decode("utf-8")
        if content.strip() and _is_valid_packages_response(content_type, content):
            return content
    except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout, UnicodeDecodeError):
        pass

    # Fall back to .gz compressed version
    gz_url = f"{base_url}.gz"
    req = urllib.request.Request(gz_url, headers=req_headers)

    try:
        content_type, compressed_data = _urlopen_with_retry_full(req, timeout=REQUEST_TIMEOUT)
        # Check Content-Type before attempting decompression
        if "text/html" in content_type.lower():
            raise RuntimeError(f"Received HTML error page instead of Packages.gz from {gz_url}")

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
    except socket.timeout as e:
        raise RuntimeError(f"Timeout fetching apt Packages from {gz_url}") from e
    except gzip.BadGzipFile as e:
        raise RuntimeError(f"Invalid gzip data from {gz_url}") from e


# Cache helpers

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


# Version parsing

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
    # Drop a Debian epoch prefix (e.g. "1:1.80.0" -> "1.80.0") so the epoch
    # integer is not parsed as a leading version segment.
    epoch_match = re.match(r"^\d+:(.*)$", v)
    if epoch_match:
        v = epoch_match.group(1)
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


# Version fetchers

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
    installed (e.g. when run from a macOS dev machine). The ordering rules
    below are asserted in test_check_versions.py (TestDebianVersionCompare):
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


def _collect_apt_versions(text: str, package: str) -> list[str]:
    """All `Version:` values for `package` in a Debian Packages index.

    Packages files are blank-line-separated stanzas, each with a `Package:` and
    a `Version:` line. Returns the version of every stanza whose Package matches;
    callers pick their own comparator (debian_version_compare vs the
    parse_version_tuple family) and any pre-release filtering on the result.
    """
    versions: list[str] = []
    in_pkg = False
    for line in text.split("\n"):
        if line.startswith("Package:"):
            in_pkg = line.split(":", 1)[1].strip() == package
        elif in_pkg and line.startswith("Version:"):
            versions.append(line.split(":", 1)[1].strip())
            in_pkg = False
    return versions


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
    # Bounded retry on transient failures (see _urlopen_with_retry).
    raw = _urlopen_with_retry(req, timeout=30)
    # gzip magic bytes are 0x1f 0x8b. Sniff the payload rather than the
    # URL extension so an apt-mirror redirect that drops `.gz` from the
    # path (or one that serves un-gzipped content over a `.gz` URL)
    # parses correctly.
    text = (
        gzip.decompress(raw).decode("utf-8", errors="replace")
        if raw[:2] == b"\x1f\x8b"
        else raw.decode("utf-8", errors="replace")
    )

    # Collect every Version line for our target package, then return the
    # highest using debian-policy version ordering (epochs, revisions, and `~`
    # pre-release semantics — a plain string-tuple compare would silently get
    # these wrong).
    versions = _collect_apt_versions(text, pkg)
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
        # Fail loud on a missing tag_name, matching the tag_filter branch above.
        # Returning "" here would have the service silently report up-to-date
        # with a blank Latest column (version_greater("", current) is False).
        if not version:
            raise RuntimeError(f"latest release for {repo} has no tag_name")
        if strip_prefix and prefix and version.startswith(prefix):
            version = version[len(prefix):]
        return version


def _dockerhub_best_tag(
    image: str,
    regex: str,
    *,
    version_prefix: str = "",
    pin_major: bool = False,
    current: str = "",
    return_full_tag: bool = False,
    name_filter: str = "",
    max_pages: int = 1,
) -> Optional[str]:
    """Highest Docker Hub tag of `image` matching `regex` (group 1 = version).

    Shared by fetch_dockerhub_version and fetch_lsio_version. With
    return_full_tag the original tag name is returned (what all.yml pins store);
    otherwise the captured version group is returned. version_prefix narrows both
    the API query (Docker Hub `name=` filter) and the accepted tags (startswith)
    to a release series; pin_major + current confine results to current's major.
    name_filter narrows ONLY the API query (substring match, no startswith
    constraint) — needed for suffix-style tag families like python's `-slim`,
    which otherwise scroll off the last_updated page behind other variants.
    Returns None if nothing matches; raises on a non-JSON response.
    """
    # For postgres, use larger page size to find alpine/trixie tags.
    # For version_prefix-pinned services, use Docker Hub's name= filter so
    # old tags that have scrolled off the first page are still found.
    page_size = 100 if image == "library/postgres" else 50
    url = f"https://hub.docker.com/v2/repositories/{image}/tags?page_size={page_size}&ordering=last_updated"
    if name_filter:
        url += f"&name={name_filter}"
    elif version_prefix:
        url += f"&name={version_prefix}"
    # Bounded pagination: high-churn repos (the *arr apps push develop/nightly
    # tags daily, 3 arch variants each) can bury a monthly stable tag beyond the
    # first page even with a name filter — observed 2026-07-19 when Prowlarr's
    # stable scrolled off and the check errored. Callers with that exposure pass
    # max_pages > 1; the default keeps every other caller at one request.
    results = []
    pages = 0
    while url and pages < max_pages:
        data = _make_request(url)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected non-JSON response from {url}")
        results.extend(data.get("results", []))
        url = data.get("next")
        pages += 1

    # Extract the major version from `current` when pinning (e.g. "17-trixie"
    # -> "17", "17.2-trixie" -> "17", "v1.2.3" -> "1"). Tolerate a leading "v" so
    # v-prefixed schemes (k3s, gluetun, redis-exporter, ...) aren't silently
    # un-pinned.
    major_filter = None
    if pin_major and current:
        m = re.match(r"^v?(\d+)", current)
        if m:
            major_filter = m.group(1)

    best = None
    best_tuple = None
    for result in results:
        tag_name = result.get("name", "")
        match = re.match(regex, tag_name)
        if not match:
            continue
        # version_prefix: only consider tags starting with this prefix
        # (e.g. "v1.15." restricts to patch updates within 1.15.x).
        if version_prefix and not tag_name.startswith(version_prefix):
            continue
        # Compare/filter on the CAPTURED version (group 1), not the raw tag: a
        # leading "v" (or a regex prefix before the digits) must not bypass the
        # major pin or wrongly reject valid same-major tags.
        extracted = match.group(1)
        if major_filter:
            tag_major = re.match(r"^v?(\d+)", extracted)
            if not tag_major or tag_major.group(1) != major_filter:
                continue  # Skip tags from a different major version
        try:
            vtuple = parse_version_tuple(extracted)
        except (TypeError, ValueError):
            continue
        if best_tuple is None or version_tuple_greater(vtuple, best_tuple):
            best_tuple = vtuple
            best = tag_name if return_full_tag else extracted
    return best


def fetch_dockerhub_version(svc: dict) -> str:
    """Fetch latest version from Docker Hub using tag_regex.

    The tag_regex should have a capture group for the version portion.
    The highest matching version (by version tuple comparison) is returned as
    the full tag name (that is what all.yml pins store).

    If pin_major_version is True, only returns versions matching the same major
    version as the current version.
    """
    image = svc["docker_image"]
    tag_regex = svc.get("tag_regex", r"^(v?\d+(?:\.\d+)*)$")
    best_tag = _dockerhub_best_tag(
        image,
        tag_regex,
        version_prefix=svc.get("version_prefix", ""),
        pin_major=svc.get("pin_major_version", False),
        current=svc.get("_current_version", ""),
        return_full_tag=True,
        name_filter=svc.get("dockerhub_name_filter", ""),
    )
    if best_tag is None:
        raise RuntimeError(f"No matching tags found for {image} (regex: {tag_regex})")
    return best_tag


def fetch_lsio_version(svc: dict) -> str:
    """Fetch latest version from LinuxServer.io Docker Hub images.

    LinuxServer.io images use canonical version tags with prefixes:
      version-vX.Y.Z (nzbget), version-X.Y.Z-rN (qbittorrent),
      version-X.Y.Z.BUILD (*arr apps - stable branch)

    The regex captures the version portion from the tag, which is returned.
    """
    image = svc["docker_image"]
    version_regex = svc["lsio_version_regex"]
    best_version = _dockerhub_best_tag(
        image,
        version_regex,
        version_prefix=svc.get("version_prefix", ""),
        name_filter=svc.get("lsio_name_filter", ""),
        max_pages=svc.get("lsio_max_pages", 1),
    )
    if best_version is None:
        raise RuntimeError(
            f"No matching tags found for {image} "
            f"(regex: {version_regex})"
        )
    return best_version


def fetch_ghcr_version(svc: dict) -> str:
    """Fetch latest version tag from GitHub Container Registry.

    Uses the registry's anonymous pull-token flow plus the standard Docker
    Registry HTTP API tags/list endpoint. This works for public packages
    without a GITHUB_TOKEN — the GitHub packages REST API requires auth even
    for public images, which would make tokenless runs error.
    """
    image = svc["ghcr_image"]
    tag_filter = svc.get("tag_filter", r"^v?\d+\.\d+")

    token_resp = _make_request(
        f"https://ghcr.io/token?scope=repository:{image}:pull&service=ghcr.io"
    )
    if not isinstance(token_resp, dict) or not token_resp.get("token"):
        raise RuntimeError(f"Could not obtain anonymous pull token for ghcr.io/{image}")

    tags_resp = _make_request(
        f"https://ghcr.io/v2/{image}/tags/list",
        headers={"Authorization": f"Bearer {token_resp['token']}"},
    )
    if not isinstance(tags_resp, dict):
        raise RuntimeError(f"Unexpected non-JSON tag list for ghcr.io/{image}")

    best_version = None
    best_tuple = None
    for tag in tags_resp.get("tags") or []:
        if not re.match(tag_filter, tag):
            continue
        try:
            vtuple = parse_version_tuple(tag)
        except (TypeError, ValueError):
            continue
        if best_tuple is None or version_tuple_greater(vtuple, best_tuple):
            best_tuple = vtuple
            best_version = tag

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
    # Indent of the first key of each chart entry (the list-item content
    # column). The chart's own `version:` is a direct child key of the entry
    # and sits at this column; a dependency/maintainer `version:` nests deeper,
    # so pinning the match here keeps a dependency version from being collected.
    entry_key_indent = None
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

            # Resolve the key name and the column it starts at. A list-item
            # line ("- key: ...") starts its first key two columns past the
            # dash; that column is the chart entry's key indent.
            if stripped.startswith("- "):
                key_indent = line_indent + 2
                key = stripped[2:].split(":", 1)[0].strip()
            else:
                key_indent = line_indent
                key = stripped.split(":", 1)[0].strip()
            if entry_key_indent is None and stripped.startswith("- "):
                entry_key_indent = key_indent

            # Capture the chart's own "version:" — a direct child key of the
            # entry (at entry_key_indent). Match on the exact key so
            # "appVersion:" is excluded and arbitrary post-colon whitespace
            # (YAML permits it) doesn't drop the line. Restricting to the entry
            # key indent skips deeper "version:" lines under a dependencies:/
            # maintainers: sub-block, which would otherwise be collected.
            if key == "version" and (entry_key_indent is None or key_indent == entry_key_indent):
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

    # The Packages file may carry multiple plexmediaserver versions; collect
    # them all (Package: plexmediaserver / Version: X.Y.Z.BUILD-hash).
    versions = _collect_apt_versions(raw, "plexmediaserver")
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

    # Collect every gitlab-ee Version (X.Y.Z-ee.N), skip pre-releases, and keep
    # the highest by semantic ordering with (type_rank, value) tuples.
    best_version = None
    best_tuple = None
    for version in _collect_apt_versions(raw, "gitlab-ee"):
        if re.search(r"(rc|beta|alpha)", version, re.IGNORECASE):
            continue
        try:
            vtuple = parse_version_tuple(version)
            if best_tuple is None or version_tuple_greater(vtuple, best_tuple):
                best_tuple = vtuple
                best_version = version
        except (TypeError, ValueError):
            pass

    if not best_version:
        raise RuntimeError("Could not find gitlab-ee version in apt repository")

    return best_version


# all.yml parser (simple YAML extraction without PyYAML)

def read_pinned_image_versions() -> dict[str, str]:
    """Current tags of digest-locked `image:` pins that live outside all.yml.

    Entries with a version_file are read from that file rather than from vars
    in all.yml: "ci" means .gitlab-ci.yml (pr-agent); any other value is one or
    more repo-relative manifest paths (kubernetes/ CronJobs / sidecars).
    Extract the tag (between ':' and the '@sha256:' digest) for each so
    check-versions can flag a stale pin. `image_ref` overrides the image name
    matched in the file when it differs from the API lookup name (a ghcr.io/
    registry prefix, Docker Hub's library/ namespace).
    """
    versions: dict[str, str] = {}
    repo_root = Path(__file__).resolve().parent.parent
    for svc in SERVICE_REGISTRY:
        version_file = svc.get("version_file")
        if not version_file:
            continue
        if version_file == "ci":
            paths = [CI_FILE]
        elif isinstance(version_file, str):
            paths = [repo_root / version_file]
        else:
            paths = [repo_root / p for p in version_file]
        image = svc.get("image_ref") or svc.get("docker_image", "")
        # Collect the tag from every readable path (not break-on-first) so
        # divergent pins between manifests that must share one tag are caught.
        matched: list[tuple[Path, str]] = []
        for path in paths:
            try:
                content = path.read_text()
            except OSError:
                continue
            m = re.search(
                rf"^\s*image:\s*{re.escape(image)}:([\w.+-]+?)(?:@sha256:[0-9a-f]+)?\s*$",
                content,
                re.MULTILINE,
            )
            if m:
                matched.append((path, m.group(1)))
        if not matched:
            continue
        distinct = {tag for _, tag in matched}
        if len(distinct) > 1:
            detail = ", ".join(
                f"{p.relative_to(repo_root)}={tag}" for p, tag in matched
            )
            # Fail loudly instead of silently selecting matched[0]: manifests that
            # must share one image tag have drifted, and swallowing that lets CI
            # go green with divergent pins. Raising surfaces it through the
            # blocking scripts:test unit run (which calls this on the real tree).
            raise RuntimeError(
                f"{svc['var_name']} pins diverge across manifests "
                f"that must share one tag: {detail}"
            )
        versions[svc["var_name"]] = matched[0][1]
    return versions


def read_current_versions() -> dict[str, str]:
    """Read current versions from all.yml without a YAML parser.

    Returns a dict mapping var_name to current version string.
    """
    content = VARS_FILE.read_text()
    versions = {}

    # Registered pins whose var_name does NOT follow the `*_version` convention
    # (e.g. lxc_template, a Proxmox appliance FILENAME rather than a semver) —
    # read those by exact top-level key match so they still resolve to a current
    # value instead of showing "unknown". version_file pins live outside all.yml.
    extra_keys = {
        s["var_name"] for s in SERVICE_REGISTRY
        if s.get("var_name") and "_version" not in s["var_name"] and not s.get("version_file")
    }

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

        _key = stripped.split(":")[0].strip()
        if not in_helm and ":" in stripped and ("_version" in _key or _key in extra_keys):
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            # Remove inline comments
            if "#" in val:
                val = val[:val.index("#")].strip().strip('"').strip("'")
            versions[key] = val

    # Digest-locked image pins (pr-agent in .gitlab-ci.yml, kubernetes/
    # manifest pins) live outside all.yml.
    versions.update(read_pinned_image_versions())
    return versions


def update_version_in_file(var_name: str, new_version: str) -> bool:
    """Update a version in all.yml, preserving formatting and comments.

    Returns True if the file was modified.
    """
    # version_file entries (pr-agent in .gitlab-ci.yml, kubernetes/ manifest
    # pins) are digest-locked outside all.yml. Flag the update but don't
    # auto-rewrite the @sha256 pin — bumping a supply-chain pinned image
    # should be a reviewed manual step.
    pinned_svc = next(
        (s for s in SERVICE_REGISTRY
         if s.get("var_name") == var_name and s.get("version_file")),
        None,
    )
    if pinned_svc:
        vf = pinned_svc["version_file"]
        if vf == "ci":
            where = ".gitlab-ci.yml"
        else:
            where = ", ".join(vf) if isinstance(vf, list) else vf
        print(
            f"  ↳ {pinned_svc['name']} is digest-pinned in {where} — update "
            f"manually: bump the tag to {new_version} and re-pin its @sha256 "
            f"digest (supply-chain pin, not auto-rewritten)."
        )
        return False

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


# Main logic

def _annotate_latest_resolution(result: ServiceVersion, current: str) -> None:
    """When a service tracks 'latest', surface the resolved version in the notes
    so the table shows it on both the cache-hit and live-fetch paths."""
    if current == "latest" and result.latest_version:
        suffix = f"'latest' resolves to {result.latest_version}"
        result.notes = (result.notes + " " + suffix) if result.notes else suffix


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
        # owner/name form resolves for both user- and org-owned packages;
        # github.com/orgs/<owner>/packages 404s for user-owned ones.
        owner, _, name = svc_def["ghcr_image"].partition("/")
        result.source_url = f"https://github.com/{owner}/{name}/pkgs/container/{name}"
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
                and cached != current
                and version_greater(cached, current)
            )
            _annotate_latest_resolution(result, current)
            return result

    # Fetch latest version
    result.fetched_live = True
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
            _annotate_latest_resolution(result, current)
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
        # Small delay between live API calls to be nice to rate limits.
        # Skip it on cache hits / manual services (no network call made).
        if result.fetched_live:
            time.sleep(0.2)

    return results


# Output formatting

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


# CLI

def get_deploy_command(result: ServiceVersion) -> str:
    """Get the deployment command for a service."""
    var_name = result.var_name
    category = result.category

    # version_file pins: no deploy task — bump the tag + re-pin the @sha256
    # digest where the pin lives ("ci" = .gitlab-ci.yml; otherwise one or more
    # kubernetes/ manifests) and commit + push.
    pinned_svc = next(
        (s for s in SERVICE_REGISTRY
         if s.get("var_name") == var_name and s.get("version_file")),
        None,
    )
    if pinned_svc:
        vf = pinned_svc["version_file"]
        if vf == "ci":
            return (
                "edit the image: tag + @sha256 digest in .gitlab-ci.yml, "
                "commit + push (applies on the next pipeline)"
            )
        files = ", ".join(vf) if isinstance(vf, list) else vf
        return (
            f"edit the image tag + @sha256 digest in {files}, "
            "commit + push (Flux reconciles on push)"
        )

    # Flux-managed workloads: all Helm charts and app container images reach
    # the cluster via the cluster-versions ConfigMap + git push + Flux.
    # Helm chart pins (helm_chart_versions.*) are routed by the
    # startswith("helm_chart") / category == "helm" checks below; only
    # container-image vars need listing here. Keep this list in sync with
    # versions tracked by the Flux ConfigMap.
    flux_managed = (
        "gluetun_version", "nzbget_version", "qbittorrent_version",
        "prowlarr_version", "sonarr_version", "radarr_version",
        "lidarr_version", "pulsarr_version", "wg_easy_version", "homarr_version",
        "hermes_version",
        "hermes_codex_version", "hermes_claude_version", "hermes_op_version",
        "hermes_camofox_version", "hindsight_version",
        "hindsight_llamacpp_version",
        "mealie_version", "mealie_postgresql_version",
        "bar_assistant_version", "salt_rim_version",
        "meilisearch_version", "redis_version", "busybox_version",
        "authentik_version", "postgresql_version",
        "gitlab_runner_helm_version", "gitlab_agent_helm_version",
        # In-cluster CI registry pull-through cache (kubernetes/apps/registry-cache)
        "registry_cache_version",
        # Observability exporter container images
        "exportarr_version", "proxmox_exporter_version",
        "zfs_exporter_version", "adguard_exporter_version",
        "unbound_exporter_version", "redis_exporter_version",
        # NVIDIA DCGM exporter (raw DaemonSet, Flux-reconciled via cluster-versions)
        "dcgm_exporter_version",
        # tailnet-dns CoreDNS resolver image (kubernetes/apps/tailnet-dns)
        "coredns_tailnet_version",
    )
    if var_name in flux_managed or var_name.startswith("helm_chart") or category == "helm":
        return "task flux:sync-versions && git commit -am '...' && git push  # Flux reconciles on push"

    # K3s infrastructure (Ansible-managed, not Flux)
    if var_name == "k3s_version":
        return "task maintenance:update-k3s-nodes"
    if var_name == "kube_vip_version":
        return "task k3s:deploy  # Re-run k3s deployment to update kube-vip"

    # NVIDIA driver + container toolkit + CUDA repo keyring on the GPU k3s agent
    # (k3s role gpu.yml, Ansible-managed node op — never CI). docs/43.
    if var_name in (
        "nvidia_driver_version",
        "nvidia_container_toolkit_version",
        "nvidia_cuda_keyring_version",
    ):
        return "task k3s:deploy  # Re-run k3s deployment on the GPU agent (docs/43)"

    # Flux CLI used by CI deploy-verify (pin + sha256 live together)
    if var_name == "flux_version":
        return "edit FLUX_VERSION + sha256 in .gitlab-ci.yml deploy-verify, update all.yml, task flux:sync-versions, commit"

    # Plex (LXC, Ansible-managed)
    if var_name == "plex_version":
        return "task maintenance:update-plex"

    # Tailscale (apt, Ansible-managed)
    if var_name == "tailscale_version":
        return "task maintenance:update-applications"

    # Host-side Alloy (apt, Ansible-managed; dpkg-held so it only moves on a bump)
    if var_name == "alloy_host_version":
        return "task maintenance:update-applications"

    # AdGuard Home (LXC, Ansible-managed)
    if var_name == "adguard_home_version":
        return "task maintenance:update-applications --limit dns"
    if var_name == "adguardhome_sync_version":
        return "task maintenance:update-applications --limit dns-01"

    # GitLab VM (Ansible-managed — not Flux)
    if var_name == "gitlab_version":
        return "task gitlab:deploy"

    # Nextcloud VM (Docker Compose, Ansible-managed — not Flux). All the
    # nextcloud_* pins (app/postgres/redis/exporter images + docker apt) are
    # applied by re-running the role.
    if var_name.startswith("nextcloud_"):
        return "task nextcloud:deploy"

    # Immich VM (docker-compose, Ansible-managed — not Flux)
    if var_name == "immich_version":
        return "task immich:deploy"

    if var_name == "virtio_win_version":
        # The driver ISO download is guarded to fresh-VM-only (get_url runs when
        # vm_exists.rc != 0), so bumping the pin only fetches + attaches the new
        # ISO when the VM does not yet exist. On the already-provisioned guest
        # this is a no-op: it keeps its old drivers until destroy+re-provision
        # (docs/39).
        return "task windows:provision  # only downloads the new ISO on a fresh VM; an existing guest keeps its drivers until destroy+re-provision"

    # Debian LXC root template (pveam appliance, Ansible-managed via proxmox_lxc).
    # Bumping the pin only changes which template a NEWLY created LXC pulls (pveam
    # downloads it on the next provisioning run); existing containers keep their
    # rootfs until destroy + recreate, so there is no fleet deploy step. Keep the
    # proxmox_lxc role default in sync (all.yml is authoritative).
    if var_name == "lxc_template":
        return "bump the proxmox_lxc role default to match; new template applies only on the next LXC create (existing containers keep their rootfs)"

    # Fallback when no specific deploy task mapping exists
    return "task infra:deploy"


def print_usage():
    """Print usage information."""
    print("""Usage: check-versions.py [OPTIONS]

Options:
  --help                Show this help message
  --service NAME        Check a specific service only
  --category CAT        Check a category only (github, dockerhub, ghcr, lsio, helm, gitlab, plex, apt_repo, manual)
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
    value_flags = ("--service", "--category", "--update")
    i = 0
    while i < len(args):
        if args[i] in value_flags and i + 1 >= len(args):
            print(f"Error: {args[i]} requires an argument", file=sys.stderr)
            sys.exit(2)
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

            if result.held:
                print(f"{result.name} is held back: {result.notes or 'documented hold'}")
                print(f"Not updating (would write {result.latest_version} into {VARS_FILE.name}).")
                print("Remove the 'held' flag in SERVICE_REGISTRY to override.")
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
            results = check_all(use_cache=False)
            updated = []
            write_failed = []
            errored = [r for r in results if r.error]
            held_skipped = [r for r in results if r.update_available and r.held and not r.error]
            for r in results:
                if r.update_available and not r.error and not r.held:
                    print(f"Updating {r.name}: {r.current_version} -> {r.latest_version}")
                    if update_version_in_file(r.var_name, r.latest_version):
                        updated.append(r)
                    else:
                        print(f"  ERROR: Could not find {r.var_name} in {VARS_FILE.name}")
                        write_failed.append(r)

            # Surface errors FIRST — an operator looking at a long successful
            # update list could easily miss that 5 other services failed their
            # version check.
            if write_failed:
                print(f"\nERROR: {len(write_failed)} service(s) could not be updated in {VARS_FILE.name}:")
                for r in write_failed:
                    print(f"  - {r.var_name}")

            if errored:
                print(f"\nWARNING: {len(errored)} service(s) had errors and were NOT checked:")
                for r in errored:
                    print(f"  - {r.name}: {r.error}")

            if held_skipped:
                print(f"\nNOTE: {len(held_skipped)} update(s) intentionally held back (not written):")
                for r in held_skipped:
                    print(f"  - {r.name}: {r.current_version} -> {r.latest_version} "
                          f"({r.notes or 'documented hold'})")

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
        elif args[i] in ("--json", "--no-cache"):
            # Boolean flags already consumed by the `in args` checks above.
            i += 1
        else:
            # Reject unknown flags loudly: a typo'd --category/--service would
            # otherwise silently run the full unfiltered check.
            print(f"Error: unknown argument '{args[i]}'", file=sys.stderr)
            print("Run with --help for usage", file=sys.stderr)
            sys.exit(2)

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
