# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**weisssrv** - Homelab Infrastructure as Code

Complete GitOps repository for a Proxmox-based homelab using Ansible, Terraform, and Kubernetes.

## Agents: start here

Before making ANY change in this repo, invoke the `weisssrv-development` skill
(Skill tool) and follow it — it carries the repo's workflow, pre-MR gates, and a
change-type decision tree pointing at every canonical doc, so the guardrails
below are applied consistently. The skill lives at
`.claude/skills/weisssrv-development/`.

## Repository Structure

**Canonical source**: https://git.ericsweiss.com/eric/weisssrv (GitLab)
**GitHub mirror**: https://github.com/ericsweiss/weisssrv (read-only)

```
weisssrv/
├── ansible/                    # Configuration management
│   ├── inventories/prod/       # Production inventory + vars
│   ├── roles/                  # one role per service — see the README roles table
│   └── playbooks/              # Deployment playbooks
├── terraform/
│   ├── cloudflare/             # External DNS management
│   └── tailscale/              # Tailnet ACL policy-as-code
├── kubernetes/                 # Flux-managed k8s state (GitOps source of truth)
│   ├── clusters/weisssrv/      # Flux entrypoint (flux-system, infrastructure-{sources,controllers,configs,observability}.yaml, apps.yaml, tenants/)
│   ├── infrastructure/         # Platform — four sibling stages reconciled in dependsOn order (sources -> controllers -> configs, which fans out to observability and apps in parallel)
│   │   ├── sources/            # HelmRepository CRs + versions-configmap.yaml (runs first, no deps)
│   │   ├── controllers/        # platform HelmReleases (dependsOn sources) — see the dir for the current set
│   │   ├── configs/            # CRs that require the controllers' CRDs (dependsOn controllers) — see the dir for the current set
│   │   └── observability/      # kube-prometheus-stack, loki, alloy, exporters, dashboards, ingress (dependsOn configs) — see the dir
│   └── apps/                   # one dir per app — either a HelmRelease (release.yaml) or raw Deployments/CRs, plus an externalsecret.yaml where the app needs ESO secrets (dependsOn infrastructure-configs — parallel to observability) — see the dir for the current set
├── docs/                       # Documentation
├── scripts/                    # Utility scripts
├── docker/                     # Molecule test/CI container images
├── .gitlab-ci.yml              # CI/CD pipeline (canonical)
└── .github/workflows/          # Single inert stub (ci-disabled.yml) — CI runs on GitLab
```

## Architecture

### Current Infrastructure (Base Parity)

- **6 Proxmox Hosts** (cluster name: weisssrv) — `.102`-`.107` (pve-nas-01 is NAS + storage, the rest are compute); host-by-host list in the README architecture diagram and `docs/01-overview.md`
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

**9-node cluster** (3 servers + 6 agents): three server nodes form the etcd
quorum; the six agents carry NAS, ingress, and compute workloads. Node-by-node
list with IPs and host placement lives in the README architecture diagram and
`docs/01-overview.md`.

**Deployment Model** (Two-phase approach):
1. **Ansible** (`task k3s:deploy`): VMs, k3s, kube-vip (API VIP .161). One-off and idempotent.
2. **Flux GitOps**: Everything in-cluster (platform controllers + apps) is reconciled by Flux from `kubernetes/` on every push to `main`. Local iteration uses `task flux:dev-apply -- <path>` (changes are reverted on next reconcile cycle unless committed).

Ansible tasks remain idempotent - safe to re-run. Flux reconciliation is push-triggered via the GitLab agent's Flux module (the ~1-minute git poll is the fallback); use `task flux:reconcile` to force immediately. See `docs/19-k3s-deployment.md` for the k3s layer and `docs/29-flux-operations.md` for Flux day-2 operations.

