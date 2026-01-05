# TLS Certificate Management

This document covers the TLS certificate pipeline using acme.sh for Let's Encrypt certificates.

## Overview

The homelab uses Let's Encrypt certificates for internal services via DNS-01 challenge with Cloudflare.

**Primary Certificate**:
- **Domain**: `esweiss.com`
- **SAN**: `*.esweiss.com` (wildcard)
- **Issued by**: Let's Encrypt
- **Challenge**: DNS-01 via Cloudflare API
- **Renewal**: Automatic via acme.sh cron

## Architecture

```
dns-01 (Primary)
  ├── acme.sh issues cert via Cloudflare DNS-01
  ├── Installs to /opt/AdGuardHome/certs
  ├── Runs homelab-cert-reload.sh hook
  └── Distributes certs to:
      ├── dns-02 (AdGuard Home)
      └── smtp-relay (Postfix TLS)
```

## Certificate Issuance

### Initial Setup

Managed by the `acme_certs` Ansible role on `dns-01`:

```bash
ansible-playbook ansible/playbooks/dns.yml --tags acme
```

### Manual Issuance (if needed)

On `dns-01`:

```bash
# Set Cloudflare credentials
export CF_Token="your-cloudflare-api-token"
export CF_Account_ID="your-account-id"

# Issue certificate
sudo -u root /root/.acme.sh/acme.sh --issue \
  -d esweiss.com \
  -d '*.esweiss.com' \
  --dns dns_cf \
  --keylength ec-256

# Install certificate
sudo -u root /root/.acme.sh/acme.sh --install-cert \
  -d esweiss.com \
  --cert-file /opt/AdGuardHome/certs/fullchain.pem \
  --key-file /opt/AdGuardHome/certs/privkey.pem \
  --reloadcmd "/usr/local/bin/homelab-cert-reload.sh"
```

**Note**: Credentials are sourced from 1Password in production.

## Certificate Distribution

### Distribution Script

The `homelab-cert-reload.sh` script on `dns-01` handles:

1. **Local AdGuard Home**: Restart service to load new cert
2. **Remote dns-02**: Copy certs via SSH, fix permissions, restart AdGuard
3. **Remote smtp-relay**: Copy certs to `/etc/postfix/tls`, fix permissions, restart Postfix

### Script Location

`/usr/local/bin/homelab-cert-reload.sh` on `dns-01`

### Permissions

Certificates have specific ownership/permissions for security:

**AdGuard Home** (`/opt/AdGuardHome/certs/`):
- Directory: `root:adguard`, mode `2750` (setgid)
- `fullchain.pem`: `root:adguard`, mode `0644`
- `privkey.pem`: `root:adguard`, mode `0640`

**Postfix** (`/etc/postfix/tls/`):
- Directory: `root:root`, mode `0755`
- `fullchain.pem`: `root:root`, mode `0644`
- `privkey.pem`: `root:root`, mode `0600`

## Certificate Usage

### AdGuard Home (dns-01, dns-02)

DoT (DNS-over-TLS) on port 853 and HTTPS admin on port 443:

```yaml
tls:
  enabled: true
  server_name: dns.esweiss.com
  force_https: true
  port_https: 443
  port_dns_over_tls: 853
  certificate_path: /opt/AdGuardHome/certs/fullchain.pem
  private_key_path: /opt/AdGuardHome/certs/privkey.pem
```

**Verify**:
```bash
# Check DoT
kdig @192.168.0.150 -p 853 +tls esweiss.com

# Check HTTPS
curl -I https://192.168.0.150
```

### Postfix SMTP Relay (smtp-relay)

TLS for SMTP submission (port 587):

```
# /etc/postfix/main.cf
smtpd_tls_cert_file = /etc/postfix/tls/fullchain.pem
smtpd_tls_key_file = /etc/postfix/tls/privkey.pem
smtpd_tls_security_level = may
smtpd_tls_auth_only = yes
```

**Verify**:
```bash
# Check SMTP TLS
openssl s_client -connect 192.168.0.151:587 -starttls smtp
```

## Automatic Renewal

acme.sh installs a cron job for automatic renewal:

```bash
# Check cron job
sudo crontab -l | grep acme

# Typical entry:
0 0 * * * /root/.acme.sh/acme.sh --cron --home /root/.acme.sh > /dev/null
```

