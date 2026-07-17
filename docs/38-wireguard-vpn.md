# WireGuard VPN (wg-easy) — internet-exit VPN

`wg-easy` (v15) is a WireGuard VPN + web admin UI that gives the user and
trusted friends/family an **internet-only exit** through the home connection.
Connected clients get a full tunnel to the internet but are **fenced out of the
home network entirely** — they cannot reach `192.168.0.0/24`, any RFC1918/CGNAT/
link-local range, the k3s pod/service networks, the tailnet, or internal DNS.

It runs as a single pod in the `wg-easy` namespace, reconciled by Flux from
`kubernetes/apps/wg-easy/`.

- **VPN endpoint (WAN):** `vpn.ericsweiss.com:51820/udp`
- **Admin UI (internal only):** `https://vpn.esweiss.com` (Authentik-gated)
- **Client subnet:** `10.8.0.0/24` (IPv4 only)
- **Image:** `ghcr.io/wg-easy/wg-easy:15.3.0` (pinned via `wg_easy_version`)

---

## Architecture

```
                      Internet
                         │
   friend's phone ── WireGuard/UDP ──► vpn.ericsweiss.com:51820  (Cloudflare
   (wg client, full                      DNS-only A record, DDNS-tracked)
    tunnel 0.0.0.0/0)                          │
                                     home public IP / router
                                     forwards :51820/udp
                                               │
                                     MetalLB VIP 192.168.0.99  (vpn-pool, L2)
                                               │  ETP Local → announced from
                                               │  the node running the pod
                                     ┌─────────▼──────────┐
                                     │  wg-easy pod       │
                                     │  wg0 (10.8.0.1)    │
                                     │  MASQUERADE → eth0 │
                                     └─────────┬──────────┘
                                               │ egress NetworkPolicy:
                                               │ 0.0.0.0/0 EXCEPT RFC1918/CGNAT
                                               ▼
                                          the Internet   (LAN is unreachable)

   admin ── https://vpn.esweiss.com ─► Traefik(internal .101) ─► lan-tailscale-only
                                        + Authentik ForwardAuth ─► wg-easy-ui:51821
```

- **State**: SQLite DB + `wg0` config live on NFS at `/appdata/wg-easy`
  (`ssd/appdata/wg-easy`, ZFS-encrypted at rest, captured by the archive
  replicator for free). The DB holds the server keypair, every peer's public +
  preshared key, and the admin credential hash — treat it as sensitive.
- **Scheduling**: the pod is pinned to an `esweiss.com/ingress` agent
  (`nodeSelector`). With `externalTrafficPolicy: Local`, MetalLB L2 announces the
  `.99` VIP from the node running the pod, so that node **must** carry
  `sg-k3s-ingress-pub` (the WAN `:51820/udp -> .99` rule). Exactly the 5 ingress
  agents (laptop/opt-01/02/03/prec-01) carry that group; the NAS agent is
  `esweiss.com/general` but **not** `esweiss.com/ingress`, so selecting on the
  ingress label excludes it by construction — a `general` selector would not,
  since the NAS agent is also `general` yet lacks the firewall group. State is on
  NFS, so the pod is not otherwise pinned to a specific node.
- **Privilege**: the pod runs in a PSA `privileged` namespace. The wg-easy
  container is root + `CAP_NET_ADMIN` only (no `SYS_MODULE` — the `wireguard`
  kernel module is already loaded by flannel's `wireguard-native` backend on
  every node). A one-shot **privileged initContainer** sets the pod-netns
  sysctls `net.ipv4.ip_forward=1` and `net.ipv4.conf.all.src_valid_mark=1`
  (namespaced sysctls; k3s does not allowlist them as unsafe sysctls, so setting
  them in the shared netns via an initContainer avoids a fleet-wide kubelet
  change).

---

## The security model — no-LAN enforcement

