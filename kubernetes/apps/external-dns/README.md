# external-dns

external-dns automatically manages DNS records in Cloudflare for services and ingresses in the k3s cluster.

## Prerequisites

- Cloudflare API token with DNS edit permissions
- Domain `ericsweiss.com` managed in Cloudflare

## Installation

### 1. Create Cloudflare API Token Secret

```bash
# Create namespace
kubectl apply -f namespace.yaml

# Create secret with Cloudflare API token
kubectl create secret generic cloudflare-api-token \
  -n external-dns \
  --from-literal=api-token="$(op read 'op://Homelab/Cloudflare DNS Token/credential')"
```

### 2. Install external-dns

```bash
# Add external-dns Helm repository
helm repo add external-dns https://kubernetes-sigs.github.io/external-dns
helm repo update

# Install external-dns
helm install external-dns external-dns/external-dns \
  -n external-dns \
  -f values.yaml

# Wait for external-dns to be ready
kubectl wait --namespace external-dns \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/name=external-dns \
  --timeout=90s
```

## Verify Installation

```bash
# Check external-dns pods
kubectl get pods -n external-dns

# Check external-dns logs
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns

# Watch for DNS record creation events
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns -f
```

## Usage

external-dns will automatically create DNS records for:

### Service with LoadBalancer

```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-service
  annotations:
    external-dns.alpha.kubernetes.io/hostname: myapp.ericsweiss.com
spec:
  type: LoadBalancer
  # ...
```

### Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-ingress
spec:
  rules:
    - host: myapp.ericsweiss.com
      # ...
```

## Configuration

- **Provider**: Cloudflare
- **Domain**: ericsweiss.com (external domain)
- **Policy**: sync (create and delete records automatically)
- **TXT Ownership**: _external-dns.{record} TXT records for ownership tracking
- **Sync Interval**: 5 minutes

## Notes

- Only manages DNS for `ericsweiss.com` (external domain)
- Internal domain `esweiss.com` is managed by AdGuard Home
- TXT records are created with ownership ID `k3s-external-dns`
- Records are automatically cleaned up when services/ingresses are deleted

## Troubleshooting

```bash
# Check external-dns logs for errors
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns

# Verify secret is created correctly
kubectl get secret cloudflare-api-token -n external-dns -o yaml

# Check Cloudflare API connectivity
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns | grep -i cloudflare
```
