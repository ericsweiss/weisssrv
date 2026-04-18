# SMTP Relay Role

Configures Postfix as the central SMTP relay. Accepts authenticated submissions from null clients (Proxmox hosts, DNS servers, k3s nodes) and relays outbound to Gmail via SASL.

## What This Role Manages

### Postfix Configuration
- Relay host configuration (smtp.gmail.com:587)
- Outbound SASL authentication to Gmail
- Inbound SASL authentication from null clients
- TLS encryption for all connections
- Submission service on port 587
- Mail aliases (root → admin email)

### Authentication
- Gmail app password for outbound relay (from 1Password)
- SASL database for incoming connections (from 1Password)
- Credential validation at deployment time
- Secure permissions on all credential files

### TLS Certificates
- Certificate directory creation
- Receives certificates from dns-01 via SSH distribution
- TLS-enabled submission service

## Configuration

### Required Variables

```yaml
# Admin email (receives root mail)
admin_email: "{{ lookup('ansible.builtin.env', 'ROOT_EMAIL_ALIAS', default='root@localhost') }}"

# SMTP relay configuration
smtp_relay_config:
  myhostname: "smtp-relay.{{ internal_domain }}"
  myorigin: "{{ internal_domain }}"
  relayhost: "[smtp.gmail.com]:587"
  smtp_sasl_auth_enable: true
  smtp_tls_security_level: "encrypt"
  smtpd_tls_security_level: "may"

# TLS certificates
smtp_tls_cert_dir: "/etc/postfix/certs"
smtp_tls_cert_file: "{{ smtp_tls_cert_dir }}/fullchain.pem"
smtp_tls_key_file: "{{ smtp_tls_cert_dir }}/privkey.pem"
```

### 1Password Secrets

```yaml
secrets:
  # Outbound authentication (to Gmail)
  smtp_gmail_user: "op://Homelab/SMTP Relay Gmail/username"
  smtp_gmail_password: "op://Homelab/SMTP Relay Gmail/password"

  # Inbound authentication (from null clients)
  smtp_relay_user: "op://Homelab/SMTP Relay Auth/username"
  smtp_relay_password: "op://Homelab/SMTP Relay Auth/password"

  # Certificate distribution
  dns01_ssh_public_key: "op://Homelab/DNS-01 SSH Key/public key"
```

## Deployment

```bash
# Deploy SMTP relay
ansible-playbook ansible/playbooks/site.yml --limit mail

# Full stack deployment
task infra:deploy
```

## Architecture

```
Null Clients (Proxmox, DNS, k3s)
  │
  ├─ Authentication: SASL (relay_user / relay_password)
  ├─ Connection: TLS on port 587
  │
  └─> smtp-relay (192.168.0.151)
        ├─ Postfix relay
        ├─ Authenticates to Gmail
        └─> Gmail SMTP (smtp.gmail.com:587)
              └─> External delivery
```

## Task Flow

```
1. Validate Gmail credentials are set (assert)
2. Validate relay auth credentials are set (assert)
3. Deploy dns-01 certificate distribution key
4. Install Postfix and dependencies
   ├─ postfix
   ├─ libsasl2-modules
   ├─ sasl2-bin
   └─ mailutils
5. Configure /etc/mailname
6. Create TLS certificate directory
7. Deploy main.cf (relay configuration)
8. Deploy master.cf (submission service)
9. Deploy SASL password file (Gmail credentials)
10. Postmap SASL password file
11. Create SASL configuration directory
12. Deploy smtpd.conf (inbound auth config)
13. Check if SASL user exists
14. Create/update relay user in SASL database
15. Set permissions on /etc/sasldb2 (root:postfix 0640)
16. Configure mail aliases
17. Run newaliases
18. Ensure Postfix is enabled and running
```

## Files

- `tasks/main.yml` - Main task orchestration
- `templates/main.cf.j2` - Postfix main configuration
- `templates/master.cf.j2` - Postfix master configuration (submission service)
- `templates/sasl_passwd.j2` - Gmail credentials
- `handlers/main.yml` - Postfix reload handlers

