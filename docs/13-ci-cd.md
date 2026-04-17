# CI/CD Pipelines

This document covers the GitLab CI/CD pipeline for automated testing, validation, and deployment.

## Overview

The repository uses **self-hosted GitLab** as the canonical source with CI/CD:

- **Canonical source**: https://git.ericsweiss.com/eric/weisssrv
- **GitHub mirror**: https://github.com/ericsweiss/weisssrv (read-only, auto-synced)

CI/CD features:
- **Linting**: Ansible playbooks, Terraform code, shell scripts, Kubernetes manifests, Flux Kustomizations
- **Validation**: Terraform plan, `kustomize build` + envsubst + kubeconform on every Flux Kustomization, Python script unit tests
- **Testing**: Molecule unit tests, multi-role integration tests
- **Security**: GitLab native secret detection
- **Auto-deployment**: Ansible + Terraform deploy on merge to main. **Kubernetes workloads
  are reconciled by Flux directly from git** — there are no CI deploy jobs for k8s.
- **Version checking**: Scheduled checks for available updates; CI fails if
  `kubernetes/infrastructure/sources/versions-configmap.yaml` is out of sync with `all.yml`

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
  - validate      # Schema and configuration validation
  - test          # Unit and integration tests
  - security      # Secret detection scanning
  - ai-review     # AI-powered code review (MRs only, after tests/security)
  - gate          # Validation gate (blocks deploys until all checks pass)
  - deploy        # Auto-deploy on merge to main
  - maintenance   # Manual approval required (reboots, HA changes)
