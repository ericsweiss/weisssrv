# K3s Cluster Deployment Guide

This guide walks through deploying the initial 3-node k3s cluster and platform services.

## Quick Reference: Complete Deployment Workflow

**IMPORTANT**: K3s deployment uses a two-phase approach:
- **Phase 1-2 (Ansible)**: Provisions VMs and deploys k3s + kube-vip
- **Phase 3 (Task/Helm)**: Deploys MetalLB, Traefik, cert-manager, external-dns, DDNS, and IngressRoutes

**All tasks are idempotent** - safe to re-run at any time.

### What Each Task Installs

| Task | Component | Notes |
|------|-----------|-------|
| `task k3s:provision-vms` | Debian VMs | Cloud-init, SSH keys |
| `task k3s:deploy` | K3s + kube-vip | Server, agents, node labels/taints |
| `task k3s:deploy-metallb` | MetalLB | LoadBalancer IPs .100/.101 |
| `task k3s:deploy-traefik` | Traefik | Ingress controller on .100 + .101 |
| `task k3s:deploy-cert-manager` | cert-manager | Let's Encrypt wildcard certs |
| `task k3s:deploy-external-dns` | external-dns | Cloudflare DNS automation |
| `task k3s:deploy-ddns` | DDNS CronJob | Updates public IP every 5 min |
| `task k3s:deploy-ingress-routes` | IngressRoutes | Per-service routing |
| `task k3s:deploy-workloads` | **ALL of above** | Runs all workload tasks in order |

### Complete Command Sequence

```bash
# === PHASE 1: VM Provisioning (Ansible) ===
task k3s:provision-vms

# === PHASE 2: K3s Cluster (Ansible) ===
task k3s:deploy
task k3s:kubeconfig
export KUBECONFIG=~/.kube/config-k3s
kubectl get nodes  # Verify cluster

# === PHASE 3: Platform Services (Task/Helm) ===
# Option A: Deploy everything at once (recommended)
task k3s:deploy-workloads

# Option B: Deploy components individually
task k3s:deploy-metallb
task k3s:deploy-traefik
task k3s:deploy-cert-manager
task k3s:deploy-external-dns
task k3s:deploy-ddns
task k3s:deploy-ingress-routes

# === VERIFY ===
task k3s:status  # Show cluster and workload status
```

### Manual Helm Commands (if needed)

<details>
<summary>Click to expand manual Helm commands</summary>

```bash
# MetalLB
helm repo add metallb https://metallb.github.io/metallb && helm repo update
helm upgrade --install metallb metallb/metallb -n metallb-system --create-namespace
kubectl apply -f kubernetes/bootstrap/metallb/ip-pool.yaml

# Traefik
helm repo add traefik https://traefik.github.io/charts && helm repo update
kubectl apply -f kubernetes/apps/traefik/namespace.yaml
helm upgrade --install traefik traefik/traefik -n traefik -f kubernetes/apps/traefik/values.yaml

# external-dns
kubectl apply -f kubernetes/apps/external-dns/namespace.yaml
kubectl create secret generic cloudflare-api-token -n external-dns \
  --from-literal=api-token="$(op read 'op://Homelab/Cloudflare DNS Token/credential')" \
  --dry-run=client -o yaml | kubectl apply -f -
helm repo add external-dns https://kubernetes-sigs.github.io/external-dns && helm repo update
helm upgrade --install external-dns external-dns/external-dns -n external-dns \
  -f kubernetes/apps/external-dns/values.yaml

# cert-manager
helm repo add jetstack https://charts.jetstack.io && helm repo update
kubectl apply -f kubernetes/apps/cert-manager/namespace.yaml
helm upgrade --install cert-manager jetstack/cert-manager -n cert-manager \
  -f kubernetes/apps/cert-manager/values.yaml
kubectl create secret generic cloudflare-api-token -n cert-manager \
  --from-literal=api-token="$(op read 'op://Homelab/Cloudflare DNS Token/credential')" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -k kubernetes/apps/cert-manager/

# IngressRoutes and middleware
kubectl apply -k kubernetes/apps/ingress-routes/
```
</details>

---

## Idempotency

**All k3s deployment tasks are idempotent** - safe to re-run at any time without side effects.

### Ansible Tasks (`task k3s:deploy`)

