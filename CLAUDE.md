# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**weisssrv** - Homelab Infrastructure as Code

Complete GitOps repository for a Proxmox-based homelab using Ansible, Terraform, and Kubernetes.

**Tech stack**: Proxmox VE + Debian 13 (trixie), Ansible (roles come from the
`weisssrv.infra` collection in `eric/weisssrv-lib`), Terraform (Cloudflare,
Tailscale, Authentik, UniFi), k3s + Flux CD GitOps, External Secrets Operator with
1Password Connect, ZFS.

## Agents: start here

Before making ANY change in this repo, invoke the `weisssrv-development` skill
(Skill tool) and follow it — it carries the repo's workflow, pre-MR gates, and a
change-type decision tree pointing at every canonical doc, so the guardrails
below are applied consistently. The skill lives at
`.claude/skills/weisssrv-development/`.

This file holds the invariants, the traps, and the pointers. It does not restate
what another file owns; when it names a canonical home, read that file.

## Template first

weisssrv is **one instance**, not the product. Before writing a change here, ask
whether it is site data or a generic improvement:

- **Site data stays here**: IPs, hostnames, VM IDs, the app estate, inventory
  values, this cluster's docs.
- **Everything generic belongs upstream**: a role, a CI job template, a Terraform
  module or a vendored gate goes to `eric/weisssrv-lib`; a repo-shape change
  (directory layout, a gate every cluster wants, a doc skeleton, a Taskfile
  pattern) goes to `eric/weisssrv-cluster-template`, and a tenant-repo shape
  change to `eric/weisssrv-app-template`.
- A generic change landed only here is drift. Land it in the template (and the
  library where the code lives), then port it here in the same program of work.

## Repo family (weisssrv is not self-contained)

| Repo | Role |
|---|---|
| `eric/weisssrv` | this repo — the cluster instance (site data: inventory, manifests, docs) |
| `eric/weisssrv-lib` | the shared building blocks: CI job templates, the **`weisssrv.infra` Ansible collection** (every role), the `weisssrv-lib-cli` template renderer, Terraform modules, and the lint profiles |
| `eric/weisssrv-cluster-template` | copier template that generates a whole new cluster repo shaped like this one; also consumes weisssrv-lib at a pinned tag |
| `eric/weisssrv-app-template` | copier template for tenant repos that deploy *into* this cluster (docs/30); also consumes weisssrv-lib at a pinned tag |

weisssrv is the SOURCE the cluster template was extracted from, not a render of
it: there is no `.copier-answers.yml` here and `copier update` cannot be run.
Improvements flow between the two by hand, in both directions.

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
   and this repo's own `scripts/vendored-manifest.yml` — enforced by
   `scripts/test_vendored_byte_identity.py` under `task lint`, driving the
   library's `check-vendored-copies.py` engine against its
   `vendorable-paths.yml` offer list — is the machine-checked one, covering
   the copies outside `scripts/` and the declared forks that are deliberately
   allowed to differ. Fix a vendored file upstream
   and re-vendor; a local edit is reverted by the next re-vendor and reds the
   gate meanwhile.
4. **Terraform modules** — all four roots (`terraform/cloudflare`,
   `terraform/tailscale`, `terraform/authentik`, `terraform/unifi`) are thin
   callers of
   `weisssrv-lib//terraform/modules/{cloudflare-zone,tailscale-acl,authentik-sso,unifi-network}`
   at a `?ref=` pin, holding only site data. All four pin the same release
   as `WEISSSRV_LIB_REF`. Those `?ref=`
   pins are bumped **by hand** — `scripts/check-lib-pins.py` reads only the
   `include:` block and `ansible/requirements.yml` — but a missed one fails
   `scripts/test_site_configs.py` (the refs must equal `WEISSSRV_LIB_REF`).

Consequences an agent must not miss:

- **A change to `Taskfile.yml`, `.gitlab-ci.yml`, `kubernetes/clusters/weisssrv/`
  or `scripts/check-*.py` is an extraction candidate.** The MR says whether it
  propagates to `weisssrv-cluster-template`, and why not if it does not.
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
  `docs/INCLUDE-CONTRACT.md` / `docs/VERSIONING.md` own the input contract, its
  `docs/EXTENSIBILITY.md` owns which behaviour is a seam and which is the
  backend, and its collection README + `MIGRATING.md` own the role-variable API.

## Repository Structure

