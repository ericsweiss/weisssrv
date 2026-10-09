# WireGuard VPN (wg-easy) — internet-exit VPN

`wg-easy` (v15) is a WireGuard VPN + web admin UI that gives the user and
trusted friends/family an **internet-only exit** through the home connection.
Connected clients get a full tunnel to the internet but are **fenced out of the
home network entirely** — they cannot reach `10.0.10.0/24`, any RFC1918/CGNAT/
link-local range, the k3s pod/service networks, the tailnet, or internal DNS.

It runs as a single pod in the `wg-easy` namespace, reconciled by Flux from
`kubernetes/apps/wg-easy/`.

- **VPN endpoint (WAN):** `vpn.ericsweiss.com:51820/udp`
- **Admin UI (internal only):** `https://vpn.esweiss.com` (Authentik-gated)
- **Client subnet:** `10.8.0.0/24` (IPv4 only)
- **Image:** `ghcr.io/wg-easy/wg-easy:${wg_easy_version}` (the pin lives in `group_vars/all.yml`)

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
                                     MetalLB VIP 10.0.10.99  (vpn-pool, L2)
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
  preshared key, and the admin credential hash — treat it as sensitive. SQLite
  on NFS tolerates exactly one writer, which is why the Deployment is
  `replicas: 1` with the `Recreate` strategy.
- **Scheduling**: the pod is pinned to an `esweiss.com/ingress` agent
  (`nodeSelector`). With `externalTrafficPolicy: Local`, MetalLB L2 announces the
  `.99` VIP from the node running the pod, so that node **must** carry
  `sg-k3s-ingress-pub` (the WAN `:51820/udp -> .99` rule). Exactly the 5 ingress
  agents (laptop/opt-01/02/03/prec-01) carry that group; the NAS agent is
  `esweiss.com/general` but **not** `esweiss.com/ingress`, so selecting on the
  ingress label excludes it by construction — a `general` selector would not,
  since the NAS agent is also `general` yet lacks the firewall group. State is on
  NFS, so the pod is not otherwise pinned to a specific node.
- **Privilege**: the pod runs in a PSA `privileged` namespace, which is
  acceptable because wg-easy is the only workload in it; `audit` and `warn` stay
  `restricted`, so any other pod landing there still trips an admission warning
  and an audit annotation. The wg-easy container is root + `CAP_NET_ADMIN` only
  (no `SYS_MODULE` — the `wireguard` kernel module is already loaded by
  flannel's `wireguard-native` backend on every node, and no `DAC_OVERRIDE` —
  the appdata export squashes every client to uid 1000, so the server's ACCESS
  RPC grants root's writes and the client-side `DAC_OVERRIDE` path is never
  reached). A one-shot **privileged initContainer** sets the pod-netns
  sysctls `net.ipv4.ip_forward=1` and `net.ipv4.conf.all.src_valid_mark=1`
  (namespaced sysctls; k3s does not allowlist them as unsafe sysctls, so setting
  them in the shared netns via an initContainer avoids a fleet-wide kubelet
  change).
- **VIP placement**: `10.0.10.99` must stay outside the Homelab VLAN's DHCP
  pool, which `terraform/unifi/networks.tf` codifies as
  `10.0.10.2-10.0.10.98` and the `unifi-drift-plan` job re-checks
  ([docs/46](46-unifi-network.md)). A colliding lease is invisible except as
  `EndpointDown`.

### Tunnel MTU

`wg0` is **1420** (the wg-easy default, stored in the SQLite DB and visible in
`wg0.conf` on the NFS volume), and the pod network is also 1420 because
`k3s_flannel_backend` is `wireguard-native`. A full-size inner packet therefore
yields a roughly 1480-byte outer datagram that is IP-fragmented on the node/pod
hop rather than black-holed, since kernel WireGuard leaves DF clear.

The non-fragmenting value is **1360** (1420 minus 20 IPv4, 8 UDP and 32
WireGuard). It can only be set in the admin UI, because the value lives in the
NFS-backed `wg-easy.db` and every peer would have to re-import its config. A CNI
or MTU change moves it.

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
malicious client that rewrites its config to route `10.0.10.0/24` still cannot
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

There are deliberately no in-pod `iptables` hooks: the CNI egress policy is
enforced outside the pod, so nothing that manipulates the pod's own netns can
undo it, and it survives wg-easy upgrades. wg-easy's optional Per-Client
Firewall (Admin Panel → Interface) can further restrict individual clients but
is not part of the no-LAN fence and is not configured.

