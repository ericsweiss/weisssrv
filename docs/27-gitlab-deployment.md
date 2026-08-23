# GitLab Deployment Guide

Complete guide for deploying GitLab EE (using CE features) on the homelab.

## Overview

GitLab is deployed as a standalone VM on pve-nas-01 with:
- **URL**: https://git.esweiss.com (internal) / https://git.ericsweiss.com (external)
- **Container Registry**: https://registry.git.ericsweiss.com
- **GitLab Pages**: https://*.pages.git.ericsweiss.com
- **CI/CD Runners**: Helm-deployed on k3s cluster
- **SSO**: Authentik SAML integration with group-based admin access

## Architecture

```
                   +-----------------+
                   |   Internet      |
                   +--------+--------+
                            |
                   +--------v--------+
                   |   Cloudflare    |
                   |  (DNS + Proxy)  |
                   +--------+--------+
                            |
                   +--------v--------+
                   |    Traefik      |
                   |  (k3s ingress)  |
                   +--------+--------+
                            |
        +-------------------+-------------------+
        |                   |                   |
+-------v-------+   +-------v-------+   +-------v-------+
|   GitLab Web  |   |   Registry    |   |    Pages      |
|  :443 (TLS)   |   |  :5050 (TLS)  |   |  :8443 (TLS)  |
+---------------+   +---------------+   +---------------+
        |                   |                   |
        +-------------------+-------------------+
                            |
                   +--------v--------+
                   |   GitLab VM     |
                   | 192.168.0.153   |
                   |   pve-nas-01    |
                   +-----------------+
```

## Specifications

| Component | Value |
|-----------|-------|
| IP Address | 192.168.0.153 |
| Proxmox Host | pve-nas-01 |
| Root Disk | ssd pool (100GB) - OS, GitLab binaries, configs |
| Repo Disk | ssd pool (200GB zvol) - Git repositories |
| Resources | 6 vCPUs, 16GB RAM |
| VMID | 153 |
| GitLab Version | See `gitlab_version` in `ansible/inventories/prod/group_vars/all.yml` |
| Git SSH Port | 22 (internal), 2222 (external via iptables redirect) |

**SSH Port Redirect:** GitLab's SSH daemon runs on port 22. External access on port 2222 is handled via iptables NAT redirect (`PREROUTING -p tcp --dport 2222 -j REDIRECT --to-ports 22`). This allows internal LAN users to use port 22 directly while external users connect on port 2222.

### Storage Architecture

GitLab uses **separate storage** for Git repository data:

```
VM Root Disk (100GB on ssd pool)
├── /                           # OS, packages
├── /opt/gitlab/                # GitLab binaries
├── /etc/gitlab/                # Configuration
├── /var/opt/gitlab/            # Runtime data
│   ├── backups/                # Backup files
│   ├── postgresql/             # PostgreSQL database
│   ├── redis/                  # Redis cache
│   ├── gitlab-rails/uploads/   # Issue/MR attachments
│   └── lfs-objects/            # Git LFS storage
└── /var/log/gitlab/            # Logs

Repository Disk (200GB zvol: ssd/appdata/gitlab/repos)
├── /mnt/gitlab-repos/git-data/ # Git repositories
│   └── repositories/           # Bare git repos (@hashed/)
└── /mnt/gitlab-repos/registry/ # Container Registry blob store (tens of GB)
```

**Note:** Gitaly repository storage **and** the Container Registry blob store use
the separate disk. Uploads (issue/MR attachments) and LFS objects remain on the
root disk - they are typically much smaller than repos/registry, and keeping them
on root disk simplifies configuration while the separate disk isolates the two
largest, fastest-growing consumers.

The registry blob store was the single largest component and the
`DiskUsageCritical` culprit on the OS disk — 43G as of 2026-08, which is why it
no longer shares a disk with the OS. It is relocated via
`gitlab_registry_data_dir` (role default: the Omnibus path
`/var/opt/gitlab/gitlab-rails/shared/registry`; prod override:
`/mnt/gitlab-repos/registry`), which renders `gitlab_rails['registry_path']` in
`gitlab.rb`.

**One-time migration (brief registry outage):** the first deploy after this
override lands moves the existing store onto the zvol. The role stops the
registry (`gitlab-ctl stop registry`), renames the directory across devices
onto `/mnt/gitlab-repos/registry`, and `gitlab-ctl reconfigure` then repoints
and restarts it. Expect a **brief registry outage** during that deploy (the web
UI, Git, Pages, and CI are unaffected). The move is idempotent - after it, the
source path is gone, so subsequent deploys skip it. A fresh-from-scratch deploy
never triggers the move (reconfigure creates the store on the zvol directly).

**Benefits:**
- Independent sizing - repos and registry can grow without filling the root disk
- Better I/O isolation - git/registry operations don't compete with PostgreSQL
- Easier backups - the zvol is raw-ZFS replicated (ssd/appdata -> archive) and
  can be snapshotted separately

## Prerequisites

1. **Authentik SSO** - the GitLab SAML provider, application and `gitlab-*`
   groups are declared in `terraform/authentik/` and applied under supervision
   ([docs/40](40-authentik-terraform.md)); do not create them in the Authentik
   UI. They must exist before deployment.
2. **1Password Items** - Create required items (see below)
3. **Terraform DNS** - Apply Cloudflare DNS records

## 1Password Items Required

