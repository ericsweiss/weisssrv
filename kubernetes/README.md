# Kubernetes Configuration

This directory contains Kubernetes manifests and configuration for the k3s cluster.

## Directory Structure

```
kubernetes/
├── bootstrap/          # Bootstrap manifests (applied first)
│   ├── metallb/        # MetalLB LoadBalancer
│   └── README.md
├── apps/               # Application deployments
│   ├── traefik/        # Ingress controller
│   ├── external-dns/   # DNS automation
│   └── ...             # Future applications
└── flux/               # Future Flux GitOps configuration
```

## Deployment Order

Follow this order when deploying the k3s cluster:

### 1. Infrastructure (Ansible)
```bash
# Provision VMs
task k3s:provision-vms

# Deploy k3s cluster
task k3s:deploy

# Get kubeconfig
task k3s:kubeconfig
export KUBECONFIG=~/.kube/config-k3s
```

### 2. Bootstrap Platform (kubectl/helm)
```bash
# Install MetalLB
cd bootstrap/
# Follow instructions in bootstrap/README.md

# Verify
kubectl get pods -n metallb-system
kubectl get ipaddresspool -n metallb-system
```

### 3. Core Applications (kubectl/helm)
```bash
# Install Traefik
cd apps/traefik/
# Follow instructions in apps/traefik/README.md

# Install external-dns
cd apps/external-dns/
# Follow instructions in apps/external-dns/README.md
```

### 4. Future: Flux GitOps
```bash
# Install Flux (future)
cd flux/
# Bootstrap Flux
```

## Quick Reference

### Cluster Access
```bash
# Get kubeconfig
task k3s:kubeconfig
export KUBECONFIG=~/.kube/config-k3s

# Access cluster
kubectl get nodes
```

### Important IPs
- **API VIP**: 192.168.0.161 (kube-vip)
- **Public LoadBalancer**: 192.168.0.100 (MetalLB)
- **Internal LoadBalancer**: 192.168.0.101 (MetalLB)

### Cluster Nodes
- **k3s-srv-nas-01**: 192.168.0.222 (server, pve-nas-01)
- **k3s-agt-nas-01**: 192.168.0.202 (agent, pve-nas-01, NAS workloads)
- **k3s-agt-opt-03**: 192.168.0.206 (agent, pve-opt-03, general workloads)

## Documentation

- **Deployment Guide**: `docs/19-k3s-deployment.md`
- **Platform Plan**: `docs/14-post-base-plan.md`
- **Bootstrap**: `bootstrap/README.md`
- **Traefik**: `apps/traefik/README.md`
- **external-dns**: `apps/external-dns/README.md`

## Namespace Conventions

- `kube-system` - Kubernetes system components
- `metallb-system` - MetalLB LoadBalancer
- `traefik` - Traefik ingress controller
- `external-dns` - external-dns for DNS automation
- `monitoring` - Prometheus + Grafana (future)
- `auth` - Authentik SSO (future)
- `media` - Media stack (future)
- `storage` - Storage services (future)
