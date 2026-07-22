# Observability Stack

This guide covers the observability platform: Prometheus for metrics, Grafana for dashboards, Loki for logs, Alloy for log collection, and a suite of exporters for infrastructure and application metrics.

## Overview

The observability stack runs entirely in the `observability` namespace and is reconciled by Flux as a single Kustomization (`infrastructure-observability`). It branches off `infrastructure-configs` in parallel with `apps`, and it owns the monitoring CRDs (ServiceMonitor, PodMonitor, PrometheusRule) that steady-state application monitoring relies on.

### Components

| Component | Chart / Image | Purpose |
|-----------|---------------|---------|
| **Prometheus** | `kube-prometheus-stack` (prometheus-community) | Metrics collection, storage, and evaluation |
| **Alertmanager** | (bundled with kube-prometheus-stack) | Alert routing to email and Discord |
| **Grafana** | (bundled with kube-prometheus-stack) | Dashboards at `grafana.esweiss.com` with Authentik OIDC |
| **node-exporter** | (bundled with kube-prometheus-stack) | Host-level metrics on every k3s node |
| **kube-state-metrics** | (bundled with kube-prometheus-stack) | Kubernetes object state metrics |
| **Loki** | `loki` (grafana) | Log aggregation, single-binary mode |
| **Alloy** | `alloy` (grafana) | DaemonSet log collector (successor to Promtail) |
| **Blackbox Exporter** | `prometheus-blackbox-exporter` (prometheus-community) | HTTP/TCP endpoint probing |
| **Proxmox Exporter** | `prompve/prometheus-pve-exporter` | Proxmox host metrics (all 6 nodes) |
| **ZFS Exporter** | `zfs_exporter` (on pve-nas-01) | ZFS pool and dataset metrics |
| **AdGuard Exporter** | `adguard-exporter` | DNS query and filter metrics (dns-01 + dns-02) |
| **Unbound Exporter** | `unbound_exporter` (on dns-01 + dns-02) | Recursive resolver metrics |
| **Exportarr** | `ghcr.io/onedr0p/exportarr` | *arr application metrics (Sonarr, Radarr, Lidarr, Prowlarr) |
| **Plex Exporter** | `jsclayton/prometheus-plex-exporter` | Plex Media Server metrics |
| **Redis Exporter** | `oliver006/redis_exporter` | Redis cache metrics (Bar Assistant) |
| **Node Exporter (host)** | `prometheus-node-exporter` (on Proxmox hosts) | Bare-metal hardware metrics (thermals, SMART, disk I/O) on port 9101 |
| **Alloy (host)** | `alloy` (Grafana APT) | Journald log collector on non-k8s hosts + 9 k3s VMs → Loki via HTTPS ingress (`loki.esweiss.com`) |

### Service Monitors

In addition to the built-in scrape targets, custom ServiceMonitors collect metrics from:

| Target | Namespace | Scrape Path | Interval |
|--------|-----------|-------------|----------|
| Flux controllers (PodMonitor) | flux-system | `/metrics` | 30s |
| Proxmox hosts (x6) | observability | `/pve` | 60s |
| ZFS exporter (pve-nas-01) | observability | `/metrics` | 60s |
| Node exporter (Proxmox hosts x6 + DNS hosts x2 + GitLab VM, port 9101) | observability | `/metrics` | 60s |
| Unbound exporter (dns-01 + dns-02) | observability | `/metrics` | 60s |
| AdGuard exporter | observability | `/metrics` | 60s |
| Plex exporter | observability | `/metrics` | 60s |
| Exportarr (Sonarr, Radarr, Lidarr, Prowlarr) | observability | `/metrics` | 60s |
| Redis exporter (Bar Assistant cache) | observability | `/metrics` | 60s |
| Meilisearch (Bar Assistant search) | recipes | `/metrics` | 60s |
| GitLab (VM) | observability (external endpoint) | `/-/metrics` | 60s |
| Home Assistant (VM) | observability (external endpoint) | `/api/prometheus` | 60s |
| Immich (VM: api + microservices) | observability (external endpoint) | `/metrics` | 60s |
| Nextcloud (VM exporter) | observability (external endpoint) | `/metrics` | 60s |
| wg-easy | wg-easy | `/metrics/prometheus` | 30s |
| Hindsight | hindsight | `/metrics` | 30s |
| Blackbox exporter (22 HTTP + 2 DNS + 3 TCP probes) | observability | `/probe` | 60s |
| cert-manager | cert-manager | `/metrics` | (chart default) |
| Traefik | traefik | `/metrics` | (chart default) |
| MetalLB | metallb-system | `/metrics` | (chart default) |
| external-dns | external-dns | `/metrics` | (chart default) |
| external-secrets | external-secrets | `/metrics` | (chart default) |

Controller ServiceMonitors (cert-manager, Traefik, MetalLB, external-dns, external-secrets) are created by their respective Helm charts when `serviceMonitor.enabled: true`.

## Architecture

### Reconciliation Chain

```
infrastructure-sources
        |
infrastructure-crds          (monitoring.coreos.com CRDs — prometheus-operator-crds)
        |
infrastructure-controllers
        |
infrastructure-configs
        |         \
infrastructure-   apps
observability    (parallel)
  <-- this stack
```

Flux reconciles the observability stack after `infrastructure-configs` is Ready (so the ClusterSecretStore, ClusterIssuer, and MetalLB IP pools exist). `apps` branches off `infrastructure-configs` in parallel — deliberately NOT gated on observability health, so a failed observability upgrade cannot freeze application reconciliation.

The `monitoring.coreos.com` CRDs are installed up-front by the `infrastructure-crds` stage (before controllers), so on a fresh bootstrap the monitoring CRs under `apps/` (the qbittorrent PodMonitor, the authentik chart ServiceMonitor) and this stack's own ServiceMonitors render cleanly — no manual CRD pre-apply. kube-prometheus-stack runs with `crds.enabled: false` + `install/upgrade.crds: Skip` (the CRD stage owns them). See docs/29 (Fresh bootstrap / disaster recovery) for the one-time live-cluster CRD adoption. In steady state the CRDs persist even if this Kustomization temporarily fails, so apps are unaffected by observability incidents.

