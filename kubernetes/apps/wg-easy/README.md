# wg-easy — internet-exit WireGuard VPN

WireGuard VPN (wg-easy v15) for the user + friends/family. It provides
**internet-only egress through the home connection**: connected clients can
reach the internet but **cannot** reach the home LAN (`10.0.10.0/24`), any
RFC1918/CGNAT/link-local range, or internal DNS. It is enforced in two
independent egress layers - the pushed client config and the pod egress
NetworkPolicy, the latter being the codified guarantee - plus a `-dest`-scoped
inbound WAN rule that is not part of the egress fence. Full architecture, the
security model, and the client-onboarding runbook are in
[`docs/38-wireguard-vpn.md`](../../../docs/38-wireguard-vpn.md).

- **VPN endpoint** (WAN): `vpn.ericsweiss.com:51820/udp` → router forwards to the
  MetalLB VIP `10.0.10.99` (`vpn-pool`).
- **Admin UI** (internal only): `https://vpn.esweiss.com`, behind
  `lan-tailscale-only` + Authentik ForwardAuth (`vpn-admins` group). Never
  exposed publicly.

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | Namespace + PSA (enforce `privileged`; warn/audit `restricted`). |
| `externalsecret.yaml` | `wg-easy-secrets` from 1Password "WireGuard VPN" (init-username/password). |
| `storage.yaml` | NFS PV+PVC for `/etc/wireguard` (SQLite DB + peer configs; encrypted at rest, archived). |
| `deployment.yaml` | Deployment (replicas 1, Recreate); privileged sysctl initContainer; wg-easy container (NET_ADMIN). |
| `service.yaml` | `wg-easy` LoadBalancer (UDP 51820, VIP .99, ETP Local) + `wg-easy-ui` ClusterIP (TCP 51821). |
| `ingress-route.yaml` | Internal-only IngressRoute (`vpn.esweiss.com`). |
| `certificate.yaml` | Internal cert `wg-easy-esweiss-tls` (CN vpn.esweiss.com). |
| `networkpolicy.yaml` | default-deny-egress + the CNI-layer no-LAN killswitch + scoped ingress. |
| `vpa.yaml` | VPA Initial for the app; Off for the sysctl initContainer. |

Metrics are scraped by a **ServiceMonitor** in the `observability` namespace
(`kubernetes/infrastructure/observability/service-monitors/wg-easy.yaml`), using
a Bearer token from `observability-exporter-secrets` — the repo convention for
scrape auth. Prometheus export is a per-install UI setting, so a rebuilt or
wiped state directory needs the enable step again (see Notes).

## Ops

```bash
task wg-easy:status     # pods, services (VIP .99), PVC, ingress
task wg-easy:peers      # live WireGuard handshakes (wg show)
task wg-easy:logs       # tail wg-easy logs
task wg-easy:restart    # pod delete (Flux-managed, Recreate)
```

## Notes

- **Versions**: `wg_easy_version` in `all.yml` (container tag has no leading
  `v`). WireGuard kernel module is provided by the nodes (flannel
  wireguard-native) — wg-easy needs **no** SYS_MODULE.
- **Metrics** live in the wg-easy database, not in git: Admin Panel > General →
  enable Prometheus and set the Bearer password to the `metrics-token` value
  from 1Password "WireGuard VPN". Redo this after any state wipe; confirm with
  `wireguard_configured_peers` in Prometheus. `WgEasyMetricsMissing` fires 30m
  after that series disappears; `WgEasyDown` keys off Deployment availability,
  so it stays valid either way.
- **Bootstrap** (`INIT_*`) applies on first boot only; later changes are UI
  actions. See docs/38.
- **Tunnel MTU**: wg0 is 1420, the same as the pod network (flannel
  wireguard-native), so full-size VPN packets are IP-fragmented on the node/pod
  hop. See [`docs/38-wireguard-vpn.md`](../../../docs/38-wireguard-vpn.md)
  § Tunnel MTU.
