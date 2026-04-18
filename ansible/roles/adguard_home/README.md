# AdGuard Home Role

Manages AdGuard Home DNS filtering and ad-blocking server.

## What This Role Manages

### Via API (Idempotent, Safe)

**Base Configuration** (`api_base_config.yml` - dns-01 only):
- DNS filtering/ad-blocking protection
- Upstream DNS servers → Unbound (127.0.0.1:5335)
- Upstream mode (load balancing, parallel, or fastest)
- Fallback DNS servers (empty - all queries via Unbound)
- DNSSEC validation
- Client reverse DNS resolution (rDNS)
- IPv6 DNS support
- Rate limiting (20 req/s per client)
- Rate limit whitelist
- TLS/DoT/DoQ configuration (HTTPS, DoT, DoQ ports)
- DNS cache enabled (8MB optimized for 2GB RAM)
- Cache TTL settings
- Cache optimistic mode
- DHCP server disabled (router handles DHCP)

**DNS Records** (`api_config.yml` - dns-01 only):
- DNS rewrites (forward A records)
- Custom filtering rules (reverse PTR records)
- Reconciliation: adds missing, deletes orphaned

**Sync**: dns-02 automatically syncs from dns-01 via `adguardhome-sync` (every 5 minutes)

### Via File Edit (Legacy, Risky)

- Admin password hash (lineinfile on AdGuardHome.yaml) -- legacy, needs migration
  - **TODO**: Migrate to API-based password management

### Not Managed (AdGuard Defaults)

- HTTP/DNS port configuration (requires service restart)
- Filter lists (managed via UI)

## Configuration

All settings defined in `ansible/inventories/prod/group_vars/dns.yml`:

```yaml
# Base settings
adguard_http_port: 3000
adguard_dns_port: 53
adguard_dot_port: 853
adguard_https_port: 443
adguard_doq_port: 853

# Upstream DNS (points to Unbound)
adguard_upstream_dns:
  - "127.0.0.1:5335"

# DNS features
adguard_protection_enabled: true
adguard_upstream_mode: "load_balance"  # load_balance, parallel, or fastest_addr
adguard_fallback_dns: []  # Empty - all queries via Unbound
adguard_enable_dnssec: true
adguard_resolve_clients: true
adguard_use_private_ptr_resolvers: false  # Disabled - we use static PTR records
adguard_disable_ipv6: false

# TLS/Encryption configuration
adguard_tls_enabled: true
adguard_tls_server_name: "dns.{{ internal_domain }}"
adguard_cert_path: "{{ adguard_install_path }}/certs"

# Cache configuration (optimized for 2GB RAM)
adguard_cache_enabled: true
adguard_cache_size: 8388608  # 8MB cache
adguard_cache_ttl_min: 0
adguard_cache_ttl_max: 0
adguard_cache_optimistic: false

# Rate limiting
adguard_ratelimit: 20
adguard_ratelimit_whitelist: []

# DHCP (disabled - router handles DHCP)
adguard_dhcp_enabled: false

# DNS rewrites (managed via API)
adguard_rewrites:
  - domain: "example.{{ internal_domain }}"
    answer: "192.168.0.x"

# Custom filtering rules (managed via API)
adguard_user_rules:
  - '||192.0.168.192.in-addr.arpa^$dnsrewrite=NOERROR;PTR;example.{{ internal_domain }}.'
```

## Deployment

```bash
# Deploy to dns-01 only (dns-02 syncs automatically)
task dns:deploy -- --limit dns-01

# Full DNS stack deployment
task dns:deploy
```

## Architecture

```
dns-01 (primary)
  ├─ Ansible manages via API
  ├─ Base settings applied
  ├─ DNS rewrites added/deleted
  └─ Filtering rules replaced

     ↓ (adguardhome-sync every 5 min)

dns-02 (replica)
  └─ Automatically synced from dns-01
```

## API Endpoints Used

- `GET /control/dns_info` - Get DNS configuration
- `POST /control/dns_config` - Update DNS configuration
- `GET /control/tls/status` - Get TLS status
- `POST /control/tls/configure` - Configure TLS
- `GET /control/rewrite/list` - List DNS rewrites
- `POST /control/rewrite/add` - Add DNS rewrite
- `POST /control/rewrite/delete` - Delete DNS rewrite
- `GET /control/filtering/status` - Get filtering rules
- `POST /control/filtering/set_rules` - Replace filtering rules

## Files

- `tasks/main.yml` - Main task orchestration
- `tasks/api_base_config.yml` - Base settings management via API
- `tasks/api_config.yml` - DNS records management via API
- `templates/adguardhome.service.j2` - Systemd service
- `handlers/main.yml` - Service restart handler

## Dependencies

- `unbound` role (provides upstream DNS on 127.0.0.1:5335)
- `acme_certs` role (provides TLS certificates)
- `adguard_sync` role (syncs dns-01 → dns-02)

## Security

- All API calls use HTTP Basic Auth with admin credentials from 1Password
- API calls use `no_log: true` to prevent credential exposure
- Runs as unprivileged `adguard` user with `CAP_NET_BIND_SERVICE`
- Config file owned by `adguard:adguard` with mode `0600`
