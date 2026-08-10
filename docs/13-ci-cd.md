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
- **Version checking**: Scheduled checks for available updates, plus a weekly
  `version-bump-bot` that raises (and keeps refreshed) a single never-auto-merged
  MR with the bumps; CI fails if
  `kubernetes/infrastructure/sources/versions-configmap.yaml` is out of sync with `all.yml`

## Shared CI library (`eric/weisssrv-lib`)

Eight of the generic jobs below are **not defined in this repo**. They are
`include:`d from the sibling library project at a pinned tag
(`.gitlab-ci.yml`, the `include:` block):

```yaml
- project: eric/weisssrv-lib
  ref: v0.5.2
  file: /ci/lint/yaml-lint.yml
```

The four templates that take **no inputs** share a single entry, because
`file:` accepts a list while `inputs:` binds per entry:

```yaml
- project: eric/weisssrv-lib
  ref: v0.5.2
  file:
    - /ci/lint/yaml-lint.yml
    - /ci/lint/shellcheck.yml
    - /ci/validate/terraform.yml
    - /ci/security/secret-detection.yml
```

| Library file | Job(s) emitted | Inputs weisssrv overrides |
|---|---|---|
| `/ci/lint/yaml-lint.yml` | `yaml-lint` | none (defaults reproduce the four `yamllint -d relaxed` invocations) |
| `/ci/lint/shellcheck.yml` | `shellcheck` | none |
| `/ci/lint/docs-link-check.yml` | `docs-link-check` | `changes` (widened to `**/*.md` — weisssrv's checker scans every tracked Markdown file, not just `docs/`) |
| `/ci/validate/terraform.yml` | `terraform-fmt` **and** `terraform-validate` | none |
| `/ci/security/secret-detection.yml` | `secret_detection` | none |
| `/ci/test/python-tests.yml` | `python-tests` | `changes` (adds the ansible + docs paths the suite validates — roles, playbooks, docs/, README, CLAUDE) |
| `/ci/maintenance/version-check.yml` | `version-check` | `setup_command` + `check_command` only. Everything else is the library default, because this template was generalised FROM weisssrv's local job. `changes` is deliberately **not** passed: the default `["**/*"]` matches every MR, which is what the local job did by carrying no filter |
| `/ci/validate/flux-lint.yml` | `flux-lint` | `substitute: true`, the kubeconform/kustomize/helm/PyYAML pins + sha256s, cluster/ConfigMap/script paths, and `extra_validation` (the HPA/VPA, scrape/NetworkPolicy, ClusterSecretStore-scope and PVC-storageClassName invariants + `validate-helm-values.py`, which stay weisssrv-local) |

Rules of engagement:

- **Do not re-add a library job inline.** A same-named local job overrides the
  included one silently; change the library instead.
- **Changing library behaviour is a three-step, cross-repo flow**: MR in
  `weisssrv-lib` → the maintainer cuts a tag (releases are deliberate, never
  automatic) → bump every `ref:` in this repo's `include:` block in one MR.
  The library's `docs/VERSIONING.md` and `docs/INCLUDE-CONTRACT.md` own the
  input contract and the upgrade flow.
- **Prove pipeline parity when bumping the ref**: compare the job list and each
  job's script/rules against the previous pipeline before merging. The library
  is consumed by more than one repo, so an input default changed there silently
  changes this pipeline.
- **`variables.WEISSSRV_LIB_REF` is the single source for the pin, and
  `scripts/check-lib-pins.py` enforces it.** `include:` is resolved at
  pipeline-creation time, before that variable exists, so every entry has to
  repeat the tag as a literal — `ref: $WEISSSRV_LIB_REF` silently does not work.
  (A project-level CI/CD variable *is* readable there, but it would move the pin
  out of git, where a bump no longer shows up in a diff and cannot be reverted as
  an MR.) The gate fails the pipeline if any entry drifts from the variable or
  pins a **branch**, which the include contract forbids: a branch deleted after
  merge takes the include with it, and until then the pipeline can change
  behaviour with no commit here at all. Bump the variable, then run
  `scripts/check-lib-pins.py --fix`. It runs in `python-tests` (and `task lint`
  via `scripts:test`), and `.gitlab-ci.yml` is in that job's `changes` list, so
  it fires on its own subject. The script is **vendored byte-identical** from
  the library (`weisssrv-lib/scripts/check-lib-pins.py`) like
  `version-bump-mr.py`; re-copy it when the ref moves rather than editing it
  here.
- Everything weisssrv-specific stays here: the Ansible/molecule jobs, all
  `deploy-*` jobs, the drift plans, `repo-sync-checks` / `repo-policy-checks`,
  `prometheus-config-lint`, and the scripts the library jobs call
  (`scripts/check-doc-links.py`, `scripts/flux-render.sh`,
  `scripts/kubeconform-skipped.py` — the library jobs run the **consumer's**
  copy at a configurable path).

One job is a **pending** ninth include: `version-bump-bot` is the library's
`/ci/maintenance/version-bump-bot.yml` reproduced inline in `.gitlab-ci.yml`
rather than included, so adopting it is a swap of the local job for the
template. It is the write half of a pair whose read half (`version-check`) is
already included above — the bot rewrites pins and raises the MR for when nobody
is looking; version-check reports for whoever already is. The
MR manager it drives, `scripts/version-bump-mr.py`, is already **vendored
byte-identical** from the library (`weisssrv-lib/scripts/version-bump-mr.py` @
v0.2.0) and does not change in that swap, since the template takes the script
path from the consumer tree like every other library job. Nothing compares the
two copies automatically: treat the library as the source and re-copy it when
the ref moves, rather than editing it here. The job's own header comment carries
the exact `include:` block that replaces it.

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

### Runner garbage collection

The `gitlab-runner-reaper` CronJob (`kubernetes/apps/gitlab-runner-reaper/`,
every 15 min) reaps leaked executor pods and their leaked per-job image-pull
credential Secrets in the two runner namespaces.

**What leaks.** The runner manager normally deletes each job's executor pod and
its `kubernetes.io/dockercfg` Secret when the job ends. When many privileged
jobs fail or get cancelled, or the manager restarts mid-cleanup, terminal pods
stay `Error`/`Completed` and their Secrets stay with them. Pods bloat etcd, add
scheduler churn, consume the namespace `pods` ResourceQuota dimension (which can
403 the manager's *next* job-pod create) and trip the maintenance/verify pod
scans; the orphaned dockercfg Secrets pile up as registry-credential blobs with
no owning job. GitLab Runner 19.x has no built-in GC for this case — the
manager-side `[runners.kubernetes] cleanup_*` settings only run on the graceful
end-of-job path, never when the manager itself dies mid-cleanup.

**Pod selection — three independent guards**, any one of which alone excludes a
manager pod:

1. The label `esweiss.com/runner-class` must **exist**. It is set on every
   executor pod via `[runners.kubernetes.pod_labels]` in both runner
   `release.yaml` files; manager Deployment pods are labelled
   `app=gitlab-runner[-privileged]` and never carry it, so a label-existence
   selector is structurally incapable of matching a manager.
2. Phase in `{Succeeded, Failed}` only. A manager is `Running`; an in-flight job
   pod is `Running` (the dind sidecar runs the whole job) or `Pending`.
3. Name matches `runner-*-project-*-concurrent-*` (the manager is
   `gitlab-runner-<hash>`) — a defensive secondary assert.

**Secret selection — three more guards**: `type == kubernetes.io/dockercfg`
(narrowed server-side with a `fieldSelector`, so the list never returns the
runner token or SA-token Secrets at all); name starts with `runner-`; and not
still referenced by a live pod via `imagePullSecrets` or owned via
`ownerReferences.uid`, so a Secret is only reaped once its job pod is gone.

**Grace floors.** A terminal pod is deleted only if its newest container
termination time is more than `MAX_AGE_MINUTES` (30) in the past, measured from
container `finishedAt` — **not** pod creation, so a 2-hour job runs `Running`
for two hours and the clock starts only when it goes terminal. A pod with no
parseable `finishedAt` anywhere is kept (k8s's own node-pod GC handles
node-lost pods). Secrets have no termination time, so they age from
`creationTimestamp` with a much higher floor (`MAX_SECRET_AGE_MINUTES` = 180)
plus the unreferenced guard; a live job never runs three hours here, so the
floor structurally protects an in-flight job's Secret and closes the
create-Secret-then-create-pod race.

**Scale and RBAC.** Lists are paged (`limit=25` + `continue`) so a large backlog
cannot OOM the 64Mi container, and a soft `BUDGET_SECONDS` stops cleanly under
`activeDeadlineSeconds` — partial progress now, the rest on the next run, rather
than a hard deadline-kill that marks the Job failed. Kubernetes RBAC cannot
scope `delete` to a label/phase/type selector, so `pods: delete` +
`secrets: delete` in the two runner namespaces is the least grant that performs
this function (no configmaps, nothing cluster-wide); the remaining protection is
the digest-pinned image, the fixed no-input script, and namespace scope.

## Pipeline Structure

```yaml
stages:
  - build         # Build/push the Molecule CI container images (rebuilt only when Dockerfiles/requirements change)
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

#### Build Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `build-molecule-test` | docker/molecule-test/** (and on a schedule) | Builds + pushes the `molecule-test` image consumed by the test stage |
| `build-molecule-ci` | docker/molecule-ci/**, requirements.txt, ansible/requirements.yml (and on a schedule) | Builds + pushes the `molecule-ci` image; emits the image-digest dotenv for downstream `needs:` |
| `build-hermes-agent` | docker/hermes-agent/**, all.yml | Clones NousResearch/hermes-agent at the pinned tag (verified against `hermes_git_sha`) and builds THEIR Dockerfile — upstream ships no image. Pinned to `esweiss.com/cpu=modern` (the Claude CLI SIGILLs on the Core 2 Quad opt nodes) |
| `build-camofox-browser` | docker/camofox-browser/**, all.yml | Same shape for jo-inc/camofox-browser (the third container of the hermes pod). Builds on every pipeline; the `:<version>`/`:latest` tags push only on `main` |

All four extend the same `.build-molecule-base`: BuildKit registry layer-caching
(`--cache-from :latest`) speeds interactive rebuilds, while the scheduled
full-test canary builds from scratch to keep catching upstream breakage.
Job-level `retry` no longer includes `script_failure`; transient
build/push failures are retried by a bounded in-script loop instead, so a
genuinely broken Dockerfile fails fast.

#### Lint Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `version-check` | All MRs/pushes (soft-fail), schedule, web manual | Check for available updates |
| `repo-sync-checks` | union of both checks' inputs (hosts.yml, `scripts/hosts.env` + generator, all.yml, versions-configmap + generator) | Generated-file freshness, two checks in one job: `scripts/hosts.env` regenerated from the inventory and the versions ConfigMap regenerated from all.yml must match their committed copies. Runs BOTH checks before failing |
| `repo-policy-checks` | union of the checks' inputs (roles/playbooks/inventories, integration-tests, scripts/**, `.gitlab-ci.yml`, versions-configmap, Taskfile.yml, the kube-prometheus-stack release) | Repo-invariant asserts, eight checks in one job: deploy coverage (`check-deploy-coverage.sh`), deploy **host** coverage (`check-deploy-host-coverage.py`), molecule-matrix coverage (`check-molecule-matrix-coverage.sh`), env-secret coverage (`check-env-secret-coverage.py`), backup-artifact app pairing (`check-backup-artifact-apps.py` — the collector's `nas_backup_artifact_apps` vs BackupArtifactStale's `absent()` arms), `FLUX_VERSION` pin vs versions-configmap, kubectl pin ±1 minor of `k3s_version`, and Taskfile smoke (pinned go-task `task --list` + `check-taskfile.sh`). Runs ALL checks before failing |
| `prometheus-config-lint` | Prometheus/Alertmanager config paths, `scripts/prometheus-rule-tests/**` | Extracts rendered rules + alertmanager config, validates with pinned `promtool` / `amtool`, and runs the `promtool test rules` unit tests in `scripts/prometheus-rule-tests/` (`scripts/extract-prometheus-config.py`, `scripts/lint-prometheus-config.sh`) |
| `docs-link-check` | any tracked `**/*.md` + the checker/test | Runs `scripts/check-doc-links.py` over every tracked Markdown file (docs/, both top-level READMEs, all 40 role READMEs, `kubernetes/**/README.md`, `AGENTS.md`, the agent skill); fails on any relative `.md` cross-link whose target file is missing |
| `shellcheck` | scripts/**, ansible/*.sh, ansible/roles/**/*.sh | Shell script linting (includes the ansible-root helpers, e.g. `test-all-roles.sh`) |
| `yaml-lint` | ansible/**, kubernetes/**, .gitlab-ci.yml | YAML syntax validation |
| `ansible-lint` | ansible/** | Ansible best practices |
| `terraform-fmt` | terraform/** | Terraform formatting |

The pip/galaxy-installing jobs and the toolchain-downloading lint jobs
(`flux-lint`, `prometheus-config-lint`) use GitLab `cache:` keyed on their
pinned inputs, so identical dependencies are not re-downloaded every run
(first run after a pin bump is a deliberate cache miss).

#### AI-Review Stage (MRs Only)
| Job | Triggers | Description |
|-----|----------|-------------|
| `pr-agent-review` | All MRs (soft-fail, requires `OP_SERVICE_ACCOUNT_TOKEN`) | AI code review via PR-Agent/Qodo Merge |

#### Validate Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `terraform-validate` | terraform/** | Terraform syntax |
| `terraform-plan` | terraform/cloudflare/** + 1Password | Full Cloudflare plan with credentials (tailscale changes no longer re-plan the Cloudflare module) |
| `tailscale-drift-plan` | terraform/tailscale/** (MR/main, token-guarded) + schedules | Read-only `terraform plan` of the tailnet ACL module against its own state backend; advisory (`allow_failure: true`), deliberately outside validation-gate |
| `authentik-drift-plan` | terraform/authentik/** (MR/main, token-guarded) + schedules | Read-only `terraform plan` of the Authentik SSO module against its own state path; catches out-of-band Admin-UI edits. Advisory, outside validation-gate. The apply stays a supervised manual `terraform apply` (docs/40) |
| `b2-drift-plan` | scripts/b2-bucket-drift.py (MR/main, token-guarded) + schedules | Read-only diff of the `weisssrv-backup` B2 bucket settings against the codified config via the raw B2 API (no terraform — see docs/42). Advisory; reconciling is the supervised `task b2:apply` |
| `flux-lint` | kubernetes/**, ansible/inventories/prod/group_vars/all.yml | `kustomize build` + envsubst (from versions ConfigMap) + kubeconform on every Flux Kustomization; also validates cluster root builds, and runs `scripts/validate-helm-values.py` — `helm template` against the pinned chart for the value-heavy releases, catching typo'd `.spec.values` keys (hard-fails where the chart ships a values.schema.json) and unrenderable values. The versions-extraction render loop is shared with `deploy-verify` via `scripts/flux-render.sh` |

> **Limitation**: chart value validation covers only the releases listed in
> `validate-helm-values.py`'s `RELEASES`, and only as deep as `helm template` + the chart's own
> schema can check. Remaining chart value compatibility issues are only caught at Flux
> reconciliation time. For high-risk chart upgrades, use `task flux:dev-apply` to test in-cluster
> before committing.

#### Test Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `molecule-tests` | main / web / scheduled full-test only (roles, ansible/molecule, playbooks/maintenance, test images, requirements, retry script) | FULL role-scenario matrix (requires Docker-in-Docker). Prod group_vars are deliberately NOT a trigger — no molecule scenario reads them (scenarios pin their own inventory), so a pure version bump no longer fires the matrix |
| `integration-tests` | main / web / scheduled full-test only (integration-tests, roles, ansible/molecule, group_vars/all.yml, test images, requirements, retry script) | FULL multi-stack matrix. all.yml IS a trigger here (the converges `vars_files` it), and `ansible/molecule/**` is too (the converges import its shared prep) |
| `molecule-plan` | MRs only, union of both suites' trigger sets | Runs `scripts/generate-molecule-pipeline.py` against the MR diff: maps changed files to affected role scenarios (transitively through role dependencies) + integration stacks, and emits the child-pipeline YAML artifact. Unknown role dirs fail loudly; over-selects when in doubt |
| `molecule-trigger` | MRs only, same paths | Triggers the generated child pipeline (`strategy: depend` — a red child reds the MR pipeline). The child `include:`s `.gitlab/ci/molecule-jobs.gitlab-ci.yml` and extends the SAME job templates as the static matrices |
| `python-tests` | scripts/**, all.yml, ansible role tasks/READMEs/molecule, ansible/playbooks/**, ansible/integration-tests/**, docs/**, README.md, CLAUDE.md, .claude/**, Taskfile.yml, .gitlab-ci.yml | pytest on the scripts/ suite. Beyond the script unit tests it carries three doc gates (`test_doc_inventories.py`): the README roles table must equal `ansible/roles/`, every documented `ansible-playbook --tags <tag>` must reach a real tag (a bogus tag exits 0 having done nothing), and every `` `task <ns>:<name>` `` in docs/ + the agent files must exist in `Taskfile.yml`. Library job (`/ci/test/python-tests.yml`) with weisssrv's full path list |
| `test-aggregate-molecule` / `test-aggregate-integration` | main pushes | Status-only fan-ins over the two parallel matrices (`artifacts: false`), so `validation-gate` can depend on one job instead of 48 matrix instances |

**Targeted MR matrix.** MR pipelines no longer run the full 43-scenario + 5-stack
matrix; `molecule-plan`/`molecule-trigger` generate and run only the scenarios
affected by the diff (a typical 1-role MR runs 1-4 jobs instead of 48). Full
coverage still runs on every merge to `main` (before any deploy, via
`validation-gate`) and on the scheduled `full-test` canary, so a cross-role
regression a targeted run missed is caught before it can deploy. The shared job
templates live in `.gitlab/ci/molecule-jobs.gitlab-ci.yml` so the child and the
static matrices cannot drift apart.

**Registry pull-through cache.** Each molecule job's fresh DinD daemon pulls the
molecule-test image through the in-cluster cache
(`registry-cache.registry-cache.svc:5000`, `kubernetes/apps/registry-cache`,
docs/27) with a timeout-bounded fallback to the direct registry — a cache outage
only loses the warm hit, never breaks CI.

> **Note:** The molecule/integration retry policy (bounded re-run with jitter on
> transient DinD flakes) lives in `scripts/molecule-retry.sh`, shared by both jobs.
> The molecule fan-out also `needs:` `ansible-lint`/`yaml-lint` (`optional: true`)
> so a fast lint failure short-circuits it before it consumes privileged-runner
> capacity.
>
> **Note:** Test jobs require Docker-in-Docker and a runner with `privileged = true`.
> All weisssrv jobs (including tests) run on the **infrastructure runner**
> (`gitlab-runner-privileged` Helm release, tag: `infrastructure`) which has `privileged = true`.
> Runner is Flux-managed at `kubernetes/apps/gitlab-runner-privileged/release.yaml`;
> push changes to reconcile.

#### Security Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `secret_detection` | All MRs/pushes, schedule | Scan for leaked secrets |

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
the versions-configmap sync check in `repo-sync-checks`). Flux reconciliation itself is push-triggered via the GitLab
agent's Flux integration (poll is the fallback — see docs/29-flux-operations.md).

#### Deploy Stage - Terraform
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-terraform` | terraform/cloudflare/** on main | Apply Cloudflare DNS |

#### Deploy Stage - Ansible Infrastructure
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-ansible-base` | ansible/roles/base/** (+ nic_tuning, resolv_conf, node_exporter_host, alloy_host, apt_signed_repo, textfile_collector, encrypted_swap), ansible/playbooks/base.yml, all.yml | Deploy base packages, SSH, users. all.yml is a trigger so host-side pins consumed here (e.g. `alloy_host_version`) auto-deploy on a version bump |
| `deploy-ansible-proxmox` | ansible/roles/qol/**, ansible/roles/tailscale/**, ansible/roles/postfix_null_client/**, group_vars/proxmox.yml, hosts.yml | Deploy Proxmox host config (qol, tailscale, mail). Deliberately NOT all.yml — its tailscale/plex pins apply via the manual maintenance jobs |
| `deploy-ansible-firewall` | ansible/roles/proxmox_firewall/**, ansible/inventories/prod/** | Deploy Proxmox firewall rules. The ipset render reads the ENTIRE inventory (every host's IP + memberships), so any inventory change re-deploys |
| `deploy-ansible-dns` | ansible/roles/unbound/**, ansible/roles/adguard_home/**, ansible/roles/adguard_sync/**, ansible/roles/postfix_null_client/**, unbound_exporter + prometheus_exporter, ansible/playbooks/dns.yml, all.yml, group_vars/dns.yml, host_vars/dns-0{1,2}.yml, hosts.yml | Deploy DNS stack (includes postfix). all.yml is a trigger so pins like `unbound_exporter_version` auto-deploy; dns.yml group/host vars carry the rewrites + upstreams |
| `deploy-ansible-storage` | ansible/roles/nas_storage/** (+ zfs_exporter, prometheus_exporter, nfs_tls, proxmox_backup, restic_offsite), ansible/playbooks/storage.yml, all.yml | Deploy storage services. all.yml is a trigger so pins like `restic_version`/`rclone_version`/`zfs_exporter_version` auto-deploy |
| `deploy-ansible-mail` | ansible/roles/smtp_relay/**, ansible/roles/postfix_null_client/**, group_vars/mail.yml, host_vars/smtp-relay.yml, hosts.yml | Deploy SMTP relay |
| `deploy-ansible-certs` | ansible/roles/acme_certs/**, host_vars/dns-01.yml, hosts.yml | Deploy certificate distribution (dns-01.yml carries the pinned target host keys) |

#### Deploy Stage - Ansible Applications
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-plex` | ansible/roles/plex/**, ansible/roles/proxmox_lxc/**, ansible/playbooks/plex.yml, group_vars/plex_servers.yml, host_vars/plex.yml, hosts.yml | Deploy Plex LXC container |
| `deploy-gitlab` | ansible/roles/gitlab/**, ansible/roles/apt_signed_repo/**, ansible/roles/zvol_mount/**, ansible/playbooks/gitlab.yml, ansible/inventories/prod/group_vars/gitlab_servers.yml, ansible/inventories/prod/group_vars/all.yml | Deploy GitLab VM and application |
| `deploy-nextcloud` | ansible/roles/nextcloud/** (+ apt_signed_repo, docker_engine, compose_app, zvol_mount, nfs_tls), ansible/playbooks/nextcloud.yml, group_vars/nextcloud_servers.yml, hosts.yml, all.yml | Deploy the Nextcloud VM (.156) docker-compose stack (docs/35) |
| `deploy-immich` | ansible/roles/immich/** (+ apt_signed_repo, docker_engine, compose_app, zvol_mount, nfs_tls), ansible/playbooks/immich.yml, group_vars/immich_servers.yml, hosts.yml, all.yml | Deploy the Immich VM (.157) docker-compose stack (docs/36) |
| `deploy-immich-ml` | ansible/roles/immich_ml/** (+ compose_app, proxmox_lxc), ansible/playbooks/immich-ml.yml, host_vars/immich-ml.yml, hosts.yml, all.yml | Deploy the Immich ML LXC (.158) — version lockstep with `deploy-immich` |
| `deploy-home-assistant-config` | ansible/roles/home_assistant/**, ansible/playbooks/home-assistant.yml | Deploy Home Assistant configuration |

#### K3s Platform and Applications: Flux-Managed

**All Kubernetes workloads deploy via Flux, not CI jobs.** Every platform component
(the `kubernetes/infrastructure/` stages: sources → crds → controllers → configs →
observability) and every application (the resource list in
`kubernetes/apps/kustomization.yaml` — authentik, download-clients, hermes, homarr,
hindsight, recipes, gitlab-runner, gitlab-runner-privileged, gitlab-runner-reaper,
gitlab-agent, registry-cache, tailnet-dns, vm-ingress, wg-easy) is
reconciled by Flux from
`kubernetes/` on every `git push` to `main`. CI only validates (`flux-lint`,
`kubeconform`, `repo-sync-checks`).

The following CI deploy jobs were **removed** (replaced by Flux reconciliation):
`deploy-k3s-platform`, `deploy-k3s-coredns`, `deploy-k3s-authentik`,
`deploy-downloads`, `deploy-recipes`, `deploy-gitlab-ingress`,
`deploy-home-assistant-ingress`, `deploy-plex-ingress`, `deploy-adguard-ingress`,
`deploy-gitlab-runner`, `deploy-gitlab-runner-privileged`.

#### Deploy Stage - Verification
| Job | Triggers | Description | Blocks pipeline? |
|-----|----------|-------------|------------------|
| `deploy-gitlab-verify` | Inherits deploy-gitlab's rules verbatim via `!reference` (ansible/roles/gitlab/**, ansible/roles/apt_signed_repo/**, ansible/roles/zvol_mount/**, ansible/playbooks/gitlab.yml, group_vars/gitlab_servers.yml, group_vars/all.yml) — Ansible-only; the k3s ingress/runner manifests are Flux-managed and do **not** trigger this job. | GitLab smoke tests (HTTP readiness, container registry, SSH port 22). | No (`allow_failure: true`) |
| `deploy-verify` | The union of the `deploy-*` jobs' own `changes:` path filters plus `kubernetes/**/*`, on pushes to main. This is per-role/per-playbook subsets, **not** blanket `ansible/**` (roles wired to no deploy job — e.g. proxmox_vm, proxmox_ha, k3s, zfs_encryption — do not trigger it), and there is **no** `scripts/**` filter (a change only under `scripts/` runs no deploy job). | Runs `scripts/deploy-verify.sh`: server-side dry-run validates rendered manifests against cluster API, triggers Flux reconciliation (fails on timeout), checks all nodes `Ready`, asserts zero Flux resources `Ready=false`, checks ExternalSecret readiness (hard failure on steady-state, warning during bootstrap), verifies GitLab HTTP. | Yes — fails the pipeline on any issue |

> **Note:** The two jobs have different semantics:
> - `deploy-gitlab-verify` is informational (`allow_failure: true`) — pipeline continues on failure.
> - `deploy-verify` is blocking — any `NotReady` node, non-Ready Flux resource, or GitLab outage
>   fails the pipeline. There is no `allow_failure` on this job.

#### Maintenance Stage
All manual buttons except `version-bump-bot`, which is the one **scheduled** job
in this stage (see [Version bump bot](#version-bump-bot) below).

| Job | Description |
|-----|-------------|
| `version-bump-bot` | Weekly (`SCHEDULE_TYPE=version-bump`): bumps the version pins and keeps one open bot MR in sync with them. Never merges |
| `maintenance-k3s-provision` | Provision k3s VMs and deploy cluster |
| `maintenance-update-packages` | OS package updates on all hosts (base, k3s, app VMs) with auto-reboot |
| `maintenance-update-applications` | Application updates: AdGuard Home, Tailscale, Plex |
| `maintenance-update-k3s-nodes` | K3s node rolling update (drain/cordon) |
| `maintenance-proxmox-ha` | Proxmox HA configuration |
| `maintenance-home-assistant-restart` | Restart Home Assistant after config deployment |
| `maintenance-run-all` | Wrapper that runs the six maintenance ops in sequence via `scripts/maintenance-run-with-verify.sh` (verify always runs after) |
| `maintenance-verify` | Post-maintenance cluster health validation (fails on critical issues) |

## Pipeline Triggers

| Trigger | Runs |
|---------|------|
| Merge request | Lint, validate, test, security, AI review stages (no deploy) |
| Push to main | Full validation + auto-deploy |
| Scheduled | Version checking, secret detection, and the advisory `tailscale-drift-plan` (when its token is present). All other jobs (lint, validate, test, ai-review, gate, deploy, maintenance) are excluded — **except** two `SCHEDULE_TYPE`-scoped opt-ins: `SCHEDULE_TYPE=full-test` also runs `build-molecule-test`/`build-molecule-ci` + `molecule-tests` + `integration-tests` as an external-dependency canary (catches upstream image/package breakage between code changes), and `SCHEDULE_TYPE=version-bump` runs `version-bump-bot` (below). |
| Manual (web) | Lint, validate, test stages only. AI review, deploy, gate, and maintenance jobs are excluded. Security (`secret_detection`) runs if branch is `main`. |

## Deployment Pipeline

### Auto-Deploy Behavior

When a merge request is merged to `main`:

1. **Validation stages run first** (lint, validate, test, security)
2. **Validation gate blocks Ansible/Terraform deploys** -- the `validation-gate` job in the `gate` stage must pass before any Ansible/Terraform deploy job can start. The gate depends on `secret_detection` and the two `test-aggregate-*` fan-ins as **required** (non-optional) dependencies, and all path-filtered lint and validate jobs (including `repo-sync-checks`, `repo-policy-checks`, and `docs-link-check`) as `optional: true` dependencies. Path-filtered jobs that were not created are skipped, but `secret_detection` and any path-filtered job that *was* created must succeed or all Ansible/Terraform deployments are blocked. `tailscale-drift-plan` is deliberately excluded (advisory-only, like `pr-agent-review`).
3. **Only changed components deploy** (path-based triggers on Ansible/Terraform jobs)
4. **Kubernetes workloads reconcile via Flux** — independent of CI. Reconciliation
   is push-triggered via the GitLab agent's Flux integration (seconds after a push);
   the 1-minute GitRepository poll remains as fallback (see docs/29-flux-operations.md).
5. **Machine reboots require manual approval** (maintenance stage)

### Deployment Categories

| Category | Mechanism | Jobs / Action | Auto-Deploy | Manual Approval |
|----------|-----------|---------------|-------------|-----------------|
| Terraform | CI job | `deploy-terraform` | Yes | No |
| Ansible Infrastructure | CI jobs | `deploy-ansible-base`, `deploy-ansible-proxmox`, `deploy-ansible-firewall`, `deploy-ansible-dns`, `deploy-ansible-storage`, `deploy-ansible-mail`, `deploy-ansible-certs` | Yes | No |
| Ansible Applications | CI jobs | `deploy-plex`, `deploy-gitlab`, `deploy-nextcloud`, `deploy-immich`, `deploy-immich-ml`, `deploy-home-assistant-config` | Yes | No |
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
- The GitLab agent's Flux integration triggers reconciliation within seconds of the push (the 1-minute GitRepository poll remains as fallback)
- `flux-system` `GitRepository` syncs the new revision
- Top-level `Kustomization`s reconcile in dependency order:
  `infrastructure-sources` → `infrastructure-controllers` → `infrastructure-configs`,
  which then reconciles `infrastructure-observability` and `apps` in parallel
- `helm-controller` upgrades HelmReleases; `kustomize-controller` applies Kustomizations
- **All Secrets** are created by `external-secrets`' `ExternalSecret` CRs that
  reference 1Password item titles via the ClusterSecretStore `onepassword-homelab`
  (Connect provider). CI does not inject any secrets into k8s.
- Version substitutions flow from the `cluster-versions` ConfigMap
  (`kubernetes/infrastructure/sources/versions-configmap.yaml`, generated from
  `all.yml`). The versions-sync check in `repo-sync-checks` fails the pipeline if the
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
| `VERSION_BUMP_BOT_TOKEN` | Variable | Yes | Yes | GitLab PAT (`api` + `write_repository`) used by `version-bump-bot` to push its branch and manage its MR. **Only** required if the version-bump schedule exists; without it that job errors out and nothing else is affected. `CI_JOB_TOKEN` cannot substitute — it cannot push, and the Merge requests API is read-only for job tokens. Store the value in 1Password too (see below) so it is rotatable from the same place as everything else |

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

**Terraform jobs** (dedicated `Cloudflare Terraform Token` item — scoped
Zone:Read + DNS:Edit + Zone Settings:Edit, separate from the in-cluster
`Cloudflare DNS Token`):
```bash
export TF_VAR_cloudflare_api_token=$(op read "op://Homelab/Cloudflare Terraform Token/credential")
export TF_VAR_cloudflare_account_id=$(op read "op://Homelab/Cloudflare Terraform Token/username")
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
the `onepassword-homelab` ClusterSecretStore, Connect provider). The only bootstrap
secrets in the cluster are `external-secrets/op-credentials` and
`external-secrets/onepassword-connect-token`, created once during initial setup.
See `task flux:bootstrap-onepassword` for instructions and `docs/29-flux-operations.md`.

### Required 1Password Items

Core 1Password items used by the CI/CD pipeline (see
[docs/15-credential-rotation.md](./15-credential-rotation.md) "Required 1Password
Items" for the complete list of items referenced by ExternalSecrets in the cluster):

| Item | Fields | Used By |
|------|--------|---------|
| SSH Key | `private key` | Ansible deployments |
| Cloudflare Terraform Token | `credential`, `username` | Terraform `terraform-plan` / `deploy-terraform` |
| Tailscale OAuth | `client id`, `credential` | `tailscale-drift-plan` (read-only ACL drift plan) |
| GitLab API Token | `credential` | `pr-agent-review` (AI code review); also hard-asserted by `task gitlab:deploy` |
| OpenAI API Key | `api-key` | `pr-agent-review` (AI code review) |
| GitHub Token | `credential` | `version-bump-bot` (via `task maintenance:update-all-versions`, which runs its checker under `op run`) |
| GitLab Version Bump Bot Token | `credential` | `version-bump-bot` — the item of record for the `VERSION_BUMP_BOT_TOKEN` CI variable, which is what the job actually reads |

> `version-check` itself does **not** read a 1Password item for GitHub
> rate-limit headroom — it uses the optional masked `GH_API_TOKEN` variable (see
> Optional Variables above). The **GitHub Token** item entered CI with
> `version-bump-bot`, which drives the same Taskfile task an operator runs by
> hand and therefore inherits its `op://Homelab/GitHub Token/credential` env
> reference. `check-versions.py` prefers `GH_API_TOKEN` when both are set.

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

This schedule runs the advisory `version-check` job (a report + MR comment). It
does **not** open MRs — that is the separate bot below.

### Version bump bot

`version-bump-bot` (maintenance stage) turns the weekly report into a reviewable
MR. It runs the same command an operator runs by hand —
`task maintenance:update-all-versions`, which rewrites the pins in
`ansible/inventories/prod/group_vars/all.yml` and regenerates
`kubernetes/infrastructure/sources/versions-configmap.yaml` — and then hands the
working tree to `scripts/version-bump-mr.py`.

**Three outcomes, all idempotent. It never merges, and it never commits anything
outside those two files:**

| Situation | What the bot does |
|---|---|
| Bumps found, no bot MR open | Force-pushes `bot/version-bumps` and opens one MR titled `chore(deps): version bumps` |
| Bumps found, bot MR already open | Updates **that** MR — force-pushes the rebuilt branch and refreshes the title/description. Never opens a second one |
| Bumps found, identical to what the branch already carries | Nothing at all: no push, no MR edit. A weekly re-run of an unreviewed MR does not re-notify |
| No bumps, a bot MR is open | Closes it (the pins it proposed have landed or been superseded) |
| No bumps, no bot MR | Nothing |

The MR body lists the changed files, a diffstat, and the checker's own output
(the full log is the `version-bump-report.txt` job artifact, kept 30 days).
Review and merge it like any other MR — it is subject to the same MR pipeline,
including `repo-sync-checks`, which is what proves the two files stay in sync.

**Setup** (one-time, both steps are required before the bot can run):

1. **Mint the PAT.** A GitLab personal (or project) access token with `api` +
   `write_repository`, on an account with at least Developer access to
   `eric/weisssrv`. Store it in 1Password as **GitLab Version Bump Bot Token**
   (field `credential`) — see
   [docs/15-credential-rotation.md](./15-credential-rotation.md) — and add the
   same value under **Settings > CI/CD > Variables** as `VERSION_BUMP_BOT_TOKEN`,
   **Masked** and **Protected** (masked matters: the token is used in a git push
   URL; protected keeps it off non-protected refs, which is why the schedule and
   the manual trigger both target `main`).
2. **Create the schedule** — separate from the version-check schedule above, so
   the bot cannot fire on unrelated scheduled pipelines:
   - **CI/CD > Schedules > New schedule**
   - **Description**: `Weekly version bump MR`
   - **Interval pattern**: `0 10 * * 1` (Monday 10am — after the version-check
     schedule, so the advisory report lands first)
   - **Target branch**: `main`
   - **Variables**: `SCHEDULE_TYPE` = `version-bump`

   Equivalent API call (`GITLAB_TOKEN` needs `api`):

   ```bash
   # Create the schedule
   SCHEDULE_ID=$(curl -sS --request POST \
     --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
     --url "https://git.ericsweiss.com/api/v4/projects/eric%2Fweisssrv/pipeline_schedules" \
     --form description="Weekly version bump MR" \
     --form ref=main \
     --form cron="0 10 * * 1" \
     --form cron_timezone="America/New_York" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

   # Scope it to this job
   curl -sS --request POST \
     --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
     --url "https://git.ericsweiss.com/api/v4/projects/eric%2Fweisssrv/pipeline_schedules/${SCHEDULE_ID}/variables" \
     --form "key=SCHEDULE_TYPE" --form "value=version-bump"
   ```

**Running it by hand.** Either press **Play** on the schedule
(CI/CD > Schedules > ⏵), or start a web pipeline on `main`
(CI/CD > Pipelines > **Run pipeline**, branch `main`) and play the manual
`version-bump-bot` job in the maintenance stage. The web path needs no
`SCHEDULE_TYPE`. Both are safe to repeat: a re-run with unchanged bumps leaves
the open MR untouched. Locally, the equivalent without the MR machinery is
`task maintenance:update-all-versions` followed by a normal branch + MR.

**When it fails.** The job is red rather than silent by design in the case that
matters most: if the version checker errors (upstream API change, rate limit)
and wrote **no** bumps, the bot refuses to read that as "everything is up to
date" — because acting on it would close a still-valid open MR. If the checker
errored but some pins *did* resolve, it ships those and the MR body carries the
checker's `WARNING` block naming what failed.

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

Scanning scope:
- `docs/*.md` is scanned normally — content-shape allowlists in
  `.gitleaks.toml` cover the placeholder forms docs use (`op://` references,
  `[your-...]` placeholders, anchored fake fixture tokens)
- The molecule / integration-test trees are scanned by every rule EXCEPT the
  `private-key` rule (rule-scoped allowlist for the throwaway PEM fixtures a
  test run generates); any other secret type committed there is still caught

## Runner Network Boundaries

Job pods carry an `esweiss.com/runner-class` label set by each runner's
executor config, and NetworkPolicies in the `gitlab-runner` namespace
(`kubernetes/apps/gitlab-runner/networkpolicy.yaml`) enforce per-class
egress:

- **shared** (other projects' untagged jobs): internet-only — DNS, the
  kube-API, and the internal Traefik VIP are allowed, but RFC1918,
  CGNAT (`100.64.0.0/10`, i.e. the tailnet), and link-local ranges are
  blocked.
- **infrastructure** (weisssrv-tagged jobs): unrestricted egress —
  these jobs run ansible/SSH against the whole LAN by design.
- All ingress to the namespace is denied. Runner managers get DNS, the
  kube-API, and public HTTPS egress (GitLab sits behind Cloudflare, so
  its addresses aren't enumerable — the policy allows :443 to non-RFC1918
  destinations rather than a fixed GitLab IP set).

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

The former per-stage workflow stubs (`lint.yml`, `molecule.yml`,
`kubernetes.yml`, `terraform.yml`, `integration-tests.yml`) were collapsed
into a single inert placeholder, `.github/workflows/ci-disabled.yml`. It uses
`on: {}` so it can never trigger (not even via `workflow_dispatch`) — all CI
runs on GitLab; GitHub is a read-only mirror.

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
   op read "op://Homelab/Cloudflare Terraform Token/credential"
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
   - Check runner pods: `kubectl get pods -n gitlab-runner-privileged -l release=gitlab-runner-privileged` (the privileged runner has its own namespace)

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
