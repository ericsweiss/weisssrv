# K3s Cluster Deployment Guide

This guide walks through deploying the 9-node k3s cluster (3 servers + 6 agents) and the Flux GitOps platform that manages every workload on top of it.

## Quick Reference: Complete Deployment Workflow

K3s deployment is a three-phase approach:

- **Phase 1 (Ansible)**: Provisions the 9 k3s VMs on Proxmox.
- **Phase 2 (Ansible)**: Deploys k3s + kube-vip to servers/agents.
- **Phase 3 (Flux bootstrap)**: Bootstraps Flux, which then reconciles every
  platform component (MetalLB, Traefik, cert-manager, external-dns,
  external-secrets, CoreDNS HelmChartConfig, DDNS, cluster-issuer, IngressRoute
  middlewares) and every application (Authentik, downloads, recipes, gitlab-*,
  vm-ingress) from this repo.

**All Ansible tasks are idempotent** — safe to re-run at any time.

### What Each Step Does

| Step | Component | Mechanism |
|------|-----------|-----------|
| `task k3s:provision-vms` | Debian VMs | Ansible + Proxmox API |
| `task k3s:deploy` | K3s + kube-vip | Ansible (server, agents, node labels/taints) |
| `task flux:bootstrap-onepassword` | `onepassword-sdk-token` Secret (bootstrap only) | `op read | kubectl create secret` |
| `task flux:bootstrap` | Flux controllers committed to `kubernetes/clusters/weisssrv/flux-system/` | `flux bootstrap gitlab` |
| (none — automatic) | All platform + apps reconcile from `kubernetes/infrastructure/` and `kubernetes/apps/` | Flux |

Everything under `kubernetes/` is Flux-managed. To deploy or update a component,
commit the YAML and push — Flux reconciles on a 1-minute interval (or immediately
via the GitLab webhook). `task flux:reconcile` triggers a sync manually.

### Complete Command Sequence (Initial Install)

```bash
# === PHASE 1: VM Provisioning (Ansible) ===
task k3s:provision-vms

# === PHASE 2: K3s Cluster (Ansible) ===
task k3s:deploy
task k3s:kubeconfig
export KUBECONFIG=~/.kube/config-k3s
kubectl get nodes  # Verify cluster

# === PHASE 3: Flux bootstrap ===
# 3a. Create the single bootstrap secret (1P SDK token for ESO)
task flux:bootstrap-onepassword

# 3b. Bootstrap Flux (reads Flux GitLab PAT from 1P, commits flux-system/ to this repo)
task flux:bootstrap

# 3c. Wait for reconciliation
task flux:status      # watch until everything reports Ready: True
flux get all -A       # detailed view

# === VERIFY ===
task k3s:status       # cluster health
task flux:verify      # flux check + get all -A
```

### Ongoing Operations

Any change to a workload (image tag, replicas, env var, ingress host, middleware, etc.)
is now a git commit under `kubernetes/`:

```bash
# Edit the YAML (e.g., kubernetes/apps/authentik/release.yaml), then:
git add kubernetes/apps/authentik/release.yaml
git commit -m "Authentik: increase worker replicas"
git push

# Flux reconciles within ~1 minute. Speed up:
task flux:reconcile

# For fast local iteration without committing:
task flux:dev-apply -- kubernetes/apps/authentik
# (Flux will revert to the committed state on its next reconcile.)
```

For version bumps that flow through `all.yml`:

```bash
task maintenance:update-version SERVICE=authentik
task flux:sync-versions    # regenerates kubernetes/infrastructure/configs/versions-configmap.yaml
git add ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/configs/versions-configmap.yaml
git commit -m "Bump Authentik"
git push
```

See `docs/29-flux-operations.md` for the full operator guide (bootstrap, adopt
Helm releases, rotate secrets, add an app, suspend/resume, troubleshoot).

---

## Idempotency and Upgrades

**All k3s Ansible tasks are idempotent** — safe to re-run at any time without side effects.

### Ansible Tasks (`task k3s:deploy`)

