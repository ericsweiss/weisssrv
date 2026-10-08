# tailnet-dns

A two-replica **CoreDNS** deployment that serves the tailnet's split-horizon
view of `*.esweiss.com`, so Tailscale clients resolve internal hostnames to
their internal addresses. The Tailscale-side wiring (the split-DNS nameserver
that points at this Service) is policy-as-code in `terraform/tailscale`.

- **Config**: `configmap.yaml` holds the Corefile; the Deployment carries
  `reloader.stakater.com/auto` so a Corefile edit rolls the pods.
- **Labels**: the selector is the legacy bare `app: tailnet-dns` (immutable);
  `app.kubernetes.io/name` is also set so cross-namespace podSelectors written
  against the repo convention still match.
- **Availability**: 2 replicas, `system-cluster-critical`, hostname
  topology-spread, plus a PDB (`pdb.yaml`).
- **Observability**: `podmonitor.yaml` scrapes CoreDNS `:9153`.
- **Image**: pinned to the CoreDNS build k3s already caches on every node, so
  there is no new pull.

## Disable

Drop `- tailnet-dns` from `kubernetes/apps/kustomization.yaml`; Flux prunes the
namespace. Drop the Split-DNS nameserver pointing at this Service from
`terraform/tailscale` in the same change, or tailnet clients keep sending
`*.esweiss.com` queries to an address that no longer answers.