The tree below is annotated for agents: which stage owns what, and the traps.
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
│   ├── authentik/              # Authentik SSO state as code (apps/providers/groups/users — docs/40)
│   └── unifi/                  # UniFi VLANs, zone firewall, WLANs (supervised apply — docs/46)
├── kubernetes/                 # Flux-managed k8s state (GitOps source of truth)
│   ├── clusters/weisssrv/      # Flux entrypoint (flux-system, infrastructure-{sources,crds,controllers,configs,observability}.yaml, infrastructure-metrics-server.yaml, apps.yaml, tenants/)
│   ├── infrastructure/         # Platform — five sibling stages reconciled in dependsOn order (sources -> crds -> controllers -> configs, which fans out to observability and apps in parallel)
│   │   ├── sources/            # HelmRepository CRs + versions-configmap.yaml (generated) + cluster-config.yaml (hand-edited cluster identity) + the PriorityClasses (must exist before controllers/ schedules a pod naming one) — runs first, no deps
│   │   ├── crds/               # prometheus-operator CRDs ahead of the controllers that reference them (dependsOn sources) — fixes the fresh-bootstrap ordering
│   │   ├── controllers/        # platform HelmReleases (dependsOn crds) — see the dir for the current set; metrics-server/ is inside it but is reconciled by its OWN Kustomization, off the chain (see that file's header)
│   │   ├── configs/            # CRs requiring the controllers' CRDs, plus the built-in-kind NetworkPolicy sets that fence namespaces this repo does not otherwise own (dependsOn controllers) — see the dir for the current set
│   │   └── observability/      # kube-prometheus-stack, loki, alloy, alloy-syslog, exporters, rules, dashboards, ingress (dependsOn configs) — see the dir
│   ├── components/             # reusable Kustomize components — netpol-baseline, the three netpol-egress-*, gitlab-runner-common; see kubernetes/components/README.md
│   └── apps/                   # one dir per app — either a HelmRelease (release.yaml) or raw Deployments/CRs, plus an externalsecret.yaml where the app needs ESO secrets (dependsOn infrastructure-configs — parallel to observability) — see the dir for the current set
├── docs/                       # Documentation — README.md § Documentation is the index
├── .gitlab/                    # GitLab agent config (agents/weisssrv-k3s/), child-pipeline job includes (ci/), secret-detection ruleset
├── scripts/                    # Gates, generators and operational helpers — scripts/README.md is the inventory
├── lint/                       # Vendored lint profiles (yamllint) — NOT at the repo root, so ansible-lint keeps its own yaml rule
├── docker/                     # App build images only (hermes-agent, camofox-browser) — the molecule test/CI images ship from weisssrv-lib
├── .gitlab-ci.yml              # CI/CD pipeline (canonical) — but see "Repo family" above: many jobs come from weisssrv-lib
├── Taskfile.yml + taskfiles/   # Task runner — root file plus one included file per namespace
└── .github/workflows/          # Single inert stub (ci-disabled.yml) — CI runs on GitLab
```

## Where the facts live

Each row is canonical; do not restate it here or in the skill.

| Subject | Canonical |
|---|---|
| Host/node/VIP topology | `docs/01-overview.md` |
| ZFS pools, datasets, tiers | `docs/06-zfs.md`; bootstrap `docs/44-storage-bootstrap.md` |
| DNS stack | `docs/08-dns.md` |
| Proxmox firewall sets + security groups | `docs/11-firewall.md` |
| UniFi network tier | `docs/46-unifi-network.md` (current state), `docs/48-unifi-audit-and-migration.md` (audit + renumber record) |
| Runbooks, upgrade workflow | `docs/12-runbooks.md` |
| CI/CD pipeline | `docs/13-ci-cd.md` |
| Credentials, 1Password items, rotation | `docs/15-credential-rotation.md` |
| Security posture (what is and is not encrypted) | `docs/47-security-posture.md` |
| Flux day-2 operations | `docs/29-flux-operations.md` |
| Observability + alerting | `docs/31-observability.md` |
| SSO as code | `docs/40-authentik-terraform.md` |
| Per-app detail | `README.md` § Applications names the doc for each |
| Command set | `task --list` |
| Scripts and their origin | `scripts/README.md` |

## Architecture

### Cluster identity and VIPs

Addresses here are a quick reference; `docs/01-overview.md` is canonical. What
no other doc owns: the VIP-forwarding trap, the `${cluster_*}` invariant and the
IP-set hierarchy.

- The homelab is `10.0.10.0/24` (UniFi VLAN 10 — docs/46 for current state,
  docs/48 for the 2026-08 audit and the renumber record). Proxmox hosts `.102-.107`;
  DNS `.150`/`.160`; SMTP `.151`; service guests `.152-.158`. Per-host detail is
  `docs/01-overview.md`.
- K3s: 9 nodes (3 servers forming the etcd quorum + 6 agents), API VIP `.161`
  via kube-vip.
- **Four MetalLB VIPs**: `.100` public ingress, `.101` internal ingress, `.99`
  the wg-easy WireGuard endpoint, `.162` the alloy-syslog receiver. A VIP-bound
  flow is FORWARDED to the announcing node's guest, so the **guest** firewall
  filters it and a datacenter `cluster.fw` rule is inert for it (docs/11).
- Domains, CIDRs and VIPs are `${cluster_*}` placeholders substituted from
  `kubernetes/infrastructure/sources/cluster-config.yaml`, never literals — see
  the Task Runner bullets below for the invariant and its four exceptions.
- Firewall IP sets are three concentric client scopes (`admin_lan` ⊂
  `lan_clients` ⊂ `dns_clients` — admin ports, user-facing service ports,
  resolver `:53`) plus the membership sets derived from inventory (`admin_ts`,
  `core-cluster`, `k3s_nodes`, `pve_hosts`, `nfs_clients`, `smb_clients`).
  `docs/11-firewall.md` is the inventory of record for those and the security
  groups; the rules are rendered by the collection's `proxmox_firewall` role
  from `hosts.yml` / `group_vars`.

### Two lifecycles

1. **Ansible** (`task infra:deploy`, `task k3s:deploy`) builds the hosts, the
   guests, the VMs, k3s itself and kube-vip. Idempotent — safe to re-run.
2. **Flux** reconciles everything *inside* the cluster from `kubernetes/` on
   every push to `main`, push-triggered by the GitLab agent's Flux module with
   the ~1-minute git poll as fallback (`task flux:reconcile` forces it). Local
   iteration is `task flux:dev-apply -- <path>`, reverted on the next reconcile.

The k3s layer is `docs/19-k3s-deployment.md`; Flux day-2 ops are
`docs/29-flux-operations.md`; the per-app index is `README.md` § Applications.

### Cross-cutting facts no single app doc owns

- Every user-facing app is behind Authentik. The OIDC **issuer host is ALWAYS the
  external one** (`auth.ericsweiss.com`) even for internal-only apps.
- Homarr's integrations talk to service URLs directly, bypassing the SSO
  perimeter by design (docs/41).
- Uptime Kuma is the one app split by AUTHENTICATION rather than by hostname:
  its status-page paths are unauthenticated on both names while the admin UI
  exists only on the internal name behind forward-auth, so the external
  hostname deliberately 404s outside that path allowlist (docs/45).
- The VM/LXC guests (plex, gitlab, HAOS, nextcloud, immich, immich-ml, windows)
  are Ansible-provisioned and fronted by in-cluster Traefik via the `vm-ingress`
  app — a k3s routing change can break a guest that Ansible never touched.
- Windows (.155) has `onboot=0` **and** auto-starts: its disks are on the
  encrypted `ssd` pool, so `pve-start-encrypted-guests` starts it after unlock
  (last entry in `zfs_encryption_guest_vmids`). Remove it from that list to stop
  it starting — do not set `onboot=1` (docs/32, docs/39).
- An **ingress default-deny is mandatory in every namespace**. The
  `netpol-baseline` component is the standard mechanism, with two documented
  exceptions (`downloads`, `flux-system`) listed canonically in docs/29
  § Network policy exceptions. `kube-system` is fenced like everything else,
  with its deny and its complete allow set kept together in
  `kubernetes/infrastructure/configs/kube-system-policies/`.

**Planned** — roadmap source of truth is `docs/16-next-steps.md` (no app is
queued any more; open non-app work lives in its § Open work, with the
blocked-on-something items in § Deferred — needs its own change).

## Common Development Commands

### Task Runner

All operations use `Taskfile.yml`. Run `task --list` for the full, current set
(grouped by namespace). The Taskfile is the source of truth — do not maintain
a copy of the task list here.

Workflow facts an agent must know (not obvious from `task --list`):

- **Never push to `main`.** Every change — even a one-line hotfix — ships via
  a feature branch + merge request on GitLab. Flux and CI act on `main` after
  merge.
- **The Taskfile is an `includes:` tree.** `Taskfile.yml` keeps the top-level
  tasks (`default`, `lint`, `scripts:test`, `collect-state`, the singleton
  deploy tasks) and one file per namespace under `taskfiles/<ns>.yml`. Task
  names are unchanged: `flux:lint` is the task `lint:` in `taskfiles/flux.yml`.
  A task calls another namespace's with a leading colon
  (`:flux:sync-versions`); the root `vars:` and the `scripts/hosts.env` dotenv
  reach every included file. Per-app `<app>:status` / `:logs` / `:restart` come
  from internal templates in the root file, and
  `scripts/test_taskfile_tree.py` fails when a new `kubernetes/apps` namespace
  with a workload ships without them.
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
  Flux Kustomization that lists only one fails its own reconcile on the first
  key it cannot resolve: kustomize-controller runs with
  `StrictPostBuildSubstitutions=true`, so an undefined `${...}` is an error,
  not an empty string.
- **Three files in the tree are generated, not hand-edited**:
  `scripts/hosts.env` (`task hosts:sync`),
  `kubernetes/infrastructure/sources/versions-configmap.yaml`
  (`task flux:sync-versions`) and
  `kubernetes/infrastructure/observability/loki/host-log-staleness.yaml`
  (`task flux:sync-host-log-staleness`, regenerated from the `alloy_host` play).
  All three are drift-gated inside `task lint` and in CI, so an inventory change
  that skips its regeneration step reds the pipeline.
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
- `task lint` mirrors the CI lint stage. The root `lint:` task's own command
  list is the source of truth for what it runs (Ansible, Terraform,
  `flux:lint`, the `taskfiles/lint.yml` gates, and `scripts:test`, which runs
  every `scripts/test_*.py`). A new gate therefore enters `task lint` either as
  a `taskfiles/lint.yml` command or as a pytest suite that exercises it against
  the real tree. `lint:prometheus-config` needs promtool + amtool on PATH.
  `task kubernetes:lint` is an alias for `flux:lint`.

### Manual Ansible

```bash
# Install collections, including weisssrv.infra at the pinned tag. Re-run after
# bumping that pin — `ansible-galaxy` will not refresh an already-installed
# collection without --force.
ansible-galaxy install -r ansible/requirements.yml

