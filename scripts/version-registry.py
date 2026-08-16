"""Site registry for scripts/check-versions.py (the default config path).

Every pinned version this cluster tracks, where its upstream lives, and how a
bump is rolled out. The Python form (rather than JSON) keeps each entry's
inline rationale next to the entry.

Schema: weisssrv-lib docs/SCRIPTS.md § check-versions.py.
"""

_SERVICES: list[dict] = [
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
        "var_name": "adguard_sync_version",
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
        # so they sit in untracked_allowlist below rather than being auto-bumped.
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
        "held": True,
        "notes": (
            "1.13.1+ held: upstream's own Dockerfile does not build. 1.13.1 "
            "moved the transitive better-sqlite3 12.9.0 -> 13.0.1, which has no "
            "prebuilt binary for node 22, so `npm ci` falls back to `node-gyp "
            "rebuild` and dies with 'not found: make' — their node:22-slim base "
            "installs python3-minimal but no compiler. We build their Dockerfile "
            "UNMODIFIED by design (docker/camofox-browser/README.md), so this is "
            "not patchable here without forking the supply chain. Re-check when "
            "upstream adds build-essential or the dep ships a node 22 prebuilt."
        ),
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
        "name": "metrics-server",
        "var_name": "helm_chart_versions.metrics_server",
        "category": "helm",
        "helm_repo": "https://kubernetes-sigs.github.io/metrics-server",
        "helm_chart": "metrics-server",
        "source_url": "https://github.com/kubernetes-sigs/metrics-server/releases",
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
    # CI tooling images
    {
        # The pr-agent AI reviewer image. The tag+digest pin lives in
        # weisssrv-lib's ci/review/pr-agent.yml, which this repo includes at
        # WEISSSRV_LIB_REF and passes no `image:` input to — so there is no
        # local pin to read and `current` reads as unknown. Kept tracked, and
        # held, so the credential-handling reviewer still shows an upstream
        # release stream: a bump is a library MR + a ref bump here, never an
        # edit in this repo. `codiumai/` is the frozen pre-rename namespace
        # (tags stop at 0.34) and would report an update that does not exist.
        "name": "pr-agent (CI reviewer)",
        "var_name": "pr_agent_version",
        "category": "dockerhub",
        "docker_image": "pragent/pr-agent",
        "tag_regex": r"^(\d+\.\d+(?:\.\d+)?)$",
        "held": True,
        "notes": (
            "pinned by tag+digest in weisssrv-lib ci/review/pr-agent.yml; bump "
            "it there, tag, then move WEISSSRV_LIB_REF"
        ),
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
        # the source_url, update proxmox_vm_virtio_win_version in all.yml, and recompute the
        # ISO sha256 (proxmox_vm_virtio_win_checksum) since Fedora ships no ISO checksum.
        # Current tag is read from all.yml (no version_file). See docs/39.
        "name": "virtio-win",
        "var_name": "proxmox_vm_virtio_win_version",
        "category": "manual",
        "source_url": "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/",
        "notes": "VirtIO driver ISO for the Windows 11 VM; no GitHub releases — check the Fedora stable-virtio dir and recompute proxmox_vm_virtio_win_checksum on bump.",
    },
    {
        # Debian LXC root template (pveam appliance). Proxmox silently rotates
        # the point build out of its index, which breaks a cached-template
        # recreate. No release feed to poll; check the pveam index on a Proxmox
        # host. Authoritative pin lives in all.yml (mirrored as the proxmox_lxc
        # role default).
        "name": "Debian LXC template (pveam)",
        "var_name": "proxmox_lxc_template",
        "category": "manual",
        "source_url": "http://download.proxmox.com/images/system/",
        "notes": "Debian LXC root template; no release feed — run `pveam update && pveam available --section system | grep debian-13-standard` on a Proxmox host, then bump proxmox_lxc_template in all.yml + the proxmox_lxc role default together.",
    },
    {
        # postgres_exporter sidecar in the immich + nextcloud compose stacks.
        "name": "postgres_exporter",
        "var_name": "postgres_exporter_version",
        "category": "github",
        "github_repo": "prometheus-community/postgres_exporter",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        # Vim plugin manager cloned at a tag by the qol role. Upstream is
        # ARCHIVED at v0.10.2 — there will never be another release, so any
        # live query is permanent noise (its Releases endpoint 404s: tags
        # only). `manual` performs no network call; the pin is final.
        "name": "Vundle.vim",
        "var_name": "qol_vundle_version",
        "category": "manual",
        "source_url": "https://github.com/VundleVim/Vundle.vim",
        "notes": "upstream archived at v0.10.2 — final pin, no query to run",
    },
]


