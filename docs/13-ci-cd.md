# CI/CD Pipelines

This document covers the GitHub Actions workflows for automated testing and deployment.

## Overview

The repository uses GitHub Actions for:
- **Linting**: Ansible playbooks, Terraform code, Kubernetes manifests
- **Molecule Testing**: Automated Ansible role testing with idempotency checks
- **Terraform Planning**: Automated `terraform plan` on PRs
- **Kubernetes Validation**: kubeconform and Helm values validation
- **Validation**: Syntax checking before merge

## Workflows

### Lint Workflow

**File**: `.github/workflows/lint.yml`

Runs on every push and pull request to validate code quality.

**Steps**:
1. Ansible playbook syntax check
2. Ansible-lint for best practices
3. Terraform format check (`terraform fmt -check`)
4. Terraform validation
5. YAML lint

**Usage**:
```bash
# Locally run the same checks (includes Kubernetes validation)
task lint

# Individual checks
task ansible:lint
task terraform:validate
task kubernetes:lint
task kubernetes:validate-helm
```

### Molecule Workflow

**File**: `.github/workflows/molecule.yml`

Runs Molecule tests for Ansible roles with change detection.

**Triggers**:
- Push/PR to main modifying `ansible/roles/**`
- Manual workflow dispatch (tests all roles)

**Features**:
- Change detection: Only tests roles with modifications
- Matrix-based parallel execution
- Idempotency verification on all roles
- K3s has both `default` and `agent` scenarios

**Tested Roles** (12 roles, 13 scenarios):
- base, qol, adguard_home, adguard_sync, acme_certs
- tailscale, nas_storage, k3s (server + agent), plex
- unbound, postfix_null_client, smtp_relay

**Usage**:
```bash
# Run all molecule tests locally
task ansible:test

# Test specific role
task ansible:test -- k3s

# Test with containers kept for debugging
MOLECULE_OPTS="--destroy=never" task ansible:test -- unbound
```

### Kubernetes Workflow

**File**: `.github/workflows/kubernetes.yml`

Validates Kubernetes manifests and Helm values.

**Triggers**:
- Push/PR to main modifying `kubernetes/**`
- Manual workflow dispatch

**Steps**:
1. **kubeconform**: Validates manifests against K8s API schemas
   - Uses CRD schemas from datreeio/CRDs-catalog
   - Kubernetes version 1.35.0
2. **Helm template**: Validates values files render correctly
   - Traefik, MetalLB, cert-manager, Authentik
3. **yamllint**: Basic YAML syntax validation

**Usage**:
```bash
# Validate Kubernetes manifests locally
task kubernetes:lint

# Validate Helm values
task kubernetes:validate-helm
```

### Terraform Workflow

**File**: `.github/workflows/terraform.yml`

Runs `terraform plan` on pull requests to show infrastructure changes.

**Triggers**:
- Pull requests modifying `terraform/**` files
- Manual workflow dispatch

**Steps**:
1. Checkout code
2. Setup Terraform
3. Initialize Terraform
4. Run `terraform plan`
5. Comment plan output on PR

**Secrets Required**:
- `OP_SERVICE_ACCOUNT_TOKEN`: 1Password service account for CI
- OR individual secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`

## GitHub Actions Secrets

### Required Secrets

Configure these in GitHub repository settings (Settings → Secrets and variables → Actions):

| Secret Name | Description | Source |
|-------------|-------------|--------|
| `OP_SERVICE_ACCOUNT_TOKEN` | 1Password service account token | 1Password |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token (fallback) | Cloudflare dashboard |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID (fallback) | Cloudflare dashboard |

### 1Password Service Account Setup

Recommended approach for CI/CD:

1. **Create Service Account**:
   - Sign in to 1Password
   - Navigate to Settings → Service Accounts
   - Create new service account named "GitHub Actions"

2. **Grant Permissions**:
   - Read access to "Homelab" vault
   - Limit to specific items if possible

3. **Generate Token**:
   - Copy the service account token
   - Add to GitHub Actions secrets as `OP_SERVICE_ACCOUNT_TOKEN`

4. **Use in Workflow**:
   ```yaml
   - name: Load secrets from 1Password
     uses: 1password/load-secrets-action@v1
     with:
       export-env: true
     env:
       OP_SERVICE_ACCOUNT_TOKEN: ${{ secrets.OP_SERVICE_ACCOUNT_TOKEN }}
       CLOUDFLARE_API_TOKEN: op://Homelab/Cloudflare DNS Token/credential
   ```

## Workflow Permissions

The workflows use these GitHub permissions:

```yaml
permissions:
  contents: read        # Read repository
  pull-requests: write  # Comment on PRs
```

## Running Workflows

### Automatic Triggers

Workflows run automatically on:
- Every `git push`
- Every pull request
- Changes to relevant files (`*.yml`, `*.tf`, etc.)

### Manual Triggers

Some workflows support manual triggering:

1. Navigate to **Actions** tab in GitHub
2. Select workflow
3. Click **Run workflow**
4. Choose branch and parameters

## Local Development

### Pre-commit Checks

Before pushing code, run local validation:

```bash
# Run all linters
task lint

# Run Ansible checks only
task ansible:lint

# Run Terraform checks only
cd terraform/cloudflare
terraform fmt -check
terraform validate
```

### Testing Terraform Changes

Before creating a PR:

```bash
cd terraform/cloudflare

