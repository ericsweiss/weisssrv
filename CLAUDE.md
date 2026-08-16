# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**weisssrv** - Homelab Infrastructure as Code

Complete GitOps repository for a Proxmox-based homelab using Ansible, Terraform, and Kubernetes.

**Tech stack**: Proxmox VE + Debian 13 (trixie), Ansible (roles come from the
`weisssrv.infra` collection in `eric/weisssrv-lib`), Terraform (Cloudflare,
Tailscale, Authentik), k3s + Flux CD GitOps, External Secrets Operator with
1Password Connect, ZFS.

## Agents: start here

Before making ANY change in this repo, invoke the `weisssrv-development` skill
(Skill tool) and follow it — it carries the repo's workflow, pre-MR gates, and a
change-type decision tree pointing at every canonical doc, so the guardrails
below are applied consistently. The skill lives at
`.claude/skills/weisssrv-development/`.

## Repository Structure

**Canonical source**: https://git.ericsweiss.com/eric/weisssrv (GitLab)
**GitHub mirror**: https://github.com/ericsweiss/weisssrv (read-only) — the
library and both templates are mirrored too, which is what makes the DR path in
`docs/17-disaster-recovery.md` work.

The tree below is annotated for agents (which stage owns what, and the traps).
`README.md` § Repository Structure is the human-facing version; neither is a
substitute for reading the directory itself.

```
weisssrv/
├── ansible/                    # Site data only — the roles live in the weisssrv.infra collection
│   ├── inventories/prod/       # Production inventory + vars (hosts.yml, group_vars, host_vars)
│   ├── playbooks/              # Deployment playbooks — roles referenced as weisssrv.infra.<role>
│   ├── integration-tests/      # Multi-role molecule scenarios (per-role scenarios live with the roles)
│   └── requirements.yml        # Galaxy pins, including weisssrv.infra
├── terraform/
│   ├── cloudflare/             # External DNS management
│   ├── tailscale/              # Tailnet ACL policy-as-code
│   └── authentik/              # Authentik SSO state as code (apps/providers/groups — docs/40)
├── kubernetes/                 # Flux-managed k8s state (GitOps source of truth)
│   ├── clusters/weisssrv/      # Flux entrypoint (flux-system, infrastructure-{sources,crds,controllers,configs,observability}.yaml, infrastructure-metrics-server.yaml, apps.yaml, tenants/)
│   ├── infrastructure/         # Platform — five sibling stages reconciled in dependsOn order (sources -> crds -> controllers -> configs, which fans out to observability and apps in parallel)
│   │   ├── sources/            # HelmRepository CRs + versions-configmap.yaml (generated) + cluster-config.yaml (hand-edited cluster identity) + the PriorityClasses (must exist before controllers/ schedules a pod naming one) — runs first, no deps
│   │   ├── crds/               # prometheus-operator CRDs ahead of the controllers that reference them (dependsOn sources) — fixes the fresh-bootstrap ordering
│   │   ├── controllers/        # platform HelmReleases (dependsOn crds) — see the dir for the current set; metrics-server/ is inside it but is reconciled by its OWN Kustomization, off the chain (see that file's header)
│   │   ├── configs/            # CRs requiring the controllers' CRDs, plus the built-in-kind NetworkPolicy sets that fence namespaces this repo does not otherwise own (dependsOn controllers) — see the dir for the current set
│   │   └── observability/      # kube-prometheus-stack, loki, alloy, exporters, dashboards, ingress (dependsOn configs) — see the dir
│   ├── components/             # reusable Kustomize components — netpol-baseline, the three netpol-egress-*, gitlab-runner-common; see kubernetes/components/README.md
│   └── apps/                   # one dir per app — either a HelmRelease (release.yaml) or raw Deployments/CRs, plus an externalsecret.yaml where the app needs ESO secrets (dependsOn infrastructure-configs — parallel to observability) — see the dir for the current set
├── docs/                       # Documentation
├── .gitlab/                    # GitLab agent config (agents/weisssrv-k3s/), child-pipeline job includes (ci/), secret-detection ruleset
├── scripts/                    # Utility scripts
├── docker/                     # App build images only (hermes-agent, camofox-browser) — the molecule test/CI images ship from weisssrv-lib
├── .gitlab-ci.yml              # CI/CD pipeline (canonical) — but see "Repo family" below: many jobs come from weisssrv-lib
└── .github/workflows/          # Single inert stub (ci-disabled.yml) — CI runs on GitLab
```