The whole point of this VPN is that **friends' devices are untrusted**. A guest
phone may be compromised, misconfigured, or actively hostile; it must be able to
browse the internet through the home IP and nothing more. The client → LAN fence
is enforced in **two egress layers** so that no single misconfiguration re-opens
the LAN. A third control — the inbound WAN firewall rule — scopes how the
endpoint is *reached*; it is described separately below and is **not** part of
the client → LAN egress fence.

### Layer 1 — client config (wg-easy)
Every client is pushed a full tunnel (`AllowedIPs = 0.0.0.0/0`) with public
resolvers (`1.1.1.1`, `1.0.0.1`) — **never** the internal AdGuard resolvers.
This keeps a well-behaved client from ever sending LAN-destined traffic or
leaking internal hostnames. It is bootstrapped by `INIT_ALLOWED_IPS` / `INIT_DNS`
and applied per-client thereafter in the UI. *This layer trusts the client, so
it is not sufficient on its own.*

### Layer 2 — NetworkPolicy egress (the codified guarantee)
All tunneled client traffic is NAT'd (`MASQUERADE`) out through the wg-easy pod's
`eth0`, so it leaves with the **pod's** source IP and is therefore governed by
the pod's egress NetworkPolicy (`allow-egress-wg-easy`). That policy allows
egress to `0.0.0.0/0` **except**:

```
10.0.0.0/8   172.16.0.0/12   192.168.0.0/16   100.64.0.0/10   169.254.0.0/16
```

Because this is enforced at the CNI layer (k3s's built-in NetworkPolicy
controller), it holds **regardless of what a client sets its AllowedIPs to** — a
malicious client that rewrites its config to route `192.168.0.0/24` still cannot
reach the LAN, because the packet is dropped as it tries to leave the pod. This
is the same policy-layer killswitch pattern the downloads/Gluetun stack uses.

**Internal DNS is inside this fence too.** The policy has **no** `kube-dns`
egress allow, and the pod runs with `dnsPolicy: None` → `1.1.1.1`/`1.0.0.1` (it
is not cluster-integrated and never needs internal DNS). This matters precisely
because forwarded client traffic obeys these same egress rules: an additive
`kube-dns` allow would let a connected client point its resolver at the CoreDNS
ClusterIP `10.43.0.10` and use cluster DNS as an open internal resolver. With no
such allow, `10.43.0.10` (inside the excepted `10.0.0.0/8`, as are the CoreDNS
pod IPs) is dropped — so a connected client genuinely **cannot reach internal
DNS**, matching the user-confirmed invariant. Client DNS to `1.1.1.1` is public
and covered by the internet rule.

### Inbound endpoint scoping — Proxmox host firewall (not a no-LAN egress layer)
The only inbound path is the WAN `:51820/udp` forward to the `.99` VIP. The
`sg-k3s-ingress-pub` rule that admits it is **`-dest`-scoped to `192.168.0.99`**,
so it opens the wg-easy endpoint without exposing the node's own `:51820/udp`
(that port is flannel's `wireguard-native` inter-node encryption, restricted to
`k3s_nodes`). This rule controls *who can reach the endpoint from the WAN* — it
does **nothing** to fence a *connected* client out of the LAN (that is Layers 1–2
above). Egress from the k3s VMs is already permitted at the guest-firewall level;
the CNI NetworkPolicy is the meaningful egress control.