# Format code
terraform fmt

# Validate syntax
terraform validate

# Plan changes
terraform plan
```

## Ansible Lint Configuration

The repository uses ansible-lint with production-grade standards.

**Configuration**: `ansible/.ansible-lint`

**Profile**: `production` - Enforces strict best practices for production infrastructure

**Skipped Rules**:
- `var-naming[no-role-prefix]` - Global variables (admin_user, etc.) intentionally lack role prefixes

All other production profile rules are enforced, including:
- Explicit file permissions on all file operations
- Proper error handling (`failed_when` instead of `ignore_errors`)
- Shell pipefail for pipeline safety
- Changed_when declarations for idempotency
- Ansible modules over shell commands where applicable

**Local validation**:
```bash
# Run ansible-lint with production profile
task ansible:lint

# Auto-fix some issues
cd ansible
python3 -m ansiblelint --fix playbooks/ roles/
```

## CI/CD for Ansible

Currently, Ansible deployments are **manual** and not automated via CI/CD.

### Future: Ansible Automation

Potential approaches for automating Ansible:

1. **Dry-run on PR**:
   - Run `ansible-playbook --check` on PRs
   - Comment results on PR
   - Requires runner with SSH access to homelab

2. **Scheduled Drift Detection**:
   - Daily/weekly `--check` runs
   - Report configuration drift
   - Alert on unexpected changes

3. **Deployment on Merge**:
   - Auto-deploy to homelab on merge to `main`
   - Requires VPN/Tailscale access from GitHub runners

**Challenges**:
- GitHub runners cannot access homelab directly
- Requires self-hosted runner OR Tailscale subnet router
- Secrets management for SSH keys

### Self-Hosted Runner (Future)

To enable Ansible automation:

1. **Setup Runner**:
   ```bash
   # On a homelab VM
   mkdir actions-runner && cd actions-runner
   curl -o actions-runner-linux.tar.gz -L \
     https://github.com/actions/runner/releases/download/v2.311.0/actions-runner-linux-x64-2.311.0.tar.gz
   tar xzf ./actions-runner-linux.tar.gz
   ./config.sh --url https://github.com/ericsweiss/weisssrv --token <TOKEN>
   sudo ./svc.sh install
   sudo ./svc.sh start
   ```

2. **Update Workflows**:
   ```yaml
   jobs:
     ansible-check:
       runs-on: self-hosted
       steps:
         - name: Ansible dry-run
           run: ansible-playbook ansible/playbooks/site.yml --check
   ```

## Migrating to GitLab CI/CD

If you migrate from GitHub to self-hosted GitLab:

### GitLab CI Configuration

Create `.gitlab-ci.yml` equivalent:

```yaml
stages:
  - lint
  - plan
  - deploy

variables:
  TF_ROOT: terraform/cloudflare

lint:ansible:
  stage: lint
  image: cytopia/ansible-lint
  script:
    - ansible-lint ansible/playbooks/*.yml

lint:terraform:
  stage: lint
  image: hashicorp/terraform:latest
  script:
    - cd $TF_ROOT
    - terraform fmt -check
    - terraform validate

terraform:plan:
  stage: plan
  image: hashicorp/terraform:latest
  before_script:
    - cd $TF_ROOT
    - terraform init
  script:
    - terraform plan
  only:
    - merge_requests

deploy:ansible:
  stage: deploy
  image: cytopia/ansible:latest-tools
  script:
    - ansible-playbook ansible/playbooks/site.yml --check
  only:
    - main
  when: manual
```

### GitLab Secrets

Configure in GitLab project settings (Settings → CI/CD → Variables):

- `OP_SERVICE_ACCOUNT_TOKEN`
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

**Advantages of GitLab**:
- Self-hosted: runners have direct access to homelab
- Better Ansible integration (no SSH access issues)
- Built-in container registry
- Free CI/CD minutes on self-hosted

## Troubleshooting

### Workflow Failing on Lint

1. **Run lint locally**:
   ```bash
   task lint
   ```

2. **Fix Ansible issues**:
   ```bash
   ansible-lint --fix ansible/playbooks/site.yml
   ```

3. **Fix Terraform formatting**:
   ```bash
   cd terraform/cloudflare
   terraform fmt
   ```

### Terraform Plan Failing

1. **Check secrets configured**:
   - Verify `CLOUDFLARE_API_TOKEN` exists
   - Test token validity locally

2. **Initialize locally**:
   ```bash
   cd terraform/cloudflare
   terraform init
   terraform plan
   ```

3. **Review workflow logs**:
   - Check for missing variables
   - Verify backend configuration

### 1Password Integration Issues

If secrets cannot be loaded:

1. **Verify service account token**:
   - Token has read access to vault
   - Token is not expired

2. **Test locally**:
   ```bash
   op read "op://Homelab/Cloudflare DNS Token/credential"
   ```

3. **Update secret in GitHub**:
   - Settings → Secrets → Update `OP_SERVICE_ACCOUNT_TOKEN`

## References

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [1Password for GitHub Actions](https://github.com/marketplace/actions/load-secrets-from-1password)
- [Terraform GitHub Actions](https://developer.hashicorp.com/terraform/tutorials/automation/github-actions)
- [GitLab CI/CD](https://docs.gitlab.com/ee/ci/)
