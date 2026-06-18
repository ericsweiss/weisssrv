# Observability Stack

This guide covers the observability platform: Prometheus for metrics, Grafana for dashboards, Loki for logs, Alloy for log collection, and a suite of exporters for infrastructure and application metrics.

## Overview

The observability stack runs entirely in the `observability` namespace and is reconciled by Flux as a single Kustomization (`infrastructure-observability`). It sits between `infrastructure-configs` and `apps` in the reconciliation chain, so CRDs like ServiceMonitor and PrometheusRule are available before application namespaces attempt to create them.

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
| Node exporter (Proxmox hosts x6 + DNS hosts x2, port 9101) | observability | `/metrics` | 60s |
| Unbound exporter (dns-01 + dns-02) | observability | `/metrics` | 60s |
| AdGuard exporter | observability | `/metrics` | 60s |
| Plex exporter | observability | `/metrics` | 60s |
| Exportarr (Sonarr, Radarr, Lidarr, Prowlarr) | observability | `/metrics` | 60s |
| Redis exporter (Bar Assistant cache) | observability | `/metrics` | 60s |
| Meilisearch (Bar Assistant search) | recipes | `/metrics` | 60s |
| GitLab (VM) | observability (external endpoint) | `/-/metrics` | 60s |
| Home Assistant (VM) | observability (external endpoint) | `/api/prometheus` | 60s |
| Blackbox exporter (14 HTTP + 2 DNS probes) | observability | `/probe` | 60s |
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
infrastructure-controllers
        |
infrastructure-configs
        |
infrastructure-observability    <-- this stack
        |
      apps
```

Flux reconciles the observability stack after `infrastructure-configs` is Ready (so the ClusterSecretStore, ClusterIssuer, and MetalLB IP pools exist) but before `apps` (so ServiceMonitor CRDs are available for application namespaces).

**Trade-off:** `apps` depends on `infrastructure-observability`, so a failed observability deploy blocks all application reconciliation. This is intentional for CRD ordering on clean bootstrap, but means observability issues can impact apps. If this becomes problematic, change `apps.yaml` `dependsOn` back to `infrastructure-configs` — ServiceMonitor CRDs persist in Kubernetes even if the observability Kustomization temporarily fails.

### Namespace

All resources deploy into the `observability` namespace with Pod Security Standards set to `privileged` (required by node-exporter's hostPID, hostNetwork, and hostPath mounts).

### Storage

Both Prometheus and Loki use persistent ZFS zvols on pve-nas-01, attached to k3s-agt-nas-01 as SCSI disks and exposed via hostPath PVs:

| Component | ZFS zvol | Size | Mount Point | Node |
|-----------|----------|------|-------------|------|
| Prometheus TSDB | `ssd/appdata/prometheus/data` | 150GB | `/mnt/prometheus-data` | k3s-agt-nas-01 |
| Loki chunks + WAL | `ssd/appdata/loki/data` | 75GB | `/mnt/loki-data` | k3s-agt-nas-01 |

Both Prometheus and Loki pods are pinned to the NAS node via `nodeSelector: esweiss.com/nas: "true"` for local disk performance.

Grafana uses an NFS-backed PV for its SQLite database (user preferences, service accounts, saved views):

| Component | NFS Path | Size | Server |
|-----------|----------|------|--------|
| Grafana SQLite DB | `/appdata/grafana` (NFS) | 1Gi | pve-nas-01 (192.168.0.102) |

### Log Collection

**In-cluster:** Alloy runs as a DaemonSet on all 9 k8s nodes (tolerates all taints). It tails pod logs from `/var/log/pods` and ships them to Loki's ClusterIP service.

**Host-side:** The `alloy_host` Ansible role installs Alloy from the Grafana APT repository on all non-k8s hosts and k3s VMs. This includes 6 Proxmox hosts, 2 DNS containers, smtp-relay, GitLab VM, Plex LXC, and all 9 k3s VMs (3 servers + 6 agents). It reads from systemd journald and ships to Loki over the TLS ingress at `https://loki.esweiss.com/loki/api/v1/push` (lan-tailscale-only + basic-auth middleware; credentials from the "Loki Push Auth" 1Password item, injected at deploy time). The plain NodePort `:31100` via the kube-vip VIP remains only as an emergency fallback — override `alloy_host_loki_url` per host if Traefik is down.