## Dependencies

- `acme_certs` role (provides TLS certificates)

## Security

- SASL credentials stored with mode 0600
- SASL database (/etc/sasldb2) owned by root:postfix with mode 0640
- All credential operations use `no_log: true`
- Credentials from 1Password (never in git)
- TLS encryption enforced for all connections
- Authentication required for all submissions

## Gmail App Password Setup

This relay requires a Gmail app password:

1. Go to https://myaccount.google.com/apppasswords
2. Generate an app password
3. Store in 1Password:
   - Item: "SMTP Relay Gmail"
   - Username: `ericsweiss1@gmail.com`
   - Password: (app password from Google)

## Null Client Configuration

Null clients connect with SASL authentication:

```yaml
# In postfix_null_client role variables
postfix_config:
  relayhost: "[smtp-relay.esweiss.com]:587"
  smtp_sasl_auth_enable: true
  smtp_sasl_password_maps: "hash:/etc/postfix/sasl_passwd"
  smtp_sasl_security_options: "noanonymous"
  smtp_tls_security_level: "encrypt"
```

SASL credentials on null clients:

```
# /etc/postfix/sasl_passwd
[smtp-relay.esweiss.com]:587  relay_user:relay_password
```

## Testing

### Test from SMTP Relay

```bash
# Test local delivery
echo "Test from smtp-relay" | mail -s "Test Subject" root

# Check mail queue
mailq

# View logs
tail -f /var/log/mail.log

# Test relay to Gmail
echo "External test" | mail -s "External Test" ericsweiss1@gmail.com
```

### Test from Null Client

```bash
# On pve-nas-01 or other null client:
echo "Test from $(hostname)" | mail -s "Test from host" root

# Check if mail was relayed
mailq

# Verify SASL auth worked
grep "sasl_username=" /var/log/mail.log
```

### Verify TLS

```bash
# Check certificate
openssl s_client -connect localhost:587 -starttls smtp

# View cert details
openssl x509 -in /etc/postfix/certs/fullchain.pem -noout -text
```

## Troubleshooting

**Gmail authentication fails:**
```bash
# Check credentials
sudo cat /etc/postfix/sasl_passwd

# View postmap database
sudo postmap -q "[smtp.gmail.com]:587" /etc/postfix/sasl_passwd.db

# Check Gmail logs
grep "gmail" /var/log/mail.log
```

**Null client authentication fails:**
```bash
# List SASL users
sudo sasldblistusers2

# Check SASL database permissions
ls -la /etc/sasldb2  # Should be root:postfix 0640

# View auth attempts
grep "authentication failed" /var/log/mail.log
```

**TLS issues:**
```bash
# Check cert files exist
ls -la /etc/postfix/certs/

# Verify cert ownership
# fullchain.pem should be readable by postfix
# privkey.pem should be readable by postfix group

# Test STARTTLS
telnet localhost 587
EHLO test
STARTTLS
```

**Mail stuck in queue:**
```bash
# View queue
mailq
postqueue -p

# Flush queue
postqueue -f

# View message details
postcat -q <QUEUE_ID>

# Delete message
postsuper -d <QUEUE_ID>
```

## Operational Notes

### Monitoring

```bash
# Watch mail logs in real-time
tail -f /var/log/mail.log

# Count messages in queue
mailq | tail -n 1

# View Postfix status
systemctl status postfix
```

### Gmail Rate Limits

Gmail imposes rate limits on app passwords:
- **500 messages per day** (for free accounts)
- **2000 messages per day** (for Workspace accounts)

This is sufficient for system mail from a homelab.

### Credential Rotation

To rotate credentials:

1. **Gmail password:**
   - Generate new app password
   - Update 1Password
   - Run `task infra:deploy`

2. **Relay auth password:**
   - Update 1Password
   - Run `task infra:deploy` (updates relay and all null clients)