## Repo family (weisssrv is not self-contained)

| Repo | Role |
|---|---|
| `eric/weisssrv` | this repo — the cluster instance (site data: inventory, manifests, docs) |
| `eric/weisssrv-lib` | the shared building blocks: CI job templates, the **`weisssrv.infra` Ansible collection** (every role), the `weisssrv-lib-cli` template renderer, Terraform modules, and the lint profiles |
| `eric/weisssrv-cluster-template` | copier template that generates a whole new cluster repo shaped like this one; also consumes weisssrv-lib at a pinned tag |
| `eric/weisssrv-app-template` | copier template for tenant repos that deploy *into* this cluster (docs/30); also consumes weisssrv-lib at a pinned tag |

This repo consumes four things from the library, all pinned:

1. **CI templates**, `include:`d at a literal `ref:`. The `include:` block in
   `.gitlab-ci.yml` is the source of truth for which ones — do not keep a count
   anywhere. `variables.WEISSSRV_LIB_REF` is the single source for the tag and
   `scripts/check-lib-pins.py` (`--fix` rewrites) enforces that every entry
   matches it and is a release tag.
2. **The `weisssrv.infra` collection**, pinned in `ansible/requirements.yml`.
   Playbooks reference roles as `weisssrv.infra.<role>`; there is no
   `ansible/roles/` here any more.
3. **Vendored files** — byte-identical copies of library files, and they are
   **many more than the two an agent usually assumes**. Never keep a list or a
   count here: `scripts/README.md`'s **Origin** column is the human inventory
   and the library's `scripts/vendored-paths.yml` registry — enforced here by
   `scripts/test_vendored_byte_identity.py` under `task lint` — is the
   machine-checked one, covering the copies outside `scripts/` and the declared
   forks that are deliberately allowed to differ. Fix a vendored file upstream
   and re-vendor; a local edit is reverted by the next re-vendor and reds the
   gate meanwhile.
4. **Terraform modules** — all three roots (`terraform/cloudflare`,
   `terraform/tailscale`, `terraform/authentik`) are thin callers of
   `weisssrv-lib//terraform/modules/{cloudflare-zone,tailscale-acl,authentik-sso}`
   at a `?ref=` pin, holding only site data. All three pin the same release
   as `WEISSSRV_LIB_REF`. Those `?ref=`
   pins are bumped **by hand** — `scripts/check-lib-pins.py` reads only the
   `include:` block and `ansible/requirements.yml` — but a missed one fails
   `scripts/test_site_configs.py` (the refs must equal `WEISSSRV_LIB_REF`).

Consequences an agent must not miss:

- **Do not "fix" an included job by editing `.gitlab-ci.yml`.** Its behaviour
  lives in the library, and a same-named local job silently overrides the
  included one. Change the library, tag it, then bump the ref.
- **Do not edit a role here** — there are none. Role changes are lib MRs; see
  § Ansible roles below for the full bump flow.
- **Bumping the library is a fan-out**: lib MR → maintainer cuts a tag → bump
  the pin in this repo **and** in both templates. Prove pipeline parity before
  merging: the library serves several consumers, so a changed input default
  silently changes this pipeline.
- What each include overrides, and what deliberately stays weisssrv-local, is in
  `docs/13-ci-cd.md` § Shared CI library. The library's own
  `docs/INCLUDE-CONTRACT.md` / `docs/VERSIONING.md` own the input contract, and
  its collection README + `MIGRATING.md` own the role-variable API.

## Architecture

Quick reference only — `docs/01-overview.md` is canonical for the topology, and
each app's row below names the doc that owns it.

### Current Infrastructure (Base Parity)

