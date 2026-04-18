# Credential Rotation Guide

This document explains how to rotate passwords, API tokens, and SSH keys stored in 1Password.

## Overview

All secrets are stored in 1Password and injected at use time. There are two
consumer paths and they rotate differently:

- **Kubernetes workloads** (Authentik, download clients, recipes, GitLab
  runners / agent, cert-manager DNS-01 token, etc.) consume 1Password via
  the External Secrets Operator (ESO). Rotate via `task flux:rotate-secret
  -- <app>` — see § "Kubernetes workloads (External Secrets Operator)"
  below and `docs/29-flux-operations.md` for the full day-2 playbook.
- **Host-side / Ansible-managed state** (SMTP, SSH keys, acme.sh, Tailscale
  auth keys, GitLab VM root password, etc.) uses `op run --` to inject
  secrets at playbook-run time. Rotate by updating 1Password, then
  re-running the relevant Ansible playbook.

## Kubernetes workloads (External Secrets Operator)

For any secret consumed inside the k3s cluster (every `ExternalSecret` in
`kubernetes/apps/*/externalsecret.yaml` and
`kubernetes/infrastructure/configs/shared-cloudflare-secrets/`):

```bash
# 1. Update the 1Password item (web/desktop app or CLI).
eval $(op signin)   # if needed
# op item edit <item-id> <field>[password]=<new-value>

# 2. Refresh ExternalSecret + restart consuming pods in one go:
task flux:rotate-secret -- authentik    # or: downloads, recipes, gitlab-runner,
                                        #     gitlab-runner-privileged, gitlab-agent

# 3. (Optional) Refresh an ExternalSecret without restarting pods — useful
#    for secrets that don't require a pod restart to take effect.
task flux:refresh-secret -- authentik/authentik-secrets
```

The `task flux:rotate-secret -- <app>` command annotates the ExternalSecret
with a force-sync timestamp, waits for ESO to re-fetch from 1Password, then
restarts the Deployments/StatefulSets that consume the produced Secret. See
`docs/29-flux-operations.md` § Secret Operations for the full dispatch table
and rate-limit considerations.

