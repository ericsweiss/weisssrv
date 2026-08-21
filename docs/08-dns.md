# DNS Stack

The homelab uses a two-tier DNS architecture with AdGuard Home for ad blocking and local resolution, backed by Unbound for secure upstream resolution.

## Architecture

```
LAN Clients
              |
              v
        AdGuard Home (listens on 192.168.0.150 / 192.168.0.160)
        - Port 53 (DNS)
        - Port 853 (DoT)
        - Port 443 (HTTPS/DoH)
        - Port 3000 (plaintext admin API — firewall-restricted to admin LAN /
          Tailscale; used by the adguard-exporter and the role's localhost
          reconcile of split-horizon rewrites). The human-facing HTTPS admin UI
          and DoH are AdGuard's own :443, which the Traefik IngressRoute proxies.
              |
              v
        Unbound (127.0.0.1:5335)
        - DNSSEC validation
        - DoT to upstream
              |
              v
     Cloudflare/Google DoT
```

## DNS Servers

| Host | IP | Role |
|------|-----|------|
| dns-01 | 192.168.0.150 | Primary, runs acme.sh and sync |
| dns-02 | 192.168.0.160 | Replica, synced from dns-01 |

### Who gets handed these resolvers

Both addresses are handed out as the DHCP DNS servers on every **client** UniFi
VLAN — Homelab, Home, IoT, Guest and Work — together with the `esweiss.com`
search domain, so split-horizon resolution behaves identically on all of them
([docs/46-unifi-network.md](46-unifi-network.md) § Networks). Three consequences
worth holding on to:

- The resolvers are the one weisssrv service every client VLAN may reach. The
  zone firewall allows `:53` from IoT/Guest/Work to the homelab zone and
  nothing else, and the Proxmox firewall mirrors that with the `dns_clients`
  ipset ([docs/11-firewall.md](11-firewall.md)). The admin surfaces (`:853`
  DoT, `:3000` API, `:443` UI) stay on the admin sets — a guest device resolves
  names and can do nothing else here. Those VLANs are also **blocked from
  reaching any external resolver** on `:53`/`:853`, so a device with a
  hardcoded `8.8.8.8` gets the weisssrv resolvers or nothing (docs/46
  § Zones and policies).
- The **management VLAN does not use them either**, and neither does the
  gateway. VLAN 1's DHCP hands out plain `1.1.1.1` / `9.9.9.9`, as does the
  gateway's WAN DNS, deliberately: the switch, the AP and the gateway all sit
  *above* the resolvers, which are LXCs behind them, so pointing them at
  `.150`/`.160` is a bootstrap loop. Nothing on VLAN 1 needs split-horizon
  answers — and because of that there is no `Internal → homelab :53` zone
  policy at all, and `10.0.1.0/24` is deliberately **not** a member of the
  `dns_clients` ipset. Re-pointing a management device at AdGuard would take
  both edits: the zone policy and the ipset entry.
- Guests resolving through AdGuard means visitors inherit the household
  ad-blocking — the reason the guest `:53` allowance exists — and can
  *enumerate* internal names from the rewrites. They cannot reach any of them;
  the disclosure is accepted (docs/46).

## Unbound Configuration

Unbound runs on localhost:5335 and is only accessible to AdGuard Home.

**Key settings** (`/etc/unbound/unbound.conf.d/weisssrv.conf`):

```yaml
server:
  interface: 127.0.0.1
  port: 5335
  hide-identity: yes
  hide-version: yes
  qname-minimisation: yes
  prefetch: yes

forward-zone:
  name: "."
  forward-tls-upstream: yes
  forward-addr: 1.1.1.1@853#cloudflare-dns.com
  forward-addr: 1.0.0.1@853#cloudflare-dns.com
  forward-addr: 9.9.9.9@853#dns.quad9.net
  forward-addr: 149.112.112.112@853#dns.quad9.net
  forward-addr: 8.8.8.8@853#dns.google
  forward-addr: 8.8.4.4@853#dns.google
```

### Ansible Role

Deploy with: the `weisssrv.infra.unbound` role (`ansible/playbooks/dns.yml`)

## AdGuard Home

AdGuard Home provides:
- DNS-level ad blocking
- DNS rewrites for internal services
- Query logging and statistics
- DoT/DoH for encrypted DNS

### Systemd Unit

Runs as unprivileged user with `CAP_NET_BIND_SERVICE`:

```ini
[Service]
User=adguard
Group=adguard
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/AdGuardHome
```

### DNS Rewrites

Internal services use AdGuard rewrites instead of split-horizon DNS. Rewrites
are **not** configured in the UI — they are codified as `adguard_home_rewrites` in
`ansible/inventories/prod/group_vars/dns.yml` (the source of truth) and applied
via the AdGuard API by the `adguard_home` role on `task dns:deploy`. The role
also **deletes any rewrite not in the codified list**, so UI-added entries
(Filters → DNS rewrites) are reverted on the next deploy. To add a permanent
rewrite, edit dns.yml and redeploy.