On k3s VMs, alloy_host collects kubelet, containerd, etcd, and other systemd journal entries. This complements (not duplicates) the in-cluster DaemonSet, which only collects container logs from `/var/log/pods`. The two collectors cover different log sources with no overlap.

Home Assistant (HAOS) does not support Alloy installation — it is a managed appliance OS without package management.

### Host-Level Metrics

The `node_exporter_host` Ansible role installs Prometheus node_exporter on all 6 bare-metal Proxmox hosts, both DNS LXC containers (dns-01, dns-02), and the GitLab VM (192.168.0.153), listening on port 9101 (port 9100 is reserved for the kube-prometheus-stack DaemonSet on k3s nodes). On Proxmox hosts this provides hardware-level metrics not available from inside VMs: CPU and board thermals, SMART disk health, physical disk I/O, and fan speeds. On the DNS containers and the GitLab VM it provides container/VM-level CPU/memory/disk metrics and enables the textfile collector for script-freshness metrics (cert renewal on DNS; backup freshness on GitLab).

Each bare host is scraped via a headless `Service` + manually pinned `Endpoints` in `kubernetes/infrastructure/observability/exporters/node-exporter-host.yaml`, selected by a single `ServiceMonitor` (`jobLabel: app.kubernetes.io/name`). The GitLab VM's 9101 scrape is authorized by the `sg-metrics` security group it already carries.

The role also configures the **textfile collector** directory (`/var/lib/node_exporter/`), which allows custom scripts to expose metrics by writing `.prom` files. Scripts running on the host (archive-backupctl, media-mover, acme.sh cert renewal, the GitLab backup wrapper) write timestamped success/failure metrics that node_exporter serves alongside its built-in hardware metrics. Prometheus scrapes these via ServiceMonitors targeting the external Endpoints, and PrometheusRules fire alerts (ArchiveBackupFailed/Stale, MediaMoverFailed/Stale, CertRenewalFailed, GitLabBackupFailed/Stale) when scripts fail or become stale.

### Shared exporter install pipeline

The two download-based exporters — `zfs_exporter` (release tarball, on pve-nas-01) and `unbound_exporter` (release `.deb`, on dns-01/dns-02) — share a single install pipeline in the `prometheus_exporter` Ansible role: probe the installed version, conditionally download + verify + install the artifact, then enable/start the service and health-check it. Each remains a thin wrapper that supplies its own systemd unit (`zfs-exporter.service` runs as root for `/dev/zfs`; `unbound-exporter.service` uses `DynamicUser` + the unbound control socket) and passes its specifics (download URL, checksum, version-check command, port) to the shared role.

`node_exporter_host` is intentionally NOT built on this pipeline: it installs from the Debian apt repo (`prometheus-node-exporter`), ships a systemd drop-in override rather than a full unit, and carries bespoke textfile collectors (corosync + zpool health) and the drivetemp module — none of which generalize. See `ansible/roles/prometheus_exporter/README.md` for the parameter reference and the full exclusion rationale.

### Secrets

Two ExternalSecrets and one templated ExternalSecret pull credentials from 1Password:

1. **observability-secrets** -- Grafana OIDC client ID/secret.
2. **alertmanager-config** -- Alertmanager SMTP password and Discord webhook URL (rendered into alertmanager.yaml via ESO template, because Prometheus Operator does not support `webhook_url_file` for Discord configs).
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

**Plex Token** (new item):
- Field: `token` -- X-Plex-Token for Plex exporter metrics

**Download Client API Keys** (new item):
- Field: `sonarr-api-key` -- from Sonarr Settings > General > API Key
- Field: `radarr-api-key` -- from Radarr Settings > General > API Key
- Field: `lidarr-api-key` -- from Lidarr Settings > General > API Key
- Field: `prowlarr-api-key` -- from Prowlarr Settings > General > API Key

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

**Community dashboards** (imported from Grafana.com by ID):

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
| Blackbox Exporter | Endpoint monitoring |
| cert-manager | Certificate health |
| Flux Cluster | Reconciliation status |
| Thermals | CPU/board temperatures and fan speeds across Proxmox hosts |
| Unbound | Recursive resolver cache and query stats |
| GitLab | GitLab server health and performance |

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

# VPN tunnel status (Gluetun — not yet available, requires exporter)
# gluetun_vpn_status
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

4. **Update `retentionSize`** in the HelmRelease if needed (currently `140GB`).

5. Commit and push. Flux will reconcile the updated PV/PVC sizes.