The single exception is `external-secrets/onepassword-sdk-token` — that's
the bootstrap token ESO uses to talk to 1Password itself. Rotate by
updating the 1P service-account token, then `task flux:bootstrap-onepassword`
to write the new value into the cluster (the old ExternalSecret machinery
can't self-rotate its own auth source).

## Host-side / Ansible-managed credentials

### SMTP Passwords

**Location**: 1Password → Homelab vault

**Items to Update**:
- `SMTP Relay Gmail` - Gmail app password (smtp-relay → Gmail)
- `SMTP Relay Auth` - Relay user password (null clients → smtp-relay)

**Rotation Procedure**:

```bash
# 1. Update password in 1Password (web/desktop app)
# 2. Ensure 1Password CLI is signed in
eval $(op signin)

# 3. Verify new password is readable
op read "op://Homelab/SMTP Relay Gmail/password"
op read "op://Homelab/SMTP Relay Auth/password"

# 4. Deploy SMTP configuration
ansible-playbook ansible/playbooks/site.yml --tags smtp

# 5. Verify mail still works
ssh eric@192.168.0.102 "echo 'Test' | mail -s 'Rotation Test' root"

# Check mail arrives at your root_email_alias
```

**What Happens**:
- Gmail password: `/etc/postfix/sasl_passwd` updated on smtp-relay, postmap rebuilds hash
- Relay user password: SASL database (`/etc/sasldb2`) updated on smtp-relay
- Null client passwords: `/etc/postfix/sasl_passwd` updated on all Proxmox hosts and DNS LXCs
- Postfix reloads on all affected hosts

**Affected Hosts**:
- `smtp-relay` - Both passwords
- `pve-nas-01`, `pve-laptop-01`, `pve-opt-01`, `pve-opt-02`, `pve-opt-03`, `pve-prec-01` - Relay auth password
- `dns-01`, `dns-02` - Relay auth password
- `plex`, `gitlab` - Relay auth password

---

### SSH Keys

**Location**: 1Password → Homelab vault → SSH Key item

**Rotation Procedure**:

```bash
# 1. Generate new key pair
ssh-keygen -t ed25519 -C "eric@MacBookPro.esweiss.com" -f ~/.ssh/id_ed25519_new

# 2. Update public key in 1Password
# Copy contents of ~/.ssh/id_ed25519_new.pub to 1Password

# 3. Verify new key is readable
op read "op://Homelab/SSH Key/public key"

# 4. Deploy new key to all hosts
ansible-playbook ansible/playbooks/base.yml --tags ssh

# 5. Test SSH with new key (before removing old one!)
ssh -i ~/.ssh/id_ed25519_new eric@192.168.0.102

# 6. Once verified, replace old key
mv ~/.ssh/id_ed25519_new ~/.ssh/id_ed25519
mv ~/.ssh/id_ed25519_new.pub ~/.ssh/id_ed25519.pub

# 7. Update SSH agent
ssh-add -D
ssh-add ~/.ssh/id_ed25519
```

**What Happens**:
- `authorized_keys` updated on all managed hosts
- Old key still works until you remove it from 1Password and redeploy

**Affected Hosts**: All (proxmox, dns, mail)

---

### Cloudflare API Token

**Location**: 1Password → Homelab vault → Cloudflare DNS Token

**Rotation Procedure**:

```bash
# 1. Generate new token in Cloudflare dashboard
# Settings: Zone:DNS:Edit, Zone:Zone:Read

# 2. Update in 1Password
# Update both credential and username (account ID) if needed

# 3. Verify new token is readable
op read "op://Homelab/Cloudflare DNS Token/credential"

# 4. Test with Terraform
task terraform:plan

# 5. If plan succeeds, token is valid
# No deployment needed - Terraform reads at runtime
```

**What Happens**:
- Terraform reads token directly from 1Password at runtime.
- In-cluster consumers (cert-manager DNS-01, external-dns, cloudflare-ddns)
  pick up the new token on the next ESO refresh (default: 24h). To rotate
  immediately across all three namespaces, force refresh each ExternalSecret:
  ```bash
  task flux:refresh-secret -- cert-manager/cloudflare-api-token
  task flux:refresh-secret -- external-dns/cloudflare-api-token
  task flux:refresh-secret -- cloudflare-ddns/cloudflare-api-token
  ```
- Old token can be revoked in Cloudflare after verification.

**Affected Systems**: Terraform (local laptop), cert-manager, external-dns,
cloudflare-ddns (all three read it via ESO from 1Password).

---

### AdGuard Home Password

**Location**: 1Password → Homelab vault → AdGuard Home

**Fields Required**:
- `password` - Plaintext password (for login)
- `password_hash` - bcrypt hash (for Ansible deployment)

**Rotation Procedure**:

```bash
# 1. Choose new password
NEW_PASSWORD="your-new-secure-password"

# 2. Generate bcrypt hash using htpasswd
# Install apache2-utils if needed: sudo apt install apache2-utils
BCRYPT_HASH=$(htpasswd -nbB admin "$NEW_PASSWORD" | cut -d: -f2)

# 3. Verify hash format (should start with $2y$)
echo "$BCRYPT_HASH"
# Example: $2y$05$KwJ0zPQqVNQ1vL4EkdgZm.xxxxxxxxxxxxxxxxxxxxxxxxxxx

# 4. Update both fields in 1Password
# - Update 'password' field with plaintext password
# - Update 'password_hash' field with the bcrypt hash

# 5. Verify both values are readable from 1Password
op read "op://Homelab/AdGuard Home/password"
op read "op://Homelab/AdGuard Home/password_hash"

# 6. Deploy AdGuard configuration
task dns:deploy

# 7. Verify deployment
ansible-playbook ansible/playbooks/postflight.yml --limit dns-01,dns-02

# 8. Test login with new password
open https://192.168.0.150:3000
# Login with username: eric, new password from step 1
```

**Alternative: Python bcrypt generation**:

```bash
# If htpasswd is not available, use Python
python3 -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt(rounds=10)).decode())"
```

**What Happens**:
- AdGuard Home admin password hash updated in `AdGuardHome.yaml` on both dns-01 and dns-02
- AdGuard Home service restarted on both hosts
- Configuration synced from dns-01 to dns-02 automatically via adguardhome-sync

**Affected Hosts**: `dns-01`, `dns-02`

**Security Notes**:
- The bcrypt hash uses a random salt generated by htpasswd/bcrypt
- Each password rotation generates a new unique hash
- Plaintext password never touches production servers
- Hash is validated (must start with `$2a$`, `$2b$`, or `$2y$`) before deployment

---

### Samba NAS User Password

**Location**: 1Password → Homelab vault → Samba NAS User

**Rotation Procedure**:

```bash
# 1. Update password in 1Password

# 2. Verify new password is readable
op read "op://Homelab/Samba NAS User/password"

# 3. Deploy storage configuration
task storage:deploy

# 4. Test Samba access
smbclient //192.168.0.102/share -U nas
# Enter new password when prompted
```

**What Happens**:
- Samba password updated using `smbpasswd` command
- Password change takes effect immediately
- No service restart required

**Affected Hosts**: `pve-nas-01`

---

### Tailscale Auth Key

**Location**: 1Password → Homelab vault → Tailscale Auth Key

**Notes**:
- Auth keys are one-time use for initial registration
- Rotating the key in 1Password only affects NEW nodes
- Existing nodes remain authenticated with their own node keys
- To re-authenticate existing nodes, you must:
  1. `sudo tailscale down`
  2. `sudo tailscale up --auth-key=<new-key>`

**Rotation Procedure** (for adding new nodes):

```bash
# 1. Generate new auth key in Tailscale admin console
# Settings → Keys → Generate auth key
# Check: Reusable, Ephemeral (optional)

# 2. Update in 1Password

# 3. Verify new key is readable
op read "op://Homelab/Tailscale Auth Key/credential"

# 4. New nodes will automatically use new key on deployment
ansible-playbook ansible/playbooks/base.yml --tags tailscale --limit new-host
```

**Affected Hosts**: Only new hosts being provisioned

---

## Emergency Rotation (Compromised Credential)

If a credential is compromised, rotate immediately:

### Quick Rotation Checklist

```bash
# 1. Update secret in 1Password immediately
# 2. Sign in to 1Password CLI
eval $(op signin)

# 3. Deploy to all affected hosts
ansible-playbook ansible/playbooks/site.yml --limit affected-hosts

# 4. For Proxmox firewall or network-level security:
ansible-playbook ansible/playbooks/site.yml --tags firewall

# 5. Revoke old credential at source (Cloudflare, Tailscale, Gmail, etc.)
```

### SSH Key Compromise

```bash
# 1. Generate new key immediately
ssh-keygen -t ed25519 -C "eric@MacBookPro.esweiss.com" -f ~/.ssh/id_ed25519_emergency

# 2. Update in 1Password

# 3. Deploy to ALL hosts immediately
ansible-playbook ansible/playbooks/base.yml --tags ssh

# 4. Verify emergency key works
ssh -i ~/.ssh/id_ed25519_emergency eric@192.168.0.102

# 5. Remove compromised key from laptop and 1Password
# 6. Check all hosts for unauthorized access:
ansible all -m shell -a "last -20"
```

---

## Verification After Rotation

### SMTP

```bash
# Test mail relay
ssh eric@192.168.0.102 "echo 'Test after rotation' | mail -s 'Test' root"

# Check smtp-relay logs
ssh eric@192.168.0.151 "sudo tail -f /var/log/mail.log"

# Verify authentication
ssh eric@192.168.0.151 "sudo grep sasl /var/log/mail.log | tail -20"
```

### SSH

```bash
# Test all hosts
ansible all -m ping

# Check authorized_keys was updated
ansible all -m shell -a "wc -l ~/.ssh/authorized_keys"
```

### DNS/AdGuard

```bash
# Test login
curl -k https://192.168.0.150:3000

# Check sync status (if password changed)
ssh eric@192.168.0.150 "sudo systemctl status adguardhome-sync"
```

---

## Scheduled Rotation Policy

**Recommended Schedule**:

| Credential Type | Rotation Frequency | Reason |
|-----------------|-------------------|--------|
| SSH Keys | Annually | Low risk, high impact if rotated incorrectly |
| SMTP Passwords | Every 6 months | Medium risk, Gmail app passwords |
| API Tokens | Every 6 months | Medium risk, scoped permissions |
| AdGuard Password | Annually | Low exposure, local network only |
| Samba NAS Password | Every 6 months | Medium risk, network file sharing |
| Tailscale Auth Keys | Generate new for each node | One-time use |

**Automation** (future):

```bash
# Add to crontab or calendar reminder
# Every 6 months: Review and rotate SMTP and API credentials
# Every 12 months: Review and rotate SSH keys
```

---

## Troubleshooting

### "Permission denied" after rotation

**Cause**: New credential not deployed or wrong format

**Fix**:
```bash
# Verify credential in 1Password
op read "op://Homelab/Item/field"

# Re-run deployment with verbose output
ansible-playbook playbook.yml -vv

# Check specific host
ansible host -m setup
```

### Mail stops working after SMTP rotation

**Cause**: Password mismatch or postmap not run

**Fix**:
```bash
# Check sasl_passwd on smtp-relay
ssh eric@192.168.0.151 "sudo cat /etc/postfix/sasl_passwd"

# Manually rebuild hash
ssh eric@192.168.0.151 "sudo postmap /etc/postfix/sasl_passwd && sudo systemctl reload postfix"

# Check logs
ssh eric@192.168.0.151 "sudo journalctl -u postfix -n 50"
```

### Terraform fails after Cloudflare rotation

**Cause**: Old token cached or wrong permissions

**Fix**:
```bash
# Clear environment
unset CLOUDFLARE_API_TOKEN
unset CLOUDFLARE_ACCOUNT_ID

# Re-read from 1Password
eval $(op signin)

# Retry using Taskfile (preferred - handles all env vars)
task terraform:plan

# Or manually export and retry
# export CLOUDFLARE_API_TOKEN=$(op read "op://Homelab/Cloudflare DNS Token/credential")
# cd terraform/cloudflare && terraform plan
```

---

## Best Practices

1. **Test before revoking**: Always verify new credential works before revoking old one
2. **One at a time**: Rotate one credential type at a time to isolate issues
3. **Document**: Note rotation date and reason in 1Password item notes
4. **Verify**: Always run verification checks after rotation
5. **Backup**: Keep old credentials in 1Password archive for 30 days before deleting
6. **Audit logs**: Check logs after rotation for failed auth attempts

---

## Related Documentation

- [02-install.md](02-install.md) - Initial 1Password setup
- [03-ssh-users.md](03-ssh-users.md) - SSH key management
- [10-mail.md](10-mail.md) - SMTP relay configuration