One boundary: an **empty** `adguard_home_rewrites` (or `adguard_home_user_rules`)
means "manage none", not "delete everything" — the role leaves live state alone.
Removing the *last* codified entry therefore does not prune it; set
`adguard_home_prune_rewrites: true` / `adguard_home_prune_user_rules: true` to
make the empty list authoritative. Both lists are non-empty today, so pruning is
already in effect for everything they name.

The codified list covers (see dns.yml for the authoritative entries):

- All six Proxmox hosts (`pve-*.esweiss.com` → .102–.107)
- Infrastructure VMs/LXCs: `smtp-relay` (.151), `plex-direct` (.152),
  `gitlab` (.153), `home-direct` (.154), `windows` (.155), `nextcloud` (.156),
  `immich` (.157). The immich-ml LXC (.158) has no rewrite on purpose — it is
  reached only by the Immich VM, by IP.
- All nine k3s nodes plus `k3s.esweiss.com` → .161 (API VIP)
- `dns.esweiss.com` → .150/.160 (direct DoT access);
  `dns-01`/`dns-02.esweiss.com` → **192.168.0.101** (Traefik internal VIP,
  for the HTTPS-fronted admin UIs — not the hosts' own IPs);
  `adguard`/`adguard-02.esweiss.com` → **192.168.0.101** (the SSO-fronted
  dashboard hostnames — see "Admin dashboards" below)
- `vip-public.ericsweiss.com` → .100; `vip-internal.esweiss.com` → .101
- ~25 app/service hostnames (`auth`, `git`, `grafana`, `loki`, `connect`,
  `traefik`, `plex`, `home`, `food`, `bar`, the *arr stack, etc.) → .101

### Admin dashboards — SSO hostnames + break-glass routes

Each AdGuard instance has **two** Traefik-fronted hostnames
(`kubernetes/apps/vm-ingress/adguard-home.yaml`; all four resolve to the
internal Traefik VIP .101 and reuse the same 443/https backend Services):

| Hostname | Auth | Purpose |
|---|---|---|
| `adguard.esweiss.com` | Authentik forward-auth (`dns-admins` group) + credential injection | Daily driver: SSO members land straight on the dns-01 dashboard, no AdGuard login |
| `adguard-02.esweiss.com` | same | dns-02 dashboard |
| `dns-01.esweiss.com` / `dns-02.esweiss.com` | AdGuard's own login | **Break-glass**: no SSO dependency |
| direct IPs `.150` / `.160` | AdGuard's own login | Last-resort break-glass (no Traefik, no cluster) |

The SSO routes carry the `authentik-auth-basic` middleware: the
Terraform-managed proxy providers (`terraform/authentik/providers_proxy.tf`,
docs/40) inject the AdGuard admin credentials — sourced from the same
1Password **AdGuard Home** item the role deploys — as an `Authorization`
header, which AdGuard validates like any basic-auth login. Membership of
`dns-admins` therefore both authorizes the route and supplies the credential.

The raw `dns-01`/`dns-02` routes and the direct IPs are deliberately
untouched: DNS is the one service whose administration must never depend on
the SSO stack (Authentik down, cluster down, or Traefik down must not lock
you out of your resolvers). `adguardhome-sync` also keeps targeting the raw
hostnames, so the sync hop never traverses the SSO middleware.

### External zone ownership (ericsweiss.com)

The public `ericsweiss.com` Cloudflare zone has **three** record owners — they
coexist safely because their record sets are disjoint and external-dns scopes
itself with a TXT registry:

- **Terraform** (`terraform/cloudflare/`) — static records (apex, `git`,
  `direct`, etc.).
- **external-dns** (`controllers/external-dns`, `policy: sync`) — records derived
  from k3s Ingresses/Services/IngressRoutes, each stamped with
  `txtOwnerId: k3s-external-dns`. Sync only deletes records carrying that owner
  TXT, so it never touches Terraform/DDNS records.
- **cloudflare-ddns CronJob** (`configs/cloudflare-ddns`) — keeps the *content*
  (public IP) of four A records current: the apex, `git`, `direct` and `vpn`.
  `proxied`, `ttl` and the record decorations (`comment`, `tags`, `settings`)
  stay Terraform-owned — the CronJob PUTs a full body but preserves the record's
  live values on update, seeding them only on creation. Anything it failed to
  carry forward would be **erased** by the PUT and read afterwards as Terraform
  drift, so the preserved set is asserted in `scripts/test_cloudflare_ddns.py`.
  If a name resolves to more than one A record the job refuses to write it at
  all: ownership is ambiguous, and updating one of them would leave the sibling
  answering stale intermittently.

Keep hostnames disjoint across the three owners; a collision would let two
tools fight over one record.

#### Reverse DNS (PTR Records)

PTR records are implemented as `$dnsrewrite` filter rules in
`adguard_home_user_rules` (same file, deployed the same way), e.g.:

```
'||150.0.168.192.in-addr.arpa^$dnsrewrite=NOERROR;PTR;dns-01.{{ internal_domain }}.'
```

dns.yml carries PTR rules for the infrastructure hosts (.102–.107,
.150/.151/.160, .152–.157), all k3s nodes, and the VIPs. immich-ml (.158) is
deliberately absent, like its forward rewrite.

**Note**: AdGuard Home syncs DNS rewrites from dns-01 to dns-02 automatically via adguardhome-sync.

### TLS Configuration

TLS certificates are managed by acme.sh on dns-01 and distributed to dns-02.

Certificate paths:
- `/opt/AdGuardHome/certs/fullchain.pem`
- `/opt/AdGuardHome/certs/privkey.pem`

### Ansible Role

Deploy with: the `weisssrv.infra.adguard_home` role (`ansible/playbooks/dns.yml`)

## AdGuard Home Sync

Settings are synced from dns-01 to dns-02 every 5 minutes using
`adguardhome-sync`. The sync URLs target the Traefik-fronted
`dns-{01,02}.esweiss.com` hostnames, so the sync hop is HTTPS via
the LAN-only IngressRoutes (the wildcard cert is trusted via the
system CA bundle).

**Configuration** (`/etc/adguardhome-sync.yaml`). The URLs target the
Traefik-fronted hostnames so the dns-01 -> dns-02 sync hop is end-to-end
TLS with the wildcard cert (LAN-only via the `lan-tailscale-only`
middleware on the IngressRoute):

```yaml
origin:
  url: "https://dns-01.esweiss.com"
  username: "eric"
  password: "<from 1Password>"

replica:
  url: "https://dns-02.esweiss.com"
  username: "eric"
  password: "<from 1Password>"

features:
  dns:
    accessLists: true
    serverConfig: true
    rewrites: true
  filters: true
  tlsConfig: false  # Not synced: cert files/paths are host-local and the adguard_home role reconciles TLS per host — syncing would fight the Ansible-managed config on dns-02
```

The block above is abbreviated — the full synced feature set
(dhcp, generalSettings, queryLogConfig, statsConfig, clientSettings,
services, theme) is defined in `adguard_sync_features` in
`group_vars/dns.yml`.

### Ansible Role

Deploy with: the `weisssrv.infra.adguard_sync` role (`ansible/playbooks/dns.yml`)

## Search Domains and Pod DNS

The `resolv_conf` role writes a `search` directive into `/etc/resolv.conf`
controlled by `resolv_conf_search_domains` (defaults to `[internal_domain]`).
Hosts use it for short-name lookups: `ssh pve-nas-01` resolves to
`pve-nas-01.esweiss.com` because `esweiss.com` is in the search list.

K3s VMs override this to `[]` in `group_vars/k3s.yml`. Reason: kubelet copies
the host's `/etc/resolv.conf` search domains into every pod's resolver, and
combines them with `<ns>.svc.cluster.local svc.cluster.local cluster.local`
plus `ndots:5`. Any name shorter than 5 dots (which is most names — even
`radarr.downloads.svc.cluster.local` is only 4 dots) gets each search suffix
appended and probed before the literal name. With `esweiss.com` in the list,
that meant every cluster-internal lookup leaked one extra NXDOMAIN query to
AdGuard, and even fully-qualified internal names like `git.esweiss.com` got
re-suffixed and probed as `git.esweiss.com.esweiss.com`.

Dropping the suffix on k3s VMs eliminates the amplification while still
letting pods resolve cluster services via the in-cluster CoreDNS suffixes.

To re-enable for a specific host or group, set `resolv_conf_search_domains`
in the appropriate `group_vars` or `host_vars` file. Verify with:

```bash
ansible <host> -m shell -a 'cat /etc/resolv.conf'
```

## Known Issues

1. **AdGuardHome.sig permissions**: On dns-02, the signature file may have world-writable permissions. The Ansible role includes a fix task.

2. **Subnetcache warning**: Unbound logs a warning about prefetch not working with subnetcache. This is cosmetic and does not affect functionality.

## Troubleshooting

### Check Unbound

```bash
# Status
systemctl status unbound

# Validate config
unbound-checkconf

# Test resolution
dig @127.0.0.1 -p 5335 google.com
```

### Check AdGuard Home

```bash
# Status
systemctl status AdGuardHome

# Logs
journalctl -u AdGuardHome -f

# Test resolution
dig @192.168.0.150 dns-01.esweiss.com
```

### Force Sync

```bash
# On dns-01
systemctl start adguardhome-sync.service
journalctl -u adguardhome-sync -f
```

---

## Related documentation

- [docs/01-overview.md](01-overview.md) — split-horizon DNS and topology
- [docs/09-certs.md](09-certs.md) — DNS-01 certificate issuance
- [docs/11-firewall.md](11-firewall.md) — `sg-dns`, the admin sources and the `dns_clients` scope
- [docs/46-unifi-network.md](46-unifi-network.md) — the per-VLAN DHCP settings that hand out these resolvers
- [docs/12-runbooks.md](12-runbooks.md) — updating DNS records