**Features**:
- kube-vip (API VIP .161), MetalLB (VIPs .100/.101)
- Traefik ingress, external-dns (Cloudflare)
- 3-node etcd quorum (tolerates 1 server failure)
- Flux CD (source-controller, kustomize-controller, helm-controller, notification-controller) + External Secrets Operator with 1Password Connect backend
- Observability stack: Prometheus + Grafana + Loki + Alloy (metrics, logs, dashboards, alerting); k3s-irrelevant components disabled (kubeProxy, kubeScheduler, kubeControllerManager); alertmanager config uses ExternalSecret template for webhook injection
- Autoscaling: VPA (Auto/Initial/Off tiers per workload class) + CoreDNS HPA pin — see docs/33-autoscaling.md
- Node/workload ops: kured (coordinated reboots), Reloader (restarts on ConfigMap changes only — Secrets are intentionally excluded via `ignoreSecrets: true`; a rare credential-Secret rotation is a manual restart) — full controller set in `kubernetes/infrastructure/controllers/kustomization.yaml`

**Applications** (deployment details live in the per-app docs):
- Authentik SSO (auth.esweiss.com) — identity provider for OIDC/SAML; PostgreSQL on a dedicated zvol
- Plex Media Server (plex.esweiss.com) — LXC container with Traefik ingress (docs/20)
- Download/media stack, `downloads` namespace (docs/21): Gluetun VPN gateway with killswitch, NZBGet (nzbget.\*), qBittorrent (qbittorrent.\*), Prowlarr (prowlarr.\*), Sonarr (tv.\*), Radarr (movies.\*), Lidarr (music.\*), Pulsarr (pulsarr.\*, NAS-pinned/AVX)
- Recipes stack, `recipes` namespace (docs/22-23): Mealie (food.\*, PostgreSQL on zvol, OpenAI parsing), Bar Assistant (bar.\*); both behind Authentik OIDC
- Home Assistant (home.esweiss.com / home.ericsweiss.com, docs/24): HAOS VM (.154, Proxmox-HA-managed), websocket ingress, hass-openid SSO, full-host SSO bypass routes for the \*arr integrations scoped to HA's IP, read-only NFS media mount
- GitLab (git.esweiss.com / git.ericsweiss.com, docs/27): EE omnibus VM on pve-nas-01 (.153), repos on 200GB zvol, Container Registry, Pages, Web IDE extension host (CVE-2026-5816 SOP isolation), k3s CI runners (infrastructure + shared), SAML SSO, SSH 22/2222
- Grafana (grafana.esweiss.com, docs/31): community + custom dashboards via `grafana_dashboard` ConfigMap sidecar, Authentik OIDC, Loki datasource

**Planned** (not yet created) — roadmap source of truth is `docs/16-next-steps.md`:
- Apps: Immich, Nextcloud

## Common Development Commands

### Task Runner

All operations use `Taskfile.yml`. Run `task --list` for the full, current set
(grouped by namespace). The Taskfile is the source of truth — do not maintain
a copy of the task list here.

Workflow facts an agent must know (not obvious from `task --list`):

- **Never push to `main`.** Every change — even a one-line hotfix — ships via
  a feature branch + merge request on GitLab. Flux and CI act on `main` after
  merge.
- **Two lifecycles.** Base infra + the k3s layer are deployed by Ansible
  (`task infra:*`, `task k3s:*`); everything *inside* the cluster (platform
  controllers + apps) is reconciled by **Flux** from `kubernetes/` on every push
  to `main`. There is no `kubectl apply` / `helm upgrade` in the normal flow.