- **6 Proxmox hosts** (cluster `weisssrv`), `.102`-`.107` — pve-nas-01 is NAS +
  storage, the rest compute; host-by-host list in `docs/01-overview.md`
- **NAS storage**: ZFS (tank/ssd/nvme/archive), NFS, Samba
- **DNS**: 2x AdGuard Home + Unbound (DoT) on .150 / .160
- **SMTP**: Gmail relay on .151. **Certs**: acme.sh with Cloudflare DNS-01
- **Remote access**: Tailscale on the Proxmox hosts
- **Firewall**: Proxmox firewall with IP sets + security groups
- **HA**: Proxmox HA for the infrastructure guests (DNS, SMTP, Home Assistant)
- **GitOps**: Flux CD reconciles all Kubernetes state from `kubernetes/`
- **Secrets to k8s**: External Secrets Operator with the 1Password Connect
  provider (`ClusterSecretStore` `onepassword-homelab`)

### K3s Platform

**9-node cluster** (3 servers + 6 agents): three server nodes form the etcd
quorum; the six agents carry NAS, ingress, and compute workloads. Node-by-node
list with IPs and host placement lives in `docs/01-overview.md` (canonical).

**Two lifecycles:**

1. **Ansible** (`task k3s:deploy`) builds the VMs, k3s itself, and kube-vip
   (API VIP .161). Idempotent — safe to re-run.
2. **Flux** reconciles everything *inside* the cluster from `kubernetes/` on
   every push to `main`, push-triggered by the GitLab agent's Flux module with
   the ~1-minute git poll as fallback (`task flux:reconcile` forces it). Local
   iteration is `task flux:dev-apply -- <path>`, reverted on the next reconcile.

The k3s layer is `docs/19-k3s-deployment.md`; Flux day-2 ops are
`docs/29-flux-operations.md`.

**Platform features:**

- kube-vip (API VIP .161), MetalLB (.100 public / .101 internal / .99 wg-easy),
  Traefik ingress, external-dns (Cloudflare), cert-manager
- 3-node etcd quorum — tolerates one server failure
- Flux CD + External Secrets Operator on the 1Password Connect backend
- Observability: Prometheus + Grafana + Loki + Alloy, with the k3s-irrelevant
  kube-prometheus-stack components disabled (kubeProxy, kubeScheduler,
  kubeControllerManager) and the Alertmanager webhook injected from an
  ExternalSecret template
- Autoscaling: VPA (Auto/Initial/Off tiers per workload class) + a CoreDNS HPA
  pin — docs/33-autoscaling.md
- GPU: the pve-prec-01 GTX 1660 Ti is VFIO-passed to the GPU agent
  (`esweiss.com/gpu=nvidia`) with nvidia-open, the container toolkit, a
  time-sliced device plugin and DCGM telemetry; Hindsight's llama.cpp offloads
  there — docs/43-gpu-passthrough.md
- Network policy: an **ingress default-deny is mandatory in every namespace**.
  The `netpol-baseline` component is the standard mechanism, with two documented
  exceptions — `downloads` (its local policy covers ingress *and* egress) and
  `flux-system` (upstream gotk manifests); docs/29 § Network policy exceptions is
  the canonical list. `kube-system` is fenced like everything else, with its deny
  and its complete allow set (CoreDNS :53/:9153, metrics-server :10250, kured
  :8080) kept together in
  `kubernetes/infrastructure/configs/kube-system-policies/`. Egress allowlists are
  per-app, three `netpol-egress-*` components ship the recurring ones
  (`kubernetes/components/README.md`), and
  `scripts/check-netpol-except-parity.py` keeps their reserved-CIDR except-lists
  identical
- Cluster identity: domains, CIDRs and VIPs are `${cluster_*}` placeholders
  substituted from `kubernetes/infrastructure/sources/cluster-config.yaml`, not
  literals — the invariant, its four literal-allowed exceptions and the
  `check-cluster-literals.py` gate are in the Task Runner bullet list below
- Node/workload ops: kured (coordinated reboots) and Reloader, which watches
  ConfigMaps only (`ignoreSecrets: true`) — rotating a credential Secret is a
  manual restart. Current controller set:
  `kubernetes/infrastructure/controllers/kustomization.yaml`