### Deviation from the original spec — no in-pod iptables hooks
The original (user-confirmed) design called for a third *in-pod* enforcement
layer: `iptables` `FORWARD … -j DROP` post-up hooks inside wg-easy that drop
LAN-destined forwarded client traffic. That was **intentionally dropped** in
favour of the CNI-layer egress NetworkPolicy (Layer 2 above), which is a strictly
stronger, config-independent guarantee: it is enforced *outside* the pod by the
CNI, so it cannot be undone by anything that manipulates the pod's own
netns/iptables, and it does not depend on wg-easy's hook configuration surviving
upgrades. The one thing an in-pod `FORWARD` drop covers that a plain
NetworkPolicy cannot express — blocking forwarded client traffic to the CoreDNS
*ClusterIP* while still letting the pod resolve names — is instead handled by
giving the pod its **own** public resolver (`dnsPolicy: None`) and removing the
`kube-dns` egress allow, which makes Layer 2 a genuine superset (see Layer 2).
wg-easy 15.3.0's optional **Per-Client Firewall** (Admin Panel → Interface)
remains available to further restrict individual clients, but is not required for
the no-LAN fence and is not configured by default. Consequently the verification
runbook below tests the **two real enforcement points** (client DNS + the
NetworkPolicy egress, including the internal-DNS check) — there is no separate
"iptables hooks" layer to verify, by design.

### Threat model summary
- **Untrusted client** (compromised friend device): can browse the internet via
  the home IP; **cannot** touch the LAN, internal DNS, or other clients' state.
  Layer 2 guarantees this even if the client tampers with its own config or
  points its resolver at the CoreDNS ClusterIP.
- **Exposed WAN endpoint**: `vpn.ericsweiss.com:51820/udp` exposes the home
  public IP (same as `git`/`direct`). WireGuard **silently drops** every packet
  that is not from a configured peer with valid crypto — no response, no
  amplification, no fingerprint — so an open `:51820/udp` is safe by design.
- **Admin UI**: internal-only (`lan-tailscale-only`) **and** behind Authentik
  ForwardAuth (`vpn-admins` group), on top of wg-easy's own admin login. Never
  reachable from the internet.
- **Why public DNS for clients**: pushing `1.1.1.1` (not AdGuard) keeps
  untrusted devices from resolving internal names or reaching `192.168.0.150/160`
  — part of the no-LAN posture, and it means friends' traffic isn't filtered/
  logged by the home resolver.
- **IPv6**: disabled (`DISABLE_IPV6=true`). The LAN has no working IPv6 egress,
  so a v6 tunnel would black-hole. v4-only keeps the model simple; the egress
  policy's `except` list is v4 (no v6 LAN to fence).

---

## Deploy plan

Order matters only in that the 1Password item and the router forward are manual
prerequisites; the Flux/Ansible/Terraform changes are otherwise independent.

### 1. Create the 1Password item (operator)
In the **Homelab** vault, create item **`WireGuard VPN`** with fields:

| Field | Value |
|-------|-------|
| `init-username` | admin username for the wg-easy UI (e.g. `eric`) |
| `init-password` | a long admin password (≥16 chars; not complexity-checked by wg-easy) |
| `metrics-token` | a random Bearer token for the Prometheus endpoint (e.g. `openssl rand -hex 24`) |

`init-*` sync into the `wg-easy-secrets` Secret (consumed **only on first boot**).
`metrics-token` syncs into `observability-exporter-secrets` in the observability
namespace (read by the ServiceMonitor) and is pasted into the UI in step 6.

### 2. Ansible — NFS export dir + firewall
```bash
# creates ssd/appdata/wg-easy and its NFS export subdir (owner 1000:2000)
task infra:deploy   # or: ansible-playbook -i ansible/inventories/prod ansible/playbooks/storage.yml --limit pve-nas-01
# firewall: renders the sg-k3s-ingress-pub -dest .99 :51820/udp rule
ansible-playbook -i ansible/inventories/prod ansible/playbooks/site.yml --tags proxmox_firewall
```
(On merge CI's `deploy-ansible-firewall` job applies the firewall rule
automatically — the `ansible/roles/proxmox_firewall/**` change is a trigger — so
the manual run above is only for out-of-band deploys.)
(`nas_appdata_dirs` gained `wg-easy`; `cluster.fw.j2` gained the VIP-scoped
WireGuard rule.)

