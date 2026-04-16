# DNS Stack

The homelab uses a two-tier DNS architecture with AdGuard Home for ad blocking and local resolution, backed by Unbound for secure upstream resolution.

## Architecture

```
LAN Clients (192.168.0.150, 192.168.0.160)
              |
              v
        AdGuard Home
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

Internal services use AdGuard rewrites instead of split-horizon DNS. Configure these in AdGuard Home (http://192.168.0.150:3000) under **DNS Settings → DNS rewrites**.

#### Forward DNS (A Records)

| Domain | IP | Description |
|--------|-----|-------------|
| dns-01.esweiss.com | 192.168.0.150 | Primary DNS server |
| dns-02.esweiss.com | 192.168.0.160 | Secondary DNS server |
| smtp-relay.esweiss.com | 192.168.0.151 | SMTP relay |
| pve-nas-01.esweiss.com | 192.168.0.102 | Proxmox NAS host |
| pve-opt-03.esweiss.com | 192.168.0.106 | Proxmox compute host |
| pve-prec-01.esweiss.com | 192.168.0.107 | Proxmox compute host |
| k3s-srv-nas-01.esweiss.com | 192.168.0.222 | K3s server node |
| k3s-srv-laptop-01.esweiss.com | 192.168.0.223 | K3s server |
| k3s-srv-prec-01.esweiss.com | 192.168.0.227 | K3s server |
| k3s-agt-nas-01.esweiss.com | 192.168.0.202 | K3s agent (NAS) |
| k3s-agt-laptop-01.esweiss.com | 192.168.0.203 | K3s agent (ingress + general) |
| k3s-agt-opt-01.esweiss.com | 192.168.0.204 | K3s agent (general) |
| k3s-agt-opt-02.esweiss.com | 192.168.0.205 | K3s agent (general) |
| k3s-agt-opt-03.esweiss.com | 192.168.0.206 | K3s agent (ingress + general) |
| k3s-agt-prec-01.esweiss.com | 192.168.0.207 | K3s agent (general + compute) |
| k3s.esweiss.com | 192.168.0.161 | K3s API VIP (kube-vip) |
| vip-public.esweiss.com | 192.168.0.100 | MetalLB public pool |
| vip-internal.esweiss.com | 192.168.0.101 | MetalLB internal pool |

#### Reverse DNS (PTR Records)

Configure these as additional rewrites for proper reverse lookups:

| PTR Record | FQDN |
|------------|------|
| 150.0.168.192.in-addr.arpa | dns-01.esweiss.com |
| 160.0.168.192.in-addr.arpa | dns-02.esweiss.com |
| 151.0.168.192.in-addr.arpa | smtp-relay.esweiss.com |
| 102.0.168.192.in-addr.arpa | pve-nas-01.esweiss.com |
| 106.0.168.192.in-addr.arpa | pve-opt-03.esweiss.com |
| 107.0.168.192.in-addr.arpa | pve-prec-01.esweiss.com |
| 202.0.168.192.in-addr.arpa | k3s-agt-nas-01.esweiss.com |
| 203.0.168.192.in-addr.arpa | k3s-agt-laptop-01.esweiss.com |
| 204.0.168.192.in-addr.arpa | k3s-agt-opt-01.esweiss.com |
| 205.0.168.192.in-addr.arpa | k3s-agt-opt-02.esweiss.com |
| 206.0.168.192.in-addr.arpa | k3s-agt-opt-03.esweiss.com |
| 207.0.168.192.in-addr.arpa | k3s-agt-prec-01.esweiss.com |
| 222.0.168.192.in-addr.arpa | k3s-srv-nas-01.esweiss.com |
| 223.0.168.192.in-addr.arpa | k3s-srv-laptop-01.esweiss.com |
| 227.0.168.192.in-addr.arpa | k3s-srv-prec-01.esweiss.com |
| 161.0.168.192.in-addr.arpa | k3s.esweiss.com |
| 100.0.168.192.in-addr.arpa | vip-public.esweiss.com |
| 101.0.168.192.in-addr.arpa | vip-internal.esweiss.com |

**Note**: AdGuard Home syncs DNS rewrites from dns-01 to dns-02 automatically via adguardhome-sync.

### TLS Configuration

TLS certificates are managed by acme.sh on dns-01 and distributed to dns-02.

Certificate paths:
- `/opt/AdGuardHome/certs/fullchain.pem`
- `/opt/AdGuardHome/certs/privkey.pem`

### Ansible Role

Deploy with: `ansible/roles/adguard_home`

## AdGuard Home Sync

Settings are synced from dns-01 to dns-02 every 5 minutes using `adguardhome-sync`.

**Configuration** (`/etc/adguardhome-sync.yaml`):

```yaml
origin:
  url: "http://192.168.0.150:3000"
  username: "eric"
  password: "<from 1Password>"

replica:
  url: "http://192.168.0.160:3000"
  username: "eric"
  password: "<from 1Password>"

features:
  dns:
    accessLists: true
    serverConfig: true
    rewrites: true
  filters: true
  tlsConfig: true
```

### Ansible Role

Deploy with: `ansible/roles/adguard_sync`

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
