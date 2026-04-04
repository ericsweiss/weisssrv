# CI/CD Pipelines

This document covers the GitLab CI/CD pipeline for automated testing, validation, and deployment.

## Overview

The repository uses **self-hosted GitLab** as the canonical source with CI/CD:

- **Canonical source**: https://git.ericsweiss.com/eric/weisssrv
- **GitHub mirror**: https://github.com/ericsweiss/weisssrv (read-only, auto-synced)

CI/CD features:
- **Linting**: Ansible playbooks, Terraform code, shell scripts, Kubernetes manifests
- **Validation**: Terraform plan, Helm template validation, kubeconform
- **Testing**: Molecule unit tests, multi-role integration tests
- **Security**: GitLab native secret detection
- **Auto-deployment**: Deploys to production on merge to main (after tests pass)
- **Version checking**: Scheduled checks for available updates

## Runner Architecture

Two GitLab Runners are deployed on the k3s cluster:

| Runner | Helm Release | Tag | Privileged | Run Untagged | Purpose |
|--------|-------------|-----|------------|--------------|---------|
| **Infrastructure** | `gitlab-runner-privileged` | `infrastructure` | Yes | No | All weisssrv CI/CD (lint, test, deploy) |
| **Shared** | `gitlab-runner` | `k8s-deploy` | No | Yes | Other GitLab projects and collaborators |

The weisssrv `.gitlab-ci.yml` sets `default: tags: [infrastructure]` so all jobs route to the
infrastructure runner. The infrastructure runner has privileged mode enabled for Docker-in-Docker
(Molecule tests) and SSH access for Ansible deployments.

The shared runner is unprivileged and intended for other GitLab projects that need to deploy to
k3s. It picks up untagged jobs from any project.

## Pipeline Structure

```yaml
stages:
  - lint          # Code quality checks
  - ai-review     # AI-powered code review (MRs only)
  - validate      # Schema and configuration validation
  - test          # Unit and integration tests
  - security      # Secret detection scanning
  - gate          # Validation gate (blocks deploys until all checks pass)
  - deploy        # Auto-deploy on merge to main
  - maintenance   # Manual approval required (reboots, HA changes)
```

### Jobs by Stage

