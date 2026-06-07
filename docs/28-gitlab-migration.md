# GitLab Migration Guide

Complete migration instructions for moving from GitHub to self-hosted GitLab with CI/CD and optional AI code review integration.

## Overview

This guide covers:
1. GitLab project creation and configuration
2. GitHub mirror setup (read-only push mirror)
3. 1Password service account setup for CI/CD
4. Runner registration
5. Scheduled pipeline configuration
6. AI code review tool integration (PR-Agent/Qodo Merge)
7. Verification checklist
8. Rollback plan

## Prerequisites

Before starting:
- GitLab instance running at `https://git.ericsweiss.com`
- GitLab account with admin or Maintainer role
- GitHub account with repository admin access
- 1Password account with access to "Homelab" vault
- Working k3s cluster with GitLab Runner capability

## Part 1: GitLab Project Setup

### Step 1.1: Create GitLab Project

1. Log in to GitLab at https://git.ericsweiss.com
2. Click **New project** > **Create blank project**
3. Configure:
   - **Project name**: `weisssrv`
   - **Project URL**: `https://git.ericsweiss.com/eric/weisssrv`
   - **Visibility level**: Private (or Internal)
   - **Initialize repository**: Unchecked (existing code is pushed in a later step)
4. Click **Create project**

### Step 1.2: Push Repository to GitLab

From your local machine:

```bash
cd /path/to/weisssrv

# Add GitLab as a remote (if not already)
git remote add gitlab ssh://git@git.ericsweiss.com:2222/eric/weisssrv.git

# Or if GitLab is the new origin, rename remotes
git remote rename origin github
git remote add origin ssh://git@git.ericsweiss.com:2222/eric/weisssrv.git

# Push all branches and tags to GitLab
git push gitlab --all
git push gitlab --tags

# Or if GitLab is now origin:
git push origin --all
git push origin --tags
```

### Step 1.3: Configure Default Branch

1. Navigate to **Settings** > **Repository** > **Branch defaults**
2. Set **Default branch** to `main`
3. Click **Save changes**

### Step 1.4: Protect Main Branch

1. Navigate to **Settings** > **Repository** > **Protected branches**
2. Find or add `main`:
   - **Allowed to merge**: Maintainers
   - **Allowed to push and merge**: No one (force MR workflow)
   - **Allowed to force push**: No
3. Click **Protect**

## Part 2: GitHub Mirror Setup

Configure GitHub as a read-only push mirror so changes pushed to GitLab automatically sync to GitHub.

### Step 2.1: Create GitHub Personal Access Token

1. Go to https://github.com/settings/tokens
2. Click **Generate new token** > **Fine-grained tokens**
3. Configure:
   - **Token name**: `GitLab Mirror - weisssrv`
   - **Expiration**: 1 year (or your preference)
   - **Repository access**: Only select repositories > `ericsweiss/weisssrv`
   - **Permissions**:
     - **Contents**: Read and write
     - **Metadata**: Read-only
