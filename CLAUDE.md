# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**weisssrv** - Homelab Infrastructure as Code

Complete GitOps repository for a Proxmox-based homelab using Ansible, Terraform, and Kubernetes.

## Repository Structure

**Canonical source**: https://git.ericsweiss.com/eric/weisssrv (GitLab)
**GitHub mirror**: https://github.com/ericsweiss/weisssrv (read-only)

```
weisssrv/
├── ansible/                    # Configuration management
│   ├── inventories/prod/       # Production inventory + vars
│   ├── roles/                  # 28 roles for all services
│   └── playbooks/              # Deployment playbooks
├── terraform/cloudflare/       # External DNS management
├── kubernetes/                 # Flux-managed k8s state (GitOps source of truth)
│   ├── clusters/weisssrv/      # Flux entrypoint (flux-system, infrastructure-{sources,controllers,configs,observability}.yaml, apps.yaml, tenants/)
│   ├── infrastructure/         # Platform — four sibling stages reconciled in dependsOn order; together with apps/ they form a five-stage Flux Kustomization chain (sources -> controllers -> configs -> observability -> apps)
│   │   ├── sources/            # HelmRepository CRs + versions-configmap.yaml (runs first, no deps)
│   │   ├── controllers/        # external-secrets, onepassword-connect, metallb, cert-manager, traefik, external-dns, vpa (HelmReleases; dependsOn sources)
│   │   ├── configs/            # cluster-secret-store, cluster-issuer, metallb-ip-pools, wildcard-certificates, coredns/, cloudflare-ddns/, shared-cloudflare-secrets/ (CRs that require the controllers' CRDs; dependsOn controllers)
│   │   └── observability/      # kube-prometheus-stack, loki, alloy, exporters, service-monitors, dashboards, ingress (dependsOn configs)
│   └── apps/                   # authentik, download-clients, recipes, gitlab-runner, gitlab-runner-privileged, gitlab-agent, vm-ingress (each with release.yaml + externalsecret.yaml; dependsOn infrastructure-observability)
├── docs/                       # Documentation
├── scripts/                    # Utility scripts
├── docker/                     # Molecule test/CI container images
├── .gitlab-ci.yml              # CI/CD pipeline (canonical)
└── .github/workflows/          # Legacy workflows (disabled)
```

## Architecture

### Current Infrastructure (Base Parity)

- **6 Proxmox Hosts** (cluster name: weisssrv):
  - pve-nas-01 (192.168.0.102) - NAS + storage
  - pve-laptop-01 (192.168.0.103) - Compute
  - pve-opt-01 (192.168.0.104) - Compute
  - pve-opt-02 (192.168.0.105) - Compute
  - pve-opt-03 (192.168.0.106) - Compute
  - pve-prec-01 (192.168.0.107) - Compute
- **NAS Storage**: ZFS (tank/ssd/nvme/archive pools), NFS, Samba
- **DNS**: 2x AdGuard Home + Unbound (DoT) - 192.168.0.150/160
- **SMTP**: Relay via Gmail - 192.168.0.151
- **Certs**: acme.sh with Cloudflare DNS-01
- **VPN**: Tailscale on Proxmox hosts (remote access)
- **Firewall**: Proxmox firewall with IP Sets + Security Groups
- **HA**: Proxmox HA for infrastructure services (DNS, SMTP, Home Assistant)
- **GitOps**: Flux CD reconciles all Kubernetes state from `kubernetes/` on every push
- **Secrets to k8s**: External Secrets Operator (ESO) with 1Password Connect provider (`ClusterSecretStore` `onepassword-homelab`)

### K3s Platform

**9-node cluster** (3 servers + 6 agents):

**Server Nodes** (etcd quorum):
- **k3s-srv-nas-01** (192.168.0.222) - Server on pve-nas-01
- **k3s-srv-laptop-01** (192.168.0.223) - Server on pve-laptop-01
- **k3s-srv-prec-01** (192.168.0.227) - Server on pve-prec-01