# Container images and Helm charts that reach the cluster through the
# cluster-versions ConfigMap: bumping the pin is a git push, and Flux does the
# rest. Helm pins route here via their category, so only image vars are listed.
_FLUX_MANAGED = {
    "gluetun_version", "nzbget_version", "qbittorrent_version",
    "prowlarr_version", "sonarr_version", "radarr_version",
    "lidarr_version", "pulsarr_version", "wg_easy_version", "homarr_version",
    "hermes_version", "hermes_codex_version", "hermes_claude_version",
    "hermes_op_version", "hermes_camofox_version",
    "hindsight_version", "hindsight_llamacpp_version",
    "mealie_version", "mealie_postgresql_version",
    "bar_assistant_version", "salt_rim_version",
    "meilisearch_version", "redis_version", "busybox_version",
    "authentik_version", "postgresql_version",
    "gitlab_runner_helm_version", "gitlab_agent_helm_version",
    "registry_cache_version",
    "exportarr_version", "proxmox_exporter_version",
    "zfs_exporter_version", "adguard_exporter_version",
    "unbound_exporter_version", "redis_exporter_version",
    "dcgm_exporter_version", "coredns_tailnet_version",
}

_FLUX_DEPLOY = (
    "task flux:sync-versions && git commit -am '...' && git push  # Flux reconciles on push"
)

# Pins whose rollout is an Ansible task rather than a Flux reconcile.
_ANSIBLE_DEPLOY = {
    "k3s_version": "task maintenance:update-k3s-nodes",
    "kube_vip_version": "task k3s:deploy  # re-run the k3s deployment to update kube-vip",
    "nvidia_driver_version": "task k3s:deploy  # GPU agent only (docs/43)",
    "nvidia_container_toolkit_version": "task k3s:deploy  # GPU agent only (docs/43)",
    "nvidia_cuda_keyring_version": "task k3s:deploy  # GPU agent only (docs/43)",
    "plex_version": "task maintenance:update-plex",
    "tailscale_version": "task maintenance:update-applications",
    "alloy_host_version": "task maintenance:update-applications",
    "adguard_home_version": "task maintenance:update-applications --limit dns",
    "adguard_sync_version": "task maintenance:update-applications --limit dns-01",
    "gitlab_version": "task gitlab:deploy",
    "immich_version": "task immich:deploy",
    "postgres_exporter_version": "task immich:deploy && task nextcloud:deploy",
    "flux_version": (
        "edit FLUX_VERSION + sha256 in .gitlab-ci.yml deploy-verify, bump all.yml, "
        "task flux:sync-versions, commit"
    ),
    # Guarded to fresh-VM-only: an existing guest keeps its drivers until
    # destroy + re-provision (docs/39).
    "proxmox_vm_virtio_win_version": (
        "task windows:provision  # downloads the new ISO only on a fresh VM"
    ),
    # Only changes which template a NEWLY created LXC pulls; existing containers
    # keep their rootfs. Keep the proxmox_lxc role default in step.
    "proxmox_lxc_template": (
        "bump the proxmox_lxc role default to match; applies on the next LXC create"
    ),
}


def _deploy_command(svc: dict) -> str | None:
    """The rollout command for one entry, or None to use the fallbacks.

    None lets check-versions derive the instruction for a `version_file` pin and
    otherwise fall back to `default_deploy_command`.
    """
    var_name = svc["var_name"]
    if svc.get("version_file"):
        return None
    if var_name in _ANSIBLE_DEPLOY:
        return _ANSIBLE_DEPLOY[var_name]
    if var_name.startswith("nextcloud_"):
        return "task nextcloud:deploy"
    if var_name in _FLUX_MANAGED or var_name.startswith("helm_chart") or svc["category"] == "helm":
        return _FLUX_DEPLOY
    return None


CONFIG = {
    "vars_file": "ansible/inventories/prod/group_vars/all.yml",
    "cache_dir": ".version-cache",
    # The vendored checker's own default heading is the library's; this is the
    # consumer's report.
    "report_title": "Homelab Version Check Report",
    # Everything with no more specific rollout path.
    "default_deploy_command": "task infra:deploy",
    # Digest-locked `image:` pins that live outside the vars file.
    "version_file_aliases": {"ci": ".gitlab-ci.yml"},
    # Pins deliberately outside the checker: no independent upstream feed, or a
    # release-coupled value that must never be bumped on its own.
    "untracked_allowlist": [
        "debian_version",  # distro major, not a per-service upstream
        # Docker CE and friends: bumped together from the Docker apt index, held
        # by dpkg so a maintenance upgrade cannot move them under a live stack.
        "docker_engine_ce_version",
        "docker_engine_containerd_version",
        "docker_engine_buildx_plugin_version",
        "docker_engine_compose_plugin_version",
        # Release-coupled to immich_version (vectorchord/pgvectors build).
        "immich_postgres_version",
        "immich_valkey_version",
        # Built here, not pulled: the -rN suffix rebuilds the same upstream tag.
        "hermes_image_version",
        # Pinned with their .deb sha256 in all.yml; bumped as a pair by hand.
        "restic_offsite_restic_version",
        "restic_offsite_rclone_version",
    ],
    "services": [
        dict(svc, **({"deploy_command": cmd} if (cmd := _deploy_command(svc)) else {}))
        for svc in _SERVICES
    ],
}