```

### Jobs by Stage

#### Lint Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `version-check` | All MRs/pushes (soft-fail), schedule, web manual | Check for available updates |
| `flux-versions-sync` | `ansible/inventories/prod/group_vars/all.yml`, `kubernetes/infrastructure/sources/versions-configmap.yaml` | Regenerates ConfigMap from all.yml; fails if committed file drifts |
| `shellcheck` | scripts/**, ansible/roles/**/*.sh | Shell script linting |
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
| `flux-lint` | kubernetes/**, ansible/inventories/prod/group_vars/all.yml | `kustomize build` + envsubst (from versions ConfigMap) + kubeconform on every Flux Kustomization; also validates cluster root builds |
| `python-tests` | scripts/** | pytest on check-versions.py and generate-versions-configmap.py |

> **Limitation**: `flux-lint` validates HelmRelease CRD schemas and envsubst placeholders but does
> not perform server-side Helm template rendering. Chart value compatibility issues (e.g., a renamed
> values key in a new chart version) are only caught at Flux reconciliation time. For high-risk chart
> upgrades, use `task flux:dev-apply` to test in-cluster before committing.

#### Test Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `molecule-tests` | ansible/roles/** | Role unit tests (requires Docker-in-Docker) |
| `integration-tests` | ansible/integration-tests/**, ansible/roles/**, ansible/inventories/** | Multi-role tests (requires Docker-in-Docker) |

> **Note:** Test jobs require Docker-in-Docker and a runner with `privileged = true`.
> All weisssrv jobs (including tests) run on the **infrastructure runner**
> (`gitlab-runner-privileged` Helm release, tag: `infrastructure`) which has `privileged = true`.
> Runner is Flux-managed at `kubernetes/apps/gitlab-runner-privileged/release.yaml`;
> push changes to reconcile.

#### Security Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `secret_detection` | All MRs/pushes | Scan for leaked secrets |

#### Gate Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `validation-gate` | Pushes to main only (not schedule, web, or MR) | Blocks Ansible/Terraform deploy jobs until all *applicable* checks pass |

The `validation-gate` job depends on `secret_detection` as a **required** (non-optional) dependency
that must always pass. All path-filtered lint, validate, and test jobs are listed as `optional: true`
dependencies:

- If a path-filtered job was **not created** (its path filter didn't match): the dependency
  is silently skipped and does not block the gate.
- If a path-filtered job **was created** (paths matched) and **failed**: the gate is blocked.
- `secret_detection` must always pass — it is not optional.

Ansible/Terraform deploy jobs depend on `validation-gate` as a required `needs`.
**Kubernetes workloads are not gated by CI** — Flux reconciles from git regardless of
pipeline state. CI's job for k8s is to validate (`flux-lint`, `kubeconform`,
`flux-versions-sync`) and optionally trigger a webhook notification.

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
| `deploy-gitlab` | ansible/roles/gitlab/**, ansible/playbooks/gitlab.yml, ansible/inventories/prod/group_vars/all.yml | Deploy GitLab VM and application |
| `deploy-home-assistant-config` | ansible/roles/home_assistant/**, ansible/playbooks/home-assistant.yml | Deploy Home Assistant configuration |

#### K3s Platform and Applications: Flux-Managed

**All Kubernetes workloads deploy via Flux, not CI jobs.** Every platform component
(MetalLB, Traefik, cert-manager, external-dns, external-secrets, CoreDNS, DDNS,
Authentik) and every application (downloads, recipes, gitlab-runner,
gitlab-runner-privileged, gitlab-agent, vm-ingress) is reconciled by Flux from
`kubernetes/` on every `git push` to `main`. CI only validates (`flux-lint`,
`kubeconform`, `flux-versions-sync`).

The following CI deploy jobs were **removed** (replaced by Flux reconciliation):
`deploy-k3s-platform`, `deploy-k3s-coredns`, `deploy-k3s-authentik`,
`deploy-downloads`, `deploy-recipes`, `deploy-gitlab-ingress`,
`deploy-home-assistant-ingress`, `deploy-plex-ingress`, `deploy-adguard-ingress`,
`deploy-gitlab-runner`, `deploy-gitlab-runner-privileged`.

#### Deploy Stage - Verification
| Job | Triggers | Description | Blocks pipeline? |
|-----|----------|-------------|------------------|
| `deploy-gitlab-verify` | ansible/roles/gitlab/**, kubernetes/apps/gitlab*/**, kubernetes/apps/vm-ingress/gitlab.yaml | GitLab smoke tests (HTTP readiness, container registry, SSH port 22). | No (`allow_failure: true`) |
| `deploy-verify` | All pushes to main (no path filter) | Server-side dry-run validates rendered manifests against cluster API, triggers Flux reconciliation (fails on timeout), checks all nodes `Ready`, asserts zero Flux resources `Ready=false`, checks ExternalSecret readiness (hard failure on steady-state, warning during bootstrap), verifies GitLab HTTP. | Yes — fails the pipeline on any issue |

> **Note:** The two jobs have different semantics:
> - `deploy-gitlab-verify` is informational (`allow_failure: true`) — pipeline continues on failure.
> - `deploy-verify` is blocking — any `NotReady` node, non-Ready Flux resource, or GitLab outage
>   fails the pipeline. There is no `allow_failure` on this job.

#### Maintenance Stage (Manual Only)
| Job | Description |
|-----|-------------|
| `maintenance-k3s-provision` | Provision k3s VMs and deploy cluster |
| `maintenance-update-packages` | OS package updates on all hosts (base, k3s, app VMs) with auto-reboot |
| `maintenance-update-applications` | Application updates: AdGuard Home, Tailscale, Plex |
| `maintenance-update-k3s-nodes` | K3s node rolling update (drain/cordon) |
| `maintenance-proxmox-ha` | Proxmox HA configuration |
| `maintenance-home-assistant-restart` | Restart Home Assistant after config deployment |
| `maintenance-verify` | Post-maintenance cluster health validation (fails on critical issues) |

## Pipeline Triggers

| Trigger | Runs |
|---------|------|
| Merge request | Lint, validate, test, security, AI review stages (no deploy) |
| Push to main | Full validation + auto-deploy |
| Scheduled | Version checking only. All other jobs (lint, validate, test, security, ai-review, gate, deploy, maintenance) are explicitly excluded via `when: never` rules. |
| Manual (web) | Lint, validate, test stages only. Security, AI review, deploy, gate, and maintenance jobs are excluded via `when: never` rules. |

## Deployment Pipeline

### Auto-Deploy Behavior

When a merge request is merged to `main`:

1. **Validation stages run first** (lint, validate, test, security)
2. **Validation gate blocks Ansible/Terraform deploys** -- the `validation-gate` job in the `gate` stage must pass before any Ansible/Terraform deploy job can start. The gate depends on `secret_detection` as a **required** (non-optional) dependency, and all path-filtered lint, validate, and test jobs as `optional: true` dependencies. Path-filtered jobs that were not created are skipped, but `secret_detection` and any path-filtered job that *was* created must succeed or all Ansible/Terraform deployments are blocked.
3. **Only changed components deploy** (path-based triggers on Ansible/Terraform jobs)
4. **Kubernetes workloads reconcile via Flux** — independent of CI. Flux polls git
   every 1 minute, and the GitLab webhook triggers Flux's `Receiver` for sub-second sync.
5. **Machine reboots require manual approval** (maintenance stage)

### Deployment Categories

| Category | Mechanism | Jobs / Action | Auto-Deploy | Manual Approval |
|----------|-----------|---------------|-------------|-----------------|
| Terraform | CI job | `deploy-terraform` | Yes | No |
| Ansible Infrastructure | CI jobs | `deploy-ansible-base`, `deploy-ansible-proxmox`, `deploy-ansible-firewall`, `deploy-ansible-dns`, `deploy-ansible-storage`, `deploy-ansible-mail`, `deploy-ansible-certs` | Yes | No |
| Ansible Applications | CI jobs | `deploy-plex`, `deploy-gitlab`, `deploy-home-assistant-config` | Yes | No |
| Kubernetes workloads (platform + apps) | Flux reconciliation from git | `kubernetes/infrastructure/`, `kubernetes/apps/*` | Yes (on push) | No |
| Verification | CI jobs | `deploy-gitlab-verify`, `deploy-verify` | Yes | No |
| K3s Provisioning | CI job (manual) | `maintenance-k3s-provision` | No | **Yes** |
| System Updates | CI jobs (manual) | `maintenance-update-packages`, `maintenance-update-applications` | No | **Yes** |
| K3s Node Updates | CI job (manual) | `maintenance-update-k3s-nodes` | No | **Yes** |
| Proxmox HA | CI job (manual) | `maintenance-proxmox-ha` | No | **Yes** |
| Home Assistant Restart | CI job (manual) | `maintenance-home-assistant-restart` | No | **Yes** |
| Post-Maintenance Verification | CI job (manual) | `maintenance-verify` | No | **Yes** |

### How Deployment Works

Ansible/Terraform deploy jobs depend on `validation-gate` (required, non-optional) as their quality gate.

**Ansible deployments** use SSH to target hosts:
- SSH key fetched from 1Password at runtime
- Known hosts added automatically for all infrastructure IPs
- Runs `op run -- ansible-playbook` to inject secrets

**Kubernetes workloads (platform + apps)** reconcile via Flux:
- A commit under `kubernetes/` lands on `main`
- The GitLab webhook POSTs to Flux's `Receiver` (or Flux's 1-minute poll catches it)
- `flux-system` `GitRepository` syncs the new revision
- Top-level `Kustomization`s reconcile in dependency order:
  `infrastructure-sources` → `infrastructure-controllers` → `infrastructure-configs` → `apps`
- `helm-controller` upgrades HelmReleases; `kustomize-controller` applies Kustomizations
- **All Secrets** are created by `external-secrets`' `ExternalSecret` CRs that
  reference 1Password item IDs via the ClusterSecretStore `onepassword-homelab`
  (SDK provider). CI does not inject any secrets into k8s.
- Version substitutions flow from the `cluster-versions` ConfigMap
  (`kubernetes/infrastructure/sources/versions-configmap.yaml`, generated from
  `all.yml`). The `flux-versions-sync` lint job fails the pipeline if the
  committed ConfigMap has drifted from `all.yml`.

**Terraform deployments**:
- Plan saved as artifact during validate stage
- Apply uses saved plan on merge to main
- Credentials fetched from 1Password

**Version pinning**: All Helm chart versions and container image tags are centralized in `ansible/inventories/prod/group_vars/all.yml`. Kubernetes manifests use `${var}` placeholders that Flux substitutes from the generated `cluster-versions` ConfigMap. Regenerate with `task flux:sync-versions` after bumping a value in `all.yml`.

## GitLab CI/CD Variables

### Required Variables

Configure in **Settings > CI/CD > Variables**:

| Variable | Type | Protected | Masked | Description |
|----------|------|-----------|--------|-------------|
| `OP_SERVICE_ACCOUNT_TOKEN` | Variable | Yes | Yes | 1Password service account token |

### Optional Variables

| Variable | Type | Protected | Masked | Description |
|----------|------|-----------|--------|-------------|
| `GH_API_TOKEN` | Variable | No | Yes | GitHub API token for version checker rate limits (check-versions.py accepts either `GH_API_TOKEN` or `GITHUB_TOKEN`; CI sets `GH_API_TOKEN` as the canonical name) |

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
# SSH key for Ansible (placed on disk outside of op run — ssh-agent reads it)
op read "op://Homelab/SSH Key/private key" > ~/.ssh/id_ed25519
chmod 600 ~/.ssh/id_ed25519

# Run Ansible via op run so inline `op://...` env refs are injected at runtime
op run -- ansible-playbook -i inventories/prod playbooks/site.yml
```

**Kubernetes secrets (no CI involvement)**:

Kubernetes Secrets are not created by CI. External Secrets Operator watches
`ExternalSecret` CRs in the cluster and syncs their values from 1Password (via
the `onepassword-homelab` ClusterSecretStore, SDK provider). The only bootstrap
secret in the cluster is `external-secrets/onepassword-sdk-token`, created once
by `task flux:bootstrap-onepassword`. See `docs/29-flux-operations.md`.

### Required 1Password Items

Core 1Password items used by the CI/CD pipeline (see CLAUDE.md for the complete list
of items referenced by ExternalSecrets in the cluster):

| Item | Fields | Used By |
|------|--------|---------|
| SSH Key | `private key` | Ansible deployments |
| Cloudflare DNS Token | `credential`, `username` | Terraform `deploy-terraform` |
| GitLab API Token | `credential` | `pr-agent-review` (AI code review) |
| OpenAI API Key | `api-key` | `pr-agent-review` (AI code review) |
| GitHub Token | `credential` | `version-check` (higher API rate limits) |

All other items (Authentik, PrivadoVPN, Mealie/Bar Assistant/SSO, GitLab Runner tokens,
GitLab Agent token, SMTP Relay Auth, etc.) are consumed by **ExternalSecrets in the
cluster**, not by CI jobs.

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
   - To change runner config: edit `kubernetes/apps/gitlab-runner-privileged/release.yaml`,
     commit, push; Flux reconciles within ~1 min (or `task flux:reconcile` to force).
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