### Retention Tuning

**Prometheus:**
- Time-based: `retention: 365d` (in kube-prometheus-stack HelmRelease)
- Size-based: `retentionSize: 140GB` (whichever limit hits first)
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

Alerts are grouped by `alertname` and `namespace` with a 30s group wait and 10m group interval; warnings repeat at 12h. Node-outage inhibition suppresses per-pod/per-target warnings while a `KubeNodeNotReady`/`KubeNodeUnreachable` alert is firing, and the custom warning/critical pairs inhibit on `instance`+`component`.

### Flux readiness alerting

Flux controllers no longer export per-resource readiness, so
`FluxResourceNotReady` is driven by kube-state-metrics
`customResourceState` (`gotk_resource_info`), covering `Kustomization`,
`HelmRelease`, and the source kinds in use (`GitRepository`,
`HelmRepository`, `HelmChart`) — a failing source therefore alerts even
while workloads merely go stale. Add new kinds (e.g. `OCIRepository`)
to the customResourceState config + RBAC when they're first adopted.

### Alert Rules

#### Storage Alerts (`homelab.storage`)

| Alert | Condition | Severity | For |
|-------|-----------|----------|-----|
| DiskUsageWarning | Filesystem usage > 80% | warning | 10m |
| DiskUsageCritical | Filesystem usage > 90% | critical | 5m |
| PVCUsageWarning | PVC usage > 80% | warning | 10m |
| PVCUsageCritical | PVC usage > 90% | critical | 5m |

#### Infrastructure Alerts (`homelab.infrastructure`)

| Alert | Condition | Severity | For |
|-------|-----------|----------|-----|
| ~~VPNDown~~ | ~~Gluetun VPN status == 0~~ | ~~warning~~ | ~~5m~~ (disabled -- requires Gluetun exporter) |
| FluxReconciliationFailure | Flux controller reconcile error rate > 0 | warning | 15m |
| OnePasswordConnectDown | Connect deployment has 0 available replicas | warning | 5m |
| ExternalSecretSyncFailure | ExternalSecret Ready=False | warning | 15m |
| CertExpiringWarning | Certificate expires in < 14 days | warning | 1h |
| CertExpiringCritical | Certificate expires in < 3 days | critical | 1h |
| TraefikHighErrorRate | 5xx rate > 5% | warning | 5m |

#### Backup Alerts (`homelab.backup`)

| Alert | Condition | Severity | For |
|-------|-----------|----------|-----|
| ZFSPoolDegraded | ZFS pool health > 0 (degraded/faulted) | critical | 5m |
| ~~ZFSSnapshotStale~~ | ~~Latest snapshot > 24 hours old~~ | ~~warning~~ | ~~1h~~ (disabled -- zfs_exporter lacks snapshot metrics) |

#### Custom Script Alerts (`homelab.scripts`)

These alerts use metrics exposed via the node_exporter textfile collector on Proxmox hosts and the GitLab VM. Custom scripts (archive-backupctl, media-mover, acme.sh, the GitLab backup wrapper) write `.prom` files to the textfile collector directory, which node_exporter serves alongside hardware metrics.

| Alert | Condition | Severity | For |
|-------|-----------|----------|-----|
| ArchiveBackupFailed | archive-backupctl last run exit code != 0 | warning | 1h |
| ArchiveBackupStale | archive-backupctl last success > 2 days ago | warning | 1h |
| MediaMoverFailed | media-mover last run exit code != 0 | warning | 1h |
| MediaMoverStale | media-mover last success > 2 days ago | warning | 1h |
| CertRenewalFailed | acme.sh cert renewal exit code != 0 | warning | 1h |
| GitLabBackupFailed | gitlab-backup-run.sh last run exit code != 0 | warning | 1h |
| GitLabBackupStale | GitLab backup last success > 2 days ago (or metric absent) | warning | 1h |

### Built-in Alerts

The kube-prometheus-stack chart ships built-in alerts for Kubernetes components (kubelet, apiserver, etcd, CoreDNS), node health, and Prometheus self-monitoring. These are enabled by default.

**Disabled k3s-irrelevant alerts:** KubeProxyDown, KubeSchedulerDown, KubeControllerManagerDown, and KubeEtcdDown are disabled because k3s embeds these components into the main process and does not expose separate metrics endpoints. Leaving them enabled causes persistent false-positive alerts.

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

## Management Commands

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
