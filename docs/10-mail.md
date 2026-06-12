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
mynetworks = 127.0.0.0/8,192.168.0.0/24,10.42.0.0/16   # 10.42/16 = k3s pod CIDR (Alertmanager relays from in-cluster)
relayhost = [smtp.gmail.com]:587
smtp_sasl_auth_enable = yes
smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd
smtpd_tls_cert_file = /etc/postfix/tls/fullchain.pem
smtpd_tls_key_file = /etc/postfix/tls/privkey.pem
```

### Incoming Authentication

The relay accepts mail on two paths (`smtp_relay_config` /
`smtp_submission_config` in `group_vars/mail.yml`):

- **Port 25**: `permit_mynetworks` only — SASL AUTH is disabled on this
  port (cleartext would be permitted and the chrooted smtpd breaks it).
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

Deploy with: `ansible/roles/smtp_relay`

## Postfix Null Client (Proxmox Hosts)

Each Proxmox host runs a null client that forwards all mail to the relay.

**Key settings** (`/etc/postfix/main.cf`):

```ini
myhostname = pve-nas-01.esweiss.com
mydestination =
relayhost = [smtp-relay.esweiss.com]:587
inet_interfaces = loopback-only
smtp_sasl_auth_enable = yes
smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd
```

### Ansible Role

Deploy with: `ansible/roles/postfix_null_client`

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
```

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