| Operation | Idempotency Mechanism |
|-----------|----------------------|
| K3s server install | Version check - installs/upgrades when version differs |
| K3s agent install | Version check - installs/upgrades when version differs |
| Config files | Template tasks with notify - only restarts on change |
| Node labels/taints | `--overwrite` flag - safe to re-apply |
| kube-vip manifest | Template task - only updates if changed |
| Package installation | `state: present` - no-op if installed |

### K3s Version Upgrades

To upgrade k3s to a new version:

1. **Update the version** in `ansible/inventories/prod/group_vars/all.yml`:
   ```yaml
   k3s_version: "v1.33.8+k3s1"  # New version
   ```

2. **Run the node upgrade task** - it drains and upgrades each node in turn:
   ```bash
   task maintenance:update-k3s-nodes
   ```

### Flux-Managed Workloads

| Operation | Mechanism |
|-----------|-----------|
| HelmReleases | helm-controller via `flux reconcile` (every 10 minutes by default) |
| Kustomizations | kustomize-controller via `flux reconcile` (every 10 minutes by default) |
| ExternalSecrets | ESO polls 1Password (24h refresh by default) or on-demand via `task flux:refresh-secret -- <ns>/<name>` |
| Substitutions | Flux re-renders every reconcile using the `cluster-versions` ConfigMap |

Flux is itself idempotent — safe to run `task flux:reconcile` anytime.

---

## Overview

Cluster topology (9 nodes: 3 servers + 6 agents):

**Server Nodes** (etcd quorum):
- **k3s-srv-nas-01** (192.168.0.222) - Server on pve-nas-01
- **k3s-srv-laptop-01** (192.168.0.223) - Server on pve-laptop-01
- **k3s-srv-prec-01** (192.168.0.227) - Server on pve-prec-01

**Agent Nodes**:
- **k3s-agt-nas-01** (192.168.0.202) - Agent on pve-nas-01 (NAS workloads)
- **k3s-agt-laptop-01** (192.168.0.203) - Agent on pve-laptop-01 (ingress + general)
- **k3s-agt-opt-01** (192.168.0.204) - Agent on pve-opt-01 (ingress + general)
- **k3s-agt-opt-02** (192.168.0.205) - Agent on pve-opt-02 (ingress + general)
- **k3s-agt-opt-03** (192.168.0.206) - Agent on pve-opt-03 (ingress + general)
- **k3s-agt-prec-01** (192.168.0.207) - Agent on pve-prec-01 (general + compute)

Cluster features:
- **kube-vip** - API VIP at 192.168.0.161
- **MetalLB** - LoadBalancer with IPs .100 (public) and .101 (internal)
- **Traefik** - Ingress controller
- **external-dns** - Automatic Cloudflare DNS management

## Prerequisites

### 1. 1Password Setup

Create the K3s Cluster Token in 1Password:

```bash
# Generate a secure random token
openssl rand -base64 32

# Store in 1Password
# Vault: Homelab
# Item Name: K3s Cluster Token
# Field: credential
# Value: <generated token>
```

### 2. DNS Configuration

DNS records are codified in `ansible/inventories/prod/group_vars/dns.yml` and deployed automatically via AdGuard Home API:

```bash
# Deploy DNS configuration (dns-02 syncs automatically)
task deploy:dns -- --limit dns-01
```

This configures:
- Forward DNS (A records) for all k3s nodes and VIPs
- Reverse DNS (PTR records) for proper name resolution
- Internal service routes via Traefik VIP (.101)

**Note**: See `docs/08-dns.md` for the complete DNS record list. Records sync from dns-01 to dns-02 automatically every 5 minutes via adguardhome-sync.

### 3. Verify Prerequisites

```bash
# Verify 1Password authentication
task op:check

# Verify Ansible connectivity to Proxmox hosts
task ansible:ping -- --limit proxmox
```

## Phase 1: VM Provisioning

### Step 1: Provision VMs

```bash
# Provision all 9 k3s VMs (3 servers + 6 agents)
task k3s:provision-vms

# This will:
# - Download Debian 13 cloud image
# - Create server VMs: k3s-srv-nas-01 (.222), k3s-srv-laptop-01 (.223), k3s-srv-prec-01 (.227)
# - Create agent VMs: k3s-agt-nas-01 (.202), k3s-agt-laptop-01 (.203),
#   k3s-agt-opt-01 (.204), k3s-agt-opt-02 (.205), k3s-agt-opt-03 (.206), k3s-agt-prec-01 (.207)
# - Configure cloud-init with SSH keys
# - Start VMs and wait for SSH
# To provision a subset: task k3s:provision-vms -- --limit k3s-srv-nas-01,k3s-agt-nas-01
```