### Namespace

All resources deploy into the `observability` namespace with Pod Security Standards set to `privileged` (required by node-exporter's hostPID, hostNetwork, and hostPath mounts).

### Storage

Both Prometheus and Loki use persistent ZFS zvols on pve-nas-01, attached to k3s-agt-nas-01 as SCSI disks and exposed via hostPath PVs:

| Component | ZFS zvol | Size | Mount Point | Node |
|-----------|----------|------|-------------|------|
| Prometheus TSDB | `ssd/appdata/prometheus/data` | 150GB | `/mnt/prometheus-data` | k3s-agt-nas-01 |
| Loki chunks + WAL | `ssd/appdata/loki/data` | 75GB | `/mnt/loki-data` | k3s-agt-nas-01 |

Both Prometheus and Loki pods are pinned to the NAS node via `nodeSelector: esweiss.com/nas: "true"` for local disk performance.

Two binding details worth knowing:

- **Loki's PVC must statically bind to the zvol PV.** The Loki chart value is
  `storageClass: "-"` — the chart's sentinel for rendering a literal
  `storageClassName: ""` into the volumeClaimTemplate. A plain `""` would be
  dropped from the rendered template, letting the PVC fall through to the
  `local-path` default StorageClass instead of binding the pre-provisioned
  `loki-data` zvol PV.
- **The Prometheus/Alertmanager VPAs target the operator CRs** (`kind:
  Prometheus` / `kind: Alertmanager` in `monitoring.coreos.com/v1`), not the
  operator-owned StatefulSets — the VPA requires a topmost scalable
  controller, and targeting the StatefulSet leaves it `ConfigUnsupported`
  with no recommendations. Both run in `Off` mode (recommendations only; the
  hand-tuned requests in the HelmRelease stay authoritative — docs/33).

Grafana uses an NFS-backed PV for its SQLite database (user preferences, service accounts, saved views):

| Component | NFS Path | Size | Server |
|-----------|----------|------|--------|
| Grafana SQLite DB | `/appdata/grafana` (NFS) | 1Gi | pve-nas-01 (192.168.0.102) |

### Log Collection

**In-cluster:** Alloy runs as a DaemonSet on all 9 k8s nodes (tolerates all taints). It tails pod logs from `/var/log/pods` and ships them to Loki's ClusterIP service.

**Host-side:** The `alloy_host` Ansible role installs Alloy from the Grafana APT repository on all non-k8s hosts and k3s VMs. This includes 6 Proxmox hosts, 2 DNS containers, smtp-relay, GitLab VM, Plex LXC, and all 9 k3s VMs (3 servers + 6 agents). It reads from systemd journald and ships to Loki over the TLS ingress at `https://loki.esweiss.com/loki/api/v1/push` (lan-tailscale-only + basic-auth middleware; credentials from the "Loki Push Auth" 1Password item, injected at deploy time). The plain NodePort `:31100` via the kube-vip VIP remains only as an emergency fallback — override `alloy_host_loki_url` per host if Traefik is down.

On k3s VMs, alloy_host collects kubelet, containerd, etcd, and other systemd journal entries. This complements (not duplicates) the in-cluster DaemonSet, which only collects container logs from `/var/log/pods`. The two collectors cover different log sources with no overlap.

Home Assistant (HAOS) does not support Alloy installation — it is a managed appliance OS without package management.

**Loki ruler (host-log-staleness alerts).** Loki runs its in-process ruler
(`loki.rulerConfig` in `loki/release.yaml`; `ruler.replicas: 0` — evaluated under
the monolithic `-target=all`, not a separate StatefulSet) and pushes firing alerts
to the in-cluster Alertmanager (`kube-prometheus-stack-alertmanager…:9093`). Rule
files are delivered by the already-running `loki-sc-rules` k8s-sidecar: the
`loki-rules-host-log-staleness` ConfigMap (generated by `loki/kustomization.yaml`,
label `loki_rule: "1"`, annotation `k8s-sidecar-target-directory: /rules/fake`)
is written into `/rules/fake` — `auth_enabled: false` means the single tenant is
`fake`, and the ruler's local storage (`directory: /rules`) scans
`<directory>/<tenant>/`. `loki/host-log-staleness.yaml` carries one
`HostLogShippingStale` alert per `alloy_host` host (23 hosts:
`base_managed` + plex/gitlab/nextcloud/immich/immich-ml + k3s servers/agents),
firing `severity: warning` (→ Discord) when `absent_over_time({job="journal",
host="<h>"}[45m])` holds for 15m — i.e. host-side journald shipping stopped past
the WAL buffer. Keep the host list in sync with the `alloy_host` play in
`ansible/playbooks/site.yml`. Post-merge verification (passive, no host
disruption): `kubectl -n observability port-forward loki-0 3100:3100` then `curl
-s localhost:3100/loki/api/v1/rules` (23 rules loaded) and
`.../prometheus/api/v1/rules` (each `health: ok` with a recent `lastEvaluation`).
The per-host ingest rate is visualised on the `infrastructure` dashboard's Logs
row ("Per-host journald Ingest Rate").

### Host-Level Metrics

The `node_exporter_host` Ansible role installs Prometheus node_exporter on all 6 bare-metal Proxmox hosts, both DNS LXC containers (dns-01, dns-02), and the GitLab VM (192.168.0.153), listening on port 9101 (port 9100 is reserved for the kube-prometheus-stack DaemonSet on k3s nodes). On Proxmox hosts this provides hardware-level metrics not available from inside VMs: CPU and board thermals, SMART disk health, physical disk I/O, and fan speeds. On the DNS containers and the GitLab VM it provides container/VM-level CPU/memory/disk metrics and enables the textfile collector for script-freshness metrics (cert renewal on DNS; backup freshness on GitLab).

Each bare host is scraped via a headless `Service` + manually pinned `Endpoints` in `kubernetes/infrastructure/observability/exporters/node-exporter-host.yaml`, selected by a single `ServiceMonitor` (`jobLabel: app.kubernetes.io/name`). The GitLab VM's 9101 scrape is authorized by the `sg-metrics` security group it already carries.

The role also configures the **textfile collector** directory (`/var/lib/node_exporter/`), which allows custom scripts to expose metrics by writing `.prom` files. Scripts running on the host (archive-backupctl, media-mover, acme.sh cert renewal, the GitLab backup wrapper) write timestamped success/failure metrics that node_exporter serves alongside its built-in hardware metrics. Prometheus scrapes these via ServiceMonitors targeting the external Endpoints, and PrometheusRules fire alerts (ArchiveBackupFailed/Stale, MediaMoverFailed/Stale, CertRenewalFailed, CertExpiringSoon/Critical, GitLabBackupFailed/Stale/StaleCritical, VzdumpBackupFailed/Stale, EtcdSnapshotStale — see Alert Rules) when scripts fail, become stale, or (for certs) approach expiry.

### Shared exporter install pipeline

The two download-based exporters — `zfs_exporter` (release tarball, on pve-nas-01) and `unbound_exporter` (release `.deb`, on dns-01/dns-02) — share a single install pipeline in the `prometheus_exporter` Ansible role: probe the installed version, conditionally download + verify + install the artifact, then enable/start the service and health-check it. Each remains a thin wrapper that supplies its own systemd unit (`zfs-exporter.service` runs as root for `/dev/zfs`; `unbound-exporter.service` uses `DynamicUser` + the unbound control socket) and passes its specifics (download URL, checksum, version-check command, port) to the shared role.

`node_exporter_host` is intentionally NOT built on this pipeline: it installs from the Debian apt repo (`prometheus-node-exporter`), ships a systemd drop-in override rather than a full unit, and carries bespoke textfile collectors (corosync + zpool health) and the drivetemp module — none of which generalize. See `ansible/roles/prometheus_exporter/README.md` for the parameter reference and the full exclusion rationale.

### Secrets

Two ExternalSecrets and one templated ExternalSecret pull credentials from 1Password:

1. **observability-secrets** -- Grafana OIDC client ID/secret.
2. **alertmanager-config** -- Alertmanager SMTP password, Discord webhook URL, and healthchecks.io Watchdog ping URL (rendered into alertmanager.yaml via ESO template, because Prometheus Operator does not support `webhook_url_file` for Discord configs).
3. **observability-exporter-secrets** -- Proxmox API token, Plex token, AdGuard Home credentials, Home Assistant API token, Meilisearch master key, and *arr API keys (Sonarr, Radarr, Lidarr, Prowlarr).

### Ingress

Grafana is exposed internally at `grafana.esweiss.com` via a Traefik IngressRoute with a dedicated cert-manager Certificate. OIDC authentication is handled natively by Grafana (no forward-auth middleware).

## Pre-deployment Steps

Steps 1--3 and 6 are **required** for the observability Kustomization to reconcile successfully. Steps 4, 5, and 7 are **post-deploy / optional** and can be completed after the stack is running.

### 1. Create 1Password Items

Create the following items in the "Homelab" vault (if they do not already exist):

**Grafana SSO** (new item):
- Field: `oidc-client-id` -- Authentik OIDC provider client ID
- Field: `oidc-client-secret` -- Authentik OIDC provider client secret

**Proxmox API Token** (new item):
- Field: `user` -- Proxmox user (e.g., `monitoring@pve`)
- Field: `token-name` -- API token name (e.g., `exporter`)
- Field: `token-secret` -- API token secret (UUID)

**Discord Alert Webhook** (new item):
- Field: `url` -- Discord channel webhook URL for alert notifications

**Healthchecks Watchdog** (new item):
- Field: `ping url` -- healthchecks.io check ping URL for the Watchdog dead-man's switch (until it exists, ESO serves the last rendered Secret and `ExternalSecretSyncFailure` fires — see docs/15)

**Plex Token** (new item):
- Field: `token` -- X-Plex-Token for Plex exporter metrics

**Download Client API Keys** (new item):
- Field: `sonarr-api-key` -- from Sonarr Settings > General > API Key
- Field: `radarr-api-key` -- from Radarr Settings > General > API Key
- Field: `lidarr-api-key` -- from Lidarr Settings > General > API Key
- Field: `prowlarr-api-key` -- from Prowlarr Settings > General > API Key

**Loki Push Auth** (new item):
- Field: `htpasswd` -- bcrypt users file for the Loki push IngressRoute basic-auth middleware (consumed by host-side `alloy_host` shippers; see Log Collection above)

All *arr API keys are configured and their exportarr Deployments are active (replicas: 1). If you need to rotate API keys, update the values in the "Download Client API Keys" 1Password item and run `task flux:rotate-secret -- observability/observability-exporter-secrets`.

All ExternalSecret manifests reference 1Password items by title. If you rename items, update the titles in:
- `kubernetes/infrastructure/observability/kube-prometheus-stack/externalsecret.yaml`
- `kubernetes/infrastructure/observability/exporters/externalsecret.yaml`

### 2. Create Proxmox API Token

On any Proxmox host:
1. Open the Proxmox web UI (Datacenter > Permissions > API Tokens)
2. Create a token for `monitoring@pve` (or another dedicated user) with the **PVEAuditor** role
3. Uncheck "Privilege Separation" if using the same user's permissions
4. In the 1Password item, store the user (e.g., `monitoring@pve`), token name (e.g., `exporter`), and the secret UUID as separate fields

### 3. Configure Authentik OIDC for Grafana

In the Authentik admin UI (`auth.esweiss.com`):
1. Create an **OAuth2/OpenID Provider** named "Grafana"
   - Client type: Confidential
   - Redirect URIs: `https://grafana.esweiss.com/login/generic_oauth`
   - Scopes: `openid`, `profile`, `email`
2. Create an **Application** named "Grafana" linked to the provider
3. Copy the Client ID and Client Secret into the "Grafana SSO" 1Password item

### 4. Enable Home Assistant Prometheus Integration (optional)

Add to Home Assistant `configuration.yaml` (deployed via `task home-assistant:deploy-config`):

```yaml
prometheus:
```

This enables the `/api/prometheus` endpoint. After enabling, uncomment `home-assistant.yaml` in `kubernetes/infrastructure/observability/service-monitors/kustomization.yaml` and commit.

### 5. Enable GitLab Prometheus Metrics (optional)

In `gitlab.rb` on the GitLab VM:

```ruby
prometheus_monitoring['enable'] = true
```

Then `sudo gitlab-ctl reconfigure`. After enabling, uncomment `gitlab.yaml` in `kubernetes/infrastructure/observability/service-monitors/kustomization.yaml` and commit.

### 6. Provision ZFS zvols

The Prometheus and Loki zvols must exist on pve-nas-01 and be attached to k3s-agt-nas-01. Add the zvol definitions to `ansible/inventories/prod/hosts.yml` under the `k3s-agt-nas-01` host's `vm_additional_disks`, then run:

```bash
task k3s:deploy
```

This provisions the zvols, attaches them to the VM, formats them as ext4, and mounts them.

### 7. Add AdGuard DNS Rewrite

Add an internal DNS entry so `grafana.esweiss.com` resolves to the MetalLB internal VIP:

```yaml
# ansible/inventories/prod/group_vars/dns.yml — append to existing list
adguard_rewrites:
  # ... existing rewrites ...
  - domain: "grafana.esweiss.com"
    answer: "192.168.0.101"
```

Then deploy: `task dns:deploy`

Or add the rewrite manually via the AdGuard Home UI at `https://192.168.0.150:3000` (Filters > DNS rewrites). It will sync to dns-02 automatically.

## Day-2 Operations

### Adding a ServiceMonitor

To add Prometheus scraping for a new application:

1. Create a `ServiceMonitor` resource in the observability namespace (or the application's own namespace -- Prometheus is configured to discover ServiceMonitors across all namespaces):

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: my-app
  namespace: observability
spec:
  namespaceSelector:
    matchNames:
      - my-namespace
  selector:
    matchLabels:
      app.kubernetes.io/name: my-app
  endpoints:
    - port: metrics
      interval: 30s
      path: /metrics
```

2. Add the file to the appropriate kustomization.yaml and commit.

For external (non-k8s) services, create a headless Service + Endpoints pair pointing at the external IP (see `service-monitors/home-assistant.yaml` or `exporters/zfs-exporter.yaml` for examples).

### Adding a Grafana Dashboard

Grafana's sidecar auto-discovers ConfigMaps with the `grafana_dashboard: "1"` label:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-dashboard
  namespace: observability
  labels:
    grafana_dashboard: "1"
  annotations:
    grafana_folder: "My Folder"
data:
  my-dashboard.json: |
    { ... Grafana dashboard JSON ... }
```

Add the ConfigMap to `kubernetes/infrastructure/observability/dashboards/` and reference it in the dashboards kustomization.yaml. Export dashboards from Grafana UI (Share > Export > Save to file) and paste the JSON into the ConfigMap.

### Dashboard Inventory

**Community dashboards** (vendored as JSON ConfigMaps in
`kubernetes/infrastructure/observability/dashboards/`, generated identically to
the custom ones below — originally exported from these Grafana.com IDs, not
imported by ID at runtime):

| Dashboard | Grafana ID | Category |
|-----------|------------|----------|
| Node Exporter Full | 1860 | Infrastructure |
| Traefik Official Kubernetes | 17347 | Networking |
| AdGuard Home | 20799 | Networking |
| Redis | 763 | Applications |
| Prometheus Self-Monitoring | 3662 | Infrastructure |
| Alertmanager | 9578 | Infrastructure |

**Custom dashboards** (maintained in `kubernetes/infrastructure/observability/dashboards/`):

| Dashboard | Purpose |
|-----------|---------|
| Cluster Overview | Every host, VM, LXC, workload with CPU/memory/disk/network |
| Home Assistant | Service health and availability |
| Media Stack | Sonarr/Radarr/Lidarr/Prowlarr library and health |
| Recipes | Mealie, Bar Assistant, Redis health |
| DNS Combined | AdGuard + Unbound for both DNS servers |
| Mail | SMTP relay status and logs |
| Infrastructure | Proxmox, ZFS, DNS overview |
| Alerts Overview | All alert rules defined across Prometheus (Infrastructure folder) |
| Blackbox Exporter | Endpoint monitoring |
| cert-manager | Certificate health |
| Flux Cluster | Reconciliation status |
| Thermals | CPU/board temperatures and fan speeds across Proxmox hosts |
| Unbound | Recursive resolver cache and query stats |
| GitLab | GitLab server health and performance |
| Immich | Photo-server availability, native OTEL metrics (users, job queues, memory), API latency, host logs |
| Nextcloud | Availability, users/files/shares, storage + database size (nextcloud-exporter), host logs |
| wg-easy | WireGuard peer counts (connected/enabled/configured), admin-UI probe, namespace logs |
| Hindsight | HTTP + LLM request rate/latency, token throughput, DB pool, resource usage, namespace logs |
| Backup — Nightly Jobs | The whole nightly chain: vzdump, media-mover, archive replication, adguard-sync, swap-clean, pg-dump CronJobs, per-app DB dumps, and restic offsite to B2. Panels for post-deploy metrics (`restic_offsite_*`, `backup_artifact_*`, `swap_clean_*`) render "No data" until those collectors ship |

The four app dashboards are hand-authored from the metrics each exporter actually
serves (`immich-metrics`/`nextcloud-metrics`/`wg-easy-ui`/`hindsight` jobs) in the
gitlab.json/recipes.json style. Homarr is intentionally **not** dashboarded — it
exposes no Prometheus `/metrics` endpoint; its availability is covered by the
`dashboard.esweiss.com` blackbox probe + the generic `EndpointDown` alert.

**Community-dashboard audit fixes (repo-wide review, 2026-07).** The vendored
community imports were corrected against live metrics: the traefik ServiceMonitor
gained `honorLabels: true` (`controllers/traefik/release.yaml`) so the
traefik-official `$service` panels resolve per-backend instead of collapsing to a
single `traefik` series; node-exporter's `processes` collector was enabled
(`prometheus-node-exporter.extraArgs: [--collector.processes]`) and the
permanently-empty tcpstat/interrupts panels removed; alertmanager-self dropped its
~40 single-replica gossip/cluster panels and fixed the GC/limit exprs;
prometheus-self moved to the `prometheus_rule_group_*` / seconds-histogram metric
names. Hand-authored dashboards gained coverage panels (cert-manager ClusterIssuer
readiness + ACME errors, mail Postfix daemon/queue-depth, flux not-ready
resources, blackbox HTTP status codes, media-stack service status + download/VPN
activity, infrastructure ARC-vs-cap + swap).

### Silencing Alerts

To temporarily silence an alert:

```bash
# Port-forward to Alertmanager
kubectl port-forward -n observability svc/kube-prometheus-stack-alertmanager 9093:9093

# Open http://localhost:9093 in a browser
# Navigate to Silences > New Silence
# Set matchers (e.g., alertname="DiskUsageWarning", instance="k3s-agt-nas-01:9100")
# Set duration and comment
```

### PromQL Tips

Common queries for this cluster:

```promql
# CPU usage per node (percentage)
100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)

# Memory usage per node (percentage)
(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100

# Disk usage per mountpoint (percentage)
(1 - node_filesystem_avail_bytes / node_filesystem_size_bytes) * 100

# Pod restart count (last hour)
increase(kube_pod_container_status_restarts_total[1h]) > 0

# Flux reconciliation errors (matches FluxReconciliationFailure alert)
sum by (controller) (rate(controller_runtime_reconcile_errors_total{job="observability/flux-system"}[5m])) > 0

# Traefik request rate (by service)
sum by(service) (rate(traefik_service_requests_total[5m]))

# Traefik 5xx error rate
sum(rate(traefik_service_requests_total{code=~"5.."}[5m])) / sum(rate(traefik_service_requests_total[5m]))

# Proxmox host CPU usage
pve_cpu_usage_ratio

# cert-manager certificates expiring soon
certmanager_certificate_expiration_timestamp_seconds - time() < 86400 * 14

# VPN tunnel status (Gluetun exporter on the qbittorrent pod)
# 1 = running, 0 = stopped/unreachable, -1 = error, -2 = unknown
gluetun_vpn_status{namespace="downloads"}
```

### LogQL Tips

Common Loki queries:

```logql
# All logs from a namespace
{namespace="downloads"}

# Error logs from a specific pod
{namespace="authentik", pod=~"authentik-server.*"} |= "error"

# Logs from a container, case-insensitive regex
{namespace="observability", container="prometheus"} |~ "(?i)warn|error"

# Count log lines per namespace (for volume analysis)
sum by(namespace) (count_over_time({namespace=~".+"}[1h]))

# Filter by JSON log fields
{namespace="downloads"} | json | level="error"

# Traefik access logs with status code filter
{namespace="traefik"} | json | status >= 500
```

### Storage Expansion

To expand a zvol (e.g., Prometheus needs more than 150GB):

1. **Resize the ZFS zvol on pve-nas-01:**
   ```bash
   ssh pve-nas-01
   sudo zfs set volsize=200G ssd/appdata/prometheus/data
   ```

2. **Resize the partition inside the VM:**
   ```bash
   ssh k3s-agt-nas-01
   sudo resize2fs /dev/sdX   # The device for the prometheus zvol
   ```

3. **Update sizes in two places:**
   - PV capacity in `kubernetes/infrastructure/observability/kube-prometheus-stack/storage.yaml`
   - VolumeClaimTemplate request in `kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml` (under `prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage`)

4. **Update `retentionSize`** in the HelmRelease if needed (currently `120GB`).

5. Commit and push. Flux will reconcile the updated PV/PVC sizes.

### Retention Tuning

**Prometheus:**
- Time-based: `retention: 365d` (in kube-prometheus-stack HelmRelease)
- Size-based: `retentionSize: 120GB` (whichever limit hits first)
- Adjust in `kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml` under `prometheusSpec`

**Loki:**
- `retention_period: 720h` (30 days)
- Compactor runs retention enforcement
- Adjust in `kubernetes/infrastructure/observability/loki/release.yaml` under `loki.limits_config`

## Alerting

### Routing Table

| Severity | Receivers | Repeat Interval |
|----------|-----------|-----------------|
| **critical** | Email (`ericsweiss1@gmail.com`) + Discord webhook | 4h |
| **warning** | Discord webhook only | 12h |
| **Watchdog** (always firing) | healthchecks.io heartbeat (`watchdog-heartbeat` receiver) | 1m ping |

Alerts are grouped by `alertname` and `namespace` with a 30s group wait and 10m group interval; warnings repeat at 12h. Node-outage inhibition suppresses per-pod/per-target warnings while a `KubeNodeNotReady`/`KubeNodeUnreachable` alert is firing, and the custom warning/critical pairs inhibit on `instance`+`component`.

**Dead-man's switch:** the chart's always-firing `Watchdog` alert is routed to
healthchecks.io (ping URL from the `Healthchecks Watchdog` 1Password item via
the alertmanager-config ExternalSecret); the external service alarms when the
pings STOP — i.e. when Prometheus, Alertmanager, the NAS node, or notification
egress is down. See docs/17 for the DR framing.

**Alerting depends on DNS:** the SMTP and Discord receivers both need name
resolution (smtp-relay hostname, discord.com). With **both** resolvers
(.150/.160) down, Alertmanager defers delivery until DNS recovers — the
`DNSResolutionDown` pages would arrive late. The external Watchdog heartbeat
is the covering signal for that scenario: pings stop, healthchecks.io alarms
out-of-band.

### Flux readiness alerting

Flux controllers no longer export per-resource readiness, so
`FluxResourceNotReady` is driven by kube-state-metrics
`customResourceState` (`gotk_resource_info`), covering `Kustomization`,
`HelmRelease`, and the source kinds in use (`GitRepository`,
`HelmRepository`, `HelmChart`) — a failing source therefore alerts even
while workloads merely go stale. Add new kinds (e.g. `OCIRepository`)
to the customResourceState config + RBAC when they're first adopted.

### Alert Rules

The `additionalPrometheusRulesMap` in
`kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml`
is the **authoritative** list of custom alerts (every rule carries a
`runbook_url` into docs/12). The tables below summarize the groups; consult
the release for exact expressions and thresholds.

#### Storage Alerts (`homelab.storage`)

| Alert | Condition | Severity | For |
|-------|-----------|----------|-----|
| DiskUsageWarning / DiskUsageCritical | Filesystem usage > 80% / > 90% | warning / critical | 10m / 5m |
| PVCUsageWarning / PVCUsageCritical | PVC usage > 80% / > 90% | warning / critical | 10m / 5m |
| InodeUsageWarning / InodeUsageCritical | Inode usage > 80% / > 90% | warning / critical | 10m / 5m |
| SMARTDeviceUnhealthy | smartctl overall-health != PASSED | warning | 15m |
| SMARTReallocatedSectorsGrowing / SMARTPendingSectors / SMARTOfflineUncorrectable / SMARTMediaErrors | SMART attribute deltas (textfile collector) | warning | — |
| SMARTCollectorStale | SMART textfile metric stopped updating | warning | 1h |
| NASSwapNotClearing | NAS swap above 2 GiB for 2 days (swap-clean textfile metric) | warning | 1h |
| LocalPathPVExists | any PV on the `local-path` StorageClass (`kube_persistentvolume_info{storageclass="local-path"}`) — nothing may use it (stateless VM bootdisk, excluded from backups); catches PVC storageClass drift (see the loki guard, docs/29) | warning | 15m |

#### Infrastructure Alerts (`homelab.infrastructure`)

| Alert | Condition | Severity | For |
|-------|-----------|----------|-----|
| VPNDown | Gluetun VPN status != 1 (downloads ns) | critical | 15m |
| VPNExporterDown | gluetun_vpn_status series absent (downloads ns) | warning | 15m |
| LokiRequestFailures / LokiIngestionRateLimited | Loki 5xx rate / 429 rejections | warning | — |
| FluxReconciliationFailure | Flux controller reconcile error rate > 0 | warning | 15m |
| FluxResourceNotReady | gotk_resource_info shows Ready=False | warning | — |
| OnePasswordConnectDown | Connect deployment has 0 available replicas | warning | 5m |
| ExternalSecretSyncFailure | ExternalSecret Ready=False | warning | 15m |
| DDNSStale | cloudflare-ddns hasn't succeeded recently | warning | — |
| CertExpiringWarning | Certificate expires in < 14 days | warning | 1h |
| CertExpiringCritical | Certificate expires in < 3 days | critical | 1h |
| TraefikHighErrorRate | 5xx rate > 5% | warning | 5m |
| VPARecommendationCapped | VPA uncapped target > capped target for 24h (maxAllowed ceiling outgrown — see docs/33) | warning | 24h |
| WgEasyDown | wg-easy VPN target down | critical | 15m |
| HindsightDown | Hindsight (Hermes memory backend) down | warning | 15m |

#### Cluster & Platform Health (`homelab.monitoring`)

| Alert | Condition | Severity |
|-------|-----------|----------|
| ProxmoxHostDown | pve_up == 0 for a cluster node | critical |
| HAInfraGuestDown | HA-managed guest down — `pve_up` == 0 for lxc/150, lxc/151, lxc/160, or qemu/154 (covers the HAOS VM, not just the LXCs) | critical |
| NFSServerDown | nfsd threads on pve-nas-01 == 0 or metric absent | critical |
| NodeStuckCordoned / MaintenanceRebootDeferred | kured maintenance flow stuck (see docs/33 / docs/12) | warning |
| CorosyncWedged / PmxcfsStale / CorosyncHealthCollectorStale | Proxmox cluster-stack health (textfile collector) | critical / warning |
| ZFSPoolDeviceErrors / ZFSPoolDataErrors / ZFSPoolNotOnline / ZFSPoolScrubStale / ZFSPoolCollectorStale | zpool-health textfile collector (per-device read/write/cksum errors, data errors, pool state, scrub age, collector freshness) | warning-critical |
| ZFSPoolSpaceWarning / ZFSPoolSpaceCritical | Pool allocated/size > 80% / > 90% (zfs_exporter) | warning / critical |
| EndpointDown | blackbox probe_success == 0 | warning |
| EndpointDownCritical | probe_success == 0 for the critical endpoints (auth/git/home .esweiss.com) | critical |
| DNSResolutionDown | blackbox DNS probe failing against a resolver | critical |
| DNSResolverProbeMissing | the .150/.160 DNS probe series is absent (probe config lost) | warning |
| BlackboxExporterDown | blackbox exporter itself unreachable | warning |
| NodeMemoryPressure | Node available memory critically low | warning |
| ProxmoxHostIOPressure | Proxmox host PSI I/O pressure sustained | warning |
| ProxmoxHostMemoryPressure | Proxmox host PSI memory pressure sustained | warning |
| ImmichDown | Immich scrape target down or absent | warning |
| NextcloudDown | `nextcloud_up` == 0 or absent | warning |
| WindowsRdpDown | Windows VM RDP (192.168.0.155:3389) unreachable while powered on | warning |

#### Custom Script Alerts (`homelab.scripts`)

These alerts use metrics exposed via the node_exporter textfile collector on Proxmox hosts, the k3s server nodes, and the GitLab VM. Custom scripts (archive-backupctl, media-mover, acme.sh, the GitLab backup wrapper, the vzdump hookscript, the etcd off-node snapshot copy) write `.prom` files to the textfile collector directory, which node_exporter serves alongside hardware metrics.

| Alert | Condition | Severity | For |
|-------|-----------|----------|-----|
| ArchiveBackupFailed | archive-backupctl last run exit code != 0 | warning | 1h |
| ArchiveBackupStale | archive-backupctl last success > 2 days ago | warning | 1h |
| ArchiveBackupDatasetStale | a single archive dataset hasn't replicated in > 2 days | warning | 1h |
| ArchiveBackupChronicallyDeferred | an archive dataset deferred >= 3 runs in a row | warning | 1h |
| MediaMoverFailed | media-mover last run exit code != 0 | warning | 1h |
| MediaMoverStale | media-mover last success > 2 days ago | warning | 1h |
| CertRenewalFailed | acme.sh cert renewal/distribution exit code != 0 | warning | 1h |
| CertExpiringSoon | host-distributed `*.esweiss.com` cert within 14 days of its real `notAfter` (`cert_local_expiry_timestamp_seconds`), or metric absent | warning | 1h |
| CertExpiringSoonCritical | host-distributed cert within 3 days of expiry | critical | 1h |
| GitLabBackupFailed | gitlab-backup-run.sh last run exit code != 0 | warning | 1h |
| GitLabBackupStale | GitLab backup last success > 2 days ago (or metric absent) | warning | 1h |
| GitLabBackupStaleCritical | GitLab backup last success > 4 days ago | critical | 1h |
| EtcdSnapshotStale | newest off-node etcd snapshot copy > 26h old, or metric absent (docs/17) | warning | 1h |
| VzdumpBackupFailed / VzdumpBackupStale | nightly vzdump guest-image backup failed / stale (hookscript deployed fleet-wide by node_exporter_host) | warning | 1h |
| NextcloudBackupFailed / NextcloudBackupStale | Nextcloud pg_dump failed / last success > 2 days ago (or metric absent) | warning | 1h |
| ImmichBackupFailed / ImmichBackupStale | Immich pg_dump failed / last success > 2 days ago (or metric absent) | warning | 1h |
| ResticOffsiteFailed | nightly restic offsite-to-B2 run failed (`restic_offsite_last_run_success == 0`) | warning | 1h |
| ResticOffsiteStale | restic offsite last success > 50h ago, or metric absent (50h tolerates one skipped night — B2 chains off archive, which can defer) | warning | 1h |
| BackupArtifactStale | NAS-side mtime of the newest `tank/backups/apps/<app>` dump > 50h old (or collector absent) — the "the dump landed offsite-eligible" signal, independent of the VM-side "the dump ran" metric | warning | 1h |

Note: the three restic/backup-artifact metrics above appear only once the
`restic_offsite` role and the NAS-side mtime textfile collector are deployed
(b2-backup work); until then the `absent()` arms stay quiet (they require the
series to have existed) and the `Backup — Nightly Jobs` dashboard panels render
"No data" gracefully.

#### Other Groups

- **`homelab.temperature`** — SATA/NVMe drive, CPU, GPU, and NIC temperature warning/critical pairs (drivetemp + hwmon via node_exporter_host).
- **`homelab.mail`** — PostfixQueueBacklog, PostfixDown, PostfixQueueCollectorStale (smtp-relay queue textfile collector).
- **`homelab.kubernetes-resources`** — the tuned KubeCPUOvercommit replacement (see Built-in Alerts below).

### Built-in Alerts

The kube-prometheus-stack chart ships built-in alerts for Kubernetes components (kubelet, apiserver, etcd, CoreDNS), node health, and Prometheus self-monitoring. These are enabled by default.

**Disabled k3s-irrelevant alerts:** KubeProxyDown, KubeSchedulerDown, and KubeControllerManagerDown are disabled because k3s embeds these components into the main process and does not expose separate metrics endpoints. Leaving them enabled causes persistent false-positive alerts.

**etcd IS scraped:** the k3s server nodes run with `etcd-expose-metrics: true` (ansible/roles/k3s), serving standard etcd metrics on `:2381` (plain HTTP, firewall-restricted to `k3s_nodes` — docs/11). `kubeEtcd.enabled: true` in the chart builds the Service/Endpoints from the server-node IPs and re-enables the upstream etcd alert rules (quorum, leader flapping, DB size).

**Tuned replacement:** the upstream KubeCPUOvercommit is disabled and re-added (`homelab.kubernetes-resources`) with the `gitlab-runner*` namespaces' deliberately over-requested burst CPU subtracted, so CI bursts no longer flap it.

**Inhibition rules:**
- Critical alerts suppress warnings with the same `alertname` and `namespace` (avoids duplicate noise when a warning escalates to critical)
- InfoInhibitor alerts suppress info-level alerts (allows dashboards to generate info alerts without flooding receivers)

**Resolved issues:**
- **PrometheusOperatorSyncFailed** -- resolved by converting the alertmanager-config Secret to an ExternalSecret template that injects `webhook_url` directly instead of using `webhook_url_file` (the Alertmanager pod could not read files from the mounted Secret)
- **CPUThrottlingHigh** -- resolved by *removing* the CPU limit (request-only, memory limit kept) on the bursty scrape exporters, since CFS throttling on a poll/scrape control loop is an anti-pattern that fires this alert without indicating real CPU starvation. This applies to the in-tree exporters (proxmox / adguard / redis / blackbox), the kube-prometheus-stack `node-exporter` DaemonSet, and the GitLab runner managers. The general rule: drop the CPU limit on bursty controllers/exporters, keep the memory limit (OOM is a real failure mode). Per-exporter VPAs still right-size the *requests*. (Do not re-add per-exporter CPU-limit numbers here — they drift; the HelmReleases/manifests are the source of truth.)

### Flux Metrics

Flux controller metrics (source-controller, kustomize-controller, helm-controller, notification-controller) are scraped via PodMonitor instead of ServiceMonitor. The controllers expose their metrics port (`http-prom:8080`) on pods, but the corresponding Services do not expose that port, so PodMonitor is required.

## Troubleshooting

### Prometheus Disk Full

**Symptoms:** Prometheus pod in CrashLoopBackOff, logs show "no space left on device" or "WAL: write failed".

**Resolution:**

1. Check current disk usage:
   ```bash
   ssh k3s-agt-nas-01
   df -h /mnt/prometheus-data
   ```

2. If full, reduce retention temporarily:
   - Edit `release.yaml`: set `retentionSize: 130GB` (or lower)
   - Commit and push, or use `task flux:dev-apply -- kubernetes/infrastructure/observability`

3. Force compaction by restarting Prometheus:
   ```bash
   kubectl delete pod -n observability -l app.kubernetes.io/name=prometheus
   ```

4. For immediate relief, expand the zvol (see [Storage Expansion](#storage-expansion) above).

5. Long-term: reduce scrape frequency for high-cardinality exporters, or increase zvol size.

### Loki WAL Issues

**Symptoms:** Loki pod restarts, log ingestion stops, errors mentioning WAL or "context deadline exceeded".

**Resolution:**

1. Check Loki pod logs:
   ```bash
   kubectl logs -n observability -l app.kubernetes.io/name=loki --tail=200
   ```

2. Check disk space on the Loki zvol:
   ```bash
   ssh k3s-agt-nas-01
   df -h /mnt/loki-data
   ```

3. If the WAL is corrupted, delete the WAL directory and restart:
   ```bash
   # Scale down Loki first
   kubectl scale statefulset -n observability loki --replicas=0
   # On the NAS node:
   ssh k3s-agt-nas-01
   sudo rm -rf /mnt/loki-data/wal
   # Scale back up
   kubectl scale statefulset -n observability loki --replicas=1
   ```
   Note: this loses in-flight log data that was not yet flushed to chunks.

4. If disk is full, reduce retention or expand the zvol.

### Exporter Down

**Symptoms:** `up == 0` in Prometheus for a scrape target, gaps in dashboard data.

**Resolution:**

1. Identify the down target:
   ```bash
   # From PromQL
   up == 0
   ```

2. For in-cluster exporters (Proxmox exporter, AdGuard exporter, Exportarr):
   ```bash
   kubectl get pods -n observability -l app.kubernetes.io/name=<exporter-name>
   kubectl logs -n observability -l app.kubernetes.io/name=<exporter-name>
   ```

3. For external exporters (ZFS exporter on pve-nas-01, Unbound exporter on dns-01/dns-02).
   Service names may vary by installation method -- check with `systemctl list-units '*exporter*'`:
   ```bash
   # ZFS exporter on pve-nas-01
   ssh pve-nas-01
   sudo systemctl status zfs_exporter
   sudo journalctl -u zfs_exporter -n 50

   # Unbound exporter on dns-01 / dns-02
   ssh dns-01  # or dns-02
   sudo systemctl status unbound_exporter
   sudo journalctl -u unbound_exporter -n 50
   ```

4. Verify network connectivity from the cluster to the external target:
   ```bash
   kubectl run -it --rm debug --image=busybox -- wget -q -O- http://192.168.0.102:9134/metrics
   ```

### Alert Routing Debug

**Symptoms:** Alerts not arriving on Discord or email.

**Resolution:**

1. Check Alertmanager is receiving alerts:
   ```bash
   kubectl port-forward -n observability svc/kube-prometheus-stack-alertmanager 9093:9093
   # Open http://localhost:9093 and check the Alerts tab
   ```

2. Verify the `observability-secrets` ExternalSecret synced:
   ```bash
   kubectl get externalsecret -n observability observability-secrets
   kubectl get secret -n observability observability-secrets -o jsonpath='{.data}' | jq 'keys'
   ```

3. Check Alertmanager logs for delivery failures:
   ```bash
   kubectl logs -n observability -l app.kubernetes.io/name=alertmanager --tail=100
   ```

4. For SMTP issues, verify the smtp-relay is reachable from the cluster:
   ```bash
   kubectl run -it --rm debug --image=busybox -- nc -zv 192.168.0.151 587
   ```

5. For Discord issues, test the webhook URL manually:
   ```bash
   curl -X POST -H "Content-Type: application/json" \
     -d '{"content": "Test alert from weisssrv"}' \
     "$(kubectl get secret -n observability observability-secrets -o jsonpath='{.data.discord-webhook-url}' | base64 -d)"
   ```

### Grafana OIDC Issues

**Symptoms:** "Login failed" or redirect loop when logging into Grafana via Authentik.

**Resolution:**

1. Verify the OIDC credentials are set:
   ```bash
   kubectl get secret -n observability observability-secrets -o jsonpath='{.data.grafana-oidc-client-id}' | base64 -d
   ```

2. Check Grafana logs:
   ```bash
   kubectl logs -n observability -l app.kubernetes.io/name=grafana --tail=100
   ```

3. Verify the Authentik OIDC provider configuration:
   - Redirect URI must be exactly `https://grafana.esweiss.com/login/generic_oauth`
   - Client type must be Confidential
   - Scopes must include `openid`, `profile`, `email`

4. Verify DNS resolution of `auth.esweiss.com` from inside the cluster:
   ```bash
   kubectl run -it --rm debug --image=busybox -- nslookup auth.esweiss.com
   ```

5. If all else fails, disable OIDC temporarily by setting `GF_AUTH_GENERIC_OAUTH_ENABLED: "false"` in the HelmRelease (use `task flux:dev-apply` for quick iteration).

### Grafana Break-Glass Access (Authentik/OIDC down)

When Authentik or the OIDC flow is down and you need Grafana:

1. The local admin credentials are user `admin` with the chart-generated
   password from the in-cluster Secret (there is **no 1Password item** for it):

   ```bash
   kubectl get secret -n observability kube-prometheus-stack-grafana \
     -o jsonpath='{.data.admin-password}' | base64 -d; echo
   ```

2. The login form is hidden by config, so go in via port-forward + the direct
   login URL, or temporarily re-enable the form:

   ```bash
   kubectl port-forward -n observability svc/kube-prometheus-stack-grafana 3000:80
   # open http://localhost:3000/login  (bypasses the OIDC auto-redirect)

   # If the form is still hidden: flip grafana.ini auth.disable_login_form to
   # false via task flux:dev-apply (reverted on the next reconcile).
   ```

```bash
task observability:status    # Show pods, services, PVCs, HelmReleases, ExternalSecrets, ServiceMonitors
task observability:logs      # View logs (COMPONENT=prometheus|loki|alloy|grafana|alertmanager)
task observability:restart   # Restart all observability workloads
task observability:silence   # Create Alertmanager silence (ALERT=alertname, DURATION=2H — BSD date units)
```

## Related Documentation

- `docs/12-runbooks.md` -- Observability-specific runbook entries
- `docs/29-flux-operations.md` -- Flux day-2 operations (reconcile, suspend/resume)
- `docs/19-k3s-deployment.md` -- K3s cluster deployment (zvol provisioning)