| Operation | Idempotency Mechanism |
|-----------|----------------------|
| K3s server install | `creates: /usr/local/bin/k3s` - skips if binary exists |
| K3s agent install | `when: not k3s_binary.stat.exists` - skips if binary exists |
| Config files | Template tasks with notify - only restarts on change |
| Node labels/taints | `--overwrite` flag - safe to re-apply |
| kube-vip manifest | Template task - only updates if changed |
| Package installation | `state: present` - no-op if installed |

### Helm Tasks (`task k3s:deploy-*`)

| Operation | Idempotency Mechanism |
|-----------|----------------------|
| Helm repos | `--force-update` flag - updates existing repos |
| Helm charts | `helm upgrade --install` - creates or updates |
| Kubernetes secrets | `--dry-run=client -o yaml | kubectl apply -f -` - creates or updates |
| Kubernetes manifests | `kubectl apply` - declarative, creates or updates |

**Safe to run**: You can run `task k3s:deploy` and `task k3s:deploy-workloads` repeatedly without issues.

---

## Overview

Initial cluster topology:
- **k3s-srv-nas-01** (192.168.0.202) - Server node on pve-nas-01
- **k3s-agt-nas-01** (192.168.0.207) - Agent node on pve-nas-01 (NAS workloads)
- **k3s-agt-opt-03** (192.168.0.206) - Agent node on pve-opt-03 (general workloads)

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
# Provision all 3 k3s VMs
task k3s:provision-vms

# This will:
# - Download Debian 13 cloud image
# - Create VMs on pve-nas-01 (k3s-srv-nas-01, k3s-agt-nas-01)
# - Create VM on pve-opt-03 (k3s-agt-opt-03)
# - Configure cloud-init with SSH keys
# - Start VMs and wait for SSH
```

### Step 2: Verify VM Provisioning

```bash
# Test SSH access to all VMs
ssh eric@192.168.0.202  # k3s-srv-nas-01
ssh eric@192.168.0.207  # k3s-agt-nas-01
ssh eric@192.168.0.206  # k3s-agt-opt-03

# Verify VMs can reach the internet
ssh eric@192.168.0.202 "ping -c 3 1.1.1.1"

# Verify DNS resolution
ssh eric@192.168.0.202 "dig google.com"
```

## Phase 2: K3s Cluster Deployment

### Step 3: Deploy K3s Cluster

```bash
# Deploy full k3s cluster
task k3s:deploy

# This will:
# 1. Deploy base configuration (base, qol, postfix, tailscale)
# 2. Deploy k3s server on k3s-srv-nas-01 with kube-vip
# 3. Deploy k3s agents on k3s-agt-nas-01 and k3s-agt-opt-03
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

## Phase 3: Platform Services

**Recommended**: Use `task k3s:deploy-workloads` to deploy all platform services at once.

For individual deployments or troubleshooting, use the tasks below:

### Step 6: Deploy MetalLB

```bash
# Using Task (recommended)
task k3s:deploy-metallb

# Verify MetalLB
kubectl get pods -n metallb-system
kubectl get ipaddresspool -n metallb-system
kubectl get l2advertisement -n metallb-system
```

### Step 7: Deploy Traefik

```bash
# Using Task (recommended)
task k3s:deploy-traefik

# Verify Traefik service has LoadBalancer IP
kubectl get svc -n traefik
# Should show EXTERNAL-IP: 192.168.0.100

# Test LoadBalancer connectivity
curl -k http://192.168.0.100
# Should get Traefik 404 page
```

### Step 8: Deploy cert-manager

```bash
# Using Task (recommended)
task k3s:deploy-cert-manager

# Verify cert-manager
kubectl get pods -n cert-manager
kubectl get clusterissuer
kubectl get certificate -A
```

### Step 9: Deploy external-dns

```bash
# Using Task (recommended)
task k3s:deploy-external-dns

# Verify external-dns
kubectl get pods -n external-dns
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns
```

### Step 10: Deploy DDNS CronJob

```bash
# Using Task (recommended)
task k3s:deploy-ddns

# Verify DDNS
kubectl get cronjob -n default
kubectl get jobs -n default
```

### Step 11: Deploy IngressRoutes