- **All Ansible tasks are idempotent** — safe to re-run.
- **Cluster Helm/image versions** live in
  `ansible/inventories/prod/group_vars/all.yml`; after editing, run
  `task flux:sync-versions`, then commit + push so Flux reconciles them.
  Manifests reference versions as `${name}` placeholders (e.g.
  `version-${sonarr_version}`) resolved at reconcile time by Flux
  `postBuild.substituteFrom` from the `cluster-versions` ConfigMap
  (`kubernetes/infrastructure/sources/versions-configmap.yaml`, generated from
  all.yml by `task flux:sync-versions`; wired in the Flux Kustomizations, e.g.
  `kubernetes/clusters/weisssrv/apps.yaml`). See docs/29-flux-operations.md
  (Version pinning / Substitution Not Applied) for details.
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
   # Format in group_vars/all.yml (that file holds the real references — the
   # canonical source of truth; do not re-copy specific item names here)
   secrets:
     <var_name>: "op://Homelab/<Item Title>/<field>"
     # Item titles with spaces are fine — `op run` parses the full path.
   ```

2. **External Secrets Operator in the cluster** — the `onepassword-homelab` ClusterSecretStore (namespace `external-secrets`, 1Password Connect provider) syncs `ExternalSecret` resources into Kubernetes `Secret`s. Connect runs in-cluster (no calls to 1Password cloud). `remoteRef.key` is the **1Password item title** and `remoteRef.property` is the **field name** — canonical format reference in `docs/29-flux-operations.md` ("1Password Connect Provider Reference Format").

3. **CI pipelines** — `.gitlab-ci.yml` uses `op run` / `op read` with `OP_SERVICE_ACCOUNT_TOKEN` to inject secrets at runtime. This is separate from Connect and unchanged by the migration.

The bootstrap Secrets `op-credentials` and `onepassword-connect-token` in the `external-secrets` namespace are the **only manually created** Kubernetes Secrets — created via `task flux:bootstrap-onepassword` (procedure in `docs/29-flux-operations.md`). Every other in-cluster Secret is produced by ESO from `ExternalSecret` manifests reconciled by Flux.

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

The roles and their one-line purposes are listed in the **Ansible Roles**
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
- **nfs_tls** (`nfs_tls_enabled`) runs tlshd on all six Proxmox hosts + every k3s
  agent. The `nas_storage` k3s export lines require TLS (`xprtsec: tls`,
  plaintext rejected); the k3s NFS PVs *mount* with `xprtsec=tls` — **by
  hostname** (`server: pve-nas-01.esweiss.com`, since the `*.esweiss.com` cert
  has no IP SAN; an IP mount fails the handshake). HAOS (.154) is the one
  documented plaintext exception (docs/24); the Proxmox `tank-proxmox` target
  now mounts with TLS, though its export does not yet require it (docs/16).
- **resolv_conf** / **zvol_mount** are shared helper roles (used by base/adguard
  and k3s/gitlab respectively).

## Code Conventions

These are the repo's Ansible conventions (canonical home for all agent
entry-points — CLAUDE.md, AGENTS.md, and `.cursorrules` all defer here):

- **Fully-qualified collection names (FQCN)** — `ansible.builtin.apt`, not `apt`.
  Partly enforced by `ansible-lint` (`profile: production`) via `task lint`.
- **snake_case** for all Ansible variables; role-specific vars are prefixed with
  the role name (e.g. `adguard_http_port`). Var precedence (low→high):
  `group_vars/all.yml` → `group_vars/<group>.yml` → `host_vars/<host>.yml`.
- **`no_log: true` on any task that handles a secret** (renders a password to a
  file, passes a credential, etc.). This is a secret-hygiene rule `ansible-lint`
  does **not** fully catch (it matches known password module params, not the
  template-writes-a-secret pattern), so apply it by hand. Secrets themselves are
  `op://Homelab/...` references in the `secrets:` dict (see Secrets Management).
- **Handler pattern**: a config-changing task `notify:`s a handler that does the
  `state: restarted`; handlers live in `handlers/main.yml`. When a readiness
  probe must see the restarted process, `meta: flush_handlers` before it.
- **Service pattern**: install packages → create a system user
  (`system: true`, `shell: /usr/sbin/nologin`) → deploy the systemd unit
  template (`notify: [Reload systemd, Restart <svc>]`) → enable + start.
- **Follow existing patterns** — mirror a similar role or
  `kubernetes/apps/<neighbour>` rather than inventing a new shape.

## User Management