### Step 2: Verify VM Provisioning

```bash
# Test SSH access to all VMs
ssh eric@192.168.0.222  # k3s-srv-nas-01
ssh eric@192.168.0.202  # k3s-agt-nas-01
ssh eric@192.168.0.206  # k3s-agt-opt-03

# Verify VMs can reach the internet
ssh eric@192.168.0.222 "ping -c 3 1.1.1.1"

# Verify DNS resolution
ssh eric@192.168.0.222 "dig google.com"
```

## Phase 2: K3s Cluster Deployment

### Step 3: Deploy K3s Cluster

```bash
# Deploy full k3s cluster
task k3s:deploy

# This will:
# 1. Deploy base configuration (base, qol, postfix, tailscale)
# 2. Deploy k3s servers (k3s-srv-nas-01, k3s-srv-laptop-01, k3s-srv-prec-01) with kube-vip
# 3. Deploy k3s agents (k3s-agt-nas-01, k3s-agt-laptop-01, k3s-agt-opt-01/02/03, k3s-agt-prec-01)
# 4. Apply node labels and taints
# 5. Verify cluster health
```

### Step 4: Get Kubeconfig

```bash
# Fetch kubeconfig from cluster
task k3s:kubeconfig

# Set KUBECONFIG environment variable
export KUBECONFIG=~/.kube/config-k3s

# Verify cluster access
kubectl get nodes
kubectl get nodes -o wide
```

### Step 5: Verify Cluster

```bash
# Check all nodes are Ready
kubectl get nodes

# Check kube-vip VIP is active
ping -c 3 192.168.0.161

# Check system pods
kubectl get pods -A

# Verify kube-vip pod
kubectl get pods -n kube-system | grep kube-vip

# Check node labels
kubectl get nodes --show-labels

# Verify taints on NAS node
kubectl describe node k3s-agt-nas-01 | grep -A 5 Taints
```

## Phase 3: Flux Bootstrap + Platform Reconciliation

Once the k3s cluster is up and `kubectl get nodes` shows all 9 nodes Ready, bootstrap
Flux. Flux then reconciles every platform component and every application from this repo
— there are no per-component deploy tasks.

### Step 6: Create the Bootstrap Secret (One Time)

Flux's ExternalSecret ClusterSecretStore uses the 1Password SDK provider, which needs a
single bootstrap Secret containing the service account token. This is the *only* Secret
ever created by `kubectl create secret`.

```bash
task flux:bootstrap-onepassword
```

This reads `op://Homelab/Service Account Auth Token weisssrv/credential` and creates
`Secret/onepassword-sdk-token` in the `external-secrets` namespace.

### Step 7: Bootstrap Flux

```bash
task flux:bootstrap
```

This:
1. Reads `op://Homelab/Flux GitLab PAT/credential` (Maintainer role, scopes
   `api,read_repository,write_repository`).
2. Runs `flux bootstrap gitlab` against this repo and path
   `kubernetes/clusters/weisssrv`.
3. Installs the Flux controllers (source, kustomize, helm, notification) into
   `flux-system`.
4. Commits `flux-system/gotk-components.yaml`, `gotk-sync.yaml`,
   `kustomization.yaml` to the current branch.
5. Creates the `GitRepository` and top-level `Kustomization` CRs that watch this repo.

After bootstrap, Flux reconciles the top-level Kustomizations:

- `infrastructure` — sources, controllers (ESO, MetalLB, Traefik, cert-manager,
  external-dns), configs (ClusterSecretStore, ClusterIssuer, CoreDNS HelmChartConfig,
  MetalLB IP pools, DDNS CronJob, cluster-versions ConfigMap)
- `apps` — Authentik, downloads, recipes, gitlab-runner, gitlab-runner-privileged,
  gitlab-agent, vm-ingress (IngressRoutes for Plex, Home Assistant, AdGuard, GitLab VM,
  router, Traefik dashboard)

