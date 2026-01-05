# Traefik Ingress Controller

Traefik is the ingress controller for the k3s cluster, providing HTTP/HTTPS routing and TLS termination.

## Prerequisites

- MetalLB installed and configured
- Cluster has nodes with label `esweiss.com/ingress: "true"`

## Installation

```bash
# Add Traefik Helm repository
helm repo add traefik https://traefik.github.io/charts
helm repo update

# Create namespace
kubectl apply -f namespace.yaml

# Install Traefik
helm install traefik traefik/traefik \
  -n traefik \
  -f values.yaml

# Wait for Traefik to be ready
kubectl wait --namespace traefik \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/name=traefik \
  --timeout=90s
```

## Verify Installation

```bash
# Check Traefik pods
kubectl get pods -n traefik

# Check Traefik service and LoadBalancer IP
kubectl get svc -n traefik

# Verify LoadBalancer has IP 192.168.0.100
kubectl get svc traefik -n traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'

# Check Traefik dashboard IngressRoute
kubectl get ingressroute -n traefik
```

## Access Dashboard

The Traefik dashboard is available at `https://traefik.esweiss.com` (requires DNS configuration).

## Configuration

- **LoadBalancer IP**: 192.168.0.100 (MetalLB public pool)
- **Replicas**: 2 (HA configuration)
- **Node Selector**: `esweiss.com/ingress: "true"` (scheduled on k3s-agt-opt-01)
- **Ports**:
  - 80 (HTTP) → Redirects to 443
  - 443 (HTTPS)
  - 9100 (Prometheus metrics)

## Next Steps

After Traefik is installed:

1. Configure DNS to point services to 192.168.0.100
2. Deploy external-dns for automatic DNS management
3. Configure TLS certificates (cert-manager or acme.sh integration)