**Agent Nodes**:
- **k3s-agt-nas-01** (.202) - NAS workloads (esweiss.com/nas)
- **k3s-agt-laptop-01** (.203) - Ingress + general
- **k3s-agt-opt-01** (.204) - Ingress + general
- **k3s-agt-opt-02** (.205) - Ingress + general
- **k3s-agt-opt-03** (.206) - Ingress + general
- **k3s-agt-prec-01** (.207) - General + compute (esweiss.com/compute)

**Deployment Model** (Two-phase approach):
1. **Ansible** (`task k3s:deploy`): VMs, k3s, kube-vip (API VIP .161). One-off and idempotent.
2. **Flux GitOps**: Everything in-cluster (platform controllers + apps) is reconciled by Flux from `kubernetes/` on every push to `main`. Local iteration uses `task flux:dev-apply -- <path>` (changes are reverted on next reconcile cycle unless committed).

Ansible tasks remain idempotent - safe to re-run. Flux reconciles automatically within ~1 minute of a push; use `task flux:reconcile` to force immediately. See `docs/19-k3s-deployment.md` for the k3s layer and `docs/29-flux-operations.md` for Flux day-2 operations.

**Features**:
- kube-vip (API VIP .161), MetalLB (VIPs .100/.101)
- Traefik ingress, external-dns (Cloudflare)
- 3-node etcd quorum (tolerates 1 server failure)
- Flux CD (source-controller, kustomize-controller, helm-controller, notification-controller) + External Secrets Operator with 1Password Connect backend
- Observability stack: Prometheus + Grafana + Loki + Alloy (metrics, logs, dashboards, alerting); k3s-irrelevant components disabled (kubeProxy, kubeScheduler, kubeControllerManager, kubeEtcd); alertmanager config uses ExternalSecret template for webhook injection
- Autoscaling: VPA (Auto/Initial/Off tiers per workload class) + CoreDNS HPA pin — see docs/33-autoscaling.md

**Applications** (deployment details live in the per-app docs):
- Authentik SSO (auth.esweiss.com) — identity provider for OIDC/SAML; PostgreSQL on a dedicated zvol
- Plex Media Server (plex.esweiss.com) — LXC container with Traefik ingress (docs/20)
- Download/media stack, `downloads` namespace (docs/21): Gluetun VPN gateway with killswitch, NZBGet (nzbget.\*), qBittorrent (qbittorrent.\*), Prowlarr (prowlarr.\*), Sonarr (tv.\*), Radarr (movies.\*), Lidarr (music.\*), Pulsarr (pulsarr.\*, NAS-pinned/AVX)
- Recipes stack, `recipes` namespace (docs/22-23): Mealie (food.\*, PostgreSQL on zvol, OpenAI parsing), Bar Assistant (bar.\*); both behind Authentik OIDC
- Home Assistant (home.esweiss.com / home.ericsweiss.com, docs/24): HAOS VM (.154, Proxmox-HA-managed), websocket ingress, hass-openid SSO, \*arr API bypass routes, read-only NFS media mount
- GitLab (git.esweiss.com / git.ericsweiss.com, docs/27): EE omnibus VM on pve-nas-01 (.153), repos on 200GB zvol, Container Registry, Pages, Web IDE extension host (CVE-2026-5816 SOP isolation), k3s CI runners (infrastructure + shared), SAML SSO, SSH 22/2222
- Grafana (grafana.esweiss.com, docs/31): community + custom dashboards via `grafana_dashboard` ConfigMap sidecar, Authentik OIDC, Loki datasource

**Planned** (not yet created):
- Apps: Immich, Nextcloud
- `weisssrv-project-template` GitLab template project for tenant-side scaffold — does not exist yet; the multi-repo onboarding flow in `docs/30-multi-repo-onboarding.md` depends on it (tracked in `docs/16-next-steps.md`)

## Common Development Commands

### Task Runner