### Step 8: Register the GitLab Webhook (Optional, Recommended)

The default reconcile interval is 10 minutes (1 minute on sources). Register a GitLab
push webhook to Flux's `Receiver` for sub-second reconciliation after push:

```bash
task flux:webhook-register
```

### Step 9: Verify

Watch reconciliation until everything is Ready:

```bash
task flux:status
# or
flux get all -A
```

Expected state:

- `flux-system` `GitRepository` — Ready
- `infrastructure` `Kustomization` — Ready
- `apps` `Kustomization` — Ready (after `infrastructure` finishes)
- Every `HelmRelease` — Ready
- Every `ExternalSecret` — `SecretSynced: True`
- Every `IngressRoute` resolved and responding

Verify key endpoints:

```bash
# LoadBalancer VIPs
kubectl get svc -n traefik    # EXTERNAL-IP 192.168.0.100
curl -k http://192.168.0.100  # Traefik 404
curl -k http://192.168.0.101  # Traefik 404

# CoreDNS
kubectl get pods -n kube-system -l k8s-app=kube-dns

# cert-manager issued certificates
kubectl get certificate -A    # all Ready: True

# Authentik
kubectl get pods -n authentik
curl -k https://auth.esweiss.com  # login page
```

## Verification

### Quick Status Check

```bash
# Cluster health (nodes, etcd, kube-vip, kubelet readiness)
task k3s:status

# Flux health (GitRepository, Kustomizations, HelmReleases, ExternalSecrets)
task flux:status
```

### Detailed Cluster Health

```bash
# Check all nodes
kubectl get nodes -o wide

# Check all pods
kubectl get pods -A

# Check services
kubectl get svc -A

# Check MetalLB IP pools
kubectl get ipaddresspool -n metallb-system

# Check Traefik LoadBalancer
kubectl get svc traefik -n traefik

# Check certificates
kubectl get certificate -A
```

### Network Connectivity

```bash
# Test API VIP
curl -k https://192.168.0.161:6443

# Test Traefik LoadBalancer
curl http://192.168.0.100

# Ping from external host
ping 192.168.0.161  # API VIP
ping 192.168.0.100  # Traefik LoadBalancer
```

### DNS

```bash
# Internal DNS (AdGuard Home)
dig k3s-srv-nas-01.esweiss.com @192.168.0.150
dig k3s.esweiss.com @192.168.0.150

# External DNS (verify external-dns logs)
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns -f
```

## Post-Deployment

### Update Tailscale (Optional)

```bash
# SSH to each k3s node and run tailscale up
ssh eric@192.168.0.222 "sudo tailscale up --accept-routes --accept-dns=false"
ssh eric@192.168.0.202 "sudo tailscale up --accept-routes --accept-dns=false"
ssh eric@192.168.0.206 "sudo tailscale up --accept-routes --accept-dns=false"
```

### Collect Cluster State

```bash
# Collect updated cluster state
task collect-state
```

## Troubleshooting

### VMs Failed to Provision

```bash
# Check Proxmox logs on host
ssh pve-nas-01 "journalctl -u pve-cluster -f"

# Manually check VM status
ssh pve-nas-01 "qm status 222"

# Check cloud-init logs on VM
ssh eric@192.168.0.222 "sudo cat /var/log/cloud-init-output.log"
```

### K3s Installation Failed

```bash
# Check k3s service on server
ssh eric@192.168.0.222 "sudo systemctl status k3s"
ssh eric@192.168.0.222 "sudo journalctl -u k3s -n 50"

# Check k3s-agent service on agents
ssh eric@192.168.0.202 "sudo systemctl status k3s-agent"
ssh eric@192.168.0.202 "sudo journalctl -u k3s-agent -n 50"

# Check kube-vip manifest
ssh eric@192.168.0.222 "sudo cat /var/lib/rancher/k3s/server/manifests/kube-vip.yaml"
```

### kube-vip Not Assigning VIP

```bash
# Check kube-vip pod logs
kubectl logs -n kube-system -l app=kube-vip

# Check kube-vip pod status
kubectl get pods -n kube-system | grep kube-vip

# Manually ping VIP
ping 192.168.0.161
```