The canonical inventory of every 1Password item the deployment expects lives in
[docs/15-credential-rotation.md](15-credential-rotation.md) ("Required 1Password
Items"). The table below is the GitLab-scoped subset needed for this setup —
create these items in your **Homelab** vault:

| Item | Type | Fields |
|------|------|--------|
| **GitLab** | Login | `root-password` |
| **GitLab SSO** | Password | `saml-cert-fingerprint` |
| **GitLab Runner** | Password | `runner-token` (runner authentication token, `glrt-*` format) |
| **GitLab Runner Privileged** | Password | `runner-token` (runner authentication token, `glrt-*` format, tags: `infrastructure`) |
| **GitLab API Token** | Password | `credential` (**instance-admin** PAT — `svc-gitlab-admin` — for the Web IDE Application Settings block; injected as `GITLAB_ADMIN_API_TOKEN`, see docs/15 and § Web IDE Extension Host) |
| **SMTP Relay Auth** | Login | `username`, `password` (for GitLab email notifications) |
| **Email Config** | Login | `root_alias` (admin email address, e.g., `admin@example.com`) |
| **SSH Key** | SSH Key | `public key` (for VM access via cloud-init) |

**Note:** The SMTP Relay Auth, Email Config, and SSH Key items should already exist if you've deployed the base infrastructure. GitLab uses SMTP Relay Auth to send email notifications (merge requests, CI/CD alerts, etc.) through the smtp-relay service.

## Deployment Steps

### Step 1: Create 1Password Items

```bash
# Create GitLab item with root password
op item create --vault Homelab --category login \
  --title "GitLab" \
  "root-password=$(openssl rand -base64 32)"

# Create placeholder for SSO (populate after Authentik setup)
op item create --vault Homelab --category password \
  --title "GitLab SSO" \
  "saml-cert-fingerprint=placeholder"

# Create placeholder for Runner (populate after GitLab is running)
op item create --vault Homelab --category password \
  --title "GitLab Runner" \
  "runner-token=placeholder"
```

### Step 2: Apply Terraform DNS Records

The GitLab DNS records are defined in `terraform/cloudflare/dns.tf`. Apply them:

```bash
task terraform:plan
task terraform:apply
```

This creates (or updates):
- `git.ericsweiss.com` → A record (DNS-only, IP updated by DDNS) - unified URL for web + SSH
- `registry.git.ericsweiss.com` → CNAME to `direct.ericsweiss.com` (DNS-only, TLS via Traefik)
- `pages.git.ericsweiss.com` → CNAME to `direct.ericsweiss.com` (DNS-only, TLS via Traefik)
- `*.pages.git.ericsweiss.com` → CNAME to `direct.ericsweiss.com` (DNS-only wildcard, TLS via Traefik)
- `direct.ericsweiss.com` → A record (DNS-only, IP updated by DDNS)

**Note:** `git.ericsweiss.com` uses DNS-only mode (not Cloudflare-proxied) to allow both HTTPS
and SSH access on the same hostname. Nested subdomains (registry.git, pages.git, *.pages.git)
use `direct.ericsweiss.com` because Cloudflare Universal SSL only covers first-level wildcards.

The internal equivalents (`pages.git.esweiss.com` and `*.pages.git.esweiss.com`)
are served by the same IngressRoutes and covered by the GitLab wildcard
Certificate in `kubernetes/apps/vm-ingress/`; internally they resolve via the
AdGuard rewrites in `group_vars/dns.yml`, so there is no Terraform record for
them.

**DNS cutover verification:** If `git.ericsweiss.com` was previously a CNAME (e.g., pointing
to `direct.ericsweiss.com`) and is being changed to an A record, verify the transition:

```bash
# 1. Preview the change
task terraform:plan
# Look for: module.zone.cloudflare_record.protected_external_content["git"]
#           changing type from CNAME to A (edit local.dns_records["git"] in dns.tf)

# 2. Apply the change
task terraform:apply

# 3. Verify DNS resolution (may take a few minutes for propagation)
dig +short git.ericsweiss.com
# Should return your public IP (same as direct.ericsweiss.com)

# 4. Verify GitLab is accessible
curl -sf --max-time 10 "https://git.ericsweiss.com/-/health"
# Should return: GitLab OK
```

### Step 3: Deploy AdGuard DNS Rewrites

```bash
task dns:deploy
```

This adds internal DNS rewrites for `git.esweiss.com`, `registry.git.esweiss.com`, etc.

### Step 4: Configure Authentik SSO (SAML)

> **Authentik objects are Terraform-managed.** The GitLab SAML provider, the
> `git` application, and the `gitlab-admins`/`gitlab-users` groups are codified
> in `terraform/authentik/` (`providers_saml.tf`, `applications.tf`, `groups.tf`)
> and changed via a supervised `terraform apply` — **not the Authentik UI**
> ([docs/40-authentik-terraform.md](40-authentik-terraform.md)). The UI walkthrough
> below is retained only as a reference for capturing the ACS URL / Issuer /
> certificate fingerprint; make the actual provider/app/group changes in the
> `.tf` files.

1. Log into Authentik at https://auth.ericsweiss.com
2. Navigate to **Applications → Providers**
3. Click **Create** and select **SAML Provider**
4. Configure:

| Field | Value |
|-------|-------|
| Name | `GitLab` |
| Authorization flow | `default-authorization-flow` |
| ACS URL | `https://git.ericsweiss.com/users/auth/saml/callback` |
| Issuer | `https://git.ericsweiss.com` |
| Service Provider Binding | `Redirect` |
| Signing Certificate | Select available certificate |

5. Click **Finish** to create the provider
6. Navigate to **System → Certificates**
7. Expand the signing certificate and copy the **SHA1 Fingerprint**
8. Update 1Password `GitLab SSO` item with the fingerprint as `saml-cert-fingerprint`
9. Navigate to **Applications → Applications**
10. Click **Create** and configure:

| Field | Value |
|-------|-------|
| Name | `GitLab` |
| Slug | `git` |
| Provider | `GitLab` (SAML provider created above) |
| Launch URL | `https://git.ericsweiss.com` |

11. **Configure group-based access** (REQUIRED):

    **IMPORTANT**: The default configuration enforces group-based access. Users must be in
    one of the required groups to log in via SSO. Complete these steps BEFORE deploying:

    - Create Authentik groups: `gitlab-users`, `gitlab-admins`
    - Assign users to appropriate groups (at minimum, add yourself to `gitlab-users`)
    - The groups are configured in `ansible/inventories/prod/group_vars/gitlab_servers.yml`:
      ```yaml
      gitlab_saml_required_groups:
        - "gitlab-users"
        - "gitlab-admins"
      gitlab_saml_admin_groups:
        - "gitlab-admins"
      ```

    If you skip this step, SSO login will fail with "User is not allowed" errors.

### Step 5: Deploy GitLab VM and Application

```bash
# Full deployment (provisions VM + installs GitLab)
task gitlab:deploy

# Or check mode first
task gitlab:deploy-check
```

> **Initial bring-up before the first cert distribution:** GitLab's nginx
> terminates TLS with the wildcard cert distributed by `acme_certs`, and the
> role asserts the cert + key exist before `gitlab-ctl reconfigure`. For a
> certless first deploy you must set **all three** of
> `gitlab_nginx_listen_https`, `gitlab_registry_enabled`, and
> `gitlab_pages_enabled` to `false` — the registry and pages nginx vhosts
> always terminate TLS with the same cert, so disabling only the web listener
> still fails. Re-enable them after the first cert distribution and re-run
> `task gitlab:deploy`.

### Step 6: Traefik IngressRoutes (Flux-managed)

The Traefik IngressRoutes for `git.ericsweiss.com` (web only — there is no SSH
entrypoint or IngressRouteTCP; :2222 reaches the VM directly, see § Git SSH
Access), `registry.git.ericsweiss.com`, and `*.pages.git.ericsweiss.com` live
under
`kubernetes/apps/vm-ingress/gitlab.yaml`, with ExternalName Services in
`services-gitlab.yaml` and certificates in `gitlab-certificate.yaml`. Flux
reconciles them from the `apps` Kustomization. To change them, edit the
YAML + commit + push; the GitLab agent's Flux module reconciles on push
(fallback: ~1-minute poll).

### Step 7: Verify Deployment

```bash
# Check GitLab status
task gitlab:status

# Test web access
curl -I https://git.esweiss.com
```

### Step 8: Get Runner Authentication Token

**Note**: GitLab 16.0+ uses runner authentication tokens (`glrt-*` format) instead of the deprecated registration tokens. The old `runnerRegistrationToken` method was removed in GitLab 18.0.

Two runners are needed:
- **Shared runner** (unprivileged): For other GitLab projects and collaborators to deploy to k3s. Runs untagged jobs. Tag: `k8s-deploy`.
- **Infrastructure runner** (privileged): Handles ALL weisssrv CI/CD jobs (lint, test, deploy). Uses DinD for Molecule tests, SSH for Ansible deploys. Tag: `infrastructure`.

The weisssrv `.gitlab-ci.yml` sets `default: tags: [infrastructure]` so all its jobs route to the infrastructure runner.

**Runner 1 (Shared - Non-privileged, instance runner):**
1. Log into GitLab as root (use password from 1Password)
2. Navigate to **Admin Area → CI/CD → Runners**
3. Click **New instance runner**
4. Configure: Tags = `k8s-deploy`, Run untagged jobs = **Yes**
5. Click **Create runner** and copy the `glrt-*` token
6. Update 1Password `GitLab Runner` item with this token as `runner-token`

**Runner 2 (Infrastructure - Privileged, PROJECT runner):**

The privileged runner must be registered as a **project runner locked to the
weisssrv project — never an instance runner**. Tags are cooperative routing
(any project could declare `tags: [infrastructure]`), so the registration
scope is the isolation boundary that keeps other projects' jobs away from
root+DinD execution.

1. In the **weisssrv project**, navigate to **Settings → CI/CD → Runners**
2. Click **New project runner**
3. Configure: Tags = `infrastructure`, Run untagged jobs = **No**, Lock to
   current projects = **Yes**
4. Click **Create runner** and copy the `glrt-*` token
5. Create 1Password item `GitLab Runner Privileged` with this token as `runner-token`

Both runner managers connect and clone via `https://git.esweiss.com`
(`gitlabUrl` + `clone_url` in the HelmRelease values) — the LAN path via the
AdGuard rewrite to the internal Traefik VIP (.101) — instead of
`git.ericsweiss.com`, keeping runner traffic off the WAN/hairpin path.

> **Security note on privileged runner:** The infrastructure runner (`gitlab-runner-privileged`)
> has `privileged = true` and `protected = false`, meaning it can run jobs from unprotected
> branches. This is acceptable for a single-user homelab where you control all branches and
> merge requests. If collaborators are added to the GitLab instance, switch to
> `protected = true` so the privileged runner only executes jobs on protected branches (e.g.,
> `main`). This prevents untrusted MR code from running with elevated privileges.
>
> The privileged runner lives in its **own** `gitlab-runner-privileged` namespace
> (PSS `enforce: privileged`), separate from the shared, untrusted runner in
> `gitlab-runner` (PSS `enforce: baseline`). This namespace boundary is the
> isolation control: the shared runner's namespace-scoped RBAC cannot read the
> privileged runner's token Secret, and PSS admission blocks untrusted shared
> job pods from escalating to privileged/root.

### Step 9: GitLab Runners (Flux-managed)

Both runners (shared and privileged) are Flux `HelmRelease`s under
`kubernetes/apps/gitlab-runner/` and `kubernetes/apps/gitlab-runner-privileged/`.
Their runner tokens flow from 1Password through `ExternalSecret`s. To deploy
or update them, edit the relevant `release.yaml` (or bump
`gitlab_runner_helm_version` in `all.yml` + run `task flux:sync-versions`)
and push. Flux reconciles the HelmRelease on push (fallback: ~1-minute poll).

See `docs/29-flux-operations.md` for the full workflow (rotating runner
tokens, bumping chart versions, troubleshooting stuck releases).

> **One-time migration note (privileged runner namespace move):** when the
> change that relocates the privileged runner from the `gitlab-runner` namespace
> to its own `gitlab-runner-privileged` namespace first reconciles, Flux prunes
> the old HelmRelease and installs the new one — i.e. helm-controller uninstalls
> the old manager (Deployment, RBAC, token Secret) and the new one re-registers
> from its freshly-synced ESO token. There is a brief teardown/recreate window
> in which an in-flight `infrastructure`-tagged CI job could be interrupted. Do
> the cutover in a CI-quiet window (or `kubectl -n gitlab-runner-privileged scale
> deploy --replicas=0` first), then verify: `kubectl -n gitlab-runner-privileged
> get pods` is Running, the runner shows online in GitLab, and nothing privileged
> remains in the old namespace (`kubectl get deploy,sa,secret -n gitlab-runner |
> grep privileged` returns nothing).

## Registry pull-through cache (CI)

Every molecule CI job starts a **fresh DinD daemon** with an empty image store,
whose first act is a cold pull of
`registry.git.ericsweiss.com/eric/weisssrv/molecule-test` (~30s/job; a pipeline
runs ~30 molecule jobs). `kubernetes/apps/registry-cache/` runs the CNCF
[distribution](https://distribution.github.io/distribution/) `registry` image in
**pull-through (proxy) mode** so the first job warms a cluster-local cache and
the rest are served over the LAN.

**Shape** (full rationale in `kubernetes/apps/registry-cache/README.md`):

- A single Deployment (`registry-cache` namespace) with
  `REGISTRY_PROXY_REMOTEURL=https://registry.git.ericsweiss.com` and a
  read_registry deploy token (below). Registry API on `:5000`, Prometheus debug
  listener on `:5001`.
- Cache storage is a node-local `emptyDir` (10Gi cap) — disposable, re-warms on
  the next pull; no NFS/zvol, nothing to back up.
- **Upstream path**: the pod pins `registry.git.ericsweiss.com` → the internal
  Traefik VIP `192.168.0.101` via `hostAliases` (the pod-scope twin of the
  node-level `k3s_registry_host_pins`), so the upstream fetch stays on the
  internal Traefik path instead of hairpinning the flaky external/Cloudflare DNS.
  Egress is default-deny + DNS + `:443` to the `traefik` namespace only.
- **Consumers**: only the `gitlab-runner-privileged` namespace (the DinD job
  pods) may reach `:5000`; the metrics port is scraped by Prometheus
  (`observability/service-monitors/registry-cache.yaml`) and paged on by the
  `RegistryCacheDown` alert (a `warning`, not `critical`: a cache outage only
  makes CI cold-pull direct — slower, not broken).
- Version pinned as `registry_cache_version` in `all.yml`
  (`${registry_cache_version}` placeholder, Flux `cluster-versions` ConfigMap).

### Deploy-token mint runbook

The cache authenticates to the upstream registry with a GitLab **deploy token**
(a repo/registry credential, distinct from runner/personal tokens), scoped
`read_registry` — the least privilege needed to pull private-project blobs.

1. In the **`eric/weisssrv` project**: **Settings → Repository → Deploy tokens**.
2. Create a token: name `registry-cache`, expiration blank (or a rotation
   cadence), scope **`read_registry` only** (leave `read_repository`,
   `write_registry`, etc. unchecked). GitLab shows the username +
   token **once**.
3. Store them in the **GitLab Registry Cache Deploy Token** 1Password item
   (fields `username`, `token`) — see the item entry in
   `docs/15-credential-rotation.md`. ESO's `registry-cache-secrets`
   ExternalSecret syncs them into the cache pod.
4. Ship the app (it is Flux-managed like every other `kubernetes/apps/*` dir).
   Verify: `kubectl -n registry-cache get pods` Running, then from a runner-side
   shell `curl -fsS http://registry-cache.registry-cache.svc.cluster.local:5000/v2/`
   returns `{}` (API up).

Rotation: mint a replacement deploy token, update both 1Password fields, then
`task flux:rotate-secret -- registry-cache`.

### CI wiring (owned by `.gitlab-ci.yml`)

Implemented in `.gitlab/ci/integration-jobs.yml` — the DinD service arg and the
cache-first pull with direct fallback. The shape, for reference:

- The DinD **service** runs with the cache added as an insecure registry
  (plaintext, cluster-internal):
  `--insecure-registry=registry-cache.registry-cache.svc.cluster.local:5000`.
- The molecule `before_script` pulls **cache-first with a direct-registry
  fallback**, so a cache outage (or cold cache) never breaks CI — it only makes
  that job cold-pull direct:

  ```yaml
  # Pull the molecule-test image via the in-cluster pull-through cache, falling
  # back to the real registry if the cache is unreachable. MOLECULE_IMAGE keeps
  # the canonical (direct) ref so the tests are unchanged.
  variables:
    REGISTRY_CACHE: "registry-cache.registry-cache.svc.cluster.local:5000"
    MOLECULE_IMAGE: "registry.git.ericsweiss.com/eric/weisssrv/molecule-test:latest"
  before_script:
    - |
      CACHED="$REGISTRY_CACHE/eric/weisssrv/molecule-test:latest"
      if docker pull "$CACHED"; then
        docker tag "$CACHED" "$MOLECULE_IMAGE"   # present tests the canonical ref
      else
        echo "registry cache miss/unreachable — pulling direct"
        docker pull "$MOLECULE_IMAGE"
      fi
  ```

## Task Commands

```bash
# VM deployment (Ansible)
task gitlab:deploy          # Full deployment (VM + GitLab application)
task gitlab:deploy-check    # Dry-run deployment

# Operations
task gitlab:status          # Show GitLab and runner status
task gitlab:verify          # Run smoke tests (web UI, registry, pages, SSH)
task gitlab:backup          # Create GitLab backup
task gitlab:console         # SSH to GitLab VM
task gitlab:logs            # View GitLab logs
task gitlab:reconfigure     # Reconfigure GitLab after changes

# Ingress, runners, agent — all Flux-managed: edit YAML + git push.
# See kubernetes/apps/{gitlab-runner,gitlab-runner-privileged,gitlab-agent}/
# and kubernetes/apps/vm-ingress/gitlab*.yaml.
```

## Backups

GitLab backups run daily at 2:00 AM from the `gitlab-backup.timer` systemd timer
(`gitlab_backup_oncalendar`; the role removes the legacy root cron job, so
`systemctl list-timers gitlab-backup` is the check, not `crontab -l`). Its
service calls `/usr/local/sbin/gitlab-backup-run.sh` (deployed by the gitlab role), which
runs `gitlab-backup create CRON=1`, copies `gitlab-secrets.json` + `gitlab.rb`
into the backup path on success (a restore needs them to decrypt CI variables,
2FA, and runner tokens), and emits backup-freshness metrics.

**Backup scope — registry and artifacts are SKIPPED.** The nightly tarball
excludes the container registry (rebuildable CI images, by far the largest
component) and CI artifacts (ephemeral job output) via
`gitlab_backup_skip: "registry,artifacts"`, so a backup is DB + repos +
config, not multi-GB.

**Restore caveat:** `gitlab-backup restore` from these tarballs does NOT bring
back registry images or artifacts, and **vzdump does not cover the registry
either** — the blobs live on the `gitlab-repos` zvol (`/mnt/gitlab-repos/registry`),
which `hosts.yml` declares `vzdump_backup: false` precisely because it is
already covered elsewhere. A vzdump restore therefore returns the OS disk and an
empty registry. The registry's actual copy is the nightly raw-encrypted
`ssd/appdata → archive` ZFS replication, so restoring it means receiving that
dataset and `zfs load-key`-ing it before the data is readable (procedures in
[docs/17](17-disaster-recovery.md)). In practice most images are also
re-pushable from CI, which is why the blob store is not in the tarball; CI
artifacts regenerate on the next pipeline run.

```bash
# Manual backup (also re-seeds the freshness metric)
task gitlab:backup

# List backups (the landing zone is the NFS mount, not the VM root disk)
ssh gitlab "sudo ls -la /mnt/backups-offsite/"

# Restore from backup (DB + repos + config; NOT registry/artifacts — see above)
ssh gitlab "sudo gitlab-backup restore BACKUP=<timestamp>"
```

Proxmox VM snapshots provide additional disaster recovery capability.

### Backup monitoring

The wrapper writes `/var/lib/node_exporter/gitlab_backup.prom` with:

- `gitlab_backup_last_run_success` (1/0)
- `gitlab_backup_last_run_duration_seconds`
- `gitlab_backup_last_success_timestamp_seconds` (preserved across failed runs,
  so staleness measures time-since-last-success, not time-since-last-attempt)
- `gitlab_backup_last_size_bytes` (newest tarball; informational, no alert)

`node_exporter_host` runs on the GitLab VM (port 9101) so the textfile metric is
scraped alongside the host's other metrics. Prometheus discovers the VM via the
shared `node-exporter-host` Service/Endpoints
(`kubernetes/infrastructure/observability/exporters/node-exporter-host.yaml`),
which lists 192.168.0.153 among its Endpoints addresses. The `sg-metrics`
security group already authorizes the 9101 scrape from the k3s nodes, so no
firewall change is needed.

Two alerts (`homelab.scripts` group) cover backup health, on different
timescales — `GitLabBackupFailed` fires ~1h after a single failed run, while
`GitLabBackupStale` only fires once no run has succeeded for 48 hours:

| Alert | Condition |
|-------|-----------|
| `GitLabBackupFailed` | `gitlab_backup_last_run_success == 0` for 1h (a single failed run) |
| `GitLabBackupStale` | no successful backup in > 2 days, or the metric is absent, for 1h |

The `absent()` arm of `GitLabBackupStale` fires before the first 02:00 run. After
a fresh deploy, run `task gitlab:backup` once to seed the metric.

## Updates

### Updating GitLab

1. Update `gitlab_version` in `ansible/inventories/prod/group_vars/all.yml`
2. Run deployment:
   ```bash
   task gitlab:deploy
   ```

### Updating GitLab Runner

Both runners share the same Helm chart version and are Flux-managed. Update:

1. Bump `gitlab_runner_helm_version` in `ansible/inventories/prod/group_vars/all.yml`
2. Regenerate the ConfigMap and commit:
   ```bash
   task flux:sync-versions
   git add ansible/inventories/prod/group_vars/all.yml \
           kubernetes/infrastructure/sources/versions-configmap.yaml
   git commit -m "Bump GitLab Runner chart to <version>"
   git push
   ```
3. Flux reconciles both `gitlab-runner` and `gitlab-runner-privileged`
   HelmReleases on the next cycle. Verify with `flux get hr -n gitlab-runner`.

## Troubleshooting

### GitLab Reconfigure Fails

```bash
ssh gitlab "sudo gitlab-ctl reconfigure"
ssh gitlab "sudo gitlab-ctl status"
```

### SAML/SSO Issues

1. Verify SAML certificate fingerprint matches Authentik certificate
2. Check ACS URL matches exactly: `https://git.ericsweiss.com/users/auth/saml/callback`
3. Verify Issuer matches `external_url`: `https://git.ericsweiss.com`
4. Check GitLab logs:
   ```bash
   ssh gitlab "sudo gitlab-ctl tail puma"
   ```
5. **Admin groups not syncing**: Ensure users are in the Authentik group and `gitlab_saml_admin_groups` includes the group name

### Container Registry Issues

```bash
# Check registry service
ssh gitlab "sudo gitlab-ctl status registry"

# Registry logs
ssh gitlab "sudo gitlab-ctl tail registry"

# Test registry access
docker login registry.git.ericsweiss.com
```

### Pages Issues

```bash
# Check pages service
ssh gitlab "sudo gitlab-ctl status gitlab-pages"

# Pages logs
ssh gitlab "sudo gitlab-ctl tail gitlab-pages"
```

### Runner Not Connecting

Both runners use `https://git.esweiss.com` for `gitlabUrl` and `clone_url` —
the LAN path via the AdGuard rewrite to the internal Traefik VIP (.101). If a
runner cannot connect, verify pods resolve `git.esweiss.com` to 192.168.0.101
(CoreDNS forwards to AdGuard on the k3s nodes' resolvers).

```bash
# Check runner pod status
kubectl get pods -n gitlab-runner

# Check runner logs
kubectl logs -n gitlab-runner -l app=gitlab-runner

# Verify CI_SERVER_URL is using the LAN URL
kubectl get deploy gitlab-runner -n gitlab-runner -o yaml | grep CI_SERVER_URL

# Verify DNS from inside the cluster. Do NOT `kubectl run` a throwaway pod:
# the `default` namespace enforces restricted PSA and carries a default-deny-all
# NetworkPolicy, so the probe fails for reasons unrelated to DNS (docs/29).
kubectl -n gitlab-runner exec deploy/gitlab-runner -- nslookup git.esweiss.com

# Verify registration token
kubectl get secret -n gitlab-runner
```

## Git SSH Access

### Summary

- **Unified URL**: `git.ericsweiss.com` works for both HTTPS and SSH (port 2222)
- **LAN (internal)**: `git.esweiss.com` resolves to the **internal Traefik VIP**
  (192.168.0.101), so it is an HTTPS name only. For direct port-22 SSH to the VM
  use `gitlab.esweiss.com` (192.168.0.153), the guest's own rewrite
- **External (internet)**: Use `git.ericsweiss.com` on port 2222 (DNS-only, same as web URL)

**Architecture:** `git.ericsweiss.com` is DNS-only (not Cloudflare-proxied), allowing both
HTTPS (via Traefik) and SSH traffic on the same hostname. This provides a unified URL for
clone operations regardless of protocol.

### Internal Access (LAN)

From the LAN, you can SSH directly to the GitLab VM using the internal domain:

```bash
# Clone via SSH direct to the VM (port 22) — note the guest's own name
git clone git@gitlab.esweiss.com:username/repo.git
```

Add to `~/.ssh/config` for easy internal access:
```
Host gitlab.esweiss.com
    Port 22
    User git
```

### External Access (Public Internet)

Git SSH access on port 2222 is open to the public internet, allowing collaborators and CI/CD
systems to clone and push without requiring Tailscale or VPN access. The Proxmox firewall
`sg-gitlab` security group allows port 2222 from any source.

**SSH Host:** External SSH uses `git.ericsweiss.com` - the same URL as the web UI. This is
possible because `git.ericsweiss.com` is DNS-only (not Cloudflare-proxied), allowing both
HTTPS and SSH traffic. GitLab displays this unified URL in clone URLs.

```bash
# Clone via SSH (port 2222) from external networks
git clone ssh://git@git.ericsweiss.com:2222/username/repo.git
```

Add to `~/.ssh/config` for easy external SSH access:
```
Host git.ericsweiss.com
    Port 2222
    User git
```

Then clone using standard syntax:
```bash
git clone git@git.ericsweiss.com:username/repo.git
```

### Universal SSH Config (Works Everywhere)

For a single config that works from any location, use `git.ericsweiss.com` with port 2222
for both hostnames. This works because LAN clients can reach the external IP (hairpin NAT)
and the iptables redirect on the GitLab VM maps port 2222 to port 22:

```
Host git.ericsweiss.com
    Port 2222
    User git

Host git.esweiss.com
    Port 2222
    User git
```

**Note:** This universal config requires hairpin NAT (NAT reflection) on your router —
**which the UniFi gateway does not provide** ([docs/46](46-unifi-network.md)): since the
2026-08 cutover, LAN clients cannot reach `git.ericsweiss.com`'s external IP, and the
AdGuard rewrite points that name at the Traefik VIP, where nothing listens on 2222. From
the LAN, use the split config above (`gitlab.esweiss.com`, port 22, straight to the VM).
External access on 2222 is unaffected.

**Why port 2222 for internal too?** `git.esweiss.com` resolves to the internal
Traefik VIP (192.168.0.101), not to the VM — the VM's own name is
`gitlab.esweiss.com` (192.168.0.153). Using `git.ericsweiss.com:2222` everywhere
keeps the config consistent across networks.
The iptables PREROUTING rule on the GitLab VM redirects port 2222 to port 22 regardless of
source. If you prefer lower latency on LAN, use the split config shown above (port 22 for
`git.esweiss.com`, port 2222 for `git.ericsweiss.com`).

### Recommended: HTTPS for Consistency

For the simplest experience across all networks (LAN, Tailscale, external), use HTTPS:
```bash
git clone https://git.ericsweiss.com/username/repo.git
```

HTTPS works uniformly via `git.ericsweiss.com` from any location and supports GitLab
personal access tokens for authentication.

**Note:** Administrative SSH access to the GitLab VM (port 22) remains restricted to admin
networks (LAN and Tailscale) for security.

## Container Registry Usage

### Authenticate

```bash
docker login registry.git.ericsweiss.com
# Use GitLab username and personal access token
```

### Build and Push

```bash
# Build image
docker build -t registry.git.ericsweiss.com/myproject/myapp:v1.0 .

# Push to registry
docker push registry.git.ericsweiss.com/myproject/myapp:v1.0
```

### Pull in k3s

```yaml
# In Kubernetes manifests
image: registry.git.ericsweiss.com/myproject/myapp:v1.0
imagePullSecrets:
  - name: gitlab-registry-secret
```

## GitLab Pages

### Enable for a Project

1. Go to project **Settings → Pages**
2. Enable Pages
3. Configure `.gitlab-ci.yml`:

```yaml
pages:
  stage: deploy
  script:
    - mkdir .public
    - cp -r public/* .public/
    - mv .public public
  artifacts:
    paths:
      - public
  only:
    - main
```

### Access Pages

Pages are available at:
- `https://<user>.pages.git.ericsweiss.com/<project>/`
- Or with custom domain configured

## Security Notes

1. **SSH architecture**: GitLab uses a single OpenSSH service with `AuthorizedKeysCommand` for git user authentication. Both administrative SSH (user `eric`) and Git SSH (user `git`) use the same SSH daemon on port 22, with port 2222 NAT-redirected to port 22 for external access.
2. **fail2ban protection**: Brute-force attacks on SSH are automatically blocked
   - 5 failed attempts within 10 minutes = 1 hour ban
   - Uses systemd journal backend with sshd filter (aggressive mode)
   - Check status: `sudo fail2ban-client status gitlab-ssh`
   - View banned IPs: `sudo fail2ban-client get gitlab-ssh banned`
   - Test filter: `sudo fail2ban-regex systemd-journal /etc/fail2ban/filter.d/sshd.conf`
3. **SAML authentication**: password auth stays available as break-glass for the
   root account; SAML is the normal path
4. **Firewall + sshd login restriction**: The `sg-gitlab` security group has differentiated access:
   - Port 2222 (Git SSH): Open to WAN for external collaborators
   - Port 22 (Admin SSH + LAN Git): Restricted to admin networks (LAN + Tailscale)
   - Port 443 (GitLab Web, TLS): k3s nodes + admin networks; port 80 stays open to admin sources for the HTTP→HTTPS redirect
   - Ports 5050/8443 (Registry/Pages, both TLS via the distributed wildcard cert): Restricted to k3s nodes only (routed via Traefik)

   Because the WAN-exposed 2222 redirects into the same system sshd as port 22,
   the admin-only intent is additionally **enforced in sshd itself** via an
   `AllowUsers` drop-in (`gitlab_ssh_allowusers_enabled`, on by default):
   `git` may log in from anywhere, the admin user only from `192.168.0.0/24`
   and `100.64.0.0/10` (the full Tailscale CGNAT range). Without it, every
   local account would accept internet pubkey auth attempts via 2222.
5. **Secrets**: All credentials via 1Password; never committed to git

## Web IDE Extension Host

GitLab's Web IDE serves the VS Code editor and per-extension iframes from a
separate "extension host" subdomain so the browser's same-origin policy isolates
extension JavaScript from the GitLab session cookie. CVE-2026-5816 (CVSS 8.0,
first fixed upstream in 18.11.1; the release pinned here is well past that — see
`gitlab_version` in `ansible/inventories/prod/group_vars/all.yml`) showed that
when the configured extension host is unreachable, GitLab falls back to serving
those assets from the GitLab origin itself — at which point a malicious
extension can hit `/api/v4/...` with the user's session cookie.

### Architecture

| Component | Value |
|---|---|
| Extension host | `*.ide.git.ericsweiss.com` (DNS-only via Cloudflare → MetalLB public VIP) |
| Apex | `ide.git.ericsweiss.com` (same target) |
| Cert | cert-manager `gitlab-web-ide-wildcard` → secret `gitlab-web-ide-ericsweiss-tls`, DNS-01 via Cloudflare |
| Route | Traefik `IngressRoute gitlab-web-ide` (HostRegexp single-label wildcard + apex Host) → `gitlab-web` Service:443 (HTTPS, `vm-tls-wildcard` ServersTransport) |
| Backend | Same GitLab nginx + Workhorse that fronts `git.ericsweiss.com` (catch-all server_name) |

### GitLab settings (Application Settings API)

Set by the `Web IDE | …` block in weisssrv-lib `ansible_collections/weisssrv/infra/roles/gitlab/tasks/main.yml`. These have no Omnibus `gitlab.rb` key on the pinned release (see `gitlab_version` in `ansible/inventories/prod/group_vars/all.yml`); they live only in the `application_settings` table.

`/api/v4/application/settings` is **instance-admin-only**, so `gitlab_api_token` must be a PAT belonging to an instance admin (`svc-gitlab-admin`), stored in the `GitLab API Token` 1Password item. A project/group access token 403s here (its bot user is never an instance admin). The role reads it from the **`GITLAB_ADMIN_API_TOKEN`** env var, deliberately *not* `GITLAB_API_TOKEN`: that name is a project-level masked CI/CD variable (the version-check comment token, `weisssrv-bot`), and project CI/CD variables outrank a job's `variables:` block — a job-level `GITLAB_API_TOKEN: op://…` would be silently shadowed by the project token, so `op run` never resolves the `op://` reference and the deploy 403s.

| Field | Value |
|---|---|
| `vscode_extension_marketplace_enabled` | `true` (best-effort on Free; tier rejection is acceptable) |
| `vscode_extension_marketplace_extension_host_domain` | `ide.git.ericsweiss.com` (bare hostname; GitLab generates per-extension subdomains from this parent) |
| `vscode_extension_marketplace_single_origin_fallback_enabled` | `false` |

### Initial deployment

The first deploy must order infrastructure → settings so Web IDE doesn't break in the gap between disabling the fallback and the new extension host being reachable. Run from the repo root:

1. **DNS first** — creates the Cloudflare records before cert-manager polls:
   ```bash
   task terraform:apply
   ```
2. **Wait for cert** — Flux + cert-manager DNS-01 typically takes 60-120s:
   ```bash
   kubectl -n gitlab wait certificate gitlab-web-ide-wildcard --for=condition=Ready --timeout=5m
   ```
3. **Smoke gate (manual, MUST pass before step 4)** — confirms DNS + TLS + IngressRoute are live:
   ```bash
   curl -sI https://probe.ide.git.ericsweiss.com/-/health | head -1
   # Expected: HTTP/2 200
   ```
4. **Apply settings + version bump**:
   ```bash
   task gitlab:deploy
   ```
5. **Verify** — Test 8 covers the new host; manual editor check confirms the iframe origin:
   ```bash
   task gitlab:verify
   # Then visit https://git.ericsweiss.com/<group>/<project>/-/ide/
   ```

If step 3 fails, do **not** run step 4 — the security flip would break Web IDE entirely until the asset host is reachable. Diagnose with `kubectl -n gitlab describe certificate gitlab-web-ide-wildcard` and `kubectl -n traefik logs deploy/traefik | grep ide.git`.

### Verification

Two independent things to check — routing and the settings flip. `task gitlab:verify`
covers only the first.

1. **Route** — Test 8 of `task gitlab:verify` probes
   `https://probe.ide.git.ericsweiss.com/-/health`. `/-/health` rather than
   `/-/readiness`: readiness is `monitoring_whitelist`-gated to the LAN and
   `*.ide.git.ericsweiss.com` hairpins via Cloudflare, so GitLab sees the WAN IP.
   A PASS proves DNS + cert + IngressRoute are wired end to end; it says nothing
   about the CVE-2026-5816 mitigation.
2. **Settings flip** — the mitigation lives in `application_settings`, which has
   no `gitlab.rb` key, so read it from Rails:
   ```bash
   ssh gitlab "sudo gitlab-rails runner 'puts ApplicationSetting.last.vscode_extension_marketplace_single_origin_fallback_enabled'"
   # expected: false
   ```
3. **Browser** — open `https://git.ericsweiss.com/<group>/<project>/-/ide/`, edit
   a file, confirm the editor iframe `src=` points at `*.ide.git.ericsweiss.com`
   and DevTools shows no SOP violations.

### Rollback

If Web IDE breaks after the flip, restore the (insecure but functional) fallback behavior:

```bash
TOKEN=$(op read "op://Homelab/GitLab API Token/credential")
curl -X PUT "https://git.ericsweiss.com/api/v4/application/settings" \
  -H "PRIVATE-TOKEN: $TOKEN" \
  -d "vscode_extension_marketplace_single_origin_fallback_enabled=true"
```

For a persisted rollback, flip `gitlab_web_ide_single_origin_fallback` to `true` in `ansible/inventories/prod/group_vars/all.yml` (or set it as an override) and re-run `task gitlab:deploy`. The DNS records and cert are inert without the route and safe to leave in place.


## Related documentation

- [docs/13-ci-cd.md](13-ci-cd.md) - pipeline structure, runners, GitHub mirroring
- [docs/19-k3s-deployment.md](19-k3s-deployment.md) - cluster where the runners live
- [docs/11-firewall.md](11-firewall.md) - `sg-gitlab` security group details
- [docs/40-authentik-terraform.md](40-authentik-terraform.md) - the SAML provider as code
- [docs/17-disaster-recovery.md](17-disaster-recovery.md) - restore paths