All operations use `Taskfile.yml`. Run `task --list` for the full, current set
(grouped by namespace: `ansible:*`, `infra:*`, `dns:*`, `storage:*`, `plex:*`,
`proxmox:*`, `zfs:*`, `k3s:*`, `flux:*`, `downloads:*`,
`recipes:*`, `authentik:*`, `observability:*`, `home-assistant:*`, `gitlab:*`,
`maintenance:*`, `terraform:*`, plus top-level `lint` and `collect-state`). The
Taskfile is the source of truth — do not maintain a copy of the task list here.

Workflow facts an agent must know (not obvious from `task --list`):

- **Two lifecycles.** Base infra + the k3s layer are deployed by Ansible
  (`task infra:*`, `task k3s:*`); everything *inside* the cluster (platform
  controllers + apps) is reconciled by **Flux** from `kubernetes/` on every push
  to `main`. There is no `kubectl apply` / `helm upgrade` in the normal flow.
- **All Ansible tasks are idempotent** — safe to re-run.
- **Cluster Helm/image versions** live in
  `ansible/inventories/prod/group_vars/all.yml`; after editing, run
  `task flux:sync-versions`, then commit + push so Flux reconciles them.
- **Local Flux iteration**: `task flux:dev-apply -- <path>` previews a change
  in-cluster but is reverted on the next reconcile unless committed.
- `task lint` runs everything (Ansible, Terraform, `flux:lint`, Python scripts);
  `task kubernetes:lint` / `kubernetes:validate-helm` are aliases for `flux:lint`.

### Manual Ansible

```bash
# Install collections
ansible-galaxy install -r ansible/requirements.yml

# Ping all hosts
ansible all -m ping

# Dry-run deployment
ansible-playbook ansible/playbooks/site.yml --check

# Deploy to specific host
ansible-playbook ansible/playbooks/site.yml --limit pve-nas-01

# Deploy specific role
ansible-playbook ansible/playbooks/base.yml --tags ssh
```

### Manual Terraform

> **Prefer `task terraform:*` commands** -- they handle Cloudflare API credentials and GitLab HTTP state backend auth automatically via `op run`. Manual commands require exporting `TF_VAR_cloudflare_api_token`, `TF_VAR_cloudflare_account_id`, and `TF_HTTP_*` env vars.

```bash
cd terraform/cloudflare
terraform init
terraform plan
terraform apply
```

## Secrets Management (1Password)

Two consumers pull from the same 1Password "Homelab" vault:

1. **Ansible / Terraform / Task wrapper** — uses `op run --` to inject `op://Homelab/Item/field` references at runtime.
   ```yaml
   # Format in group_vars/all.yml
   secrets:
     smtp_gmail_password: "op://Homelab/SMTP Relay Gmail/password"
     # Item names with spaces are fine here — `op run` parses the full path.
   ```

2. **External Secrets Operator in the cluster** — the `onepassword-homelab` ClusterSecretStore (namespace `external-secrets`, 1Password Connect provider) syncs `ExternalSecret` resources into Kubernetes `Secret`s. Connect runs in-cluster (no calls to 1Password cloud). `ExternalSecret.spec.data[].remoteRef.key` is the **1Password item title**, and `remoteRef.property` is the **field name**:
   ```yaml
   remoteRef:
     key: <1Password item title>
     property: <field name>
   ```

3. **CI pipelines** — `.gitlab-ci.yml` uses `op run` / `op read` with `OP_SERVICE_ACCOUNT_TOKEN` to inject secrets at runtime. This is separate from Connect and unchanged by the migration.

The bootstrap Secrets `op-credentials` and `onepassword-connect-token` in the `external-secrets` namespace are the **only manually created** Kubernetes Secrets. Every other in-cluster Secret is produced by ESO from `ExternalSecret` manifests reconciled by Flux.

```bash
# Create Connect server (generates 1password-credentials.json in current dir)
op connect server create weisssrv-connect --vaults Homelab

# Create access token
op connect token create weisssrv-eso --server <server-id> --vaults Homelab

# Create bootstrap secrets in cluster
kubectl -n external-secrets create secret generic op-credentials \
  --from-file=1password-credentials.json=./1password-credentials.json
kubectl -n external-secrets create secret generic onepassword-connect-token \
  --from-literal=token=<TOKEN>
```

