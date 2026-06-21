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
        - Port 3000 (Web UI)
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

Deploy with: `ansible/roles/unbound`

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
are **not** configured in the UI — they are codified as `adguard_rewrites` in
`ansible/inventories/prod/group_vars/dns.yml` (the source of truth) and applied
via the AdGuard API by the `adguard_home` role on `task dns:deploy`. The role
also **deletes any rewrite not in the codified list**, so UI-added entries
(Filters → DNS rewrites) are reverted on the next deploy. To add a permanent
rewrite, edit dns.yml and redeploy.

The codified list covers (see dns.yml for the authoritative entries):

- All six Proxmox hosts (`pve-*.esweiss.com` → .102–.107)
- Infrastructure VMs/LXCs: `smtp-relay` (.151), `plex-direct` (.152),
  `gitlab` (.153), `home-direct` (.154), `windows` (.155)
- All nine k3s nodes plus `k3s.esweiss.com` → .161 (API VIP)
- `dns.esweiss.com` → .150/.160 (direct DoT access);
  `dns-01`/`dns-02.esweiss.com` → **192.168.0.101** (Traefik internal VIP,
  for the HTTPS-fronted admin UIs — not the hosts' own IPs)
- `vip-public.ericsweiss.com` → .100; `vip-internal.esweiss.com` → .101
- ~25 app/service hostnames (`auth`, `git`, `grafana`, `loki`, `connect`,
  `traefik`, `plex`, `home`, `food`, `bar`, the *arr stack, etc.) → .101

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
- **cloudflare-ddns CronJob** (`configs/cloudflare-ddns`) — keeps the apex public
  IP current and co-owns the `proxied` flag on those records.

Keep hostnames disjoint across the three owners; a collision would let two
tools fight over one record.

#### Reverse DNS (PTR Records)

PTR records are implemented as `$dnsrewrite` filter rules in
`adguard_user_rules` (same file, deployed the same way), e.g.:

```
'||150.0.168.192.in-addr.arpa^$dnsrewrite=NOERROR;PTR;dns-01.{{ internal_domain }}.'
```

dns.yml carries PTR rules for the infrastructure hosts (.102–.107,
.150/.151/.160, .152–.155), all k3s nodes, and the VIPs.

**Note**: AdGuard Home syncs DNS rewrites from dns-01 to dns-02 automatically via adguardhome-sync.

### TLS Configuration

TLS certificates are managed by acme.sh on dns-01 and distributed to dns-02.

Certificate paths:
- `/opt/AdGuardHome/certs/fullchain.pem`
- `/opt/AdGuardHome/certs/privkey.pem`

### Ansible Role

Deploy with: `ansible/roles/adguard_home`

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
  tlsConfig: false  # Disabled - each host has its own TLS server name (dns-01 vs dns-02)
```

The block above is abbreviated — the full synced feature set
(dhcp, generalSettings, queryLogConfig, statsConfig, clientSettings,
services, theme) is defined in `adguardhome_sync_features` in
`group_vars/dns.yml`.

### Ansible Role

Deploy with: `ansible/roles/adguard_sync`

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
