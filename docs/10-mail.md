# Mail Configuration

The homelab uses a centralized SMTP relay for outgoing mail from all hosts.

## Architecture

```
Proxmox Hosts / LXC Containers
    |
    | Postfix null client
    | localhost only
    v
smtp-relay.esweiss.com (192.168.0.151)
    |
    | SASL auth over TLS
    v
smtp.gmail.com:587
    |
    v
External Recipients
```

## SMTP Relay (smtp-relay.esweiss.com)

### Configuration

The relay server accepts mail from the internal network and forwards to Gmail.

**Key settings** (`/etc/postfix/main.cf`):

```ini
myhostname = smtp-relay.esweiss.com
mynetworks = 127.0.0.0/8                               # loopback only — every sender SASL-auths on 587
smtpd_relay_restrictions = permit_sasl_authenticated, reject_unauth_destination
relayhost = [smtp.gmail.com]:587
smtp_sasl_auth_enable = yes
smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd
smtpd_tls_cert_file = /etc/postfix/tls/fullchain.pem
smtpd_tls_key_file = /etc/postfix/tls/privkey.pem
```

### Incoming Authentication

The relay accepts mail on two paths (`smtp_relay_config` /
`smtp_relay_submission_config` in `group_vars/mail.yml`). `mynetworks` and
`smtpd_relay_restrictions` are deliberately **not** restated there, so the
collection's loopback-only + SASL-only defaults reach this relay:

- **Port 25**: refuses unauthenticated relay. `smtpd_relay_restrictions` is
  `permit_sasl_authenticated, reject_unauth_destination` and `mynetworks` is
  loopback-only, so the `permit_mynetworks` path grants nothing off-host. SASL
  AUTH is not offered on this port (cleartext would be permitted and the
  chrooted smtpd breaks it), which makes :25 effectively local-only.
- **Port 587 (submission)**: mandatory TLS (`smtpd_tls_security_level =
  encrypt`) plus SASL auth (`permit_sasl_authenticated,reject`). Null
  clients authenticate with the "SMTP Relay Auth" credentials from
  1Password.

### Gmail App Password

The relay uses a Gmail app password for authentication.

1. Enable 2FA on the Gmail account
2. Generate an app password at https://myaccount.google.com/apppasswords
3. Store in 1Password as `SMTP Relay Gmail`

### TLS Certificates

Certificates are distributed from dns-01 via the `homelab-cert-reload.sh` script.

Path: `/etc/postfix/tls/`

### Ansible Role

Deploy with: the `weisssrv.infra.smtp_relay` role (`ansible/playbooks/mail.yml`)

## Postfix Null Client (Proxmox Hosts)

Each Proxmox host runs a null client that forwards all mail to the relay.

**Key settings** (`/etc/postfix/main.cf`):

```ini
myhostname = pve-nas-01.esweiss.com
mydestination = $myhostname, localhost.localdomain, localhost
relayhost = [smtp-relay.esweiss.com]:587
inet_interfaces = loopback-only
smtp_sasl_auth_enable = yes
smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd
```

`$myhostname` must stay in `mydestination` so the `/etc/aliases`
root→admin_email rewrite applies (it only fires on locally delivered mail).
The bare domain is deliberately absent — listing it would make every null
client deliver/bounce `*@esweiss.com` locally instead of relaying it.

### Ansible Role

Deploy with: the `weisssrv.infra.postfix_null_client` role (`ansible/playbooks/site.yml`)

## Mail Aliases

Standard aliases on all hosts (`/etc/aliases`):

```
postmaster: root
nobody: root
hostmaster: root
webmaster: root
root: {{ admin_email }}   # ROOT_EMAIL_ALIAS via 1Password 'Email Config/root_alias'
```

Run `newaliases` after changes.

## Firewall Rules

The smtp-relay accepts connections only from the core cluster:

```
[group sg-smtp-relay]
IN ACCEPT -source +dc/core-cluster -p tcp -dport 587 -log nolog
IN ACCEPT -source +dc/core-cluster -p tcp -dport 25 -log nolog
```

The rendered group is owned by the collection's `proxmox_firewall` template —
see [docs/11-firewall.md](./11-firewall.md) if these ever disagree.

## Testing

### Send Test Email

```bash
# From any Proxmox host (delivered to the external root alias)
echo "Test from $(hostname)" | mail -s "Test Subject" root
```

### Check Mail Queue

```bash
# View queue
mailq

# Flush queue
postqueue -f

# Delete all queued mail
postsuper -d ALL
```

### View Logs

```bash
# On sending host
journalctl -u postfix -f

# On smtp-relay
journalctl -u postfix -f
tail -f /var/log/mail.log
```

## Troubleshooting

### Connection Refused

1. Check firewall rules on smtp-relay
2. Verify smtp-relay is listening: `ss -lntp | grep :587`
3. Check DNS resolution: `dig smtp-relay.esweiss.com`

### Authentication Failed

1. Verify sasl_passwd file exists and has correct format
2. Run `postmap /etc/postfix/sasl_passwd`
3. Check permissions: `ls -la /etc/postfix/sasl_passwd*`

### TLS Errors

1. Check certificate validity: `openssl s_client -connect smtp-relay.esweiss.com:587 -starttls smtp`
2. Verify cert files exist and are readable
3. Check certificate expiry

### Gmail Rejection

1. Verify app password is correct
2. Check for security alerts in Gmail
3. Ensure "Less secure app access" is not blocking (should not be needed with app passwords)

---

## Related documentation

- [docs/12-runbooks.md](12-runbooks.md) — SMTP relay alerts and recovery
- [docs/15-credential-rotation.md](15-credential-rotation.md) — the SMTP 1Password items
- [docs/11-firewall.md](11-firewall.md) — `sg-smtp-relay`