# Ping all hosts
ansible -i ansible/inventories/prod all -m ping

# Dry-run deployment
ansible-playbook -i ansible/inventories/prod ansible/playbooks/site.yml --check

# Deploy to specific host
ansible-playbook -i ansible/inventories/prod ansible/playbooks/site.yml --limit pve-nas-01

# Deploy specific role
ansible-playbook -i ansible/inventories/prod ansible/playbooks/base.yml --tags ssh
```

There is no `ansible.cfg` at the repo root — the only one is `ansible/ansible.cfg`
and Ansible loads it from the CWD — so a repo-root invocation without `-i`
matches zero hosts and exits 0.

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

Three consumers pull from the same 1Password "Homelab" vault: host-side tooling
(`op run --` resolving `op://Homelab/<Item>/<field>` references declared in each
task's `env:` block, mirrored by the matching CI job's `variables:`), External
Secrets Operator in-cluster (`onepassword-homelab` ClusterSecretStore, 1Password
Connect provider, `remoteRef.key` = item **title** and `remoteRef.property` =
**field**), and CI (`OP_SERVICE_ACCOUNT_TOKEN`).

**NEVER commit secrets to git.** Every sensitive value is a 1Password reference:
`op://` for host-side tooling, item titles in ExternalSecrets for in-cluster.

