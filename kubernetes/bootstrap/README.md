# Kubernetes Bootstrap Manifests

This directory contains bootstrap manifests for the k3s cluster that should be applied before deploying applications.

## Installation Order

### 1. MetalLB (LoadBalancer Implementation)

MetalLB provides LoadBalancer implementation for bare-metal Kubernetes clusters.

```bash
# Install MetalLB via Helm
helm repo add metallb https://metallb.github.io/metallb
helm repo update
helm install metallb metallb/metallb -n metallb-system --create-namespace

# Wait for MetalLB to be ready
kubectl wait --namespace metallb-system \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/name=metallb \
  --timeout=90s

# Apply IP address pools and L2 advertisement
kubectl apply -f metallb/namespace.yaml
kubectl apply -f metallb/ip-pool.yaml
```

### 2. Verify MetalLB

```bash
# Check MetalLB pods
kubectl get pods -n metallb-system

# Verify IP pools
kubectl get ipaddresspool -n metallb-system

# Verify L2 advertisement
kubectl get l2advertisement -n metallb-system
```

## IP Allocation

- **192.168.0.100** (public-pool) - Traefik ingress controller and publicly accessible services
- **192.168.0.101** (internal-pool) - Internal services and dashboards

## Next Steps

After MetalLB is installed and configured:

1. Deploy Traefik ingress controller (`kubernetes/apps/traefik/`)
2. Deploy external-dns for automatic DNS management (`kubernetes/apps/external-dns/`)
3. Configure Flux for GitOps (future)