**Applications** — the per-app doc is canonical for every deployment detail:

| App | Hostnames | Doc |
|---|---|---|
| Authentik SSO (IdP for everything below) | auth.esweiss.com / auth.ericsweiss.com | `kubernetes/apps/authentik/README.md`; Terraform layer docs/40 |
| Plex (LXC .152) | plex.esweiss.com | docs/20 |
| Download/media stack (`downloads` ns) | nzbget / qbittorrent / prowlarr / tv / movies / music / pulsarr .esweiss.com | docs/21 |
| Recipes (`recipes` ns) | food.esweiss.com; bar.ericsweiss.com (bar.esweiss.com redirects to it) | docs/22, docs/23 |
| Home Assistant (HAOS VM .154) | home.esweiss.com / home.ericsweiss.com | docs/24 |
| GitLab (VM .153) | git.esweiss.com / git.ericsweiss.com (+ registry, pages) | docs/27 |
| Observability (Grafana) | grafana.esweiss.com | docs/31 |
| wg-easy VPN | vpn.ericsweiss.com:51820/udp endpoint; vpn.esweiss.com admin UI | docs/38 |
| Nextcloud (VM .156) | cloud.esweiss.com / cloud.ericsweiss.com | docs/35 |
| Immich (VM .157) | photos.esweiss.com / photos.ericsweiss.com | docs/36 |
| Hermes Agent + Hindsight memory (`hermes`, `hindsight` ns) | agent.esweiss.com / agent.ericsweiss.com (Hindsight has no ingress) | docs/37 |
| Homarr | dashboard.esweiss.com / dashboard.ericsweiss.com | docs/41 |
| Windows 11 desktop (VM .155) | none — RDP only | docs/39 |
| Immich ML (LXC .158) | none — LAN API for the Immich VM only | docs/36 |

Cross-cutting facts no single app doc owns:

- Every user-facing app is behind Authentik. The OIDC **issuer host is ALWAYS the
  external one** (`auth.ericsweiss.com`) even for internal-only apps.
- Homarr's integrations talk to service URLs directly, bypassing the SSO
  perimeter by design (docs/41).
- The VM/LXC guests (plex, gitlab, HAOS, nextcloud, immich, immich-ml, windows)
  are Ansible-provisioned and fronted by in-cluster Traefik via the `vm-ingress`
  app — a k3s routing change can break a guest that Ansible never touched.
- Windows (.155) has `onboot=0` **and** auto-starts: its disks are on the
  encrypted `ssd` pool, so `pve-start-encrypted-guests` starts it after unlock
  (last entry in `zfs_encryption_guest_vmids`). Remove it from that list to stop
  it starting — do not set `onboot=1` (docs/32, docs/39).

**Planned** — roadmap source of truth is `docs/16-next-steps.md` (Uptime Kuma is
the one queued app; open non-app work is split across its § Decisions needed,
§ Pending supervised steps and § Planned work).

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
  **Every stage after `sources` substitutes from TWO ConfigMaps**, both
  `optional: false`: `cluster-versions` *and* `cluster-config` (below). A new
  Flux Kustomization that lists only one ships unsubstituted `${...}` into a
  live manifest.
- **Cluster identity is single-sourced** in the sibling
  `kubernetes/infrastructure/sources/cluster-config.yaml` — hand-edited, not
  generated. Manifests spell domains, CIDRs and VIPs as `${cluster_*}`
  placeholders (`app.${cluster_internal_domain}`,
  `${cluster_metallb_internal_vip}`), and `scripts/check-cluster-literals.py`
  (`task lint:cluster-literals`) fails a hard-coded value as well as drift
  between the ConfigMap and the Ansible inventory. Literals stay ONLY where a
  tool parses the manifest before Flux substitutes: NetworkPolicy `ipBlock`
  CIDRs, `observability/rules/`, backslash-escaped (regex) domain spellings,
  and per-guest/per-node addresses. The ConfigMap's own header is canonical.
- **Local Flux iteration**: `task flux:dev-apply -- <path>` previews a change
  in-cluster but is reverted on the next reconcile unless committed.