All hosts use `eric` for SSH with passwordless sudo; LXC containers are
unprivileged (mapped UIDs); services run as dedicated users (adguard, unbound,
plex; Postfix runs as root, normal for mail servers). Canonical detail lives in
the README "User Management" section and `docs/03-ssh-users.md` — refer there
rather than restating it here.

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

2. **Ship** (via feature branch + MR — never push to `main` directly):
   ```bash
   git add kubernetes/...
   git commit
   git push -u origin <branch>   # open an MR; on merge, the GitLab agent's Flux module triggers reconcile (~1-min poll fallback)
   task flux:reconcile           # Optional: force immediate reconcile
   ```

3. **Verify**:
   ```bash
   task flux:status           # Concise health summary
   task flux:verify           # `flux check` + all managed resources
   ```

See `docs/29-flux-operations.md` for day-2 operations including secret rotation, suspend/resume, and webhook setup. Multi-repo tenant onboarding is covered in `docs/30-multi-repo-onboarding.md`.

## Version Management

All application, Helm chart, and container image versions are pinned centrally
in `ansible/inventories/prod/group_vars/all.yml` (Home Assistant OS is the
exception — updated manually via its UI). After editing a version pin, run
`task flux:sync-versions`, then commit + push so Flux reconciles the
`cluster-versions` ConfigMap. The version-discovery/update tasks
(`task maintenance:check-versions`, `maintenance:update-version`,
`maintenance:update-all-versions`) and the pinning philosophy are documented
in `docs/12-runbooks.md` (update workflow).

## Storage Architecture

Full pool topology, creation commands, and design rationale (storage-tier
selection, lz4-vs-zstd) live in `docs/06-zfs.md`. Quick reference:

**Automated Storage Selection**: `proxmox_vm` / `proxmox_lxc` pick storage
from the host's `proxmox_role` — `nas` → `ssd`, `compute`/`general` →
`local-ssd` — overridable per-guest via `proxmox_storage` / `lxc_storage`.

### NAS Node (pve-nas-01) - Specialized ZFS Pools

**ZFS Pools**: `tank` (bulk media), `ssd` (app data), `nvme` (hot
downloads/scratch), `archive` (cold storage/backups). Geometry, capacities,
and datasets: `docs/06-zfs.md`.

**Persistent Storage (ZFS zvols)**: per-app zvols under `ssd/appdata/*`
(Authentik/Mealie Postgres, GitLab repos, Prometheus, Loki) plus the Grafana
NFS-backed PV. The full list with sizes, SCSI slots, and mount points is the
`vm_additional_disks` block in hosts.yml (created and mounted by the
proxmox_vm/zvol_mount roles) and is documented in `docs/06-zfs.md`. Data
survives pod and VM recreation (zvols persist on the Proxmox host's ZFS pool).

### Compute Nodes - local-ssd ZFS Pool

All five compute nodes carry a 1TB `local-ssd` pool (lz4, ashift=12,
atime=off, autotrim=on) for k3s VMs and the HA-managed containers
(dns-01/02, smtp-relay, home-assistant). Rationale in docs/06.

### Resource Pools and Storage Management

**Resource Pools** (`proxmox_resource_pools` in `all.yml`): infra-core (dns, smtp), apps-public (plex), apps-private (gitlab + internal apps), platform (k3s VMs)

**NEVER create/destroy ZFS pools via Ansible** - pools are created manually (too critical to automate). Ansible only sets properties and mounts. Zvols for persistent storage are managed via `vm_additional_disks` but the parent pools are never touched.

## Documentation

The full, numbered `docs/` index (grouped Getting Started /
Infrastructure Services / Operations and Planning) lives in the **Documentation**
section of `README.md`. Browse `docs/` directly or that table — do not maintain a
second copy of the index here. Per-area pointers already appear inline in the
relevant sections above.

## Important Context Files

- `CLUSTER_STATUS.txt` - Full cluster state snapshot (gitignored; generated locally via `task collect-state`)

## Credits

Repository structure inspired by [FreekingDean/homelab](https://github.com/FreekingDean/homelab)
