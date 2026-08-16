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
  └── Distributes certs to (acme_certs_distribution_targets in host_vars/dns-01.yml):
      ├── Forced-command receiver targets (one SSH round-trip each; the
      │   receiver validates, installs, and reloads):
      │   ├── dns-02 (AdGuard Home)
      │   ├── smtp-relay (Postfix TLS)
      │   ├── gitlab (/etc/gitlab/ssl, `gitlab-ctl hup nginx`)
      │   ├── nextcloud (/etc/ssl/nextcloud, `systemctl reload nginx`)
      │   ├── immich (/etc/nginx/ssl, `systemctl reload nginx`)
      │   ├── pve-nas-01 (/etc/ssl/private, tlshd/NFS-TLS)
      │   └── plex (/etc/ssl/plex, PKCS#12 conversion via plex-cert-reload.sh)
      └── Legacy scp push (operator-managed appliance, no sudo):
          └── home (HAOS /ssl via SSH :22222, `ha core restart`)
```

## Certificate Issuance

### Initial Setup

The `acme_certs` Ansible role sets up the certificate infrastructure:

```bash
task dns:deploy
# Or directly:
ansible-playbook ansible/playbooks/dns.yml
```

This role:
- Installs acme.sh on dns-01
- Creates the certs directory at `/opt/AdGuardHome/certs`
- Deploys the `homelab-cert-reload.sh` distribution script
- Deploys the `cert-receive` forced-command receiver (+ its sudoers drop-in)
  to every sudo target, and pins the distribution pubkey in each target's
  `authorized_keys` to that receiver (see "Distribution security model" below)
- **Automatically installs certificates** if they exist in acme.sh but are not yet in the target directory
- Configures the `--reloadcmd` hook for automatic distribution on future renewals

### Certificate Issuance Workflow

**Step 1: Issue Certificate (Manual - One Time)**

Ansible does NOT issue certificates from Let's Encrypt automatically (to avoid accidental rate limit hits).
You must manually issue the certificate once:

```bash
# On dns-01 as root
export CF_Token=$(op read "op://Homelab/Cloudflare DNS Token/credential")
export CF_Account_ID=$(op read "op://Homelab/Cloudflare DNS Token/username")

/root/.acme.sh/acme.sh --issue --dns dns_cf \
  -d esweiss.com \
  -d '*.esweiss.com' \
  --keylength ec-256
```

**Step 2: Install and Distribute (Automatic via Ansible)**

After certificates are issued, run Ansible to automatically install and distribute them:

```bash
task dns:deploy
```

This will:
1. Detect that certificates exist in `/root/.acme.sh/esweiss.com_ecc/`
2. Install them to `/opt/AdGuardHome/certs/` on dns-01
3. Configure the `--reloadcmd` hook to run `homelab-cert-reload.sh`
4. The reload script distributes certs to all eight targets, then restarts/reloads each service

**Important**: This is idempotent - once certificates are installed, subsequent runs will skip
the installation step.

### Manual Certificate Installation (Alternative)

If you prefer to install manually instead of using Ansible:

```bash
# On dns-01 as root
/root/.acme.sh/acme.sh --install-cert -d esweiss.com \
  --cert-file /opt/AdGuardHome/certs/cert.pem \
  --key-file /opt/AdGuardHome/certs/privkey.pem \
  --fullchain-file /opt/AdGuardHome/certs/fullchain.pem \
  --reloadcmd "/usr/local/sbin/homelab-cert-reload.sh"
```

## Certificate Distribution

### Distribution Script

The `homelab-cert-reload.sh` script on `dns-01` is generated from the
data-driven `acme_certs_distribution_targets` list in `host_vars/dns-01.yml` —
the source of truth for targets, per-target SSH host-key pinning, cert
paths/permissions, and restart commands. It also emits per-target
Prometheus metrics.

For the seven **sudo targets** (dns-02, smtp-relay, gitlab, nextcloud, immich,
pve-nas-01, plex) the push is a single SSH round-trip: the script streams the cert bundle
(fullchain + delimiter + privkey) to the target's stdin, where the pinned
forced command runs `/usr/local/sbin/cert-receive`. The receiver validates,
installs, reloads, and prints `OK` / `unchanged` / `FAIL` — no scp, no remote
mktemp/chown, no client-side pre-checks. Per-target install specifics:

1. **Local AdGuard Home** (dns-01): restart service to load the new cert —
   done last, and skipped when the local cert is unchanged
2. **dns-02**: `/opt/AdGuardHome/certs`, restart AdGuard
3. **smtp-relay**: `/etc/postfix/tls`, restart Postfix
4. **gitlab**: `/etc/gitlab/ssl`, `gitlab-ctl hup nginx`
5. **nextcloud** (.156): `/etc/ssl/nextcloud`, `systemctl reload nginx` — replaces the
   self-signed bootstrap cert the `nextcloud` role seeds, which is what lets Traefik's
   `vm-tls-wildcard` backend validation succeed for `cloud.esweiss.com` (docs/35)
6. **immich** (.157): `/etc/nginx/ssl`, `systemctl reload nginx` — same bootstrap-cert
   replacement for `photos.esweiss.com` (docs/36)
7. **pve-nas-01**: `/etc/ssl/private` (tlshd for NFS-over-TLS; the NFS server is the sole TLS cert holder — k3s agents are xprtsec=tls clients that validate via the system CA and hold no cert)
8. **plex**: `/etc/ssl/plex`, PKCS#12 conversion via `plex-cert-reload.sh`

**home (HAOS)** is the one remaining **legacy scp push** (`/ssl` via SSH
:22222 as root, `ha core restart`): its `authorized_keys` is operator-managed
inside the appliance, so the role cannot deploy a receiver there — see the
HAOS runbook below.

### Distribution security model (forced-command receiver)

The dns-01 push key is pinned in each sudo target's `authorized_keys` as:

```
from="192.168.0.150",command="sudo /usr/local/sbin/cert-receive",restrict ssh-ed25519 AAAA...
```

- `restrict` disables pty/agent/port/X11 forwarding and user rc files;
  `command=` forces every connection into the receiver — a leaked or abused
  key never gets a shell or arbitrary sudo. `from=` keeps the source pinned
  to dns-01 (which holds .150 across Proxmox-HA moves) as defense in depth.
- The receiver (`/usr/local/sbin/cert-receive`, root:root `0500`, invoked
  via the `/etc/sudoers.d/cert-receive` drop-in) treats stdin as untrusted:
  size-capped read, receiver-assigned filenames (nothing from the wire
  becomes a path), then full validation — cert and key parse, cert not
  expired, key matches the leaf, and the SAN covers `*.esweiss.com` — before
  an atomic install with **baked-in** owner/group/mode and the baked-in
  reload command. Paths, permissions, and reload are rendered at deploy time
  from `acme_certs_distribution_targets`; the client controls only the cert bytes.
- Unchanged-vs-apply is decided **server-side** (sha256 marker written only
  after a clean reload, so a failed reload self-heals on the next push).
- Blast radius of a leaked key: install a *validated* wildcard cert and run
  one baked-in reload — not a root shell.

### Script Locations

- `/usr/local/sbin/homelab-cert-reload.sh` on `dns-01`
- `/usr/local/sbin/cert-receive` + `/etc/sudoers.d/cert-receive` on each sudo target

### HAOS operator runbook (legacy path + optional hardening)

HAOS (`home`, `ssh_no_sudo: true`, SSH add-on on :22222 as root) keeps the
legacy scp+install push because its `authorized_keys` lives inside the
appliance and is operator-managed — the acme_certs role does not touch it.
Current posture is mitigated by the `from="192.168.0.150"` source pin and by
HAOS being a single non-sudo appliance.

To harden it to the receiver model later (operator steps, via the SSH
add-on):

1. Install a receiver script at `/config/cert-receive.sh` on HAOS (same
   validate-then-install-then-`ha core restart` shape as `cert-receive`,
   without sudo).
2. Replace the cert key's line in the add-on's authorized_keys with:
   `from="192.168.0.150",command="/config/cert-receive.sh",restrict <key>`
3. Flip the `home` target off `ssh_no_sudo` handling only if the push flow
   is also updated — until then the legacy path expects plain scp+ssh.

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

DoT (DNS-over-TLS) on port 853 and AdGuard's own HTTPS/DoH listener on port 443. The human-facing HTTPS admin UI and DoH are AdGuard's own :443, which the Traefik IngressRoute (scheme https) at `dns-01.esweiss.com`/`dns-02.esweiss.com` proxies. `force_https` stays false (see below) because the role reconciles AdGuard over the plaintext localhost :3000 API (incl. split-horizon rewrites); a global redirect would 301 those to :443 and fail TLS verification, breaking reconciliation. The :3000 listener is firewall-restricted to admin LAN/Tailscale.

```yaml
tls:
  enabled: true
  server_name: dns.esweiss.com
  force_https: false  # role reconciles via the plaintext localhost :3000 API; a global redirect would 301 those calls to :443 and break reconciliation
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

TLS for the SMTP relay — the global `main.cf` defaults (govern port 25):

```
# /etc/postfix/main.cf
smtpd_tls_cert_file = /etc/postfix/tls/fullchain.pem
smtpd_tls_key_file = /etc/postfix/tls/privkey.pem
smtpd_tls_security_level = may
smtpd_tls_auth_only = yes
```

Port 587 (submission) is stricter: a `master.cf` per-service override sets
`-o smtpd_tls_security_level=encrypt`, so TLS is **mandatory** on 587 (not the
opportunistic `may` above). See docs/10-mail.md.

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
sudo journalctl -u AdGuardHome -f
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

1. **Check SSH connectivity** (using the cert distribution key; repeat for
   each target in `acme_certs_distribution_targets`). On the seven sudo targets the
   key is pinned to the forced-command receiver, so any connection runs
   `cert-receive` — an empty stdin probe answering `FAIL: empty bundle`
   proves SSH + forced command + sudoers are all wired:
   ```bash
   # From dns-01 (as root — the reload script runs as root)
   ssh -i /home/eric/.ssh/id_ed25519_certs eric@192.168.0.160 < /dev/null  # dns-02
   # Expected output: "FAIL: empty bundle" (exit non-zero) — that is success
   # for a connectivity probe. A permission/hostkey error means SSH is broken;
   # a shell prompt would mean the forced-command pin is missing.
   ```

2. **Manually run distribution** (streams real bundles; prints per-target
   `OK` / `unchanged` / `FAIL`):
   ```bash
   sudo /usr/local/sbin/homelab-cert-reload.sh
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
   # AdGuard Home (systemd unit name is case-sensitive: AdGuardHome)
   sudo systemctl restart AdGuardHome

   # Postfix
   sudo systemctl restart postfix
   ```

3. **Check service logs**:
   ```bash
   sudo journalctl -u AdGuardHome -n 50
   sudo journalctl -u postfix -n 50
   ```

### Cloudflare API Errors

If DNS-01 challenge fails:

1. **Verify API token permissions** (the `Cloudflare DNS Token` item used by
   acme.sh and the in-cluster ESO consumers is scoped to exactly these two —
   Terraform uses the separate `Cloudflare Terraform Token` item, which adds
   Zone Settings:Edit):
   - Zone: DNS: Edit
   - Zone: Zone: Read

2. **Check rate limits**:
   - Let's Encrypt: 5 **duplicate** certificates (identical SAN set) per week —
     this deployment issues one cert, so re-issuing `esweiss.com` +
     `*.esweiss.com` more than 5 times in a week is the limit you will hit.
     50 certificates per registered domain per week is the other ceiling
   - Cloudflare: No strict rate limits for API

3. **Use staging for testing**:
   ```bash
   sudo /root/.acme.sh/acme.sh --set-default-ca --server letsencrypt_test
   ```

## K3s Certificate Management (cert-manager)

cert-manager is deployed on the k3s cluster and manages certificates for all Kubernetes-hosted services:

- **Domains**: `*.ericsweiss.com` and `*.esweiss.com` (wildcard certificates)
- **Issuer**: Let's Encrypt via DNS-01 challenge (Cloudflare API)
- **Distribution**: Automatic via Kubernetes TLS secrets consumed by Traefik IngressRoutes

The acme.sh pipeline remains active for non-k3s services (AdGuard Home, SMTP relay).

## Ansible Deployment

### Deploy Certificate Pipeline

```bash
# Full pipeline (site.yml tags the role as acme_certs; dns.yml defines no tags)
ansible-playbook ansible/playbooks/site.yml --tags acme_certs

# Manual distribution: run the reload script on dns-01
# sudo /usr/local/sbin/homelab-cert-reload.sh
```

### Variables

Configured in `group_vars/dns.yml`:

```yaml
adguard_home_tls_enabled: true
adguard_home_cert_path: /opt/AdGuardHome/certs
```

The Cloudflare credentials are not inventory values. The
`op://Homelab/Cloudflare DNS Token/{credential,username}` references live in the
Taskfile task's `env:` block (mirrored by the CI job's `variables:`), and the
inventory reads them back with `lookup('ansible.builtin.env', ...)` — docs/15
§ Secrets model is canonical.

## Related documentation

- [docs/08 — DNS](08-dns.md) (the Cloudflare DNS-01 zone)
- [docs/15 — Credential rotation](15-credential-rotation.md) (the Cloudflare token and the wildcard key)
- [docs/12 — Runbooks](12-runbooks.md) § Certificate Renewal Issues
- [docs/07 — File services](07-fileservices.md) (the cert NFS-over-TLS depends on)

## External references

- [acme.sh Documentation](https://github.com/acmesh-official/acme.sh)
- [Cloudflare DNS-01](https://github.com/acmesh-official/acme.sh/wiki/dnsapi#dns_cf)
- [Let's Encrypt Rate Limits](https://letsencrypt.org/docs/rate-limits/)