### 3. Terraform — external DNS record
```bash
task terraform:plan      # review the new cloudflare_record.vpn (A, DNS-only)
task terraform:apply
# seed the live IP immediately (record is created at the placeholder until the
# next DDNS run):
kubectl -n cloudflare-ddns create job --from=cronjob/cloudflare-ddns manual-$(date +%s)
```

### 4. Flux — the app + platform edits
Commit + push the branch, merge the MR. Flux reconciles:
- `kubernetes/apps/wg-easy/` (the app)
- `metallb-ip-pools.yaml` (`vpn-pool` .99 + L2Advertisement)
- `cloudflare-ddns/cronjob.yaml` (adds `vpn.ericsweiss.com`)
- observability (ServiceMonitor + blackbox target + `WgEasyDown` alert +
  `observability-exporter-secrets` metrics token)
- `versions-configmap.yaml` (`wg_easy_version`)

Force it if impatient: `task flux:reconcile`. Verify:
```bash
task vpn:status                          # pod Ready, svc EXTERNAL-IP 192.168.0.99
kubectl get svc -n wg-easy wg-easy       # confirms the VIP was assigned
```

### 5. Router — WAN port-forward (operator)
On the Asus GT-AX11000 Pro (`192.168.0.1`):
- Forward **WAN UDP 51820 → 192.168.0.99:51820**.
- Ensure **192.168.0.99 is excluded from the DHCP pool** (it sits in the
  `.1–99` workstation range). Set a DHCP reservation-exclusion or shrink the
  pool so DHCP never hands out `.99`.

### 6. Enable metrics (operator, one-time)
Log into `https://vpn.esweiss.com` (Authentik → wg-easy admin login) →
**Admin Panel → General** → enable **Prometheus** and set the **Bearer
password** to the exact `metrics-token` value from 1Password. The ServiceMonitor
(`observability`) then scrapes `wireguard_*` metrics. (Until this is done the
scrape is 401/`up=0`; no alert keys off it — `WgEasyDown` watches Deployment
availability instead.)

### 7. Authentik provider + application (operator, one-time)
The pinned wg-easy 15.3.0 has no native OIDC (generic `OAUTH_PROVIDERS` support
landed on upstream `master` ~4 weeks after the 15.3.0 release and is not in this
tag), so the UI is protected by Traefik ForwardAuth via the shared
`authentik-auth` outpost — the same pattern the `*arr` apps use. In the Authentik
admin UI:

1. **Directory → Groups →** create group `vpn-admins`; add yourself.
2. **Applications → Providers → Create → Proxy Provider**:
   - Name: `wg-easy`
   - Authorization flow: `default-provider-authorization-implicit-consent`
   - **Forward auth (single application)**
   - External host: `https://vpn.esweiss.com`
3. **Applications → Applications → Create**:
   - Name: `wg-easy`, Slug: `wg-easy`
   - Provider: `wg-easy`
   - **Policy bindings** → bind group `vpn-admins` (only that group may enter).
4. **Outposts →** edit the embedded `authentik Embedded Outpost` and ensure the
   `wg-easy` application is added to it (the `authentik-auth` middleware points
   at that outpost).

This is the same forward-auth pattern the `*arr` apps use; see
`kubernetes/apps/authentik/README.md` and `docs/23-recipes-sso-setup.md`.

---

## Client onboarding (per person)

1. `https://vpn.esweiss.com` → **New Client** → name it (e.g. `alice-phone`).
2. wg-easy assigns a `10.8.0.x` address and pushes `AllowedIPs=0.0.0.0/0`,
   DNS `1.1.1.1,1.0.0.1`, endpoint `vpn.ericsweiss.com:51820`.
3. Hand off the config: **QR code** (mobile — WireGuard app → scan) or download
   the `.conf` (desktop). Use a one-time link for remote handoff.
4. To revoke: disable or delete the client in the UI (takes effect immediately).

There is nothing to configure on the client for the no-LAN fence — it is
enforced server-side (layer 2).

---

