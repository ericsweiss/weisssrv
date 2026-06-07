# AdGuard Home Sync Role

Configures one-way sync of AdGuard Home settings from dns-01 (primary) to dns-02 (replica) via a systemd timer running every 5 minutes.

## What This Role Manages

### Synchronization
- Binary installation (adguardhome-sync from GitHub releases)
- Configuration file with origin and replica endpoints
- Systemd service unit
- Systemd timer (every 5 minutes)
- Automatic sync of:
  - General settings
  - Filters and blocklists
  - DNS rewrites
  - Custom filtering rules
  - Clients
  - DHCP configuration

### Service Management
- Systemd timer enabled and started
- Home directory creation (/var/lib/adguardhome-sync)
- Configuration secured (mode 0600)
- Version pinning from group_vars

## Configuration

### Required Variables

```yaml
# Version (from group_vars/all.yml)
adguardhome_sync_version: "0.8.2"

# AdGuard Home instances. The URLs target the Traefik-fronted
# `dns-{01,02}.esweiss.com` hostnames (TLS-terminated by Traefik,
# wildcard cert) rather than the LXCs' :3000 ports directly. The
# admin credential is the same on both instances.
adguardhome_sync_origin: "https://dns-01.{{ internal_domain }}"
adguardhome_sync_replica: "https://dns-02.{{ internal_domain }}"

# Sync settings
adguard_sync_features:
  general: true
  filters: true
  rewrites: true
  services: true
  clients: true
  dhcp: false  # DHCP disabled in homelab (router handles it)
```

### 1Password Secrets

```yaml
secrets:
  adguard_admin_password: "op://Homelab/AdGuard Home/password"
```

## Deployment

```bash
# Deploy DNS stack (includes sync configuration)
task dns:deploy

# Deploy to dns-01 only
ansible-playbook ansible/playbooks/dns.yml --limit dns-01
```

## Architecture

```
dns-01 (primary)
  ├─ AdGuard Home (https://dns-01.esweiss.com  via Traefik)
  ├─ adguardhome-sync service
  └─ Timer runs every 5 minutes
       │
       └─> Syncs to → dns-02 (replica)
                      └─ AdGuard Home (https://dns-02.esweiss.com via Traefik)
```

### Dependency on the k3s cluster

Both URLs go through Traefik on the internal MetalLB VIP
(`192.168.0.101`), so the sync timer has a runtime dependency on the
k3s cluster being up — even though both AdGuard instances themselves
are healthy LXCs that don't depend on k3s. Practical impact during a
sustained k3s outage:

- DNS resolution itself stays up (both LXCs serve port 53 directly).
- Configuration drift stops being corrected. Bounded by how long the
  outage lasts: at the 5-minute timer interval it's typically minutes
  of drift, not hours, but the timer can sit idle for the duration of
  the outage if Traefik doesn't recover.
- Once k3s is healthy again, the next timer tick picks up where it
  left off — no manual intervention needed.

If you anticipate a long k3s outage during a config-heavy maintenance
window, mirror the change on dns-02 by hand (paste the same DNS
rewrite / blocklist edit on both UIs) so drift never opens up.

**Sync Direction**: Always dns-01 → dns-02 (one-way)

**What Gets Synced**:
- General settings (upstream DNS, cache, rate limits)
- Filter lists and rules
- DNS rewrites
- Custom filtering rules
- Client configurations
- Service settings

**What Doesn't Get Synced**:
- Admin password (managed by Ansible)
- TLS certificates (managed by acme_certs role)
- DHCP settings (disabled)

## Task Flow

```
1. Check if adguardhome-sync binary exists
2. Download binary from GitHub releases (if not present)
3. Extract tarball
4. Install binary to /usr/local/bin/adguardhome-sync
5. Cleanup download files
6. Create home directory (/var/lib/adguardhome-sync)
7. Deploy configuration file (/etc/adguardhome-sync.yaml)
8. Deploy systemd service unit
9. Deploy systemd timer unit (OnBootSec=1min, OnUnitActiveSec=5min)
10. Enable and start timer
```

## Files

- `tasks/main.yml` - Main task orchestration
- `templates/adguardhome-sync.yaml.j2` - Sync configuration
- `templates/adguardhome-sync.service.j2` - Systemd service
- `templates/adguardhome-sync.timer.j2` - Systemd timer
- `handlers/main.yml` - Systemd reload handler

## Dependencies

- `adguard_home` role (must be deployed first on both dns-01 and dns-02)
- Both DNS servers must be accessible

## Security

- Configuration file mode 0600 (admin password included)
- Credentials from 1Password (never in git)
- Operations use `no_log: true` for secrets
- Only runs on dns-01 (never on dns-02)

## Operational Notes

### Checking Sync Status

```bash
# Check timer status
systemctl status adguardhome-sync.timer

# View recent sync runs
journalctl -u adguardhome-sync.service -n 20

# Manually trigger sync
systemctl start adguardhome-sync.service
```

### Sync Frequency

```yaml
# Timer configuration
OnBootSec=1min        # First sync 1 minute after boot
OnUnitActiveSec=5min  # Subsequent syncs every 5 minutes
```

### Manual Sync

```bash
# Trigger immediate sync
sudo systemctl start adguardhome-sync.service

# Watch logs
sudo journalctl -u adguardhome-sync.service -f
```

### Version Updates

Update version in `group_vars/all.yml`:

```yaml
adguardhome_sync_version: "0.8.2"
```

Then run:

```bash
task maintenance:update-applications
```

## Troubleshooting

**Sync not running:**
```bash
# Check timer
systemctl status adguardhome-sync.timer

# Enable if disabled
systemctl enable --now adguardhome-sync.timer
```

**Sync failures:**
```bash
# View error logs
journalctl -u adguardhome-sync.service | grep -i error

# Test connectivity to replica (Traefik-fronted)
curl -I https://dns-02.esweiss.com

# Verify credentials
# (Check 1Password for correct admin password)
```

**Configuration drift:**
```bash
# Compare configurations via the Traefik-fronted endpoints (HTTPS, wildcard cert).
# On dns-01:
curl -u admin:password https://dns-01.esweiss.com/control/dns_info

# On dns-02:
curl -u admin:password https://dns-02.esweiss.com/control/dns_info

# Backend diagnostic only — bypasses Traefik and talks to the AdGuard
# Home HTTP listener directly on the LXC (handy when Traefik itself is suspect):
#   curl -u admin:password http://192.168.0.150:3000/control/dns_info
#   curl -u admin:password http://192.168.0.160:3000/control/dns_info
```

**Reset sync:**
```bash
# Stop timer
systemctl stop adguardhome-sync.timer

# Clear any lock files
rm -rf /var/lib/adguardhome-sync/*

# Trigger manual sync
systemctl start adguardhome-sync.service

# Restart timer
systemctl start adguardhome-sync.timer
```

## Important Notes

1. **One-way sync**: Changes on dns-02 will be overwritten
2. **Make changes on dns-01 only**: Use dns-01 for all configuration
3. **Sync delay**: Up to 5 minutes for changes to propagate
4. **Password management**: Admin password managed by Ansible, not synced
5. **Certificate management**: TLS certs managed by acme_certs role, not synced
