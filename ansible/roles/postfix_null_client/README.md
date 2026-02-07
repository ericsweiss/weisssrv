# Postfix Null Client Role

Configures Postfix as a null client (satellite system) that forwards all local mail to the central SMTP relay. System mail from cron, alerts, etc. gets relayed to Gmail.

## What This Role Manages

### Postfix Configuration
- Null client configuration (no local delivery)
- SASL authentication to smtp-relay
- Hostname configuration (/etc/mailname)
- Virtual aliases for address rewriting
- Mail aliases for local users
- Postfix main.cf deployment

### Authentication
- SASL credentials for smtp-relay (from 1Password)
- Hashed password file (postmap)
- Secure permissions (mode 0600)

## Configuration

### Required Variables

```yaml
# SMTP relay settings (from group_vars/all.yml)
smtp_relay_host: smtp-relay.esweiss.com
smtp_relay_port: 587

# Postfix configuration
postfix_config:
  myhostname: "{{ inventory_hostname }}.{{ internal_domain }}"
  myorigin: "{{ internal_domain }}"
  relayhost: "[{{ smtp_relay_host }}]:{{ smtp_relay_port }}"
  smtp_sasl_auth_enable: true
  smtp_sasl_password_maps: "hash:/etc/postfix/sasl_passwd"
  smtp_sasl_security_options: "noanonymous"

# Virtual aliases (optional)
postfix_virtual_aliases:
  root: "{{ admin_email }}"

# Mail aliases
postfix_mail_aliases:
  root: "{{ admin_email }}"
```

### 1Password Secrets

```yaml
secrets:
  smtp_relay_user: "op://Homelab/SMTP Relay Auth/username"
  smtp_relay_password: "op://Homelab/SMTP Relay Auth/password"
  root_email_alias: "op://Homelab/Email Config/root_alias"
```

## Deployment

```bash
# Deploy to Proxmox hosts
ansible-playbook ansible/playbooks/site.yml --tags mail

# Deploy to DNS servers
ansible-playbook ansible/playbooks/dns.yml --tags mail
```

## Architecture

```
Managed Hosts (null clients)
├─ pve-nas-01
├─ pve-opt-03
├─ dns-01
├─ dns-02
└─ k3s nodes
     │
     └─> smtp-relay (192.168.0.151)
          └─> Gmail SMTP (via SASL)
               └─> ericsweiss1@gmail.com
```

## Files

- `tasks/main.yml` - Main task orchestration
- `templates/main.cf.j2` - Postfix main configuration
- `templates/sasl_passwd.j2` - SASL credentials
- `templates/virtual.j2` - Virtual alias table
- `templates/aliases.j2` - Mail aliases
- `handlers/main.yml` - Postfix reload handlers

## Dependencies

- SMTP relay must be running and accessible

## Security

- SASL credentials stored with mode 0600
- Credentials from 1Password (never in git)
- No local delivery (everything forwarded)
- All operations use `no_log: true` for secrets

## Testing

```bash
# Test mail delivery
echo "Test from $(hostname)" | mail -s "Test Subject" root

# Check mail queue
mailq

# Check logs
tail -f /var/log/mail.log
```
