# GitLab Role

Installs and configures GitLab EE (using CE features) via Omnibus package.

## Features

- GitLab EE on Debian 12/13 (Bookworm/Trixie)
- Authentik SAML SSO integration
- Container Registry (registry.git.ericsweiss.com)
- GitLab Pages (*.pages.git.ericsweiss.com)
- SMTP via internal relay (smtp-relay.esweiss.com)
- Traefik reverse proxy integration (no local TLS)
- Git SSH on port 22 (external access via port 2222)

## Requirements

- Debian 12+ (Trixie recommended)
- 6 vCPUs, 12GB RAM minimum
- 100GB disk space
- Network access to smtp-relay.esweiss.com:587
- Authentik SAML provider configured (for SSO)

## 1Password Items Required

| Item | Fields |
|------|--------|
| **GitLab** | `root-password` |
| **GitLab SSO** | `saml-cert-fingerprint` |
| **SMTP Relay Auth** | `username`, `password` |

## Usage

```bash
# Full deployment (VM provisioning + GitLab installation)
task gitlab:deploy

# Check mode (dry-run)
task gitlab:deploy-check
```

## Architecture

```
Internet → Cloudflare → Traefik (k3s) → GitLab VM
                                         ├─ Web UI (:80)
                                         ├─ Registry (:5050)
                                         └─ Pages (:8090)

Git SSH: External port 2222 → GitLab VM port 22
```

## Services

| Service | Internal Port | External URL |
|---------|---------------|--------------|
| Web UI | 80 | https://git.esweiss.com |
| Registry | 5050 | https://registry.git.ericsweiss.com |
| Pages | 8090 | https://*.pages.git.ericsweiss.com |
| SSH | 22 | git@git.ericsweiss.com:2222 |

## Configuration

Key variables in `defaults/main.yml`:

```yaml
gitlab_external_url: "https://git.ericsweiss.com"
gitlab_registry_enabled: true
gitlab_pages_enabled: true
gitlab_saml_enabled: true
gitlab_smtp_enabled: true
```

## Backup

GitLab backups run daily at 2:00 AM:

```bash
# Manual backup
ssh gitlab "sudo gitlab-backup create"

# List backups
ssh gitlab "sudo ls -la /var/opt/gitlab/backups/"
```

## Troubleshooting

```bash
# Check GitLab status
ssh gitlab "sudo gitlab-ctl status"

# View logs
ssh gitlab "sudo gitlab-ctl tail"

# Reconfigure after changes
ssh gitlab "sudo gitlab-ctl reconfigure"
```

## Dependencies

- `base` role (SSH, packages, users)
- `postfix_null_client` role (local mail relay)
