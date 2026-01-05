# cert-manager

cert-manager provides automatic TLS certificate management for the k3s cluster using Let's Encrypt with Cloudflare DNS-01 challenge.

## Prerequisites

- Traefik installed and configured
- Cloudflare API token with DNS edit permissions for ericsweiss.com

## Installation

```bash
# Add cert-manager Helm repository
helm repo add jetstack https://charts.jetstack.io
helm repo update

# Create namespace
kubectl apply -f namespace.yaml

# Install cert-manager with CRDs
helm install cert-manager jetstack/cert-manager \
  -n cert-manager \
  -f values.yaml

# Wait for cert-manager to be ready
kubectl wait --namespace cert-manager \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/name=cert-manager \
  --timeout=90s
```

## Configure Cloudflare API Token

Create a secret with your Cloudflare API token:

```bash
# Get token from 1Password
export CLOUDFLARE_API_TOKEN=$(op read "op://Homelab/Cloudflare DNS Token/credential")

# Create secret in cert-manager namespace
kubectl create secret generic cloudflare-api-token \
  --namespace=cert-manager \
  --from-literal=api-token="${CLOUDFLARE_API_TOKEN}"
```

## Deploy Certificate Resources

```bash
# Apply ClusterIssuer and Certificate
kubectl apply -k .

# Wait for certificate to be ready
kubectl wait --namespace default \
  --for=condition=ready certificate \
  ericsweiss-com-wildcard \
  --timeout=300s
```

## Verify Installation

```bash
# Check cert-manager pods
kubectl get pods -n cert-manager

# Check ClusterIssuer
kubectl get clusterissuer

# Check Certificate status
kubectl get certificate -n default
kubectl describe certificate ericsweiss-com-wildcard -n default

# Verify TLS secret was created
kubectl get secret ericsweiss-com-tls -n default
```

## Configuration

- **Issuer**: Let's Encrypt production (letsencrypt-prod)
- **Challenge Type**: DNS-01 via Cloudflare
- **Domain**: *.ericsweiss.com (wildcard)
- **Secret Name**: ericsweiss-com-tls
- **Renewal**: 30 days before expiration (720h)

## Certificate Details

The wildcard certificate covers:
- *.ericsweiss.com (all subdomains)
- ericsweiss.com (apex domain)

This single certificate is used by all IngressRoutes in the cluster.

## Troubleshooting

```bash
# Check certificate order status
kubectl get certificaterequest -n default

# Check ACME challenge
kubectl get challenges -n default

# View cert-manager logs
kubectl logs -n cert-manager -l app=cert-manager

# Check certificate details
kubectl describe certificate ericsweiss-com-wildcard -n default
```

## Next Steps

After cert-manager is installed and the certificate is issued:

1. Deploy IngressRoutes that reference the `ericsweiss-com-tls` secret
2. Verify HTTPS works on your services
3. External-dns will automatically create DNS records pointing to Traefik LoadBalancer
