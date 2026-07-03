# ACME Certificates Role

Manages Let's Encrypt wildcard certificates using acme.sh with Cloudflare DNS-01 validation. Certificates are issued on dns-01 and distributed to the hosts listed in `cert_distribution_targets` (dns-02, smtp-relay, gitlab, pve-nas-01, plex, HAOS) via SSH.

## What This Role Manages

### Certificate Management (dns-01 only)
- acme.sh installation and configuration
- SSH key pair for certificate distribution
- Automatic certificate installation to local directory
- Certificate distribution script
- Proactive certificate distribution to targets
- Proper ownership and permissions

### Certificate Distribution
- SSH public key deployment to every target host (host_vars/dns-01.yml)
- Automated copying via homelab-cert-reload.sh script
- Per-target service reload, skipped when the cert is unchanged
- Per-target Prometheus metrics + error handling

### Domains
- Primary domain certificate (esweiss.com)
- Wildcard certificate (*.esweiss.com)

## Configuration

### Default Variables

```yaml
# ACME configuration (defaults/main.yml)
acme_email: "admin@esweiss.com"
local_cert_dir: "/opt/AdGuardHome/certs"
internal_domain: "esweiss.com"
acme_sh_version: "3.1.2"
```

Certificate distribution targets live in
`inventories/prod/host_vars/dns-01.yml` (`cert_distribution_targets`), not
in this role's defaults. Each entry uses the schema:

```yaml
cert_distribution_targets:
  - host: dns-02                    # inventory hostname (drives the IP env var)
    ip: 192.168.0.160               # SSH target address
    host_key: "ssh-ed25519 AAAA..." # REQUIRED; capture via task certs:show-host-keys
    cert_dir: /opt/AdGuardHome/certs
    owner: root
    group: adguard
    cert_mode: "0644"
    key_mode: "0640"
    restart_service: AdGuardHome    # or restart_command for compound reloads
    # ssh_port / ssh_user / ssh_no_sudo override the defaults (22 / eric / sudo)
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

## SSH host-key pinning

Cert distribution pushes wildcard private-key material from dns-01 to
each target over SSH, so the cert-reload script runs with
`StrictHostKeyChecking=yes` against `/root/.ssh/known_hosts` populated
by this role from inventory. Each entry in
`host_vars/dns-01.yml` `cert_distribution_targets` MUST set a
`host_key` field — the role asserts it's non-empty before deploying
the script. A fingerprint mismatch (host rebuild without inventory
update, or MITM) fails the cert push loudly.

Capture / rotate keys with the helper task:

```bash
# Run from the repo root
task certs:show-host-keys
```

It runs `ssh-keyscan -t ed25519` against every target IP from dns-01
and prints a paste-ready block of `host_key:` values. The playbook
honors a per-target `ssh_port` field (default 22) so non-standard SSH
ports work — Home Assistant OS runs SSH on port 22222 and is captured
via the existing `ssh_port: 22222` entry in `host_vars/dns-01.yml`.
Copy each value into the matching `cert_distribution_targets` entry,
then re-run `task dns:deploy`.

To capture a single HAOS host key manually outside the playbook:

```bash
ssh-keyscan -t ed25519 -p 22222 192.168.0.154
```

When a target is rebuilt and its host key changes, the next cert
push will fail with a clear `ssh: REMOTE HOST IDENTIFICATION HAS
CHANGED` error. Re-run the capture task, paste the new value over
the stale one, and re-run `task dns:deploy`.

## Architecture

```
dns-01 (certificate authority)
  ├─ acme.sh (Let's Encrypt client)
  ├─ /root/.acme.sh/*.esweiss.com_ecc/ (managed by acme.sh)
  ├─ /opt/AdGuardHome/certs/ (local installation)
  └─ homelab-cert-reload.sh
       │
       ├─> dns-02      → /opt/AdGuardHome/certs/ → restart AdGuard Home
       ├─> smtp-relay  → /etc/postfix/tls/       → restart Postfix
       ├─> gitlab      → /etc/gitlab/ssl/        → gitlab-ctl hup nginx
       ├─> pve-nas-01  → /etc/ssl/private/       → restart tlshd (NFS+TLS)
       ├─> plex        → /etc/ssl/plex/          → rebuild PKCS#12 + restart
       └─> HAOS        → /ssl/                   → ha core restart
```

The script skips any target whose `fullchain.pem` already matches the cert
being pushed, so a proactive run with no renewal does not restart services.
The full, authoritative target list is in `host_vars/dns-01.yml`.

## Manual Certificate Issuance

If certificates don't exist yet, the role will display instructions:

```bash
# 1. Set Cloudflare credentials (from 1Password)
export CF_Token=$(op read "op://Homelab/Cloudflare DNS Token/credential")
export CF_Account_ID=$(op read "op://Homelab/Cloudflare DNS Token/username")

# 2. Issue wildcard certificate. The role pins Let's Encrypt as the
#    default CA at install time (acme.sh 3.x defaults to ZeroSSL, but
#    our Cloudflare CAA only authorises letsencrypt.org). Pass
#    --server letsencrypt explicitly here so this command also works
#    against any pre-existing acme.sh install that wasn't installed
#    via this role.
/root/.acme.sh/acme.sh --issue --dns dns_cf --server letsencrypt \
  -d "esweiss.com" \
  -d "*.esweiss.com"

# 3. Re-run Ansible to install and distribute
task dns:deploy
```

## Task Flow

All meaningful tasks gate on `inventory_hostname == 'dns-01'`.

```
1. Validate required acme.sh variables (SSH keys, email, domain)
2. Create eric .ssh directory and deploy the distribution key pair
3. Deploy dns-01 pubkey to every cert_distribution_target's authorized_keys
   (looped over host_vars/dns-01.yml; delegate_to each target)
4. Install acme.sh dependencies (curl, openssl)
5. Download + install acme.sh (default CA pinned to Let's Encrypt)
6. Ensure renewal cronjob exists and default CA is Let's Encrypt
7. Deploy homelab-cert-reload.sh script
8. Pin each target's host key into /root/.ssh/known_hosts
9. Install certificates to local_cert_dir (only if acme.sh has them and the
   local dir is empty) and set ownership (root:adguard)
10. Distribute certificates to remote hosts (skips targets that already have
    the current cert; only changed targets are reloaded)
11. Display status messages
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

Every Ansible run invokes the distribution script if certificates exist locally, target hosts are defined, and the run is not in check mode. The script compares each target's `fullchain.pem` against the cert being pushed and skips the copy + service reload when they match, so an unchanged cert never restarts services. Targets that are missing the cert, have an older one, or were rebuilt get the full push — covering recovery from a failed previous distribution.

## Operational Notes

### Checking Certificate Status

```bash
# On dns-01:
/root/.acme.sh/acme.sh --list

# Check expiration
openssl x509 -in /opt/AdGuardHome/certs/fullchain.pem -noout -dates

# View certificate details
openssl x509 -in /opt/AdGuardHome/certs/fullchain.pem -noout -text
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
sudo chown root:adguard /opt/AdGuardHome/certs/*
sudo chmod 0640 /opt/AdGuardHome/certs/privkey.pem
sudo chmod 0644 /opt/AdGuardHome/certs/fullchain.pem
```

### Adding Distribution Targets

To add a new host, append an entry to `cert_distribution_targets` in
`inventories/prod/host_vars/dns-01.yml`:

```yaml
cert_distribution_targets:
  - host: new-host
    ip: 192.168.0.x
    host_key: "ssh-ed25519 AAAA..."   # capture via task certs:show-host-keys
    cert_dir: /etc/certs
    owner: root
    group: root
    cert_mode: "0644"
    key_mode: "0640"
    restart_service: service-name      # or restart_command for compound reloads
```

Then run `task dns:deploy`. The role deploys the distribution pubkey to the
new host's authorized_keys and pins its host key automatically; only HAOS
(ssh_no_sudo) needs the pubkey pasted in by hand.
