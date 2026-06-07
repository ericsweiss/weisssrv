# Home Assistant Configuration Management

This Ansible role manages Home Assistant configuration with 1Password secret injection.

## Overview

Home Assistant OS cannot be managed by Ansible like other infrastructure (it's not a traditional Linux distribution). This role:
- **Generates configuration** from Jinja2 templates
- **Injects secrets** from 1Password at deployment time
- **Deploys via SSH/SCP** to the Home Assistant VM

## Directory Structure

```
ansible/roles/home_assistant/
├── tasks/
│   └── main.yml              # Deployment tasks
├── templates/
│   ├── configuration.yaml.j2 # HA configuration template
│   └── secrets.yaml.j2       # Secrets template
├── defaults/
│   └── main.yml              # Default variables
└── README.md
```

## Deployment Workflow

1. **Edit templates** in `templates/` directory
2. **Update variables** in `defaults/main.yml` or group_vars if needed
3. **Commit to Git** for version control
4. **Deploy** via `task home-assistant:deploy-config`
5. **Restart** Home Assistant to apply changes: `task home-assistant:restart-after-config`

## Quick Start

```bash
# Deploy configuration to HAOS (the ingress/IngressRoute is Flux-managed
# under kubernetes/apps/vm-ingress/home-assistant.yaml; only the HAOS
# configuration deploy uses Ansible).
task home-assistant:deploy-config

# Deploy and restart in one step
task home-assistant:deploy-config && task home-assistant:restart-after-config
```

## Host-key pinning

HAOS runs SSH on a non-standard port (22222) as `root` with no `sudo`, so
the global `accept-new` TOFU path used by the rest of the inventory isn't
appropriate here — a swapped HAOS image (rebuild, factory reset,
restore-from-backup) would silently register a new host key and the role
would still happily push secrets to it.

Instead, the host key is **pinned** in
`ansible/inventories/prod/host_vars/home.yml` as `home_assistant_host_key`
and the role:

1. Asserts the pin is non-empty (fails fast with the capture command if not).
2. Writes the pin into the operator's `~/.ssh/known_hosts` for
   `[192.168.0.154]:22222` via the `ansible.builtin.known_hosts` module.
3. Runs every `scp` / `ssh` to HAOS with `StrictHostKeyChecking=yes` so a
   key change blows up loudly instead of being silently accepted.

### Capturing the pin (first deploy)

```bash
ssh-keyscan -t ed25519 -p 22222 192.168.0.154
```

Paste the algorithm + base64 key portion (the part **after**
`[192.168.0.154]:22222`) into `host_vars/home.yml`:

```yaml
home_assistant_host_key: "ssh-ed25519 AAAAC3Nza...example..."
```

Commit and run the deploy.

### Rotating the pin after a HAOS rebuild

If the HAOS VM is reinstalled, restored from a snapshot taken before the
SSH add-on was configured, or otherwise re-keys, the role's pre-flight
SSH calls will fail with a host-key mismatch. To recover:

1. Remove any stale entry from your local known_hosts (the `known_hosts`
   module won't overwrite an existing key with a different value cleanly):
   ```bash
   ssh-keygen -R "[192.168.0.154]:22222"
   ```
2. Re-capture and update the pin in `host_vars/home.yml`:
   ```bash
   ssh-keyscan -t ed25519 -p 22222 192.168.0.154
   ```
3. Commit the new pin (the change should be reviewable — a HAOS rebuild
   is a real event that warrants a deliberate update, which is the whole
   point of pinning).
4. Re-run `task home-assistant:deploy-config`.

## Required 1Password Items

In vault "Homelab":
- **SMTP Relay Auth** - username, password (for SMTP notifications)
- **Email Config** - root_alias (admin email for notifications)
- **Home Assistant SSO** - authentik-client-id, authentik-client-secret (for OIDC login via Authentik)

These are automatically injected via Taskfile environment variables resolved by `op run`.

## Configuration Files

### configuration.yaml.j2

Core Home Assistant configuration including:
- HTTP reverse proxy settings (for Traefik)
- Trusted proxy networks (LAN, k3s)
- SMTP notification platform
- Default integrations

Variables used:
- `home_assistant_trusted_proxies` - List of trusted proxy CIDRs
- `home_assistant_smtp_host` - SMTP server hostname
- `home_assistant_smtp_port` - SMTP server port
- `home_assistant_smtp_encryption` - Encryption method (starttls)
- `home_assistant_smtp_sender` - Email sender address
- `home_assistant_smtp_sender_name` - Sender display name

### secrets.yaml.j2

Sensitive values injected from 1Password via environment variables:
- `smtp_username` - SMTP relay username (from `SMTP_RELAY_USER` env var)
- `smtp_password` - SMTP relay password (from `SMTP_RELAY_PASSWORD` env var)
- `admin_email` - Admin email for notifications (from `ADMIN_EMAIL` env var)
- `oidc_client_id` - Authentik OIDC client ID (from `HA_OIDC_CLIENT_ID` env var)
- `oidc_client_secret` - Authentik OIDC client secret (from `HA_OIDC_CLIENT_SECRET` env var)

These environment variables are automatically populated by `op run` when the task is executed.

## Manual Configuration

Some configuration must be done manually via Home Assistant UI:

1. **SSH Add-on** - Install and configure (Settings > Add-ons)
   - Port: 22222
   - Required for Ansible deployment
2. **Integrations** - Add device integrations as needed
3. **Automations** - Create via UI or edit `automations.yaml`
4. **Backups** - Configure automatic backups (Settings > System > Backups)

## Backup Strategy

Before deploying configuration changes:

```bash
# Create backup via task
task home-assistant:snapshot NAME=pre-config-update DESC="Before config deployment"
```

Or manually via UI: Settings > System > Backups > Create Backup

## How It Works

The Ansible role:

1. **Generates configuration** locally from Jinja2 templates
2. **Injects secrets** from 1Password via environment variables (resolved by `op run` at runtime, accessed via `lookup('ansible.builtin.env', ...)`)
3. **Deploys via SCP** to Home Assistant VM at 192.168.0.154:22222
4. **Validates configuration** using `ha core check`
5. **Cleans up** temporary files

All operations run on localhost (the playbook targets localhost) since Home Assistant OS doesn't support native Ansible execution.

## Troubleshooting

**Configuration errors:**
1. Check Home Assistant logs: Settings > System > Logs
2. Validate YAML syntax locally before deployment
3. Configuration is automatically validated during deployment
4. Restore from backup if needed

**SSH connection fails:**
- Ensure SSH add-on is running
- Verify port 22222 is accessible
- Check firewall rules
- Test manually: `ssh -p 22222 root@192.168.0.154`

**Secrets not injecting:**
- Verify 1Password CLI is authenticated: `op whoami`
- Check Taskfile.yml home-assistant:deploy-config task has correct env vars
- Secrets are resolved by `op run` and passed as environment variables to Ansible

**Deployment fails:**
- Check SSH connectivity first
- Ensure Home Assistant is accessible at 192.168.0.154
- Review Ansible output for specific errors
- Validate templates locally: `ansible-playbook --syntax-check ansible/playbooks/home-assistant.yml`

## Variables

Key variables in `defaults/main.yml`:

```yaml
home_assistant_host: "192.168.0.154"
home_assistant_ssh_port: 22222
home_assistant_config_path: "/config"
home_assistant_smtp_host: "smtp-relay.esweiss.com"
home_assistant_trusted_proxies:
  - "192.168.0.0/24"      # Local LAN
  - "10.42.0.0/16"        # k3s pod network
  - "10.43.0.0/16"        # k3s service network
home_assistant_oidc_client_id: "{{ lookup('ansible.builtin.env', 'HA_OIDC_CLIENT_ID') }}"
home_assistant_oidc_client_secret: "{{ lookup('ansible.builtin.env', 'HA_OIDC_CLIENT_SECRET') }}"
```

Override in group_vars or host_vars as needed.