Renewals occur:
- **Frequency**: Daily check (renews when < 30 days until expiry)
- **Distribution**: Automatic via `--reloadcmd` hook
- **Verification**: Logs to `/root/.acme.sh/acme.sh.log`

## Monitoring

### Check Certificate Expiry

```bash
# On dns-01
sudo /root/.acme.sh/acme.sh --list

# Check installed cert
openssl x509 -in /opt/AdGuardHome/certs/fullchain.pem -noout -dates

# Check remote service
echo | openssl s_client -connect 192.168.0.150:853 2>/dev/null | \
  openssl x509 -noout -dates
```

### Renewal Logs

```bash
# View acme.sh logs
sudo tail -f /root/.acme.sh/acme.sh.log

# Check distribution script logs
sudo journalctl -u adguardhome -f
sudo journalctl -u postfix -f
```

## Troubleshooting

### Certificate Not Renewing

1. **Check acme.sh cron**:
   ```bash
   sudo crontab -l | grep acme
   ```

2. **Force renewal**:
   ```bash
   sudo /root/.acme.sh/acme.sh --renew -d esweiss.com --force
   ```

3. **Check Cloudflare API**:
   ```bash
   # Verify token has DNS edit permissions
   curl -X GET "https://api.cloudflare.com/client/v4/user/tokens/verify" \
     -H "Authorization: Bearer $CF_Token"
   ```

### Distribution Failing

1. **Check SSH connectivity** (using cert distribution key):
   ```bash
   # From dns-01 - uses dedicated cert distribution key
   ssh -i /home/eric/.ssh/id_ed25519_certs eric@192.168.0.160 "echo OK"  # dns-02
   ssh -i /home/eric/.ssh/id_ed25519_certs eric@192.168.0.151 "echo OK"  # smtp-relay
   ```

2. **Manually run distribution**:
   ```bash
   sudo /usr/local/bin/homelab-cert-reload.sh
   ```

3. **Check remote permissions**:
   ```bash
   # On dns-02
   ls -la /opt/AdGuardHome/certs/

   # On smtp-relay
   ls -la /etc/postfix/tls/
   ```

### Service Not Using New Cert

1. **Verify cert was copied**:
   ```bash
   # Compare cert dates
   stat /opt/AdGuardHome/certs/fullchain.pem
   ```

2. **Restart service**:
   ```bash
   # AdGuard Home
   sudo systemctl restart adguardhome

   # Postfix
   sudo systemctl restart postfix
   ```

3. **Check service logs**:
   ```bash
   sudo journalctl -u adguardhome -n 50
   sudo journalctl -u postfix -n 50
   ```

### Cloudflare API Errors

If DNS-01 challenge fails:

1. **Verify API token permissions**:
   - Zone: DNS: Edit
   - Zone: Zone: Read

2. **Check rate limits**:
   - Let's Encrypt: 5 certs/domain/week
   - Cloudflare: No strict rate limits for API

3. **Use staging for testing**:
   ```bash
   sudo /root/.acme.sh/acme.sh --set-default-ca --server letsencrypt_test
   ```

## Future: k3s Certificate Management

Once k3s is deployed, cert-manager will handle cluster certificates:

- **Internal services**: Internal CA or self-signed
- **External ingress**: Let's Encrypt via DNS-01 (same Cloudflare API)
- **Distribution**: Automatic via Kubernetes secrets

The acme.sh pipeline will remain for non-k3s services (AdGuard, SMTP).

## Ansible Deployment

### Deploy Certificate Pipeline

```bash
# Full deployment
ansible-playbook ansible/playbooks/dns.yml --tags acme

# Distribution script only
ansible-playbook ansible/playbooks/dns.yml --tags acme-distribute
```

### Variables

Configured in `group_vars/dns.yml`:

```yaml
adguard_tls_enabled: true
adguard_cert_path: /opt/AdGuardHome/certs

secrets:
  cloudflare_api_token: "op://Homelab/Cloudflare DNS Token/credential"
  cloudflare_account_id: "op://Homelab/Cloudflare DNS Token/account_id"
```

## References

- [acme.sh Documentation](https://github.com/acmesh-official/acme.sh)
- [Cloudflare DNS-01](https://github.com/acmesh-official/acme.sh/wiki/dnsapi#dns_cf)
- [Let's Encrypt Rate Limits](https://letsencrypt.org/docs/rate-limits/)
