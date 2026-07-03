# Unbound Role

Installs and configures Unbound as a recursive DNS resolver with DNS-over-TLS (DoT). Listens on localhost:5335 as the upstream resolver for AdGuard Home.

## What This Role Manages

### DNS Resolution
- Recursive DNS resolver configuration
- DNS-over-TLS (DoT) to Cloudflare (1.1.1.1, 1.0.0.1)
- DNS-over-TLS (DoT) to Quad9 (9.9.9.9, 149.112.112.112)
- DNS-over-TLS (DoT) to Google (8.8.8.8, 8.8.4.4)
- DNS root hints from dns-root-data package
- DNSSEC validation
- Localhost-only listening (127.0.0.1:5335)

### Security & Performance
- Cache configuration (optimized for 2GB RAM)
- Access control (localhost only)
- Private address filtering
- Remote control socket
- Prefetch and cache optimizations

## Configuration

### Default Variables

```yaml
# Listen interface and port
unbound_interface: "127.0.0.1"
unbound_port: 5335  # Non-standard port (AdGuard uses 53)

# DoT upstreams (forward-tls-upstream is always enabled in the template)
unbound_forwarders:
  # Cloudflare
  - addr: "1.1.1.1"
    port: 853
    name: "cloudflare-dns.com"
  - addr: "1.0.0.1"
    port: 853
    name: "cloudflare-dns.com"
  # Quad9
  - addr: "9.9.9.9"
    port: 853
    name: "dns.quad9.net"
  - addr: "149.112.112.112"
    port: 853
    name: "dns.quad9.net"
  # Google
  - addr: "8.8.8.8"
    port: 853
    name: "dns.google"
  - addr: "8.8.4.4"
    port: 853
    name: "dns.google"

# Cache sizes
unbound_msg_cache_size: "16m"     # Message cache
unbound_rrset_cache_size: "32m"   # RRset cache
```

`cache-min-ttl` (60s) and `cache-max-ttl` (86400s) are hardcoded in the
template, not variables. There is no DNSSEC key-cache variable.

## Deployment

```bash
# Deploy DNS stack (includes Unbound)
task dns:deploy

# Deploy to specific DNS server
ansible-playbook ansible/playbooks/dns.yml --limit dns-01
```

## Architecture

```
AdGuard Home (port 53)
      │
      ├─ Queries → Unbound (127.0.0.1:5335)
      │               │
      │               └─> DoT to Cloudflare (1.1.1.1@853)
      │               └─> DoT to Quad9 (9.9.9.9@853)
      │               └─> DoT to Google (8.8.8.8@853)
      │
      └─ Filtering/blocking applied before reaching Unbound
```

**Why port 5335?**
- AdGuard Home binds to port 53 (standard DNS)
- Unbound runs on port 5335 to avoid conflicts
- AdGuard forwards to Unbound as upstream

## Task Flow

```
1. Install unbound and dns-root-data packages
2. Deploy main configuration (weisssrv.conf)
   ├─ Server settings (interface, port, cache)
   ├─ Forward zone (DoT upstreams)
   └─ Access control (localhost only)
3. Deploy remote-control configuration
4. Restart Unbound service
5. Verify Unbound is listening on 127.0.0.1:5335
```

## Files

- `tasks/main.yml` - Main task orchestration
- `templates/weisssrv.conf.j2` - Main Unbound configuration
- `defaults/main.yml` - Default variables
- `handlers/main.yml` - Service restart handler

## Dependencies

- `dns-root-data` package (DNS root hints)
- Must run before `adguard_home` role

## Security

- Listens only on localhost (127.0.0.1)
- DNS-over-TLS encrypts queries to upstreams
- DNSSEC validation enabled
- Private addresses filtered
- Access control prevents unauthorized queries

## Testing

```bash
# Test Unbound directly
dig @127.0.0.1 -p 5335 example.com

# Test with DoT
dig @127.0.0.1 -p 5335 +dnssec example.com

# Check Unbound stats
unbound-control stats_noreset

# View cache contents
unbound-control dump_cache

# Flush cache
unbound-control flush_zone .
```

## Performance Tuning

Configured for 2GB RAM DNS servers:

- **Message cache**: 16MB (stores query responses)
- **RRset cache**: 32MB (stores resource records)
- **Prefetch**: Enabled (refreshes popular domains before expiry)
- **Cache min TTL**: 60 seconds (hardcoded in the template)
- **Cache max TTL**: 24 hours (hardcoded in the template)

## Operational Notes

### Viewing Logs

```bash
# Unbound logs to syslog
journalctl -u unbound -f
```

### Clearing Cache

```bash
# Full cache flush
unbound-control flush_zone .

# Flush specific domain
unbound-control flush example.com
```

### Statistics

```bash
# View resolver statistics
unbound-control stats
```

### Troubleshooting

**Unbound not starting:**
```bash
# Check configuration
unbound-checkconf /etc/unbound/unbound.conf.d/weisssrv.conf

# Check permissions
ls -la /etc/unbound/unbound.conf.d/
```

**Queries not working:**
```bash
# Verify listening
ss -tlnp | grep 5335

# Test locally
dig @127.0.0.1 -p 5335 google.com
```

**DoT issues:**
```bash
# Check TLS connectivity
openssl s_client -connect 1.1.1.1:853

# View Unbound logs
journalctl -u unbound | grep -i tls
```
