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
  hosts take `lan-tailscale-only`; the router and the AdGuard UIs take
  `lan-tailscale-strict`, which drops the k3s pod CIDR **and** narrows the Home
  VLAN to the `10.0.20.8/29` admin block — these are the routes with no
  forward-auth, so the allowlist is the only gate in front of the appliance
  login (docs/46).
- **Per-target docs**: Plex [docs/20](../../../docs/20-plex-deployment.md),
  Home Assistant [docs/24](../../../docs/24-home-assistant-deployment.md),
  GitLab [docs/27](../../../docs/27-gitlab-deployment.md),
  Nextcloud [docs/35](../../../docs/35-nextcloud.md),
  Immich [docs/36](../../../docs/36-immich.md),
  AdGuard/DNS [docs/08](../../../docs/08-dns.md),
  Router/gateway [docs/46](../../../docs/46-unifi-network.md) — the one target
  whose backend scheme, port and ServersTransport are doc-dependent (HTTPS-only
  on `:443` behind `unifi-self-signed`).