**NEVER commit secrets to git**. All sensitive values use 1Password references (`op://` for host-side tooling, item titles in ExternalSecrets for in-cluster).

### Required 1Password Items

The canonical, authoritative inventory of every item the deployment expects in
the **Homelab** vault lives in **`docs/15-credential-rotation.md`** under
"Required 1Password Items". Add or update items there, not here.

### Using 1Password

```bash
# Sign in
eval $(op signin)

# Read a secret
op read "op://Homelab/SMTP Relay Gmail/password"

# Inject into environment
export CLOUDFLARE_API_TOKEN=$(op read "op://Homelab/Cloudflare DNS Token/credential")
```

## DNS Architecture (Important!)

**Split-horizon DNS**:
- **Internal** (`*.esweiss.com`): AdGuard Home rewrites → internal IPs
- **External** (`*.ericsweiss.com`): Cloudflare (Terraform) → public/VIP IPs

**Current DNS servers**:
- Primary: 192.168.0.150 (dns-01)
- Secondary: 192.168.0.160 (dns-02)
- Upstream: Unbound on 127.0.0.1:5335 (DoT to Cloudflare/Google)

## Network / IP Allocation

Quick reference (full host-by-host topology in `docs/01-overview.md`):

- Proxmox hosts `.102-.107`; DNS `.150`/`.160`; SMTP `.151`; services `.152-.155`.
- K3s: API VIP `.161`; servers `.222`/`.223`/`.227`; agents `.202-.207`;
  MetalLB VIPs `.100` (public) / `.101` (internal).

Firewall IP sets and security groups (`admin_lan`, `admin_ts`, `core-cluster`,
`k3s_nodes`, `pve_hosts`, `nfs_clients`, `smb_clients`) are documented in
`docs/11-firewall.md` and rendered from `ansible/roles/proxmox_firewall/`.

## Ansible Roles

The 28 roles and their one-line purposes are listed in the **Ansible Roles**
table in `README.md` (source of truth). A few carry editing constraints worth
knowing up front:

- **alloy_host** ships journald logs to Loki via the internal Traefik
  IngressRoute (`https://loki.esweiss.com`); it does NOT duplicate the in-cluster
  DaemonSet (which covers container logs only). NodePort `:31100` is an emergency
  fallback (`alloy_host_loki_url`).
- **prometheus_exporter** is the shared download-install pipeline (probe →
  conditional download → install → enable/start → health) for `tarball` and
  `deb` artifacts. **zfs_exporter** and **unbound_exporter** are thin wrappers
  that pass their specifics as `vars` and keep their own `*.service.j2` unit +
  notify the shared `Restart prometheus exporter` handler. A change to the
  shared role redeploys both consumers (storage + dns deploy jobs).