```bash
# Using Task (recommended)
task k3s:deploy-ingress-routes

# Verify IngressRoutes
kubectl get ingressroute -A
kubectl get middleware -n traefik
```

## Verification

### Quick Status Check

```bash
# Using Task (recommended) - shows all cluster and workload status
task k3s:status
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
dig k3s-srv-01.esweiss.com @192.168.0.150
dig k3s.esweiss.com @192.168.0.150

# External DNS (verify external-dns logs)
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns -f
```

## Post-Deployment

### Update Tailscale (Optional)

```bash
# SSH to each k3s node and run tailscale up
ssh eric@192.168.0.202 "sudo tailscale up --accept-routes --accept-dns=false"
ssh eric@192.168.0.207 "sudo tailscale up --accept-routes --accept-dns=false"
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
ssh pve-nas-01 "qm status 202"

# Check cloud-init logs on VM
ssh eric@192.168.0.202 "sudo cat /var/log/cloud-init-output.log"
```

### K3s Installation Failed

```bash
# Check k3s service on server
ssh eric@192.168.0.202 "sudo systemctl status k3s"
ssh eric@192.168.0.202 "sudo journalctl -u k3s -n 50"

# Check k3s-agent service on agents
ssh eric@192.168.0.207 "sudo systemctl status k3s-agent"
ssh eric@192.168.0.207 "sudo journalctl -u k3s-agent -n 50"

# Check kube-vip manifest
ssh eric@192.168.0.202 "sudo cat /var/lib/rancher/k3s/server/manifests/kube-vip.yaml"
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

## Expanding to HA (5 Server Nodes)

The initial 3-node cluster can be expanded to full HA with 5 server nodes (required for etcd quorum in case of 2 server failures).

### HA Expansion Nodes

| Node | IP | Proxmox Host | Purpose |
|------|-----|--------------|---------|
| k3s-srv-laptop-01 | 192.168.0.203 | pve-laptop-01 | HA server #2 |
| k3s-srv-opt-01 | 192.168.0.204 | pve-opt-01 | HA server #3 |
| k3s-agt-opt-02 | 192.168.0.205 | pve-opt-02 | HA agent #1 |

### Step 1: Uncomment Inventory Entries

Edit `ansible/inventories/prod/hosts.yml` and uncomment the additional server nodes:

```yaml
k3s_servers:
  hosts:
    k3s-srv-nas-01:
      # ... existing first server ...
    # Uncomment these for HA:
    k3s-srv-laptop-01:
      ansible_host: 192.168.0.203
      k3s_is_first_server: false
      proxmox_host: pve-laptop-01
      # ...
```

**Important**: Ensure `k3s_is_first_server: false` is set on all new servers.

### Step 2: Deploy DNS (Already Configured)

DNS records for the expansion nodes are already in `dns.yml` and deployed. Verify:

```bash
dig k3s-srv-laptop-01.esweiss.com @192.168.0.150
dig k3s-srv-opt-01.esweiss.com @192.168.0.150
dig k3s-agt-opt-02.esweiss.com @192.168.0.150
```

### Step 3: Provision New VMs

```bash
# Provision only the new server nodes
task k3s:provision-vms -- --limit k3s-srv-laptop-01,k3s-srv-opt-01
```

### Step 4: Deploy k3s to New Servers

```bash
# Deploy k3s to new servers (joins existing cluster)
task k3s:deploy -- --limit k3s-srv-laptop-01,k3s-srv-opt-01
```

### Step 5: Verify HA Cluster

```bash
# All 5 servers should show as control-plane
kubectl get nodes
# Expected: 5 nodes with control-plane,master role

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

## Next Steps

1. **Configure TLS certificates** - Integrate cert-manager or extend acme.sh
2. **Deploy Flux** - GitOps for application deployments
3. **Deploy workloads** - Media stack, Nextcloud, Immich, etc.
4. **Configure backups** - Velero for cluster backups
5. **Set up monitoring** - Prometheus + Grafana stack
6. **Configure Authentik** - SSO for cluster services

## Related Documentation

- `docs/14-post-base-plan.md` - Full k3s platform roadmap
- `kubernetes/bootstrap/README.md` - MetalLB installation
- `kubernetes/apps/traefik/README.md` - Traefik configuration
- `kubernetes/apps/external-dns/README.md` - external-dns setup
