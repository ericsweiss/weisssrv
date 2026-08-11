# vm-ingress

Traefik routing objects for workloads that are **not** in Kubernetes: the Plex
LXC, the Home Assistant VM, the AdGuard LXCs, the router, the GitLab VM, and the
Nextcloud/Immich VMs. There are no pods here — only IngressRoutes, Services with
hand-written EndpointSlices, ServersTransports, a certificate, and the `gitlab`
namespace.

- **Namespaces**: resources deliberately span `default`, `gitlab` and `traefik`,
  so this kustomization sets **no** `namespace:` transformer; each file carries
  its own. The shared `netpol-baseline` component is namespaced to `gitlab` by a
  per-inclusion patch.
- **Middlewares**: `middleware.yaml` holds the vm-specific chains. Internal-only
  hosts take `lan-tailscale-only`; the router takes `lan-tailscale-strict`,
  which additionally drops the k3s pod CIDR.
- **Per-target docs**: Plex [docs/20](../../../docs/20-plex-deployment.md),
  Home Assistant [docs/24](../../../docs/24-home-assistant-deployment.md),
  GitLab [docs/27](../../../docs/27-gitlab-deployment.md),
  Nextcloud [docs/35](../../../docs/35-nextcloud.md),
  Immich [docs/36](../../../docs/36-immich.md),
  AdGuard/DNS [docs/08](../../../docs/08-dns.md).
