# ACME Certificates Role

Manages Let's Encrypt wildcard certificates using acme.sh with Cloudflare DNS-01 validation. Certificates are issued on dns-01 and distributed to other hosts (dns-02, smtp-relay) via SSH.

## What This Role Manages

### Certificate Management (dns-01 only)
- acme.sh installation and configuration
- SSH key pair for certificate distribution
- Automatic certificate installation to local directory
- Certificate distribution script
- Proactive certificate distribution to targets
- Proper ownership and permissions

### Certificate Distribution
- SSH public key deployment to target hosts (dns-02, smtp-relay)
- Automated copying via homelab-cert-reload.sh script
- Service reload on target hosts (AdGuard Home, Postfix)
- Error handling and warnings

### Domains
- Primary domain certificate (esweiss.com)
- Wildcard certificate (*.esweiss.com)

## Configuration

### Default Variables

```yaml
# ACME configuration
acme_email: "{{ admin_email }}"
local_cert_dir: "/etc/acme-certs"
internal_domain: "esweiss.com"

# Certificate distribution targets
cert_distribution_targets:
  - host: dns-02.{{ internal_domain }}
    cert_dir: /opt/AdGuardHome/certs
    reload_cmd: "sudo systemctl reload AdGuardHome"
  - host: smtp-relay.{{ internal_domain }}
    cert_dir: /etc/postfix/certs
    reload_cmd: "sudo systemctl reload postfix"
```

### 1Password Secrets

```yaml
secrets:
  cloudflare_api_token: "op://Homelab/Cloudflare DNS Token/credential"
  cloudflare_account_id: "op://Homelab/Cloudflare DNS Token/username"
  dns01_ssh_private_key: "op://Homelab/DNS-01 SSH Key/private key"
  dns01_ssh_public_key: "op://Homelab/DNS-01 SSH Key/public key"
```

## Deployment

```bash
# Deploy DNS stack (includes cert management)
task dns:deploy

# Deploy to dns-01 only
ansible-playbook ansible/playbooks/dns.yml --limit dns-01
```

## Architecture

```
dns-01 (certificate authority)
  ├─ acme.sh (Let's Encrypt client)
  ├─ /root/.acme.sh/*.esweiss.com_ecc/ (managed by acme.sh)
  ├─ /etc/acme-certs/ (local installation)
  └─ homelab-cert-reload.sh
       │
       ├─> Distributes to → dns-02
       │                    └─ /opt/AdGuardHome/certs/
       │                    └─ Reloads AdGuard Home
       │
       └─> Distributes to → smtp-relay
                            └─ /etc/postfix/certs/
                            └─ Reloads Postfix
```

## Manual Certificate Issuance

If certificates don't exist yet, the role will display instructions:

```bash
# 1. Set Cloudflare credentials (from 1Password)
export CF_Token=$(op read "op://Homelab/Cloudflare DNS Token/credential")
export CF_Account_ID=$(op read "op://Homelab/Cloudflare DNS Token/username")

# 2. Issue wildcard certificate
/root/.acme.sh/acme.sh --issue --dns dns_cf \
  -d "esweiss.com" \
  -d "*.esweiss.com"

# 3. Re-run Ansible to install and distribute
task dns:deploy
```

## Task Flow

```
1. Create eric .ssh directory on dns-01
2. Deploy dns-01 SSH private key (for distribution)
3. Deploy dns-01 SSH public key
4. Deploy dns-01 public key to dns-02 authorized_keys
5. Deploy dns-01 public key to smtp-relay authorized_keys
6. Install acme.sh dependencies (curl, openssl, socat)
7. Check if acme.sh is installed
8. Download and install acme.sh (if not present)
9. Deploy homelab-cert-reload.sh script
10. Check if certificates exist in acme.sh
11. Check if certificates are installed locally
12. Install certificates to local directory (if needed)
    ├─ Copy cert.pem, privkey.pem, fullchain.pem
    ├─ Set reload command (homelab-cert-reload.sh)
    └─ Set proper ownership (root:adguard)
13. Distribute certificates to remote hosts
    ├─ SCP certs to dns-02 and smtp-relay
    ├─ Set proper ownership on targets
    └─ Reload services on targets
14. Display status messages
```

## Files

- `tasks/main.yml` - Main task orchestration
- `templates/homelab-cert-reload.sh.j2` - Certificate distribution script
- `defaults/main.yml` - Default variables

## Dependencies

- `adguard_home` role (provides target directory and group)
- SSH connectivity from dns-01 to targets

## Security

- Private key stored with mode 0600
- Certificates owned by root:adguard (group read for AdGuard)
- Private key has mode 0640 (group readable)
- SSH keys from 1Password (never in git)
- All secret operations use `no_log: true`

## Automatic Renewal

acme.sh handles automatic renewal:

```bash
# Renewal cron job (installed by acme.sh)
0 0 * * * /root/.acme.sh/acme.sh --cron --home /root/.acme.sh
```

When renewal occurs:
1. acme.sh renews certificate
2. Runs reloadcmd: `/usr/local/sbin/homelab-cert-reload.sh`
3. Script distributes to targets and reloads services

## Distribution on Every Run

Every Ansible run triggers distribution if certificates exist locally, target hosts are defined, and the run is not in check mode. This covers cases where a target host was rebuilt or a previous distribution failed.

## Operational Notes

### Checking Certificate Status

```bash
# On dns-01:
/root/.acme.sh/acme.sh --list

# Check expiration
openssl x509 -in /etc/acme-certs/fullchain.pem -noout -dates

# View certificate details
openssl x509 -in /etc/acme-certs/fullchain.pem -noout -text
```

### Manual Distribution

```bash
# On dns-01:
sudo /usr/local/sbin/homelab-cert-reload.sh
```

### Force Renewal

```bash
# On dns-01:
/root/.acme.sh/acme.sh --renew -d esweiss.com -d *.esweiss.com --force
```

### Troubleshooting

**Certificate issuance fails:**
```bash
# Check Cloudflare credentials
env | grep CF_

# Test DNS-01 challenge manually
/root/.acme.sh/acme.sh --issue --dns dns_cf --test \
  -d esweiss.com -d *.esweiss.com
```

**Distribution fails:**
```bash
# Test SSH connectivity
ssh -i /home/eric/.ssh/id_ed25519_certs eric@dns-02.esweiss.com

# Check authorized_keys on target
cat /home/eric/.ssh/authorized_keys | grep dns-01
```

**Permissions issues:**
```bash
# Fix cert ownership
sudo chown root:adguard /etc/acme-certs/*
sudo chmod 0640 /etc/acme-certs/privkey.pem
sudo chmod 0644 /etc/acme-certs/fullchain.pem
```

### Adding Distribution Targets

To add a new host:

```yaml
# In group_vars/dns.yml
cert_distribution_targets:
  - host: new-host.esweiss.com
    cert_dir: /etc/certs
    reload_cmd: "sudo systemctl reload service-name"
```

Then:
1. Deploy dns-01 public key to new host's authorized_keys
2. Run deployment: `task dns:deploy`