## Verification (from a connected client)

After connecting a test client, confirm the fence:

```bash
# Internet works, and exits via the HOME public IP:
curl -s https://checkip.amazonaws.com          # should print your home WAN IP
curl -s https://1.1.1.1                          # reachable

# LAN is unreachable (all of these MUST fail / time out):
curl -m 5 http://192.168.0.1/            ; echo "exit=$?"   # router  -> fail
curl -m 5 https://192.168.0.101/         ; echo "exit=$?"   # Traefik -> fail
ping -c1 -W2 192.168.0.150               ; echo "exit=$?"   # AdGuard -> fail

# Internal DNS must NOT be used (name resolution goes to 1.1.1.1):
nslookup git.esweiss.com                  # should NOT resolve to 192.168.0.101

# Cluster DNS must be unreachable even if a client deliberately targets the
# CoreDNS ClusterIP directly (this is why the pod uses public DNS and the egress
# policy has no kube-dns allow — Layer 2):
nslookup git.esweiss.com 10.43.0.10       ; echo "exit=$?"   # MUST fail / time out
```

Server-side:
```bash
task vpn:peers        # shows the handshake for the connected client
task vpn:status
```

---

## Operations & runbook

| Task | Command |
|------|---------|
| Status (pod/svc/VIP/PVC/ingress) | `task vpn:status` |
| Live peers / handshakes | `task vpn:peers` |
| Logs | `task vpn:logs` |
| Restart the pod | `task vpn:restart` |

- **Config changes** (client subnet, endpoint, hooks, per-client firewall) are
  made in the **UI**. The `INIT_*` env vars apply on first boot only — editing
  them later is a no-op.
- **Admin password rotation**: change it in the UI (Admin Panel), then update
  `init-password` in 1Password for documentation parity (it is not re-read).
- **Optional server-side per-client firewall**: v15.3 has an experimental
  "Per-Client Firewall" (Admin Panel → Interface) that enforces destination
  allowlists per client with iptables. It is redundant with layer 2 for the
  no-LAN guarantee, but can further restrict individual clients (e.g. web-only).
- **`WgEasyDown`** (critical) fires when the Deployment has 0 available replicas
  for 15m. **`EndpointDown`** covers `vpn.esweiss.com` (UI reachability via the
  blackbox `http_sso` probe). No handshake-staleness alert exists — an idle VPN
  with no connected clients is normal, not an incident.

## Backup & restore

State is on `ssd/appdata/wg-easy` (a child of the `ssd/appdata` archive root), so
it is snapshotted + replicated to `archive/appdata` by the nightly archive
replicator with no extra configuration.

**Restore** (lost/rebuilt cluster, NFS data intact): the PV/PVC re-bind to the
existing `/appdata/wg-easy` and wg-easy comes back with all peers — no INIT
re-bootstrap (the DB already exists, so `INIT_*` is skipped). If the NFS dataset
itself was lost, restore it from the archive replica first:
```bash
# on pve-nas-01, restore the dataset from the archive pool, then let Flux
# recreate the pod (peers + server key are in the restored SQLite DB).
```
If the DB is unrecoverable, wg-easy re-bootstraps a **new** server key on next
start (INIT applies to an empty `/etc/wireguard`); every client must then be
re-issued a config.

## Gotchas

- **`.99` is in the DHCP range** (`.1–99`). It MUST be excluded from the router's
  DHCP pool or a client will collide with the VIP.
- **flannel owns node `:51820/udp`**. The WAN firewall rule is `-dest`-scoped to
  the `.99` VIP so it never exposes flannel's inter-node WireGuard. Do not
  broaden it to a bare `-dport 51820`.
- **SQLite on NFS** is safe here only because there is exactly one writer
  (`replicas: 1`, `Recreate`). Do not scale wg-easy.
- **Metrics need the one-time UI enable** (step 6) — the ServiceMonitor alone
  does not turn them on.