### MetalLB Not Assigning IPs

```bash
# Check MetalLB controller logs
kubectl logs -n metallb-system -l app.kubernetes.io/component=controller

# Check MetalLB speaker logs
kubectl logs -n metallb-system -l app.kubernetes.io/component=speaker

# Verify IP pools
kubectl get ipaddresspool -n metallb-system -o yaml

# Check service events
kubectl describe svc traefik -n traefik
```

### Traefik Not Accessible

```bash
# Check Traefik pods
kubectl get pods -n traefik

# Check Traefik logs
kubectl logs -n traefik -l app.kubernetes.io/name=traefik

# Check service
kubectl get svc traefik -n traefik -o yaml

# Verify LoadBalancer IP assignment
kubectl describe svc traefik -n traefik
```

## Expanding Beyond 3 Server Nodes

The current 3-server cluster (k3s-srv-nas-01, k3s-srv-laptop-01, k3s-srv-prec-01) with 6 agents provides etcd quorum tolerating 1 server failure. For full HA tolerating 2 server failures, expand to 5 server nodes.

### HA Expansion Nodes

The cluster uses a split IP scheme: servers in the .22X range, agents in the .20X range.

| Node | IP | VMID | Proxmox Host | Purpose |
|------|-----|------|--------------|---------|
| k3s-srv-laptop-01 | 192.168.0.223 | 223 | pve-laptop-01 | HA server #2 |
| k3s-srv-prec-01 | 192.168.0.227 | 227 | pve-prec-01 | HA server #3 |
| k3s-agt-laptop-01 | 192.168.0.203 | 203 | pve-laptop-01 | Ingress + general agent |
| k3s-agt-opt-01 | 192.168.0.204 | 204 | pve-opt-01 | General agent |
| k3s-agt-opt-02 | 192.168.0.205 | 205 | pve-opt-02 | General agent |
| k3s-agt-prec-01 | 192.168.0.207 | 207 | pve-prec-01 | General + compute agent |

### Step 1: Add New Server Nodes to Inventory

Edit `ansible/inventories/prod/hosts.yml` and add entries for the new server nodes under `k3s_servers`. Set `k3s_is_first_server: false` on all new servers.

### Step 2: Provision and Deploy

```bash
# Provision VMs for new servers only
task k3s:provision-vms -- --limit <new-server-hosts>

# Deploy k3s to new servers (joins existing cluster)
task k3s:deploy -- --limit <new-server-hosts>
```

### Step 3: Verify Expanded Cluster

```bash
# All servers should show as control-plane with etcd role
kubectl get nodes
# Current: 3 servers (srv-nas-01, srv-laptop-01, srv-prec-01) + 6 agents

# Verify etcd cluster health
kubectl get pods -n kube-system | grep etcd

# Test API VIP failover (optional)
# SSH to current VIP holder, reboot, verify VIP moves to another server
```

### HA Considerations

- **Odd number of servers**: etcd requires odd number for quorum (3 or 5)
- **VIP failover**: kube-vip automatically handles API VIP failover
- **Rolling updates**: Deploy one server at a time using `serial: 1`
- **etcd backup**: Consider backing up etcd before expansion
- **5-node HA**: For 2-failure tolerance, add 2 more servers at .224 and .225

## Next Steps

1. **Deploy additional workloads** - Immich, Nextcloud, observability stack (all via Flux)
2. **Configure backups** - Velero for cluster backups
3. **Set up monitoring** - Prometheus + Grafana stack (future work)
4. **Complete secrets-encryption** - see `docs/16-next-steps.md` Outstanding Follow-Ups

## Related Documentation

- `docs/14-post-base-plan.md` - k3s platform roadmap (historical)
- `docs/29-flux-operations.md` - Flux operator guide: bootstrap, adopt, rotate secrets, add an app, suspend, rollback
- `docs/30-multi-repo-onboarding.md` - Adding external repos that deploy into this cluster
- `kubernetes/README.md` - Top-level k8s layout guide (Flux-aware)
- `kubernetes/infrastructure/` - Platform components (sources, controllers, configs)
- `kubernetes/apps/` - Applications (authentik, download-clients, recipes, gitlab-*, vm-ingress)