### Inbound endpoint scoping — Proxmox host firewall (not a no-LAN egress layer)
The only inbound path is the WAN `:51820/udp` forward to the `.99` VIP. The
`sg-k3s-ingress-pub` rule that admits it is **`-dest`-scoped to `10.0.10.99`**,
so it opens the wg-easy endpoint without exposing the node's own `:51820/udp`
(that port is flannel's `wireguard-native` inter-node encryption, restricted to
`k3s_nodes`). This rule controls *who can reach the endpoint from the WAN* — it
does **nothing** to fence a *connected* client out of the LAN (that is Layers 1–2
above). Egress from the k3s VMs is already permitted at the guest-firewall level;
the CNI NetworkPolicy is the meaningful egress control.

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
  untrusted devices from resolving internal names or reaching `10.0.10.150/160`
  — part of the no-LAN posture, and it means friends' traffic isn't filtered/
  logged by the home resolver.
- **IPv6**: disabled (`DISABLE_IPV6=true`). The LAN has no working IPv6 egress,
  so a v6 tunnel would black-hole. v4-only keeps the model simple; the egress
  policy's `except` list is v4 (no v6 LAN to fence).

---

## Rebuild reference

Everything below is already in place. It is recorded so the VPN can be
reconstructed, not as a checklist to work through. Only the 1Password item and
the WAN forward are manual prerequisites; the Flux, Ansible and Terraform state
is otherwise independent and already committed.

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
automatically — `ansible/inventories/prod/hosts.yml` and `ansible/requirements.yml`
are triggers — so
the manual run above is only for out-of-band deploys.)
The NFS export subdir comes from `nas_storage_appdata_dirs`
(`host_vars/pve-nas-01.yml`); the VIP-scoped WireGuard rule is rendered into
`[group sg-k3s-ingress-pub]` by the collection's `cluster.fw.j2` from
`proxmox_firewall_wan_wireguard_vips` (`group_vars/all.yml`).

### 3. Terraform — external DNS record
```bash
# review the new module.zone.cloudflare_record.protected_external_content["vpn"]
# (A, DNS-only) — the entry itself is local.dns_records["vpn"] in dns.tf
task terraform:cloudflare-plan
task terraform:cloudflare-apply
# seed the live IP immediately (record is created at the placeholder until the
# next DDNS run):
kubectl -n cloudflare-ddns create job --from=cronjob/cloudflare-ddns manual-$(date +%s)
```

### 4. Flux
Commit + push the branch, merge the MR; Flux reconciles `kubernetes/`. Force it
if impatient: `task flux:reconcile`. Verify:
```bash
task wg-easy:status                          # pod Ready, svc EXTERNAL-IP 10.0.10.99
kubectl get svc -n wg-easy wg-easy       # confirms the VIP was assigned
```

### 5. Gateway — WAN port-forward (codified, nothing to do by hand)
Both halves live in `terraform/unifi/` on the UCG-Fiber
([docs/46](46-unifi-network.md)) and are applied with the rest of the network
state:
- `local.port_forwards.wg` forwards **WAN UDP 51820 → 10.0.10.99:51820**
  (UDP, not TCP — the one forward in that map that overrides the default).
- The Homelab VLAN's DHCP pool stops at `.98`, so `.99` can never be leased.

Change either one in the controller UI and the next `unifi-drift-plan` flags it;
change it in `terraform/unifi/networks.tf` and apply.

### 6. Enable metrics (operator, one-time)
Log into `https://vpn.esweiss.com` (Authentik → wg-easy admin login) →
**Admin Panel → General** → enable **Prometheus** and set the **Bearer
password** to the exact `metrics-token` value from 1Password. The ServiceMonitor
(`observability`) then scrapes `wireguard_*` metrics. No alert keys off it —
`WgEasyDown` watches Deployment availability instead.

A rebuild has to redo this step. With Prometheus disabled in the Admin Panel,
`/metrics/prometheus` serves the Nuxt SPA HTML with a **200** rather than a 401,
so the scrape fails on content type and the target sits permanently down while
the app looks healthy.

### 7. Authentik objects (Terraform)

wg-easy's native OIDC (generic `OAUTH_PROVIDERS`) is not used: the UI is
protected by Traefik ForwardAuth via the shared `authentik-auth` outpost.
Re-evaluate the design if a pin bump is ever taken specifically to adopt native
OIDC; check the running tag against `wg_easy_version` in `group_vars/all.yml`
first.

The proxy provider, application and `vpn-admins` group are declared in
`terraform/authentik/` (`providers_proxy.tf`, `applications.tf`, `groups.tf`,
`policy_bindings.tf`) and applied under supervision
([docs/40](40-authentik-terraform.md)) — not in the admin UI. Values:

| Setting | Value |
|---|---|
| Provider type | Proxy — **forward auth (single application)** |
| Provider / application name | `wg-easy` |
| Application slug | `wg-easy` |
| Authorization flow | `default-provider-authorization-implicit-consent` |
| External host | `https://vpn.esweiss.com` |
| Access gate | `vpn-admins` group binding |