- `task lint` mirrors the CI lint stage. The `lint:` task's own command list in
  `Taskfile.yml` is the source of truth for what it runs (Ansible, Terraform,
  `flux:lint`, the Python script tests, shellcheck, yamllint, and the
  coverage/sync/version-pin/policy gates); `lint:prometheus-config` needs
  promtool + amtool on PATH. `task kubernetes:lint` is an alias for `flux:lint`.

### Manual Ansible

```bash
# Install collections, including weisssrv.infra at the pinned tag. Re-run after
# bumping that pin — `ansible-galaxy` will not refresh an already-installed
# collection without --force.
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

> **Prefer the `task terraform:*` commands** — they inject the Cloudflare API
> credentials and the GitLab HTTP state-backend auth via `op run`. A manual
> invocation needs `TF_VAR_cloudflare_api_token`,
> `TF_VAR_cloudflare_account_id`, and the `TF_HTTP_*` variables exported by hand.

```bash
cd terraform/cloudflare
terraform init
terraform plan
terraform apply
```

## Secrets Management (1Password)

Three consumers pull from the same 1Password "Homelab" vault: Ansible/Terraform
and the Task wrapper (`op run --` resolving `op://Homelab/<Item>/<field>`
references declared in each task's `env:` block, mirrored by the matching CI
job's `variables:`), External Secrets
Operator in-cluster (`onepassword-homelab` ClusterSecretStore, 1Password Connect
provider, `remoteRef.key` = item **title** and `remoteRef.property` = **field**),
and CI (`OP_SERVICE_ACCOUNT_TOKEN`). The canonical description of the model —
including which Secrets are bootstrap-only and how each path rotates — is
**`docs/15-credential-rotation.md` § Secrets model**; the ESO reference format is
in `docs/29-flux-operations.md`.

**NEVER commit secrets to git.** Every sensitive value is a 1Password reference:
`op://` for host-side tooling, item titles in ExternalSecrets for in-cluster.

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

- Proxmox hosts `.102-.107`; DNS `.150`/`.160`; SMTP `.151`; service guests
  `.152-.158` (plex, gitlab, HAOS, windows, nextcloud, immich, immich-ml).
- K3s: API VIP `.161`; servers `.222`/`.223`/`.227`; agents `.202-.207`;
  MetalLB VIPs `.100` (public) / `.101` (internal) / `.99` (wg-easy endpoint).

Firewall IP sets and security groups (`admin_lan`, `admin_ts`, `core-cluster`,
`k3s_nodes`, `pve_hosts`, `nfs_clients`, `smb_clients`) are documented in
`docs/11-firewall.md`; the rules are rendered by the collection's
`proxmox_firewall` role from `hosts.yml` / `group_vars`.

## Ansible roles