`docs/15-credential-rotation.md` is canonical for the secrets model, the
inventory of required items, which Secrets are bootstrap-only, and how each path
rotates. Add or update items there.

## DNS Architecture

Split-horizon: `*.esweiss.com` resolves internally via AdGuard Home rewrites,
`*.ericsweiss.com` externally via Cloudflare (Terraform). Resolver addresses,
upstreams and the rewrite set are `docs/08-dns.md`.

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
   the gates. The four Terraform `?ref=` pins are still bumped by hand.
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
live in **`ansible/README.md` § Code conventions** (canonical). Role *code*
follows the collection's conventions in weisssrv-lib. For anything
Kubernetes-side, mirror the closest `kubernetes/apps/<neighbour>` rather than
inventing a shape.

**Comments describe the current state, briefly, in the present tense.** Three
lines per block is the ceiling. A block that guards a trap causing an outage may
open `CRITICAL:` and run to eight, and then still as short as it can be — that
marker is for outages, not for emphasis and not for a long file header.
`task lint:comment-length` enforces both. No dates, MR or issue numbers, no
"used to / previously", no incident narrative, prior values or rejected
alternatives. Runbook and rationale detail belongs in the owning doc or README,
not above the code.

## Storage Architecture

Pool topology, dataset layout and tier rationale are `docs/06-zfs.md`; pool and
dataset bootstrap is `docs/44-storage-bootstrap.md`; backups and DR are
`docs/42-offsite-backup.md` and `docs/17-disaster-recovery.md`. Two imperatives
live here because getting either wrong is destructive:

- **Persistent app storage is ZFS zvols declared in the `vm_additional_disks`
  blocks of `hosts.yml`** (created by `proxmox_vm`, mounted by `zvol_mount`).
  That block is the inventory of record, and the zvols outlive the pods and VMs
  on top of them.
- **NEVER create or destroy ZFS pools from Ansible.** Pools are created by hand;
  Ansible only sets properties, creates zvols, and mounts.

## User Management and Documentation index

`README.md` owns both: its "User Management" section plus `docs/03-ssh-users.md`
for the user model, and its **Documentation** section for the docs index and the
conventions a new doc must follow.

## Important Context Files

- `CLUSTER_STATUS.txt` - Full cluster state snapshot (gitignored; generated locally via `task collect-state`)

## Credits

Repository structure inspired by [FreekingDean/homelab](https://github.com/FreekingDean/homelab)