#### Lint Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `version-check` | All MRs/pushes (soft-fail), schedule, web manual | Check for available updates |
| `shellcheck` | scripts/** | Shell script linting |
| `yaml-lint` | ansible/**, kubernetes/**, .gitlab-ci.yml | YAML syntax validation |
| `ansible-lint` | ansible/** | Ansible best practices |
| `terraform-fmt` | terraform/** | Terraform formatting |

#### AI-Review Stage (MRs Only)
| Job | Triggers | Description |
|-----|----------|-------------|
| `pr-agent-review` | All MRs (soft-fail, requires `OP_SERVICE_ACCOUNT_TOKEN`) | AI code review via PR-Agent/Qodo Merge |

#### Validate Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `terraform-validate` | terraform/** | Terraform syntax |
| `terraform-plan` | terraform/** + 1Password | Full plan with credentials |
| `kubeconform` | kubernetes/**, ansible/inventories/prod/group_vars/all.yml | K8s manifest validation |
| `helm-validate` | kubernetes/**, ansible/inventories/prod/group_vars/all.yml | Helm values validation |

#### Test Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `molecule-tests` | ansible/roles/** | Role unit tests (requires Docker-in-Docker) |
| `integration-tests` | ansible/integration-tests/**, ansible/roles/**, ansible/inventories/** | Multi-role tests (requires Docker-in-Docker) |

> **Note:** Test jobs require Docker-in-Docker and a runner with `privileged = true`.
> All weisssrv jobs (including tests) run on the **infrastructure runner**
> (`gitlab-runner-privileged` Helm release, tag: `infrastructure`) which has `privileged = true`.
> Deploy with `task gitlab:deploy-runner-privileged`.

#### Security Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `secret_detection` | All MRs/pushes | Scan for leaked secrets |

#### Gate Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `validation-gate` | Pushes to main only (not schedule, web, or MR) | Blocks all deploy jobs until all *applicable* checks pass |

The `validation-gate` job lists every lint, validate, test, and security job as an `optional: true`
`needs` dependency. The `optional: true` flag means:

- If a check job was **not created** (its path filter didn't match the changeset): the dependency
  is silently skipped and does not block the gate.
- If a check job **was created** (paths matched) and **failed**: the gate is blocked, which in turn
  blocks all deploy jobs that depend on it.

This enforces the guarantee that deployments only proceed when all *applicable* quality checks pass,
while path-filtered jobs that aren't relevant to the current changeset don't prevent deployment.

Most deploy jobs depend on the gate directly (non-optional `needs`), including all Terraform
and Ansible deploy jobs, `deploy-k3s-platform`, `deploy-k3s-authentik`, `deploy-downloads`,
`deploy-recipes`, `deploy-gitlab-ingress`, `deploy-verify`, and others. A few IngressRoute
and runner jobs rely only on optional dependencies to other gated deploy jobs for ordering.

#### Deploy Stage - Terraform
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-terraform` | terraform/** on main | Apply Cloudflare DNS |

#### Deploy Stage - Ansible Infrastructure
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-ansible-base` | ansible/roles/base/**, ansible/playbooks/base.yml | Deploy base packages, SSH, users |
| `deploy-ansible-proxmox` | ansible/roles/qol/**, ansible/roles/tailscale/**, ansible/roles/postfix_null_client/** | Deploy Proxmox host config (qol, tailscale, mail) |
| `deploy-ansible-firewall` | ansible/roles/proxmox_firewall/** | Deploy Proxmox firewall rules |
| `deploy-ansible-dns` | ansible/roles/unbound/**, ansible/roles/adguard_home/**, ansible/roles/adguard_sync/**, ansible/roles/postfix_null_client/**, ansible/playbooks/dns.yml | Deploy DNS stack (includes postfix) |
| `deploy-ansible-storage` | ansible/roles/nas_storage/**, ansible/playbooks/storage.yml | Deploy storage services |
| `deploy-ansible-mail` | ansible/roles/smtp_relay/**, ansible/roles/postfix_null_client/** | Deploy SMTP relay |
| `deploy-ansible-certs` | ansible/roles/acme_certs/** | Deploy certificate distribution |

#### Deploy Stage - Ansible Applications
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-plex` | ansible/roles/plex/**, ansible/roles/proxmox_lxc/**, ansible/playbooks/plex.yml | Deploy Plex LXC container |
| `deploy-gitlab` | ansible/roles/gitlab/**, ansible/playbooks/gitlab.yml | Deploy GitLab VM and application |
| `deploy-home-assistant-config` | ansible/roles/home_assistant/**, ansible/playbooks/home-assistant.yml | Deploy Home Assistant configuration |

#### Deploy Stage - K3s Platform
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-k3s-platform` | kubernetes/bootstrap/**, kubernetes/apps/traefik/**, kubernetes/apps/cert-manager/**, kubernetes/apps/external-dns/**, kubernetes/apps/cloudflare-ddns/**, kubernetes/apps/ingress-routes/middleware.yaml, kubernetes/apps/ingress-routes/router.yaml, kubernetes/apps/ingress-routes/services.yaml, ansible/inventories/prod/group_vars/all.yml | Deploy MetalLB, Traefik, cert-manager, external-dns, DDNS, base services |

#### Deploy Stage - K3s Applications
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-k3s-authentik` | kubernetes/apps/authentik/**, ansible/inventories/prod/group_vars/all.yml | Deploy Authentik SSO |
| `deploy-downloads` | kubernetes/apps/download-clients/**, ansible/inventories/prod/group_vars/all.yml | Deploy Gluetun, *arr apps, NZBGet, qBittorrent |
| `deploy-recipes` | kubernetes/apps/recipes/**, ansible/inventories/prod/group_vars/all.yml | Deploy Mealie, Bar Assistant |

#### Deploy Stage - K3s IngressRoutes and Runners
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-gitlab-ingress` | kubernetes/apps/gitlab/** | Deploy GitLab IngressRoutes |
| `deploy-home-assistant-ingress` | kubernetes/apps/ingress-routes/home-assistant.yaml | Deploy Home Assistant IngressRoutes |
| `deploy-plex-ingress` | kubernetes/apps/ingress-routes/plex.yaml | Deploy Plex IngressRoutes |
| `deploy-adguard-ingress` | kubernetes/apps/ingress-routes/adguard-home.yaml | Deploy AdGuard Home IngressRoutes |
| `deploy-gitlab-runner` | kubernetes/apps/gitlab-runner/**, ansible/inventories/prod/group_vars/all.yml | Deploy shared GitLab Runner (unprivileged, for other projects) |
| `deploy-gitlab-runner-privileged` | kubernetes/apps/gitlab-runner-privileged/**, ansible/inventories/prod/group_vars/all.yml | Deploy infrastructure GitLab Runner (privileged, for weisssrv CI) |

#### Deploy Stage - Verification
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-gitlab-verify` | ansible/roles/gitlab/**, kubernetes/apps/gitlab/**, kubernetes/apps/gitlab-runner/**, kubernetes/apps/gitlab-runner-privileged/** | GitLab smoke tests (health, registry, SSH) |
| `deploy-verify` | All pushes to main (no path filter) | Post-deployment health check |

> **Note:** Both verification jobs have `allow_failure: true` -- they are informational and non-blocking.
> A verification failure reports status in the pipeline UI but does not fail the overall pipeline.

#### Maintenance Stage (Manual Only)
| Job | Description |
|-----|-------------|
| `maintenance-k3s-provision` | Provision k3s VMs and deploy cluster |
| `maintenance-update-full` | Full system update (may reboot) |
| `maintenance-update-k3s-nodes` | K3s node rolling update (drain/cordon) |
| `maintenance-proxmox-ha` | Proxmox HA configuration |
| `maintenance-home-assistant-restart` | Restart Home Assistant after config deployment |

## Pipeline Triggers

| Trigger | Runs |
|---------|------|
| Merge request | Lint, AI review, validate, test, security stages (no deploy) |
| Push to main | Full validation + auto-deploy |
| Scheduled | Version checking and secret detection only. All other jobs (lint, validate, test, gate, deploy, maintenance) are explicitly excluded via `when: never` rules. |
| Manual (web) | Lint, validate, test, and security stages. Deploy, gate, and maintenance jobs are explicitly excluded via `when: never` rules. Use push-to-main for deployments. |

## Deployment Pipeline

### Auto-Deploy Behavior

When a merge request is merged to `main`:

1. **Validation stages run first** (lint, validate, test, security)
2. **Validation gate blocks deploys** -- the `validation-gate` job in the `gate` stage must pass before any deploy job can start. The gate lists all lint, validate, test, and security jobs as `optional: true` dependencies: jobs that were not created (path filter didn't match) are skipped, but any job that *was* created must succeed or all deployments are blocked.
3. **Only changed components deploy** (path-based triggers)
4. **Service restarts are automatic** (AdGuard, Postfix, etc.)
5. **Machine reboots require manual approval** (maintenance stage)

### Deployment Categories

| Category | Jobs | Auto-Deploy | Manual Approval |
|----------|------|-------------|-----------------|
| Terraform | `deploy-terraform` | Yes | No |
| Ansible Infrastructure | `deploy-ansible-base`, `deploy-ansible-proxmox`, `deploy-ansible-firewall`, `deploy-ansible-dns`, `deploy-ansible-storage`, `deploy-ansible-mail`, `deploy-ansible-certs` | Yes | No |
| Ansible Applications | `deploy-plex`, `deploy-gitlab`, `deploy-home-assistant-config` | Yes | No |
| K3s Platform | `deploy-k3s-platform` | Yes | No |
| K3s Applications | `deploy-k3s-authentik`, `deploy-downloads`, `deploy-recipes` | Yes | No |
| K3s IngressRoutes & Runners | `deploy-gitlab-ingress`, `deploy-home-assistant-ingress`, `deploy-plex-ingress`, `deploy-adguard-ingress`, `deploy-gitlab-runner`, `deploy-gitlab-runner-privileged` | Yes | No |
| Verification | `deploy-gitlab-verify`, `deploy-verify` | Yes | No |
| K3s Provisioning | `maintenance-k3s-provision` | No | **Yes** |
| System Updates | `maintenance-update-full` | No | **Yes** |
| K3s Node Updates | `maintenance-update-k3s-nodes` | No | **Yes** |
| Proxmox HA | `maintenance-proxmox-ha` | No | **Yes** |
| Home Assistant Restart | `maintenance-home-assistant-restart` | No | **Yes** |

### How Deployment Works

**Ansible deployments** use SSH to target hosts:
- SSH key fetched from 1Password at runtime
- Known hosts added automatically for all infrastructure IPs
- Runs `op run -- ansible-playbook` to inject secrets

**K3s platform deployments** (`deploy-k3s-platform`) use kubectl/helm:
- Kubeconfig fetched from 1Password at runtime
- Helm repos added and charts deployed with pinned versions from `all.yml`
- Cloudflare API token injected via `--set` for Traefik and external-dns
- Deploys in order: MetalLB → cert-manager → Traefik → external-dns → DDNS → IngressRoutes

**K3s application deployments** (`deploy-k3s-authentik`, `deploy-downloads`, `deploy-recipes`):
- Secrets fetched from 1Password and created as Kubernetes secrets
- Container image versions read from `all.yml` and substituted via `envsubst`
- VPN credentials injected for Gluetun sidecar in downloads namespace
- SSO credentials injected for Mealie and Bar Assistant

**Terraform deployments**:
- Plan saved as artifact during validate stage
- Apply uses saved plan on merge to main
- Credentials fetched from 1Password

**Version pinning**: All Helm chart versions and container image tags are centralized in `ansible/inventories/prod/group_vars/all.yml`. CI jobs extract these versions using `yq` and inject them during deployment.

## GitLab CI/CD Variables

### Required Variables

Configure in **Settings > CI/CD > Variables**:

| Variable | Type | Protected | Masked | Description |
|----------|------|-----------|--------|-------------|
| `OP_SERVICE_ACCOUNT_TOKEN` | Variable | Yes | Yes | 1Password service account token |

### Optional Variables

| Variable | Type | Protected | Masked | Description |
|----------|------|-----------|--------|-------------|
| `GITHUB_TOKEN` | Variable | No | Yes | GitHub API token for version checker rate limits |

## 1Password Service Account Setup

The pipeline uses a 1Password Service Account to fetch secrets at runtime.

### Create Service Account

1. Sign in to 1Password at https://my.1password.com
2. Navigate to **Developer** > **Service Accounts**
3. Click **New Service Account**
4. Configure:
   - **Name**: `GitLab CI weisssrv`
   - **Vault Access**: Read access to "Homelab" vault only
5. Generate and copy the token (format: `ops_...`)

### Add to GitLab

1. Navigate to project **Settings** > **CI/CD** > **Variables**
2. Click **Add variable**
3. Configure:
   - **Key**: `OP_SERVICE_ACCOUNT_TOKEN`
   - **Value**: (paste the token)
   - **Type**: Variable
   - **Protected**: Yes (only available on protected branches)
   - **Masked**: Yes (hidden in job logs)
4. Click **Add variable**

### Secrets Fetched at Runtime

The pipeline fetches these secrets from 1Password during job execution:

**Terraform jobs:**
```bash
export TF_VAR_cloudflare_api_token=$(op read "op://Homelab/Cloudflare DNS Token/credential")
export TF_VAR_cloudflare_account_id=$(op read "op://Homelab/Cloudflare DNS Token/username")
```

**Ansible deploy jobs:**
```bash
# SSH key for Ansible
op read "op://Homelab/SSH Key/private key" > ~/.ssh/id_ed25519
# Then run with op run for additional secret injection
op run -- ansible-playbook -i inventories/prod playbooks/site.yml
```

**K3s platform jobs:**
```bash
# Kubeconfig for kubectl/helm
op read "op://Homelab/K3s Kubeconfig/kubeconfig" > ~/.kube/config
# Cloudflare token for Traefik, external-dns, DDNS
CF_TOKEN=$(op read "op://Homelab/Cloudflare DNS Token/credential")
```

**K3s application jobs:**
```bash
# Authentik
AUTHENTIK_SECRET_KEY=$(op read "op://Homelab/Authentik Secrets/secret-key")
AUTHENTIK_PG_PASS=$(op read "op://Homelab/Authentik Secrets/postgresql-password")

# Downloads (VPN credentials)
VPN_USER=$(op read "op://Homelab/PrivadoVPN Credentials/openvpn-user")
VPN_PASS=$(op read "op://Homelab/PrivadoVPN Credentials/openvpn-password")

# Recipes (Mealie + Bar Assistant)
MEALIE_PG_PASS=$(op read "op://Homelab/Mealie Secrets/postgres-password")
MEALIE_OIDC_ID=$(op read "op://Homelab/Mealie SSO/oidc-client-id")
BAR_MEILI_KEY=$(op read "op://Homelab/Bar Assistant Secrets/meilisearch-master-key")

# GitLab Runner
RUNNER_TOKEN=$(op read "op://Homelab/GitLab Runner/runner-token")
```

### Required 1Password Items

Core 1Password items used by CI/CD pipeline (see CLAUDE.md for the complete list):

| Item | Fields | Used By |
|------|--------|---------|
| SSH Key | `private key` | Ansible deployments |
| K3s Kubeconfig | `kubeconfig` | K3s/Helm deployments |
| Cloudflare DNS Token | `credential`, `username` | Terraform, Traefik, external-dns, DDNS |
| Authentik Secrets | `secret-key`, `postgresql-password`, `postgresql-admin-password` | `deploy-k3s-authentik` |
| PrivadoVPN Credentials | `openvpn-user`, `openvpn-password` | `deploy-downloads` (Gluetun VPN sidecar) |
| Mealie Secrets | `postgres-password` | `deploy-recipes` |
| Mealie SSO | `oidc-client-id`, `oidc-client-secret` | `deploy-recipes` |
| Bar Assistant Secrets | `meilisearch-master-key` | `deploy-recipes` |
| Bar Assistant SSO | `authentik-client-id`, `authentik-client-secret` | `deploy-recipes` |
| OpenAI API Key | `api-key` | `deploy-recipes` (optional, for Mealie recipe parsing), `pr-agent-review` (AI code review) |
| GitLab Runner | `runner-token` | `deploy-gitlab-runner` |
| GitLab Runner Privileged | `runner-token` | `deploy-gitlab-runner-privileged` |
| GitLab API Token | `credential` | `pr-agent-review` (AI code review) |

**Creating the K3s Kubeconfig item:**
1. Fetch kubeconfig: `task k3s:kubeconfig`
2. Create new item in 1Password:
   - **Title**: `K3s Kubeconfig`
   - **Type**: Secure Note or API Credential
   - **Field**: `kubeconfig` (paste entire kubeconfig content)

## Scheduled Pipelines

### Version Checking

Configure weekly version checks:

1. Navigate to **CI/CD** > **Schedules**
2. Click **New schedule**
3. Configure:
   - **Description**: `Weekly version check`
   - **Interval pattern**: `0 9 * * 1` (Monday 9am)
   - **Cron timezone**: Your timezone
   - **Target branch**: `main`
4. Click **Save pipeline schedule**

## Local Development

### Pre-commit Checks

Before pushing code, run local validation:

```bash
# Run all linters
task lint

# Individual checks
task ansible:lint
task terraform:validate
task kubernetes:lint
task kubernetes:validate-helm
```

### Testing Locally

```bash
# Run all Molecule tests
task ansible:test

# Test specific role
task ansible:test -- k3s

# Run integration tests
task ansible:test-integration
```

### Terraform

```bash
# Format code
cd terraform/cloudflare
terraform fmt

# Validate syntax
terraform validate

# Plan changes (requires 1Password)
task terraform:plan
```

## Secret Detection

GitLab native secret detection scans for:
- API keys and tokens
- Private keys
- Passwords in code
- Cloud provider credentials

Excluded paths:
- `docs/` - Documentation may contain example values
- `ansible/roles/*/molecule/` - Test fixtures

## GitHub Mirror

GitHub is configured as a read-only push mirror:

### How Mirroring Works

1. Changes are pushed to GitLab
2. GitLab automatically pushes to GitHub via configured mirror
3. GitHub receives updates within minutes
4. GitHub Actions are disabled (CI runs on GitLab only)

### GitHub Repository Settings

The GitHub repository is configured as read-only:
- Branch protection on `main` (locked)
- GitHub Actions disabled
- Description indicates GitLab is canonical

## Legacy GitHub Actions

The `.github/workflows/` directory contains disabled workflows retained for reference.
All workflows have `workflow_dispatch` only triggers and will not run automatically.

To view the legacy workflows:
- `lint.yml` - Shell/Ansible/Terraform/YAML linting
- `molecule.yml` - Ansible role testing
- `integration-tests.yml` - Multi-role integration tests
- `kubernetes.yml` - K8s manifest validation
- `terraform.yml` - Terraform validation

## Troubleshooting

### Pipeline Failing on Lint

1. Run lint locally:
   ```bash
   task lint
   ```

2. Fix Ansible issues:
   ```bash
   ansible-lint --fix ansible/playbooks/site.yml
   ```

3. Fix Terraform formatting:
   ```bash
   cd terraform/cloudflare
   terraform fmt
   ```

### Terraform Plan Failing

1. Verify 1Password token is configured:
   - Check `OP_SERVICE_ACCOUNT_TOKEN` variable exists
   - Verify it's not expired

2. Test 1Password access locally:
   ```bash
   op read "op://Homelab/Cloudflare DNS Token/credential"
   ```

3. Check 1Password is configured:
   - `terraform-plan` requires `OP_SERVICE_ACCOUNT_TOKEN` to be set
   - Verify the token hasn't expired

### Molecule Tests Failing

1. Check Docker-in-Docker is available:
   - All weisssrv jobs run on the infrastructure runner (`gitlab-runner-privileged` Helm release, tag: `infrastructure`)
   - This runner has `privileged = true` which enables Docker-in-Docker
   - Deploy/redeploy the infrastructure runner: `task gitlab:deploy-runner-privileged`
   - Check runner pods: `kubectl get pods -n gitlab-runner -l release=gitlab-runner-privileged`

2. Run tests locally:
   ```bash
   cd ansible/roles/<role>
   molecule test
   ```

3. Keep containers for debugging:
   ```bash
   MOLECULE_OPTS="--destroy=never" molecule test
   ```

### Secret Detection False Positives

If secret detection flags a false positive:

1. Check if it's a real secret (never commit secrets)
2. For false positives in tests, add to excluded paths in `.gitlab-ci.yml`:
   ```yaml
   secret_detection:
     variables:
       SECRET_DETECTION_EXCLUDED_PATHS: "docs/,ansible/roles/*/molecule/,path/to/exclude/"
   ```

## References

- [GitLab CI/CD Documentation](https://docs.gitlab.com/ee/ci/)
- [GitLab Secret Detection](https://docs.gitlab.com/ee/user/application_security/secret_detection/)
- [1Password Service Accounts](https://developer.1password.com/docs/service-accounts/)
- [Molecule Testing](https://molecule.readthedocs.io/)