**There are no roles in this repo.** All of them ship in the `weisssrv.infra`
collection (weisssrv-lib), pinned in `ansible/requirements.yml`; playbooks
address them as `weisssrv.infra.<role>`. What lives here is the site data the
roles consume: `hosts.yml`, `group_vars`, `host_vars`, the playbooks, and the
Taskfile/CI wiring. Role behaviour, variables, and defaults are documented in
the collection (its README + each role's README); `MIGRATING.md` there is the
old→new variable map.

**Changing role behaviour is a two-repo flow:**

1. Make the change in `weisssrv-lib` (`ansible_collections/weisssrv/infra/roles/<role>/`)
   with its molecule scenario, MR it, and have a release tag cut.
2. Here: bump the collection `version:` in `ansible/requirements.yml`, bump
   `variables.WEISSSRV_LIB_REF` in `.gitlab-ci.yml` and run
   `scripts/check-lib-pins.py --fix` **and**
   `scripts/check-molecule-image-pin.py --fix` (the molecule-test fallback tags
   in the integration scenarios and `ansible/TESTING.md` — CI overrides the
   image, so only local runs read them), re-vendor the byte-identical scripts,
   then `ansible-galaxy install -r ansible/requirements.yml --force` and re-run
   the gates. The three Terraform `?ref=` pins are still bumped by hand.
3. Land the inventory changes a renamed/emptied variable requires **in the same
   MR** — the collection's variables are `| default(...)`-guarded, so a missed
   rename does not fail, it silently takes the role default.

Site-facing constraints worth knowing before touching inventory:

- **nfs_tls** — the `nas_storage` k3s exports require TLS (`xprtsec: tls`,
  plaintext rejected) and the k3s NFS PVs mount **by hostname**
  (`pve-nas-01.esweiss.com`): the `*.esweiss.com` cert has no IP SAN, so an IP
  mount fails the handshake. HAOS (.154) is the one documented plaintext
  exception (docs/24).
- **node_exporter_host** binds **9101**, because the k3s node-exporter DaemonSet
  already owns 9100 on the same LAN.
- **zfs_encryption** is the sole consumer of the 1Password Connect token at
  `/etc/onepassword-connect/token`, gated on `zfs_encryption_pools` — compute
  hosts with an empty list get no token (docs/32).
- **alloy_host** ships journald to Loki through the internal Traefik
  IngressRoute; it does not duplicate the in-cluster DaemonSet, which covers
  container logs only.

## Code Conventions

The Ansible conventions that govern the playbooks and inventory here — FQCN,
snake_case role-prefixed vars and var precedence, `no_log: true` on every
secret-touching task, the handler and service patterns, and the `--tags` caveat —
live in **`ansible/README.md` § Code conventions** (canonical; `AGENTS.md` and
`.cursorrules` point at the same place). Role *code* follows the collection's
conventions in weisssrv-lib. For anything Kubernetes-side, mirror the closest
`kubernetes/apps/<neighbour>` rather than inventing a shape.

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

Everything in `kubernetes/` is reconciled by Flux. There is no `kubectl apply` or
`helm upgrade` step in the normal workflow — edit YAML, commit, push:

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

Day-2 operations (secret rotation, suspend/resume, webhook setup) are in
`docs/29-flux-operations.md`; multi-repo tenant onboarding is
`docs/30-multi-repo-onboarding.md`.

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

Pool topology, geometry, dataset layout, and the tier-selection rationale are in
`docs/06-zfs.md`; pool/dataset bootstrap is `docs/44-storage-bootstrap.md`;
backups and DR are `docs/42-offsite-backup.md` and `docs/17-disaster-recovery.md`.
Quick reference:

- **Pools**: NAS (pve-nas-01) carries `tank` (bulk media), `ssd` (app data),
  `nvme` (hot downloads/scratch), `archive` (cold/backups); each compute host
  carries a `local-ssd` pool for k3s VMs and the HA-managed containers.
- **Persistent app storage** is ZFS zvols declared in the `vm_additional_disks`
  blocks of `hosts.yml` (created by `proxmox_vm`, mounted by `zvol_mount`) —
  that block is the inventory of record. Zvols outlive the pods and VMs on top
  of them.
- **Guest storage is derived**, not hand-placed: `proxmox_role` `nas` → `ssd`,
  `compute`/`general` → `local-ssd`, overridable per guest with
  `proxmox_storage` / `proxmox_lxc_storage`.
- **Backup chain**: encrypted datasets → nightly raw-encrypted `archive`
  replication → nightly restic → Backblaze B2 (client-side ciphertext, GFS
  retention). Logical dumps land on `tank/backups/apps`.
- **NEVER create or destroy ZFS pools from Ansible** — pools are created by hand.
  Ansible only sets properties, creates zvols, and mounts.

## Documentation

The numbered `docs/` index — grouped Getting Started / Infrastructure Services /
Platform / Applications / Operations and Planning / Historical, plus the
component docs outside the numbered set — lives in the **Documentation** section
of `README.md`, together with the conventions new docs must follow. Do not keep a
second copy of the index here; per-area pointers appear inline above.

## Important Context Files

- `CLUSTER_STATUS.txt` - Full cluster state snapshot (gitignored; generated locally via `task collect-state`)

## Credits

Repository structure inspired by [FreekingDean/homelab](https://github.com/FreekingDean/homelab)
