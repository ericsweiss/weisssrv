"""Site registry for scripts/check-versions.py: every pinned version this
cluster tracks and where its upstream lives.

Schema: weisssrv-lib docs/SCRIPTS.md, check-versions.py section.
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
        "coupled_vars": ["k3s_install_script_checksum"],
        "checksum_var": "k3s_install_script_checksum",
        "checksum_url": "https://raw.githubusercontent.com/k3s-io/k3s/{version}/install.sh",
        "notes": "k3s_install_script_checksum is the sha256 of install.sh at the tag.",
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
        "held": True,
        "notes": (
            "Held on 1.x. v2 migrates the SQLite database in place and 1.x "
            "cannot read it afterwards, so putting the old image back is not a "
            "rollback. Unholding needs a snapshot of the /appdata PV, the "
            "SECRET_ENCRYPTION_KEY retained, and a board re-layout for v2's "
            "200px fixed cells. Rationale in docs/41."
        ),
    },
    {
        # Tracked on Docker Hub because the manifest pins the Docker tag
        # (`${uptime_kuma_version}-rootless`, docs/45). The regex keeps bare
        # X.Y.Z and drops the floating majors, channel tags and variants.
        "name": "Uptime Kuma",
        "var_name": "uptime_kuma_version",
        "category": "dockerhub",
        "docker_image": "louislam/uptime-kuma",
        "tag_regex": r"^(\d+\.\d+\.\d+)$",
        "source_url": "https://github.com/louislam/uptime-kuma/releases",
    },
    {
        # The runners' S3 cache backend (kubernetes/apps/ci-cache). The pin
        # carries the full vX.Y.Z tag; the regex drops the floating majors and
        # arch variants.
        "name": "Garage (CI cache)",
        "var_name": "garage_version",
        "category": "dockerhub",
        "docker_image": "dxflrs/garage",
        "tag_regex": r"^(v\d+\.\d+\.\d+)$",
        "source_url": "https://garagehq.deuxfleurs.fr/documentation/",
    },
    {
        # immich-server and immich-machine-learning share this tag. The coupled
        # DB/Valkey pins come from the SAME release's docker-compose.yml, so they
        # sit in untracked_allowlist rather than being auto-bumped.
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
        "coupled_vars": ["hermes_git_sha", "hermes_image_version"],
        # The checker writes both companions itself: the PEELED annotated-tag
        # commit, and the image revision restarted at -r1.
        "revision_var": "hermes_image_version",
        "sha_var": {
            "var": "hermes_git_sha",
            "repo": "https://github.com/NousResearch/hermes-agent",
        },
        "notes": (
            "hermes_git_sha is the PEELED annotated-tag commit "
            "(`git ls-remote 'refs/tags/<tag>*'`, the ^{} row); hermes_image_version "
            "re-starts at -r1 for the new tag."
        ),
    },
    {
        # Baked into the hermes-agent image (npm @openai/codex). The pin is the
        # bare npm version, so the `rust-v` prefix is stripped and the filter
        # excludes the per-platform alpha tags. Requires >=0.130.0.
        "name": "Codex CLI (Hermes)",
        "var_name": "hermes_codex_version",
        "category": "github",
        "github_repo": "openai/codex",
        "version_prefix": "rust-v",
        "strip_prefix": True,
        "tag_filter": r"^rust-v\d+\.\d+\.\d+$",
        "coupled_vars": ["hermes_image_version"],
        "notes": (
            "Baked into the image, which the pods pull IfNotPresent: a new CLI "
            "pin needs a new hermes_image_version revision or the cache serves "
            "the old CLI."
        ),
    },
    {
        # Baked into the hermes-agent image alongside Codex (npm
        # @anthropic-ai/claude-code) for headless `claude -p` runs. The pin is
        # the bare npm version, so the `v` prefix is stripped.
        "name": "Claude Code CLI (Hermes)",
        "var_name": "hermes_claude_version",
        "category": "github",
        "github_repo": "anthropics/claude-code",
        "version_prefix": "v",
        "strip_prefix": True,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
        "coupled_vars": ["hermes_image_version"],
        "notes": (
            "Baked into the image, which the pods pull IfNotPresent: a new CLI "
            "pin needs a new hermes_image_version revision or the cache serves "
            "the old CLI."
        ),
    },
    {
        # Baked into the hermes-agent image so the 1Password skill can drive
        # `op`. Shipped only via 1Password's signed apt repo, and the pin is the
        # full DEB version, hence manual: `apt-cache madison 1password-cli`.
        "name": "1Password CLI (Hermes)",
        "var_name": "hermes_op_version",
        "category": "manual",
        "source_url": "https://app-updates.agilebits.com/product_history/CLI2",
        "notes": "op CLI baked into the hermes image (docker/hermes-agent). Full DEB version pin — bump via `apt-cache madison 1password-cli` against 1Password's signed apt repo, sync-versions, commit; CI rebuilds the wrapper. The pods pull IfNotPresent, so a new pin needs a new hermes_image_version revision.",
        "coupled_vars": ["hermes_image_version"],
    },
    {
        # Built from source by build-camofox-browser (upstream publishes no
        # image). hermes_camofox_git_sha, the tag's commit, moves in lockstep.
        "name": "Camofox browser (Hermes)",
        "var_name": "hermes_camofox_version",
        "category": "github",
        "github_repo": "jo-inc/camofox-browser",
        "version_prefix": "v",
        "strip_prefix": True,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
        "notes": (
            "Built from upstream's UNMODIFIED Dockerfile by build-camofox-browser "
            "(docker/camofox-browser/README.md), so a bump also needs "
            "hermes_camofox_git_sha re-resolved to the PEELED tag commit "
            "(`git ls-remote 'refs/tags/<tag>*'`, the ^{} row)."
        ),
        "coupled_vars": ["hermes_camofox_git_sha"],
        # The pin strips the tag's "v", so the ref puts it back.
        "sha_var": {
            "var": "hermes_camofox_git_sha",
            "repo": "https://github.com/jo-inc/camofox-browser",
            "ref": "refs/tags/v{version}",
        },
    },
    {
        # Hindsight agent-memory server, Hermes' memory backend. The pin is the
        # ghcr.io/vectorize-io/hindsight image tag: the release tag less its "v".
        "name": "Hindsight (Hermes memory)",
        "var_name": "hindsight_version",
        "category": "github",
        "github_repo": "vectorize-io/hindsight",
        "version_prefix": "v",
        "strip_prefix": True,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        # llama.cpp server sidecar (ghcr.io/ggml-org/llama.cpp:server-<pin>).
        # Upstream tags builds many times a day and only some publish a server
        # image, so this is bumped by hand after checking the tag exists.
        "name": "llama.cpp server (Hindsight)",
        "var_name": "hindsight_llamacpp_version",
        "category": "manual",
        "source_url": "https://github.com/ggml-org/llama.cpp/releases",
        "notes": "Pin bNNNN whose ghcr server-cuda-bNNNN (CUDA) image tag exists; any recent build serves the pinned GGUF.",
    },
    {
        # nvidia-open from NVIDIA's debian13 CUDA apt repo, the exact apt version
        # installed on the GPU k3s agent. >=570 is required on GeForce (docs/43).
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
        # cuda-keyring apt package: the debian13 CUDA repo signing key and
        # sources file. Bump it and nvidia_cuda_keyring_sha256 in all.yml
        # together.
        "name": "NVIDIA cuda-keyring",
        "var_name": "nvidia_cuda_keyring_version",
        "category": "manual",
        "source_url": "https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/",
        "notes": "cuda-keyring_<version>_all.deb; nvidia_cuda_keyring_sha256 is the sha256 of that .deb in the source_url directory.",
        "coupled_vars": ["nvidia_cuda_keyring_sha256"],
        "checksum_var": "nvidia_cuda_keyring_sha256",
        "checksum_url": (
            "https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/"
            "cuda-keyring_{version}_all.deb"
        ),
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
        # authentik_version is read as a CHART tag by the HelmRelease, and the
        # chart publishes days after the GitHub release, so query the chart repo:
        # a GitHub tag Flux cannot resolve fails reconciliation outright.
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
        # Hub pages the tag list; the -trixie tag for the pinned major is off
        # page one at the default size.
        "dockerhub_page_size": 100,
        "notes": "Used by Authentik (bundled PostgreSQL). Only checks updates within current major version.",
        "pin_major_version": True,  # Only suggest updates within same major version
    },
    {
        "name": "PostgreSQL (Mealie)",
        "var_name": "mealie_postgresql_version",
        "category": "dockerhub",
        "docker_image": "library/postgres",
        "tag_regex": r"^(\d+(?:\.\d+)?)-alpine$",  # Matches 16-alpine, 16.1-alpine, etc.
        "dockerhub_page_size": 100,
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
    # LinuxServer.io container images. lsio_version_regex selects the tag and
    # captures the bare version in group 1. Stable tags sit under daily develop
    # pushes, hence lsio_name_filter and lsio_max_pages here.
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
            "held at 0.15.x: 0.16.x floods the apiserver on this topology. "
            "Confirm upstream has shipped a fix before unholding. Rationale in "
            "kubernetes/infrastructure/controllers/metallb/release.yaml."
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
        "notes": (
            "controllers/external-dns/release.yaml pins "
            "--annotation-prefix=external-dns.alpha.kubernetes.io/; 0.22.0 changed the "
            "default and dropping the flag makes policy=sync delete every owned record."
        ),
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
        # Tracked on the apt repo, not GitHub: install is `apt install
        # tailscale=<pin>`. The index is trixie/amd64 because every Tailscale
        # host is one.
        "category": "apt_repo",
        "apt_index_url": "https://pkgs.tailscale.com/stable/debian/dists/trixie/main/binary-amd64/Packages.gz",
        "apt_package": "tailscale",
        "source_url": "https://pkgs.tailscale.com/stable/debian/dists/trixie/main/binary-amd64/Packages.gz",
    },
    {
        "name": "Grafana Alloy (host)",
        "var_name": "alloy_host_version",
        # Host-side Alloy apt package (alloy_host role), distinct from the
        # in-cluster helm_chart_versions.alloy chart entry above. The Grafana
        # repo is `stable main`, so binary-amd64 is the index for this fleet.
        "category": "apt_repo",
        "apt_index_url": "https://apt.grafana.com/dists/stable/main/binary-amd64/Packages.gz",
        "apt_package": "alloy",
        "source_url": "https://apt.grafana.com/dists/stable/main/binary-amd64/Packages.gz",
    },
    # GitLab
    {
        "name": "GitLab EE",
        "var_name": "gitlab_version",
        "category": "apt_repo",
        "apt_url": [
            "https://packages.gitlab.com/gitlab/gitlab-ee/debian/dists/trixie/main/binary-amd64/Packages",
            "https://packages.gitlab.com/gitlab/gitlab-ee/debian/dists/bookworm/main/binary-amd64/Packages",
        ],
        "apt_package": "gitlab-ee",
        "apt_exclude_regex": r"(rc|beta|alpha)",
        "source_url": "https://packages.gitlab.com/gitlab/gitlab-ee",
        "notes": "GitLab EE (CE features).",
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
        # Pull-through registry cache for CI (docs/27): the CNCF distribution
        # image on Docker Hub. The regex keeps bare X.Y.Z and drops the floating
        # majors and pre-releases that share the repo.
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
        "coupled_vars": ["helm_chart_versions.prometheus_operator_crds"],
        "notes": (
            "prometheus_operator_crds moves with it: the CRD stage must carry the "
            "prometheus-operator appVersion this chart expects."
        ),
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
            "plane blind is the highest-blast-radius change in the repo. A 2.9.x "
            "patch is re-evaluated on each upstream release and ships as its own "
            "MR; a minor or major move stays a dedicated bootstrap-tested change."
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
        "notes": "zfs_exporter_checksum is the sha256 of the release .tar.gz asset for the new tag.",
        "coupled_vars": ["zfs_exporter_checksum"],
        "checksum_var": "zfs_exporter_checksum",
        "checksum_url": (
            "https://github.com/pdf/zfs_exporter/releases/download/"
            "v{version}/zfs_exporter-{version}.linux-amd64.tar.gz"
        ),
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
        "notes": "unbound_exporter_checksum is computed here: upstream ships no checksum file.",
        "coupled_vars": ["unbound_exporter_checksum"],
        "checksum_var": "unbound_exporter_checksum",
        "checksum_url": (
            "https://github.com/letsencrypt/unbound_exporter/releases/download/"
            "v{version}/unbound_exporter-v{version}.x86_64.deb"
        ),
    },
    {
        "name": "Redis Exporter",
        "var_name": "redis_exporter_version",
        "category": "dockerhub",
        "docker_image": "oliver006/redis_exporter",
        "tag_regex": r"^(v\d+\.\d+\.\d+)$",
    },
    {
        "name": "Plex Media Server",
        "var_name": "plex_version",
        "category": "apt_repo",
        "apt_url": "https://repo.plex.tv/deb/dists/public/main/binary-amd64/Packages",
        "apt_package": "plexmediaserver",
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
        "dockerhub_page_size": 100,
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
        # The pin lives in weisssrv-lib's ci/review/pr-agent.yml, so `current`
        # reads as unknown here and a bump is a library MR plus a ref bump.
        # Tracked anyway to keep the reviewer's release stream visible.
        "name": "pr-agent (CI reviewer)",
        "var_name": "pr_agent_version",
        "category": "dockerhub",
        "docker_image": "pragent/pr-agent",
        "tag_regex": r"^(\d+\.\d+(?:\.\d+)?)$",
        # unreadable_current keeps the row non-fatal and reports it as such:
        # `current` cannot be read from this repo at all.
        "unreadable_current": True,
        "notes": (
            "current is UNREADABLE here — the tag+digest lives in weisssrv-lib "
            "ci/review/pr-agent.yml. Compare Latest against that file, bump it "
            "there, tag, then move WEISSSRV_LIB_REF."
        ),
        "source_url": "https://github.com/qodo-ai/pr-agent/releases",
    },
    {
        # PY_JOB_IMAGE: the digest-pinned image of the jobs that hold vault
        # credentials. `current` is the tag of the first python job image in
        # .gitlab-ci.yml, which the variable tracks.
        "name": "python (CI job image)",
        "var_name": "py_job_image_version",
        "category": "dockerhub",
        "docker_image": "library/python",
        "image_ref": "python",
        "tag_regex": r"^(3\.\d+)-slim$",
        "dockerhub_name_filter": "-slim",
        "version_file": ".gitlab-ci.yml",
        "held": True,
        "notes": (
            "Held at 3.13, the minor the library job images run and every "
            "python job here is proven on. It is also above ansible 14's "
            ">= 3.12 floor, which the python-tests pip install needs. Unholding "
            "is a pipeline-wide move and bumps the tag AND re-pins "
            "PY_JOB_IMAGE's @sha256 together in the .gitlab-ci.yml variables "
            "block."
        ),
        "source_url": "https://hub.docker.com/_/python",
    },
    {
        # TF_JOB_IMAGE: tag and digest move together in the .gitlab-ci.yml
        # variables block.
        "name": "Terraform (CI job image)",
        "var_name": "tf_job_image_version",
        "category": "dockerhub",
        "docker_image": "hashicorp/terraform",
        "image_ref": "hashicorp/terraform",
        "tag_regex": r"^(\d+\.\d+\.\d+)$",
        "version_file": ".gitlab-ci.yml",
        "notes": (
            "Bump the tag AND re-pin the @sha256 of TF_JOB_IMAGE together; the "
            "terraform roots' required_version floor moves with it."
        ),
        "source_url": "https://hub.docker.com/r/hashicorp/terraform/tags",
    },
    {
        # KUSTOMIZE_VERSION in the .gitlab-ci.yml variables block; the flux-lint
        # include repeats it as a literal input and ci-pin-parity holds the two
        # equal, so the pin is read once here.
        "name": "kustomize (CI tooling)",
        "var_name": "kustomize_version",
        "category": "github",
        "github_repo": "kubernetes-sigs/kustomize",
        "version_prefix": "kustomize/v",
        "strip_prefix": True,
        "tag_filter": r"^kustomize/v\d+\.\d+\.\d+$",
        "version_file": "ci",
        "pin_regex": r'^\s*KUSTOMIZE_VERSION:\s*"([^"]+)"',
        "notes": (
            "Bump KUSTOMIZE_VERSION with KUSTOMIZE_SHA256, and the flux-lint "
            "include's kustomize_version/kustomize_sha256 inputs with them."
        ),
    },
    {
        # Spelled only as the flux-lint include's kubeconform_version input.
        "name": "kubeconform (CI tooling)",
        "var_name": "kubeconform_version",
        "category": "github",
        "github_repo": "yannh/kubeconform",
        "version_prefix": "v",
        "strip_prefix": True,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
        "version_file": "ci",
        "pin_regex": r'^\s*kubeconform_version:\s*"([^"]+)"',
        "notes": "Bump kubeconform_version with kubeconform_sha256 on the flux-lint include.",
    },
    {
        # Spelled only as the flux-lint include's helm_version input. The tag
        # filter keeps the bot on the 3.x line: helm 4 is a migration, not a bump.
        "name": "Helm (CI tooling)",
        "var_name": "helm_version",
        "category": "github",
        "github_repo": "helm/helm",
        "version_prefix": "v",
        "strip_prefix": True,
        "tag_filter": r"^v3\.\d+\.\d+$",
        "version_file": "ci",
        "pin_regex": r'^\s*helm_version:\s*"([^"]+)"',
        "notes": "Bump helm_version with helm_sha256 on the flux-lint include.",
    },
    {
        # ANSIBLE_VERSION: the community `ansible` package the deploy jobs pip
        # install. PyPI is its only feed, so the check is manual.
        "name": "ansible (CI control node)",
        "var_name": "ansible_version",
        "category": "manual",
        "version_file": "ci",
        "pin_regex": r'^\s*ANSIBLE_VERSION:\s*"([^"]+)"',
        "source_url": "https://pypi.org/project/ansible/#history",
        "notes": (
            "requirements.txt pins the same version for host-side gates and the "
            "deploy-base include repeats it as a literal input; move all three "
            "together, and the collection's requires_ansible floor allows it."
        ),
    },
    {
        # The privileged dind service of .build-image-base, spelled as the
        # docker-dind include's dind_service input. Its embedded BuildKit frontend
        # accepts upstream hermes' symbolic `COPY --chmod`, which 27.x rejects.
        "name": "docker (dind service)",
        "var_name": "docker_dind_version",
        "category": "dockerhub",
        "docker_image": "library/docker",
        "tag_regex": r"^(\d+\.\d+(?:\.\d+)?-dind)$",
        "dockerhub_name_filter": "-dind",
        "version_file": "ci",
        "pin_regex": r'^\s*dind_service:\s*"docker:([\w.+-]+)@sha256:',
        "held": True,
        "notes": (
            "Held at 24.0-dind for the BuildKit frontend .build-image-base "
            "needs; the tag and its @sha256 move together. The integration jobs "
            "carry their own newer dind pin in .gitlab/ci/integration-jobs.yml."
        ),
    },
    # Manifest-pinned container images: tag+digest `image:` pins living in
    # kubernetes/ manifests with no ${...} substitution. version_file names
    # every manifest carrying the pin.
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
            "kubernetes/apps/hindsight-reaper/cronjob.yaml",
        ],
        "source_url": "https://hub.docker.com/_/python",
        "notes": "Three CronJobs share one tag+digest pin; bump them together.",
    },
    {
        # No upstream releases or tags: versions are cut only to the Fedora ISO
        # archive, so this is a manual check. A bump also recomputes
        # proxmox_vm_virtio_win_checksum, since Fedora ships none (docs/39).
        "name": "virtio-win",
        "var_name": "proxmox_vm_virtio_win_version",
        "category": "manual",
        "source_url": "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/",
        "notes": "VirtIO driver ISO for the Windows 11 VM; no GitHub releases — check the Fedora stable-virtio dir, and proxmox_vm_virtio_win_checksum is the sha256 of the ISO you pick.",
        "coupled_vars": ["proxmox_vm_virtio_win_checksum"],
    },
    {
        # Debian cloud image for proxmox_vm guests. `latest/` moves and its
        # SHA512SUMS moves with it, so the site pins a dated directory. No
        # release feed; read the dated index at the source_url.
        "name": "Debian cloud image",
        "var_name": "proxmox_vm_cloud_image_url",
        "category": "manual",
        "source_url": "https://cloud.debian.org/images/cloud/trixie/",
        "notes": "Pick the newest dated directory, then bump proxmox_vm_cloud_image_url, _name and _checksum in all.yml together — the sha512 comes from that directory's SHA512SUMS.",
    },
    {
        # Proxmox silently rotates the point build out of its pveam index,
        # which breaks a cached-template recreate. No feed to poll: check the
        # index on a Proxmox host. The pin in all.yml is authoritative.
        "name": "Debian LXC template (pveam)",
        "var_name": "proxmox_lxc_template",
        "category": "manual",
        "source_url": "http://download.proxmox.com/images/system/",
        "notes": "Debian LXC root template; no release feed — run `pveam update && pveam available --section system | grep debian-13-standard` on a Proxmox host, then bump proxmox_lxc_template in all.yml + the proxmox_lxc role default together.",
    },
    {
        # postgres_exporter sidecar in the immich + nextcloud compose stacks.
        # The k8s mealie sidecar pins the same exporter separately (own entry
        # below).
        "name": "postgres_exporter",
        "var_name": "postgres_exporter_version",
        "category": "github",
        "github_repo": "prometheus-community/postgres_exporter",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
    },
    {
        # postgres_exporter sidecar in the mealie-postgres pod (tag+digest,
        # hand-edited). Same upstream as the all.yml postgres_exporter entry
        # used by the immich/nextcloud compose stacks — bump both together.
        "name": "postgres_exporter (mealie sidecar)",
        "var_name": "postgres_exporter_k8s_version",
        "category": "github",
        "github_repo": "prometheus-community/postgres_exporter",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d+\.\d+\.\d+$",
        "image_ref": "quay.io/prometheuscommunity/postgres-exporter",
        "version_file": "kubernetes/apps/recipes/mealie.yaml",
        "source_url": "https://github.com/prometheus-community/postgres_exporter/releases",
    },
    {
        # Vim plugin manager cloned at a tag by the qol role. Upstream is
        # archived, so the pin is final and `manual` makes no network call.
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
    "uptime_kuma_version",
    "hermes_version", "hermes_codex_version", "hermes_claude_version",
    "hermes_op_version", "hermes_camofox_version",
    "hindsight_version", "hindsight_llamacpp_version",
    "mealie_version", "mealie_postgresql_version",
    "bar_assistant_version", "salt_rim_version",
    "meilisearch_version", "redis_version", "busybox_version",
    "authentik_version", "postgresql_version",
    "gitlab_runner_helm_version", "gitlab_agent_helm_version",
    "registry_cache_version", "garage_version",
    "exportarr_version", "proxmox_exporter_version",
    "adguard_exporter_version", "redis_exporter_version",
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
    # Host-side exporters: they ship in the versions ConfigMap but no manifest
    # reads them, so the rollout is the playbook that installs the binary.
    "zfs_exporter_version": "task storage:deploy",
    "unbound_exporter_version": "task dns:deploy",
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
    # Only changes which image a NEWLY created VM downloads; existing guests
    # keep their disks.
    "proxmox_vm_cloud_image_url": (
        "applies on the next proxmox_vm create; no rollout needed"
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
    # Short names for `version_file` paths outside the vars file.
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
        # Pinned with its .deb sha256 in all.yml; bumped as a pair by hand.
        "restic_offsite_rclone_version",
    ],
    "services": [
        dict(svc, **({"deploy_command": cmd} if (cmd := _deploy_command(svc)) else {}))
        for svc in _SERVICES
    ],
}