The one step that is **not** in Terraform: the application must be attached to the
embedded outpost (`authentik Embedded Outpost`), which is what the
`authentik-auth` middleware points at.


Forward-auth is the front door, not the only login: wg-easy 15.x seeds a
mandatory account from `INIT_USERNAME`/`INIT_PASSWORD` and ships no switch to
disable it, so its own form follows Authentik on every request. That is the
deliberate two-factor class Uptime Kuma is in ([docs/45](45-uptime-kuma.md)
§ Authentik objects), NOT the single-login *arr pattern
([docs/21](21-download-clients-deployment.md) § Authentik SSO Integration). Outpost
and middleware detail: `kubernetes/apps/authentik/README.md` and
[docs/23](23-recipes-sso-setup.md).

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
curl -m 5 http://10.0.10.1/            ; echo "exit=$?"   # router  -> fail
curl -m 5 https://10.0.10.101/         ; echo "exit=$?"   # Traefik -> fail
ping -c1 -W2 10.0.10.150               ; echo "exit=$?"   # AdGuard -> fail

# Internal DNS must NOT be used (name resolution goes to 1.1.1.1):
nslookup git.esweiss.com                  # should NOT resolve to 10.0.10.101

# Cluster DNS must be unreachable even if a client deliberately targets the
# CoreDNS ClusterIP directly (this is why the pod uses public DNS and the egress
# policy has no kube-dns allow — Layer 2):
nslookup git.esweiss.com 10.43.0.10       ; echo "exit=$?"   # MUST fail / time out
```

Server-side:
```bash
task wg-easy:peers        # shows the handshake for the connected client
task wg-easy:status
```

---

## Operations & runbook

| Task | Command |
|------|---------|
| Status (pod/svc/VIP/PVC/ingress) | `task wg-easy:status` |
| Live peers / handshakes | `task wg-easy:peers` |
| Logs | `task wg-easy:logs` |
| Restart the pod | `task wg-easy:restart` |

- **Config changes** (client subnet, endpoint, hooks, per-client firewall) are
  made in the **UI**. The `INIT_*` env vars apply on first boot only — editing
  them later is a no-op.
- **Admin password rotation**: change it in the UI (Admin Panel), then update
  `init-password` in 1Password for documentation parity (it is not re-read).
- **Optional server-side per-client firewall**: wg-easy ships an experimental
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
itself was lost, restore it from the archive replica first — the file-wise recipe
in [docs/17 § Other backup types](17-disaster-recovery.md#other-backup-types):

```bash
# On pve-nas-01: restore the appdata dataset, unlock it, then copy the SQLite DB
# back into place (peers + server key both live in it).
sudo archive-backupctl restore appdata
sudo zfs load-key -r -L prompt ssd/appdata-restore-<ts>
sudo zfs mount -r ssd/appdata-restore-<ts>
kubectl -n wg-easy scale deploy/wg-easy --replicas=0
sudo cp -a /mnt/restore/appdata/<ts>/wg-easy/. /mnt/ssd/appdata/wg-easy/
kubectl -n wg-easy scale deploy/wg-easy --replicas=1
```
If the DB is unrecoverable, wg-easy re-bootstraps a **new** server key on next
start (INIT applies to an empty `/etc/wireguard`); every client must then be
re-issued a config.

## Gotchas

- **`.99` sits just above the DHCP pool, by design.** The Homelab VLAN's scope
  is `10.0.10.2`–`10.0.10.98` in `terraform/unifi/networks.tf`
  (`local.networks.homelab.dhcp`), so `.99` can never be leased — codified, not
  an operator step, and `unifi-drift-plan` flags a UI edit that widens it.
  Widening the pool past `.98` would let a client lease collide with the VIP,
  which is invisible except as `EndpointDown`.
- **flannel owns node `:51820/udp`**. The WAN firewall rule is `-dest`-scoped to
  the `.99` VIP so it never exposes flannel's inter-node WireGuard. Do not
  broaden it to a bare `-dport 51820`.
- **SQLite on NFS** is safe here only because there is exactly one writer
  (`replicas: 1`, `Recreate`). Do not scale wg-easy.
- **Metrics need the one-time UI enable** (step 6) — the ServiceMonitor alone
  does not turn them on.

## Related documentation

- [docs/08-dns.md](08-dns.md) - split-horizon DNS for the VPN endpoint name
- [docs/29-flux-operations.md](29-flux-operations.md) - how the manifests reconcile
- [docs/31-observability.md](31-observability.md) - the metrics endpoint and its alerts
- [docs/40-authentik-terraform.md](40-authentik-terraform.md) - the forward-auth provider as code