- **node_exporter_host** binds port **9101** to avoid colliding with the k3s
  node-exporter DaemonSet on 9100. It is deliberately NOT a prometheus_exporter
  wrapper — it installs from the apt repo and uses a drop-in override plus
  bespoke textfile collectors (see that role's README).
- **zfs_encryption** fetches each pool's passphrase from 1Password Connect at
  boot via `zfs-load-key@<pool>` ordered before `zfs-mount.service`. It is the
  sole consumer of the shared Connect token at `/etc/onepassword-connect/token`,
  gated on `zfs_encryption_pools` (no token on compute hosts with empty lists).
  Runbooks in `docs/32-zfs-encryption.md`.
- **nfs_tls** (`nfs_tls_enabled`) runs tlshd on `pve-nas-01` + every k3s
  agent. The `nas_storage` k3s export lines require TLS (`xprtsec: tls`,
  plaintext rejected); the k3s NFS PVs *mount* with `xprtsec=tls` — **by
  hostname** (`server: pve-nas-01.esweiss.com`, since the `*.esweiss.com` cert
  has no IP SAN; an IP mount fails the handshake). HAOS (.154) and the Proxmox
  `tank-proxmox` target are documented plaintext exceptions (docs/24, docs/16).
- **resolv_conf** / **zvol_mount** are shared helper roles (used by base/adguard
  and k3s/gitlab respectively).

## User Management

- **Proxmox hosts**: User `eric` with passwordless sudo
- **LXC containers**: User `eric` with passwordless sudo
- **VMs**: User `eric` via cloud-init
- **Services**: Run as dedicated users (adguard, unbound, plex; postfix runs as root)

All hosts use `eric` for SSH access with passwordless sudo. LXC containers are unprivileged (mapped UIDs for security). On smtp-relay SSH access is via `eric`, but Postfix itself runs as root (normal for mail servers).

## Testing / Deployment Workflow

### Base infrastructure (Ansible/Terraform)

1. **Pre-deployment**:
   ```bash
   task ansible:ping          # Verify connectivity
   task lint                  # Ansible + Terraform + flux:lint + scripts:test (all in one)
   task infra:check           # Dry-run
   ```

2. **Deploy**:
   ```bash
   task infra:deploy          # Full stack
   # Or target specific hosts/roles
   ansible-playbook ansible/playbooks/base.yml --limit pve-nas-01
   ```

3. **Post-deployment**:
   ```bash
   task infra:verify          # Post-deployment verification
   task collect-state         # Snapshot current state
   ```

### Kubernetes workloads (Flux GitOps)

Everything in `kubernetes/` is reconciled by Flux. There is no `kubectl apply` or `helm upgrade` step in the normal workflow — edit YAML, commit, push:

1. **Pre-change**:
   ```bash
   task flux:lint             # kustomize build + kubeconform on every Flux Kustomization
   task flux:dev-apply -- kubernetes/apps/<app>   # Optional: preview change in-cluster (reverted on next reconcile)
   ```

2. **Ship**:
   ```bash
   git add kubernetes/...
   git commit
   git push                   # Flux polls every ~1 min; planned webhook will make this sub-second
   task flux:reconcile        # Optional: force immediate reconcile
   ```

3. **Verify**:
   ```bash
   task flux:status           # Concise health summary
   task flux:verify           # `flux check` + all managed resources
   ```

See `docs/29-flux-operations.md` for day-2 operations including secret rotation, suspend/resume, and webhook setup. Multi-repo tenant onboarding is covered in `docs/30-multi-repo-onboarding.md`.

## Version Management

Application versions are centralized in `ansible/inventories/prod/group_vars/all.yml`. See that file for current version pins — they include base infrastructure (k3s, kube-vip, Authentik, Plex, GitLab), Helm charts (MetalLB, Traefik, cert-manager, external-dns), download clients (Gluetun, NZBGet, qBittorrent, Prowlarr, Sonarr, Radarr, Lidarr, Pulsarr), recipe stack (Mealie, Bar Assistant, Salt Rim), and PostgreSQL versions. Home Assistant (HAOS) is updated manually via its UI and is not version-pinned in all.yml.

**Automated version discovery** (`scripts/check-versions.py`):
- Checks every managed service across GitHub releases, Docker Hub, LinuxServer.io, and Helm repos (registry in `SERVICE_REGISTRY`)
- Run `task maintenance:check-versions` to see available updates
- Run `task maintenance:update-version SERVICE=<name>` to update a single version in all.yml
- Run `task maintenance:update-all-versions` to update all outdated versions
- Results cached for 1 hour in `.version-cache/`; set `GITHUB_TOKEN` for higher API rate limits

**Update strategy:**
1. **Check for updates:** `task maintenance:check-versions`
2. **Update versions in all.yml:** `task maintenance:update-version SERVICE=<name>` or `task maintenance:update-all-versions`
3. **Deploy:** Run appropriate task (see `docs/12-runbooks.md` for update workflow)

**Version pinning philosophy:**
- k3s, Authentik, Helm charts: Pinned to specific versions for stability
- Download/recipe containers: Pinned to specific stable tags (no "latest") for reproducible deployments
- Bar Assistant / Salt Rim: Pinned to specific versions (check for breaking changes on major bumps)
- Tailscale: Pinned to specific apt version
- Plex: Pinned to specific apt version (set to "latest" for auto-update behavior)
- Home Assistant: Manual updates via HAOS UI (documented version only)

## Storage Architecture

Full pool topology, creation commands, and design rationale (storage-tier
selection, lz4-vs-zstd) live in `docs/06-zfs.md`. Quick reference:

**Automated Storage Selection**: `proxmox_vm` / `proxmox_lxc` pick storage
from the host's `proxmox_role` — `nas` → `ssd`, `compute`/`general` →
`local-ssd` — overridable per-guest via `proxmox_storage` / `lxc_storage`.

### NAS Node (pve-nas-01) - Specialized ZFS Pools

**ZFS Pools**:
- `tank` - 6x 22TB raidz2 (~88TB usable, 122TB raw) - Media and bulk storage
- `ssd` - 3x 4TB raidz1 (~10.9TB) - App data, databases, and containers
- `nvme` - 1x 4TB NVMe (~2.27TB) - Hot downloads and fast scratch
- `archive` - 4x 6TB raidz1 (~21.8TB) - Cold storage and backups

**Key Datasets**: tank/media, tank/share, ssd/appdata, nvme/media, nvme/fast

**Persistent Storage (ZFS zvols)**:
- `ssd/appdata/authentik/postgres` - 10GB zvol, ext4, attached to k3s-agt-nas-01 as /dev/sdb, mounted at /mnt/postgres-data
- `ssd/appdata/mealie/postgres` - 32GB zvol, ext4, attached to k3s-agt-nas-01 as /dev/sdc, mounted at /mnt/mealie-postgres-data
- `ssd/appdata/gitlab/repos` - 200GB zvol, ext4, attached to gitlab VM as /dev/sdb, mounted at /mnt/gitlab-repos
- `ssd/appdata/prometheus/data` - 150GB zvol, ext4, attached to k3s-agt-nas-01, mounted at /mnt/prometheus-data
- `ssd/appdata/loki/data` - 75GB zvol, ext4, attached to k3s-agt-nas-01, mounted at /mnt/loki-data
- Grafana SQLite DB uses NFS-backed PV at `/appdata/grafana` (1Gi, NFS from pve-nas-01) — persists user preferences and service accounts
- Zvols are defined in `vm_additional_disks` in hosts.yml, created by proxmox_vm role, formatted/mounted by role
- Data survives pod and VM recreation (zvols persist on Proxmox host's ZFS pool)

### Compute Nodes - local-ssd ZFS Pool

All five compute nodes carry a 1TB `local-ssd` pool (lz4, ashift=12,
atime=off, autotrim=on) for k3s VMs and the HA-managed containers
(dns-01/02, smtp-relay, home-assistant). Rationale in docs/06.

### Resource Pools and Storage Management

**Resource Pools**: infra-core (dns, smtp), apps-public (plex), platform (k3s VMs)

**NEVER create/destroy ZFS pools via Ansible** - pools are created manually (too critical to automate). Ansible only sets properties and mounts. Zvols for persistent storage are managed via `vm_additional_disks` but the parent pools are never touched.

## Documentation

The full, numbered `docs/00`–`docs/33` index (grouped Getting Started /
Infrastructure Services / Operations and Planning) lives in the **Documentation**
section of `README.md`. Browse `docs/` directly or that table — do not maintain a
second copy of the index here. Per-area pointers already appear inline in the
relevant sections above.

## Important Context Files

- `CLUSTER_STATUS.txt` - Full cluster state snapshot (gitignored; generated locally via `task collect-state`)

## Credits

Repository structure inspired by [FreekingDean/homelab](https://github.com/FreekingDean/homelab)
