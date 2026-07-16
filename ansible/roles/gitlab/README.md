# GitLab Role

Installs and configures GitLab EE (using CE features) via Omnibus package.

## Features

- GitLab EE on Debian 13 (Trixie)
- Authentik SAML SSO integration
- Container Registry (registry.git.ericsweiss.com)
- GitLab Pages (*.pages.git.ericsweiss.com)
- SMTP via internal relay (smtp-relay.esweiss.com)
- Traefik reverse proxy integration (TLS terminated on GitLab nginx via the acme-distributed cert)
- Git SSH on port 22 (external access via port 2222)

## Requirements

- Debian 13 (Trixie)
- 6 vCPUs, 12GB RAM minimum
- 100GB disk space
- Network access to smtp-relay.esweiss.com:587
- Authentik SAML provider configured (for SSO)

## 1Password Items Required

| Item | Fields |
|------|--------|
| **GitLab** | `root-password` |
| **GitLab SSO** | `saml-cert-fingerprint` |
| **GitLab API Token** | `credential` (admin PAT, `api` scope — drives the Web IDE Application Settings block) |
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
Internet → Cloudflare → Traefik (k3s) → GitLab VM (nginx terminates TLS)
                                         ├─ Web UI  (:443,  HTTPS)
                                         ├─ Registry (:5050, HTTPS)
                                         └─ Pages   (:8443, HTTPS)

Git SSH: External port 2222 → GitLab VM port 22
```

GitLab's nginx terminates TLS on 443/5050/8443 using the wildcard cert
distributed to `/etc/gitlab/ssl` by acme_certs, so the Traefik->GitLab hop is
HTTPS rather than plain :80.

## Services

| Service | Internal Port | External URL |
|---------|---------------|--------------|
| Web UI | 443 (HTTPS) | https://git.ericsweiss.com |
| Registry | 5050 (HTTPS) | https://registry.git.ericsweiss.com |
| Pages | 8443 (HTTPS) | https://*.pages.git.ericsweiss.com |
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

GitLab backups run daily at 2:00 AM. The cron entry calls
`/usr/local/sbin/gitlab-backup-run.sh` (deployed by this role), which runs
`gitlab-backup create CRON=1 SKIP=registry,artifacts` (the skip list is
`gitlab_backup_skip`, default `registry,artifacts` — container images and CI
artifacts are rebuildable and were the root cause of backup disk bloat; a
restore therefore does not bring them back, see docs/27), copies
`gitlab-secrets.json` + `gitlab.rb` into
the backup path on success, and emits node_exporter textfile metrics
(`gitlab_backup_last_run_success`, `..._last_run_duration_seconds`,
`..._last_success_timestamp_seconds` preserved across failures, and
`..._last_size_bytes`). The `GitLabBackupFailed`/`GitLabBackupStale`
PrometheusRules consume these.

For the metric to be scraped, `node_exporter_host` must run on the GitLab VM —
the `gitlab_servers` group is included in the node_exporter play in
`ansible/playbooks/site.yml`, and the VM (192.168.0.153) is scraped
via the shared `node-exporter-host` Service/Endpoints (one Endpoints object
listing all scraped hosts) in
`kubernetes/infrastructure/observability/exporters/node-exporter-host.yaml`.

```bash
# Manual backup (runs the same wrapper as the daily cron:
# SKIP=registry,artifacts, secrets copy, and freshness metric)
ssh gitlab "sudo /usr/local/sbin/gitlab-backup-run.sh"

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
- `apt_signed_repo` role (fingerprint-verified GitLab EE apt repo)