4. Click **Generate token**
5. **Copy the token immediately** (you won't see it again)

### Step 2.2: Configure Push Mirror in GitLab

1. Navigate to **Settings** > **Repository** > **Mirroring repositories**
2. Click **Add new**
3. Configure:
   - **Git repository URL**: `https://github.com/ericsweiss/weisssrv.git`
   - **Mirror direction**: Push
   - **Authentication method**: Password
   - **Username**: Your GitHub username
   - **Password**: The personal access token from Step 2.1
   - **Keep divergent refs**: Unchecked
   - **Mirror only protected branches**: Unchecked (mirror all)
4. Click **Mirror repository**
5. Click the refresh icon to trigger initial sync

### Step 2.3: Update GitHub Repository Settings

1. Go to https://github.com/ericsweiss/weisssrv
2. Navigate to **Settings** > **General**
3. Update description:
   ```
   [Mirror] Homelab Infrastructure as Code - Canonical source: https://git.ericsweiss.com/eric/weisssrv
   ```
4. Add website: `https://git.ericsweiss.com/eric/weisssrv`
5. Navigate to **Settings** > **Actions** > **General**
6. Select **Disable actions** (CI runs on GitLab only)
7. Click **Save**

### Step 2.4: Configure Branch Protection on GitHub

1. Navigate to **Settings** > **Branches**
2. Click **Add branch protection rule**
3. Configure:
   - **Branch name pattern**: `main`
   - **Lock branch**: Checked (prevents direct pushes from GitHub)
4. Click **Create** or **Save changes**

## Part 3: 1Password Service Account Setup

### Step 3.1: Create Service Account

1. Sign in to 1Password at https://my.1password.com
2. Navigate to **Developer** > **Service Accounts**
3. Click **New Service Account**
4. Configure:
   - **Name**: `GitLab CI weisssrv`
   - **Description**: `CI/CD pipeline for weisssrv homelab infrastructure`
5. Click **Next**
6. Under **Vault Access**, click **Grant access**
7. Select **Homelab** vault with **Read** permission
8. Click **Create Service Account**
9. **Copy the token** (format: `ops_...`) - store temporarily in a secure location

### Step 3.2: Verify Required 1Password Items

The service account needs read access to these items in the "Homelab" vault. The table below covers the core items needed to get started. For the complete and authoritative list of all required 1Password items (including items added as new pipeline jobs are created), see the **Required 1Password Items** section in `docs/13-ci-cd.md` or `CLAUDE.md`.

| Item Name | Required Fields | Used By |
|-----------|----------------|---------|
| SSH Key | `private key` | Ansible deployments |
| Cloudflare DNS Token | `credential`, `username` | Terraform `deploy-terraform` |
| GitLab API Token | `credential` | AI code review (PR-Agent) |
| OpenAI API Key | `api-key` | AI code review (PR-Agent) |

Most cluster secrets (Authentik, VPN, Mealie/Bar Assistant, runner tokens, agent
token, SMTP, etc.) are consumed in-cluster by ExternalSecrets, not by CI — they do
not need to be visible to the CI service account. See `CLAUDE.md` for the complete
list of 1Password items.

### Step 3.3: Create K3s Kubeconfig Item (Optional)

The `K3s Kubeconfig` item is used by the `.k3s-deploy-base` CI template as a fallback
mechanism — the GitLab Agent is the preferred path for CI-to-cluster access. If you
want the fallback available:

```bash
# Fetch kubeconfig from cluster
task k3s:kubeconfig

# View the kubeconfig
cat ~/.kube/config-k3s
```

1. In 1Password, create new **Secure Note** in "Homelab" vault
2. **Title**: `K3s Kubeconfig`
3. Add field:
   - **Label**: `kubeconfig`
   - **Type**: Password (or text)
   - **Value**: Paste entire kubeconfig content (base64-encoded to preserve YAML)
4. Save the item

For ongoing Flux-driven deploys, neither the kubeconfig nor the GitLab Agent is
strictly needed — Flux reconciles from git directly.

### Step 3.4: Add Service Account Token to GitLab

1. Navigate to project **Settings** > **CI/CD** > **Variables**
2. Click **Add variable**
3. Configure:
   - **Key**: `OP_SERVICE_ACCOUNT_TOKEN`
   - **Value**: (paste the `ops_...` token from Step 3.1)
   - **Type**: Variable
   - **Environment scope**: All (default)
   - **Protect variable**: Yes (only runs on protected branches)
   - **Mask variable**: Yes (hidden in job logs)
   - **Expand variable reference**: No
4. Click **Add variable**

### Step 3.5: (Optional) Add GitHub Token for Version Checker

If you want higher API rate limits for version checking:

1. Create a GitHub personal access token with `public_repo` scope
2. Add to GitLab:
   - **Key**: `GITHUB_TOKEN`
   - **Value**: (paste GitHub token)
   - **Protected**: No (can run on any branch)
   - **Masked**: Yes
3. Click **Add variable**

## Part 4: GitLab Runner Registration

The pipeline uses two GitLab Runners deployed on the k3s cluster:

- **Infrastructure runner** (privileged): Handles ALL weisssrv CI/CD jobs (lint, test, deploy). Uses Docker-in-Docker for Molecule tests, SSH for Ansible deploys, kubectl for k8s deploys. Tag: `infrastructure`.
- **Shared runner** (unprivileged): For other GitLab projects and collaborators to deploy to k3s. No SSH, no DinD, no privileged access. Tag: `k8s-deploy`, runs untagged jobs.

The weisssrv `.gitlab-ci.yml` has `default: tags: [infrastructure]` so all jobs route to the infrastructure runner.

> **Ordering note**: the order below matches `docs/27-gitlab-deployment.md`
> § Step 8 — shared runner first, infrastructure runner second.

### Step 4.1: Create Shared Runner Token

1. Navigate to **Admin Area** > **CI/CD** > **Runners**
2. Click **New instance runner**
3. Configure:
   - **Tags**: `k8s-deploy`
   - **Run untagged jobs**: Yes
4. Click **Create runner**
5. **Copy the runner token** (format: `glrt-...`)

### Step 4.2: Store Shared Runner Token in 1Password

1. In 1Password, create item **GitLab Runner** in "Homelab" vault
2. Add field:
   - **Label**: `runner-token`
   - **Value**: (paste the `glrt-...` token)
3. Save the item

### Step 4.3: Create Infrastructure Runner Token

1. Navigate to **Admin Area** > **CI/CD** > **Runners**
2. Click **New instance runner** again
3. Configure:
   - **Tags**: `infrastructure`
   - **Run untagged jobs**: No
4. Click **Create runner**
5. **Copy the runner token** (format: `glrt-...`)

### Step 4.4: Store Infrastructure Runner Token in 1Password

1. In 1Password, create item **GitLab Runner Privileged** in "Homelab" vault
2. Add field:
   - **Label**: `runner-token`
   - **Value**: (paste the `glrt-...` token)
3. Save the item

### Step 4.5: Deploy GitLab Runners on k3s

GitLab Runners are Flux-managed. The Helm releases live under
`kubernetes/apps/gitlab-runner/` (shared, unprivileged) and
`kubernetes/apps/gitlab-runner-privileged/` (infrastructure, privileged). Each has
its own ExternalSecret referencing the runner token in 1Password.

```bash
# First-time install: commit the folders + ExternalSecrets, push, and Flux deploys them
git add kubernetes/apps/gitlab-runner kubernetes/apps/gitlab-runner-privileged
git commit -m "Add GitLab runners"
git push
task flux:reconcile   # or wait ~1 minute

# Verify both runners are registered (reads HelmRelease status via flux get)
task gitlab:status
flux get hr -n gitlab-runner     # lists both the shared runner AND the privileged runner
                                 # (both live in the gitlab-runner namespace)
```

Runner token rotation (glrt-* tokens):

1. Regenerate the token in GitLab Admin Area > CI/CD > Runners.
2. Update `op://Homelab/GitLab Runner/runner-token` (or `GitLab Runner Privileged/runner-token`).
3. `task flux:rotate-secret -- gitlab-runner` (or `gitlab-runner-privileged`).

### Step 4.6: Verify Runner Status

1. Navigate to **Admin Area** > **CI/CD** > **Runners**
2. Confirm both runners show as **Online** with green status dots
3. Verify tags: infrastructure runner has `infrastructure`, shared runner has `k8s-deploy`

## Part 5: Scheduled Pipeline Configuration

### Step 5.1: Create Weekly Version Check Schedule

1. Navigate to **CI/CD** > **Schedules**
2. Click **New schedule**
3. Configure:
   - **Description**: `Weekly version check`
   - **Interval pattern**: `0 9 * * 1` (Monday 9am)
   - **Cron timezone**: America/Chicago (or your timezone)
   - **Target branch**: `main`
   - **Activated**: Yes
4. Click **Save pipeline schedule**

### Step 5.2: (Optional) Create Additional Schedules

Additional schedules can be created for specific maintenance tasks. Note that scheduled
pipelines run only `version-check` and `secret_detection` -- all other jobs (lint,
validate, test, ai-review, gate, deploy, and maintenance) are explicitly excluded from
scheduled pipelines via `when: never` rules, so schedules will never trigger builds,
tests, deployments, or maintenance operations.

To add a scheduled pipeline:
1. Click **New schedule**
2. Configure the interval, timezone, and target branch
3. Click **Save pipeline schedule**

## Part 6: AI Code Review Integration

### Option A: Qodo Merge / PR-Agent (Recommended for Self-Hosted)

PR-Agent by Qodo is open-source and fully supports self-hosted GitLab.

#### Step 6.1: Add PR-Agent CI Job

**Note:** The canonical CI configuration is in `.gitlab-ci.yml` at the repo root. The snippet below is for reference only and may not reflect the latest production settings.

The job below runs in whatever stage is configured in the canonical `.gitlab-ci.yml`
(currently `ai-review`). See the live pipeline file for the definitive stage list.

```yaml
# AI-powered code review via PR-Agent
pr-agent-review:
  image: codiumai/pr-agent:latest
  variables:
    CONFIG__GIT_PROVIDER: "gitlab"
    GITLAB__URL: "https://git.ericsweiss.com"
  script:
    - |
      # Get secrets from 1Password
      export GITLAB__PERSONAL_ACCESS_TOKEN=$(op read "op://Homelab/GitLab API Token/credential")
      export OPENAI__KEY=$(op read "op://Homelab/OpenAI API Key/api-key")

      # Run PR-Agent review (must use full MR URL, not just the IID)
      python -m pr_agent.cli \
        --pr_url="https://git.ericsweiss.com/eric/weisssrv/-/merge_requests/${CI_MERGE_REQUEST_IID}" \
        review
      python -m pr_agent.cli \
        --pr_url="https://git.ericsweiss.com/eric/weisssrv/-/merge_requests/${CI_MERGE_REQUEST_IID}" \
        improve
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  allow_failure: true  # Don't block MRs on AI review
```

#### Step 6.2: Create GitLab API Token

1. Navigate to **User Settings** > **Access Tokens**
2. Click **Add new token**
3. Configure:
   - **Token name**: `PR-Agent`
   - **Expiration date**: 1 year
   - **Scopes**: `api`, `read_repository`
4. Click **Create personal access token**
5. Store in 1Password:
   - **Item**: `GitLab API Token`
   - **Field**: `credential` = (paste token)

#### Step 6.3: Verify OpenAI API Key

Ensure `OpenAI API Key` item exists in 1Password with field `api-key`.

### Option B: Cursor Bugbot (Requires Paid GitLab)

Cursor Bugbot supports self-hosted GitLab but requires GitLab Premium or Ultimate for project access tokens.

#### Requirements:
- **GitLab Premium or Ultimate** (for project access tokens)
- **Network access**: GitLab must be accessible from cursor.com servers or via IP whitelist

#### Step 6.4: Setup (If Using Paid GitLab)

1. Navigate to Cursor dashboard: https://cursor.com/dashboard
2. Go to **Advanced** > **Bugbot**
3. Enter your GitLab instance URL: `https://git.ericsweiss.com`
4. Create GitLab application:
   - Navigate to **Admin Area** > **Applications** (instance-level)
   - Create application with:
     - **Name**: `Cursor Bugbot`
     - **Redirect URI**: `https://cursor.com/gitlab-connected`
     - **Trusted**: Yes
     - **Confidential**: Yes
     - **Scopes**: `api`, `write_repository`
5. Enter Application ID and Secret in Cursor dashboard
6. Sync repositories and enable Bugbot on weisssrv

### Option C: OpenAI Codex CI Integration

The OpenAI Codex cookbook approach uses Codex CLI in a GitLab CI job.

#### Step 6.5: Add Codex Review Job

```yaml
codex-review:
  stage: ai-review
  image: node:20
  before_script:
    - npm install -g @openai/codex-cli
  script:
    - |
      export OPENAI_API_KEY=$(op read "op://Homelab/OpenAI API Key/api-key")

      # Get changed files
      git diff --name-only $CI_MERGE_REQUEST_DIFF_BASE_SHA...HEAD > changed_files.txt

      # Review each file
      for file in $(cat changed_files.txt); do
        if [ -f "$file" ]; then
          echo "Reviewing: $file"
          codex review "$file" >> review_output.txt 2>&1 || true
        fi
      done

      # Post results as MR comment
      if [ -s review_output.txt ]; then
        curl --request POST \
          --header "PRIVATE-TOKEN: $(op read 'op://Homelab/GitLab API Token/credential')" \
          --data-urlencode "body@review_output.txt" \
          "https://git.ericsweiss.com/api/v4/projects/$CI_PROJECT_ID/merge_requests/$CI_MERGE_REQUEST_IID/notes"
      fi
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  allow_failure: true
```

### Comparison of AI Code Review Options

| Feature | PR-Agent (Qodo) | Cursor Bugbot | OpenAI Codex CI |
|---------|-----------------|---------------|-----------------|
| Self-hosted GitLab | Yes | Requires Premium+ | Yes |
| Open source | Yes | No | Partial |
| AI model | OpenAI/Claude/etc. | Proprietary | OpenAI |
| Setup complexity | Medium | Low | High |
| Cost | Free (self-hosted) + AI API | $20/seat/month | AI API only |
| MR comments | Yes | Yes | Custom implementation |
| Multi-line suggestions | Yes | Yes | Limited |

**Recommendation**: Start with **PR-Agent** as it's open-source, supports self-hosted GitLab on any tier, and covers all standard review features.

## Part 7: Verification Checklist

### Step 7.1: Verify GitLab Configuration

- [ ] Project created at https://git.ericsweiss.com/eric/weisssrv
- [ ] All branches and tags pushed
- [ ] Main branch protected (no direct pushes)
- [ ] `.gitlab-ci.yml` visible in repository

### Step 7.2: Verify GitHub Mirror

- [ ] Push mirror configured in GitLab
- [ ] Initial sync completed (check mirror status)
- [ ] GitHub repository description updated
- [ ] GitHub Actions disabled
- [ ] GitHub main branch locked

### Step 7.3: Verify 1Password Integration

- [ ] Service account created with Homelab vault access
- [ ] `OP_SERVICE_ACCOUNT_TOKEN` variable added to GitLab
- [ ] All required 1Password items exist with correct fields

### Step 7.4: Verify CI/CD Pipeline

Run a test pipeline:

```bash
# Create a test branch
git checkout -b test/ci-verification
echo "# Test" >> README.md
git add README.md
git commit -m "Test CI pipeline"
git push origin test/ci-verification
```

1. Create a merge request in GitLab
2. Verify lint jobs run (shellcheck, yaml-lint, ansible-lint, terraform-fmt)
3. Verify validate jobs run (terraform-validate, kubeconform, helm-validate)
4. Verify terraform-plan runs (requires 1Password)
5. Close MR without merging

### Step 7.5: Verify Deployment Pipeline

After merging to main:

1. Push a real change to main
2. Verify deploy jobs run only for changed components
3. Check job logs for successful 1Password secret injection
4. Verify infrastructure changes applied

### Step 7.6: Verify GitLab Runners

- [ ] Infrastructure runner shows online in GitLab admin (tag: `infrastructure`)
- [ ] Shared runner shows online in GitLab admin (tag: `k8s-deploy`)
- [ ] weisssrv jobs execute on the infrastructure runner
- [ ] Docker-in-Docker works for Molecule tests (infrastructure runner only)

### Step 7.7: Verify Scheduled Pipelines

1. Navigate to **CI/CD** > **Schedules**
2. Click **Play** on the version check schedule
3. Verify pipeline runs and `version-check` and `secret_detection` jobs complete (these are the only two jobs that run on scheduled pipelines)

## Part 8: Rollback Plan

If migration issues occur, use this rollback procedure. This should be treated as a
**last-resort** option -- prefer fixing issues in GitLab over rolling back entirely.

### Step 8.1: Restore GitHub as Primary

**Prerequisites** (complete these BEFORE changing remotes):

1. **Disable the GitLab push mirror first** to prevent GitLab from overwriting your
   GitHub changes:
   - Navigate to GitLab **Settings** > **Repository** > **Mirroring repositories**
   - Pause or delete the GitHub mirror
2. **Back up refs** in case you need to recover:
   ```bash
   git fetch --all
   git branch backup-main main
   ```
3. **Verify your GitHub remote is correct**:
   ```bash
   git remote -v
   # Ensure 'github' remote points to the right repo
   ```
4. **Disable GitHub branch protection** temporarily:
   - Navigate to GitHub **Settings** > **Branches**
   - Edit main branch protection, uncheck **Lock branch**
   - Save changes

Once prerequisites are met:

```bash
# Remove GitLab remote
git remote remove origin

# Restore GitHub as origin
git remote rename github origin

# Push any missing commits (force-with-lease is safer than --force,
# but still destructive -- only use after verifying prerequisites above)
git push origin main --force-with-lease
```

### Step 8.2: Re-enable GitHub Actions

1. Go to https://github.com/ericsweiss/weisssrv
2. Navigate to **Settings** > **Actions** > **General**
3. Select **Allow all actions and reusable workflows**
4. Click **Save**

### Step 8.3: Update Workflows

Re-enable workflow triggers by reverting to original `.github/workflows/*.yml` files:

```bash
git checkout <commit-before-migration> -- .github/workflows/
git commit -m "Restore GitHub Actions workflows"
git push
```

### Step 8.4: Remove GitLab Mirror

1. In GitLab, navigate to **Settings** > **Repository** > **Mirroring repositories**
2. Click the delete button next to the GitHub mirror (if not already removed in Step 8.1)
3. Confirm deletion

## Post-Migration Tasks

### Update Documentation

The following files have already been updated to reference GitLab:
- `README.md` - Canonical source notice
- `CLAUDE.md` - Project structure and CI/CD info
- `docs/13-ci-cd.md` - Complete CI/CD documentation
- `.gitlab-ci.yml` - Pipeline configuration

### Update Local Git Config

Configure your local repository to push to GitLab by default:

```bash
# Set GitLab as default push target
git config push.default current
git remote set-url --push origin ssh://git@git.ericsweiss.com:2222/eric/weisssrv.git
```

### Clean Up Stale DDNS CronJob

The DDNS CronJob was moved from the `default` namespace to `cloudflare-ddns`. Remove the
old CronJob to avoid duplicate executions:

```bash
kubectl delete cronjob cloudflare-ddns -n default --ignore-not-found
```

(The DDNS CronJob itself is now part of `kubernetes/infrastructure/configs/cloudflare-ddns/`
and Flux-managed.)

### Notify Collaborators

If others access the repository:
1. Inform them of the GitLab migration
2. Share GitLab instance URL and login instructions
3. Update any external integrations or webhooks

## Troubleshooting

### Pipeline Not Running

1. Check `.gitlab-ci.yml` is in repository root
2. Verify CI/CD is enabled: **Settings** > **CI/CD** > **General pipelines**
3. Check for YAML syntax errors in pipeline editor

### 1Password Token Not Working

1. Verify token starts with `ops_`
2. Check service account has Homelab vault read access
3. Test token locally:
   ```bash
   export OP_SERVICE_ACCOUNT_TOKEN="ops_..."
   op read "op://Homelab/SSH Key/private key"
   ```

### Mirror Sync Failing

1. Check GitHub PAT hasn't expired
2. Verify PAT has correct repository permissions
3. Try re-authenticating the mirror in GitLab settings

### Runner Not Picking Up Jobs

1. Check runner is online in GitLab (**Admin Area** > **CI/CD** > **Runners**)
2. Verify runner tags match job requirements:
   - weisssrv jobs use the `infrastructure` tag (routed via `default: tags: [infrastructure]`)
   - Other projects use `k8s-deploy` tag or no tag (handled by shared runner with `runUntagged: true`)
3. Check runner executor (should be `kubernetes` for k3s runner)
4. Review runner logs:
   ```bash
   # Infrastructure runner
   kubectl logs -n gitlab-runner -l app=gitlab-runner-privileged
   # Shared runner
   kubectl logs -n gitlab-runner -l app=gitlab-runner
   ```

## References

- [GitLab CI/CD Documentation](https://docs.gitlab.com/ee/ci/)
- [GitLab Push Mirroring](https://docs.gitlab.com/ee/user/project/repository/mirror/push.html)
- [1Password Service Accounts](https://developer.1password.com/docs/service-accounts/)
- [PR-Agent Documentation](https://qodo-merge-docs.qodo.ai/)
- [Cursor Bugbot Setup](https://cursor.com/docs/bugbot)
- [OpenAI Codex GitLab Cookbook](https://developers.openai.com/cookbook/examples/codex/secure_quality_gitlab/)
