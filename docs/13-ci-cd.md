# CI/CD Pipelines

This document covers the GitLab CI/CD pipeline for automated testing, validation, and deployment.

## Overview

The repository uses **self-hosted GitLab** as the canonical source with CI/CD:

- **Canonical source**: https://git.ericsweiss.com/eric/weisssrv
- **GitHub mirror**: https://github.com/ericsweiss/weisssrv (read-only, auto-synced)

CI/CD features:
- **Linting**: Ansible playbooks, Terraform code, shell scripts, Kubernetes manifests, Flux Kustomizations
- **Validation**: Terraform plan, `kustomize build` + envsubst + kubeconform on every Flux Kustomization, Python script unit tests
- **Testing**: pytest for the script suite, Molecule multi-role integration tests (role scenarios run in `weisssrv-lib`)
- **Security**: GitLab native secret detection
- **Auto-deployment**: Ansible + Terraform deploy on merge to main. **Kubernetes workloads
  are reconciled by Flux directly from git** — there are no CI deploy jobs for k8s.
- **Version checking**: Scheduled checks for available updates, plus a weekly
  `version-bump-bot` that raises (and keeps refreshed) a single never-auto-merged
  MR with the bumps; CI fails if
  `kubernetes/infrastructure/sources/versions-configmap.yaml` is out of sync with `all.yml`

## Shared CI library (`eric/weisssrv-lib`)

A set of library templates (plus reusable fragments) is **not defined in this
repo**. They are `include:`d from the sibling library project at a pinned tag —
the `include:` block in `.gitlab-ci.yml` is the source of truth for which ones,
and the table below explains only what this repo overrides:

```yaml
- project: eric/weisssrv-lib
  ref: <WEISSSRV_LIB_REF>   # the literal tag; see variables: in .gitlab-ci.yml
  file: /ci/lint/yaml-lint.yml
```

Every entry carries the same literal tag, and `scripts/check-lib-pins.py`
enforces that it equals `variables.WEISSSRV_LIB_REF` (`--fix` rewrites them). It
does not scan Markdown, so this snippet deliberately shows the variable name
rather than a tag that would go stale here.

Templates that take **no inputs** share a single entry, because `file:` accepts a
list while `inputs:` binds per entry.

| Library file | Job(s) emitted | Inputs weisssrv overrides |
|---|---|---|
| `/ci/lint/yaml-lint.yml` | `yaml-lint` | `config: "-c lint/yamllint-relaxed.yml"` (without it the job runs yamllint's shipped `-d relaxed`, not the vendored profile), `targets` (`ansible/ kubernetes/ terraform/ scripts/ lint/ .gitlab-ci.yml .gitlab/ Taskfile.yml taskfiles/`), and a widened `changes` |
| `/ci/lint/shellcheck.yml` | `shellcheck` | `direct_globs` (`scripts/*.sh terraform/*/*.sh`) and `find_dir: ""` — `ansible/*.sh` is dropped and there is no local roles tree under `ansible/` to walk |
| `/ci/lint/docs-link-check.yml` | `lint-docs-links` | `job_name` (the library default is `docs-link-check`), and `changes` — the library default (`**/*.md` plus `scripts/check-doc-links.py`) plus `.gitlab-ci.yml`, which carries the `CHECK_DOC_LINKS_*` variables the gate reads |
| `/ci/lint/ansible-lint.yml` | `ansible-lint` | `targets: "ansible/"`, `galaxy_requirements: ansible/requirements.yml`, `collections_path: ""` (no in-repo collection, so the only search path is the galaxy install), `image: python:3.13` (galaxy needs git), `changes` (adds `.ansible-lint` and `lint/yamllint-relaxed.yml`) |
| `/ci/lint/python-lint.yml` | `python-lint` | `targets: "scripts kubernetes"` (`kubernetes` covers the ConfigMap-mounted Python), `config: --config ruff.toml` (the library's `lint/ruff.toml`, vendored to the repo root), `ruff_version: "0.16.0"` |
| `/ci/deploy/deploy-base.yml` | `.deploy-base` (hidden) | `op_vault`, `ansible_version`, `apt_packages`, `ansible_dir`, `hosts_env`, `tags` |
| `/ci/deploy/kubectl-setup.yml` | `.kubectl-setup` (hidden) | `op_vault`, `kubeconfig_item`, and this cluster's `kubectl_version` / `kubectl_sha256`. The pin stays in `.gitlab-ci.yml` as an input, which is where the vendored `check-kubectl-version-pin.py` reads it |
| `/ci/lint/comment-length.yml` | `comment-length` | `config: "--config scripts/comment-length.yaml"` — the scope file listing the vendored copies, whose comments only a re-vendor can change |
| `/ci/validate/terraform-drift-plan.yml` | `tailscale-drift-plan`, `authentik-drift-plan`, `unifi-drift-plan` (one include each) | `image: $TF_JOB_IMAGE` (the template default is an older patch, and a plan cannot read state an apply wrote with a newer one), `job_name`, `module_dir`, `state_name`, `changes`, `secrets_exports` (that module's `op read` block) and `tags: ["infrastructure"]`. `secrets_guard` stays at the default `"true"`: a credential in it would delete the scheduled detector instead of reding it |
| `/ci/validate/terraform.yml` | `terraform-fmt` **and** `terraform-validate` | `image: hashicorp/terraform:1.16.5`, digest-pinned — the exact patch the plan/apply jobs run, so a plan cannot be refused by a different patch |
| `/ci/validate/flux-lint.yml` | `flux-lint` | `substitute: true`, the kubeconform/kustomize/helm/PyYAML pins + sha256s, cluster/ConfigMap/script paths, `expected_skipped_file` (the committed unvalidated-kind baseline, so an unreachable catalog reds the job), a widened `changes`, and `extra_validation` (the weisssrv-local corpus gates) |
| `/ci/security/secret-detection.yml` | `secret_detection` | `cpu_selector: "esweiss.com/cpu=modern"` — gitleaks SIGILLs without POPCNT, so the scan is pinned off the Core 2 Quad opt nodes |
| `/ci/test/python-tests.yml` | `python-tests` | `image: $PY_JOB_IMAGE` (the template default is below the Python ansible 14 needs), `changes` (adds the ansible + docs paths the suite validates), `setup_command` (clones weisssrv-lib so the vendored-byte-identity gate can run, then fetches jq with `scripts/ci-fetch-tools.py`), and `pytest_version` / `pyyaml_version`, so the repo `variables:` block stays the single source those pins are checked against |
| `/ci/review/pr-agent.yml` | `pr-agent-review` | `secrets_source: env`, `gate: "$OPENAI__KEY && $GITLAB__PERSONAL_ACCESS_TOKEN"`, plus a `needs:` override limiting it to the lint + secret-detection jobs so the review does not wait on the DinD suite. Model, effort and timeouts are the template defaults, which already equal this repo's values |
| `/ci/maintenance/version-check.yml` | `version-check` | `setup_command` + `check_command` only; everything else is the library default |

Why some of those overrides are the shape they are:

- **ansible-lint** gets no `config:` input. The job runs from the repo root and
  ansible-lint discovers `./.ansible-lint` by walking up, which is why that file
  sits at the root rather than in `ansible/`.
- **flux-lint**'s `changes` adds the two site-data files the gates read and the
  gate/render scripts, so loosening a policy file or a gate cannot skip the job.
- **version-check** deliberately passes no `changes`: the default `["**/*"]`
  matches every MR, which is what the local job did by carrying no filter.
- **pr-agent-review**'s gate cannot be `$OP_SERVICE_ACCOUNT_TOKEN`. That
  variable is protected and therefore absent on MR refs, which would delete the
  reviewer from every MR.

Four fragments emit no job of their own and are consumed via `extends:` /
`!reference` exactly as their former inline copies were:
`/ci/templates/dep-cache.yml` (`.dep-cache`),
`/ci/templates/install-1password.yml` (`.install-1password`,
`.install-1password-alpine`), `/ci/templates/terraform-http-backend.yml`
(`.terraform-http-backend`) and `/ci/templates/docker-dind.yml`
(`.docker-dind`, which `.build-image-base` extends; `dind_service` is held at
24.0 because 27.x's BuildKit frontend rejects upstream hermes' symbolic
`COPY --chmod`).

**Pending adoption.** This table is the authority on what the library has
extracted but this pipeline does not yet include (the library keeps no
per-consumer ledger — since v0.9.0 each consumer records its own adoption
state); each entry is blocked on the local block it would replace:

| Library file | Local counterpart | Blocker |
|---|---|---|
| `/ci/deploy/ansible-deploy.yml` (+ its image) | the hand-written `deploy-ansible-*` job bodies, and the `.maintenance-base` / `.k3s-deploy-base` blocks layered on them | each job carries bespoke `variables:` and `rules:`, so adoption is per job; the image stays pinned locally until the library one is adopted |
| `/ci/lint/runbook-anchors.yml` | the `runbook-anchors` check inside `check-repo-policies`, triggered by `.paths-repo-policy` | the check shares that job's single pod with twenty others; splitting one out trades a job slot for nothing until the job is re-cut |
| `/ci/lint/terraform-tflint.yml` | the local `terraform-tflint` job and its `.paths-terraform-lint` path list | the local job runs all four roots in one pass; the include is per-root |
| `/ci/deploy/cluster-verify-base.yml` | the `.k3s-deploy-base` definition in `.gitlab-ci.yml` | the local base layers op, kubectl and a reachability probe the fragment does not carry |
| `/ci/validate/cluster-drift-plan.yml` | the `.k3s-deploy-base` definition in `.gitlab-ci.yml` | depends on adopting `cluster-verify-base.yml` first: the template extends that fragment by name |

Since the library keeps no per-consumer ledger, this table **is** this repo's
adoption record. `scripts/test_ci_gates.py::TestPendingAdoptionTable` keeps it
honest: it checks every row's library path and local anchor, and sweeps for
library templates that are neither included, tabled, nor declared not-consumed.

Every row names its local counterpart as a backticked `.anchor`, because that is
what the gate checks: the anchor's DEFINITION still existing is what "not yet
adopted" means, and a row whose definition is gone must be dropped in the
adopting MR. `/ci/build/docker-build.yml` is deliberately absent from the
table: `.build-image-base` extends the `.docker-dind` fragment instead, so the
full build-job template is not on an adoption path here. The gate's
not-consumed declaration records that with its reason, as it does for every
other extracted template this pipeline deliberately does not include.

The one remaining local job with a library counterpart, `version-bump-bot`, is
documented at [Version bump bot](#version-bump-bot).

Rules of engagement:

- **Do not re-add a library job inline.** A same-named local job overrides the
  included one silently; change the library instead.
- **Changing library behaviour is a three-step, cross-repo flow**: MR in
  `weisssrv-lib` → the maintainer cuts a tag (releases are deliberate, never
  automatic) → bump every `ref:` in this repo's `include:` block in one MR.
  The library's `docs/VERSIONING.md` and `docs/INCLUDE-CONTRACT.md` own the
  input contract and the upgrade flow.
- **The library tag also pins the Ansible roles.** Since the migration to the
  `weisssrv.infra` collection, `ansible/requirements.yml` pins the same library
  repo at a release tag. A library bump is therefore a CI change *and* a
  platform change — bump both, and expect every `deploy-*` job to fire (they all
  list `ansible/requirements.yml` in `changes:`).
- **Prove pipeline parity when bumping the ref**: compare the job list and each
  job's script/rules against the previous pipeline before merging. The library
  is consumed by more than one repo, so an input default changed there silently
  changes this pipeline.
- **The pin in git is the record of what was validated.** A pipeline proves this
  cluster state against the tag in `variables.WEISSSRV_LIB_REF` at that commit,
  and the gates hold `ansible/requirements.yml` and the four Terraform `?ref=`
  pins equal to it. After a rollback, `git show <sha>:.gitlab-ci.yml` names the
  library release that state was tested against, so no separate table is kept.
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
- **The molecule fallback image tags ride the same pin.** Each integration
  scenario writes `${MOLECULE_TEST_IMAGE:-…/molecule-test:vX.Y.Z}` and
  `ansible/TESTING.md` repeats it. CI always overrides the variable
  (`.gitlab/ci/integration-jobs.yml` builds it from `$WEISSSRV_LIB_REF`), so a
  stale literal is invisible in the pipeline and only makes a LOCAL
  `task ansible:test-integration` run validate against an old image.
  `scripts/check-molecule-image-pin.py` gates them (via `python-tests` /
  `scripts:test`) and `--fix` rewrites them. It is site-local rather than part of
  `check-lib-pins.py` because that script is vendored byte-identical.
- **Pins duplicated into `include:` inputs are gated too.** `include:` inputs
  cannot read `variables:`, so `kustomize_version`, `kustomize_sha256`,
  `pytest_version` and `pyyaml_version` appear both places. The `ci-pin-parity`
  check in `check-repo-policies` fails the pipeline when a copy drifts.
- **Each duplicated pin is a floor assertion, not a copy of a copy.** GitLab
  resolves `include:` at pipeline-creation time, before the `variables:` block
  exists, so an include input cannot read `$KUSTOMIZE_VERSION` and friends. Each
  pin is written twice and `check-ci-pin-parity.sh` holds the copies equal. The
  compared set is derived from the file, and `FLOOR_PINS` asserts a floor on
  that derivation so a parser regression cannot pass by inspecting zero pins.
- **`check-collection-pin-trigger.py` selects jobs by the `deploy-` name
  prefix**, not by stage, so a job inheriting `stage: deploy` through `extends:`
  is still seen. It resolves `!reference [.paths-x, changes]` against the same
  document, so a job using the shared-paths convention still contributes its
  literal paths.
- Everything weisssrv-specific stays here: the integration-test job, all
  `deploy-*` jobs, the drift plans, `check-generated-files` / `check-repo-policies`,
  `prometheus-config-lint`, and the scripts the library jobs call
  (`scripts/check-doc-links.py`, `scripts/flux-render.sh`,
  `scripts/kubeconform-skipped.py` — the library jobs run the **consumer's**
  copy at a configurable path).

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

The `gitlab-runner-reaper` CronJob (every 15 min) reaps leaked executor pods and
their per-job dockercfg Secrets in both runner namespaces. Selection guards,
grace floors and the RBAC rationale are in
[kubernetes/apps/gitlab-runner-reaper/README.md](../kubernetes/apps/gitlab-runner-reaper/README.md).

A partial sweep exits 0, so `GitlabRunnerReaperPartialSweep` (a Loki ruler rule)
fires on the `BUDGET STOP` line the reaper logs when it runs out of budget before
finishing. Read the CronJob logs for the namespaces it skipped; repeated stops
mean orphan pods and Secrets are accumulating and the budget needs raising.

## Pipeline Structure

```yaml
stages:
  - lint          # Code quality checks
  - validate      # Schema and configuration validation
  - test          # Unit and integration tests
  - security      # Secret detection scanning
  - build         # Build/push the two app container images upstream does not ship
  - ai-review     # AI-powered code review (MRs only, after tests/security)
  - gate          # Validation gate (blocks deploys until all checks pass)
  - deploy        # Auto-deploy on merge to main
  - verify        # Post-deploy verification — runs even when a deploy failed
  - maintenance   # Manual approval required (reboots, HA changes)
```

### Jobs by Stage

#### Build Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `build-hermes-agent` | docker/hermes-agent/**, all.yml | Clones NousResearch/hermes-agent at the pinned tag (verified against `hermes_git_sha`) and builds THEIR Dockerfile — upstream ships no image. Pinned to `esweiss.com/cpu=modern` (the Claude CLI SIGILLs on the Core 2 Quad opt nodes) |
| `build-camofox-browser` | docker/camofox-browser/**, all.yml | Same shape for jo-inc/camofox-browser (the third container of the hermes pod). Builds on every pipeline; the `:<version>`/`:latest` tags push only on `main` |

The builds sit after the security stage, so no image is pushed before Secret
Detection has run, and neither build job carries a `needs:`.

Both extend `.build-image-base`: BuildKit registry layer-caching
(`--cache-from :latest`) speeds interactive rebuilds. Neither job runs on any
schedule — their first rule is `if: schedule → when: never`, with no `full-test`
exemption — so upstream breakage in these two images surfaces on the next
pipeline that touches their paths, not on a canary.
Job-level `retry` does not include `script_failure`; transient build/push
failures are retried by a bounded in-script loop instead, so a genuinely broken
Dockerfile fails fast.

The **Molecule images are no longer built here** — `molecule-ci` and
`molecule-test` are published by `weisssrv-lib` and pulled at
`$WEISSSRV_LIB_REF`.

#### Lint Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `version-check` | All MRs/pushes (soft-fail), schedule, web manual | Check for available updates |
| `check-generated-files` | union of both checks' inputs (hosts.yml, `scripts/hosts.env` + generator, all.yml, versions-configmap + generator) | Generated-file freshness, two checks in one job: `scripts/hosts.env` regenerated from the inventory and the versions ConfigMap regenerated from all.yml must match their committed copies. Runs BOTH checks before failing |
| `check-repo-policies` | union of the checks' inputs — playbooks, inventories, integration-tests, `scripts/**`, `kubernetes/**`, `.gitlab-ci.yml`, versions-configmap, `Taskfile.yml` plus `taskfiles/**/*`, the kube-prometheus-stack release | Every repo-invariant assert in one job, failing once at the end — see below |
| `prometheus-config-lint` | Prometheus/Alertmanager config paths, `scripts/prometheus-rule-tests/**` | Extracts rendered rules + alertmanager config, validates with pinned `promtool` / `amtool`, and runs the `promtool test rules` unit tests in `scripts/prometheus-rule-tests/` (`scripts/extract-prometheus-config.py`, `scripts/lint-prometheus-config.sh`) |
| `lint-docs-links` | any tracked `**/*.md` + the checker/test | Runs `scripts/check-doc-links.py` over every tracked Markdown file (docs/, both top-level READMEs, `kubernetes/**/README.md`, `AGENTS.md`, the agent skill); fails on any relative `.md` cross-link whose target file is missing |
| `comment-length` | every file | Runs `scripts/check-comment-length.py` with this repo's scope config. Library job (`/ci/lint/comment-length.yml`) with a local `needs: [yaml-lint]` override, so it queues behind yaml-lint instead of opening a second fan-out root |
| `check-role-inputs` | `ansible/inventories/prod/**`, `ansible/playbooks/**`, `ansible/requirements.yml`, the gate + `.gitlab-ci.yml` | Installs the pinned `weisssrv.infra` collection and runs `scripts/check-role-inputs.py` over the inventory: an opt-in role invoked with its flag set nowhere, and an asserted role input with no default and no assignment. Mirrors `task lint:role-inputs` |
| `check-integration-matrix` | `ansible/integration-tests/**`, `.gitlab/ci/integration-jobs.yml`, the gate + `scripts/ci_yaml.py` | Runs `scripts/check-integration-matrix-coverage.py` both directions: a suite with no `parallel:matrix` entry, and a matrix entry naming no suite |
| `shellcheck` | `scripts/**`, `terraform/*/*.sh`, `.gitlab-ci.yml` | Shell script linting |
| `yaml-lint` | ansible/**, kubernetes/**, .gitlab-ci.yml | YAML syntax validation |
| `ansible-lint` | ansible/** | Ansible best practices over `ansible/` (playbooks, integration tests, inventories). Its `syntax-check` rule runs `ansible-playbook --syntax-check` per playbook, so the roles are resolved from the installed `weisssrv.infra` collection |
| `python-lint` | `scripts/**/*.py`, `kubernetes/**/*.py`, `ruff.toml`, `.gitlab-ci.yml` | `ruff check` over `scripts` and `kubernetes` (the ConfigMap-mounted Python) against the repo-root `ruff.toml` (vendored from the library) |
| `terraform-fmt` | terraform/** | Terraform formatting |
| `terraform-tflint` | terraform/** | `tflint --recursive` over all four roots at `--minimum-failure-severity=error` |
| `flux-lint` | `kubernetes/**`, `all.yml`, the gate scripts and the site-data files they read (the `changes:` list on the include is the source of truth) | `kustomize build` + two-ConfigMap envsubst + kubeconform on every Flux Kustomization, then the corpus gates — see below |

#### What `check-repo-policies` asserts

Each gate runs before the job fails, so one red pipeline shows every violation.

- Deploy coverage — `check-deploy-coverage.sh`, which owns the path /
  `changes:` / `requirements.yml` half. `check-deploy-host-coverage.py` owns the
  other half: every host a playbook declares a role for is reached by some
  deploy job. Neither covers the other's ground.
- Collection-pin trigger — `check-collection-pin-trigger.py`: every
  playbook-running `deploy-*` job must also list `ansible/requirements.yml`, the
  only in-repo signal that a role's content changed.
- Deploy **host** coverage — `check-deploy-host-coverage.py`.
- Backup-artifact app pairing — `check-backup-artifact-apps.py`.
- NetworkPolicy `except:` parity — `check-netpol-except-parity.py`.
- Cluster-identity literals — `check-cluster-literals.py`, cross-checked against
  the Ansible inventory.
- Generated-file freshness for the third generated file —
  `generate-host-log-staleness.py --check`. The other two,
  `scripts/hosts.env` and the versions ConfigMap, are checked in
  `check-generated-files`. This repo has three generators and all three outputs must
  be regenerated and committed before lint is green: `task hosts:sync`,
  `task flux:sync-versions`, `task flux:sync-host-log-staleness`.
- Busybox pin parity — `post-maintenance-verify.sh` vs `busybox_version`.
- CI pin parity — `include:` inputs vs the `variables:` single sources.
- Paired-pin freshness — `check-versions.py --check-partner-pins` against
  `$CI_MERGE_REQUEST_DIFF_BASE_SHA`, so a version that moved without its
  checksum, digest or git SHA reds the MR. Skipped off an MR pipeline, which
  has no diff base.
- Flux version pin — `check-flux-version-pin.py`: the CI `flux` CLI pin, the
  versions ConfigMap and every committed `gotk-components.yaml` header name one
  version and the four stock controllers.
- kubectl pin within one minor of `k3s_version`.
- Tailscale policy syntax — `task lint:tailscale-policy`. Nothing else parses
  `policy.hujson` before the supervised apply; terraform reads it via `file()`.
- Taskfile smoke — pinned go-task `task --list` plus `check-taskfile.sh`.

The `run_check` list in the job is authoritative.

#### What `flux-lint` adds beyond kubeconform

- Each cluster root is built as well as each Flux Kustomization.
- The tenants directory is appended to the rendered corpus, which is what lets
  `check-secretstore-scope.py` see tenant stores at all.
- Corpus gates: every gate `scripts/flux-corpus-gates.sh` runs. That script is
  the source of truth for the set and this doc keeps no copy of it — each gate
  there carries a comment saying what it catches.
- `scripts/validate-helm-values.py` — `helm template` against the pinned chart,
  hard-failing where the chart ships a `values.schema.json`.
- Substitution comes from both the `cluster-versions` and `cluster-config`
  ConfigMaps, which is why `flux_render_script` is `scripts/flux-env.sh`.
- Substitution keys come from `scripts/flux-env.sh`, which `deploy-verify` also
  uses, so both resolve `${cluster_*}` the same way; `flux-env.sh` delegates
  per-file parsing to the vendored `scripts/flux-render.sh`.
- The `flux-lint` job's `extra_validation` input is asserted by
  `scripts/test_flux_corpus_gates.py` to be the same
  `scripts/flux-corpus-gates.sh` call `taskfiles/flux.yml` makes, both passing
  the versions ConfigMap, so both sides move in one commit. The `python-tests`
  job additionally runs `check-nfs-tls.py` over the PV manifests read straight
  from git, so a PV edit meets it even when `flux-lint` is not in the pipeline.

> **Limitation**: chart value validation covers only the releases listed in
> `scripts/helm-values-releases.yaml`, and only as deep as `helm template` plus
> the chart's own schema can check. Remaining chart-value compatibility issues
> surface at Flux reconciliation time; for high-risk chart upgrades use
> `task flux:dev-apply` to test in-cluster before committing.

The pip/galaxy-installing jobs and the toolchain-downloading lint jobs
(`flux-lint`, `prometheus-config-lint`) declare GitLab `cache:` keyed on their
pinned inputs, backed by the in-cluster **Garage S3 cache**
(`kubernetes/apps/ci-cache` — its README carries the design and the
degrade-not-break posture; `CiCacheDown` alerts on outage). Both runners
share one bucket (`[runners.cache]` `Shared = true`), so a cache warmed by
either runner serves both. A cold cache or a dead backend only re-downloads
dependencies — nothing fails.

#### AI-Review Stage (MRs Only)
| Job | Triggers | Description |
|-----|----------|-------------|
| `pr-agent-review` | All MRs (soft-fail, gated on `$OPENAI__KEY`) | AI code review via PR-Agent/Qodo Merge. Library job (`/ci/review/pr-agent.yml`) — model, effort and timeouts are the template defaults. `secrets_source: env` keeps 1Password out of a job whose CI config comes from the branch under review; its credentials are the `GITLAB__PERSONAL_ACCESS_TOKEN` / `OPENAI__KEY` CI variables, and the gate is the same key so an absent review means the reviewer genuinely could not run |

#### Validate Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `terraform-validate` | terraform/** | Terraform syntax |
| `check-deploy-playbooks` | ansible/playbooks/**, ansible/inventories/prod/**, ansible/requirements.yml, ansible/ansible.cfg, `.gitlab-ci.yml` | Credential-free (no 1Password, no SSH) so it runs on MRs too. Installs the pinned collection, parses every `ansible-playbook` invocation out of each `deploy-*`/`maintenance-*` job's own `script:`, and asserts each playbook exists and each `--tags` selection reaches a real task — a bogus tag exits 0 having deployed nothing. Two known gaps, both stated in the job header: it cannot catch a job that forgot an `op://` variable, and it does not follow a `bash scripts/*.sh` wrapper, so the six invocations inside `scripts/maintenance-all-ops.sh` (which `maintenance-run-all` delegates to) are unchecked — they are duplicates of the individual maintenance jobs today, but a playbook or tag added only there would go unwalked |
| `terraform-plan` | terraform/cloudflare/** + 1Password | Full Cloudflare plan with credentials (tailscale changes no longer re-plan the Cloudflare module). Its MR rule is **inert** while `OP_SERVICE_ACCOUNT_TOKEN` is protected — see the credential note below |
| `tailscale-drift-plan` | terraform/tailscale/** on **main** + schedules | Read-only `terraform plan` of the tailnet ACL module against its own state backend; advisory on exit 2 via `allow_failure: exit_codes: [2]`, so drift warns while a broken plan — plan error, expired credentials, unreachable endpoint, state lock — exits 1 and fails red; deliberately outside validation-gate. No MR rule — see the credential note below |
| `authentik-drift-plan` | terraform/authentik/** on **main** + schedules | Read-only `terraform plan` of the Authentik SSO module against its own state path; catches out-of-band Admin-UI edits. Advisory, outside validation-gate. The apply stays a supervised `task terraform:authentik-apply` (docs/40). No MR rule — see the credential note below |
| `unifi-drift-plan` | terraform/unifi/** on **main** + schedules | Read-only `terraform plan` of the UniFi network module (VLANs, firewall zones and policies, WLANs) against its own state path; catches out-of-band UniFi-console edits. Advisory, outside validation-gate. The apply stays a supervised `task terraform:unifi-apply` (docs/46). No MR rule — see the credential note below |
| `cluster-drift-plan` | schedules (`allow_failure: exit_codes: [1]`: 0 clean, 1 drift, 2 error, so an auth or API error stays red instead of reading as allowed drift) | Reads the live cluster and runs `check-live-cpu-limits.py` and `check-unmanaged-secrets.py` against it — the drift classes no rendered-manifest gate can see |
| `unifi-settings-drift` | scripts/unifi-settings-drift.py + scripts/unifi-settings.json on **main** (token-guarded) + schedules | Read-only check of the console-owned UniFi settings the terraform module ignores (IPS posture) against `scripts/unifi-settings.json`. Advisory on drift (exit 1); an auth or API error (exit 2) fails red. No MR rule — see the credential note below |
| `b2-drift-plan` | scripts/b2-bucket-drift.py on **main** (token-guarded) + schedules | Read-only diff of the `weisssrv-backup` B2 bucket settings against the codified config via the raw B2 API (no terraform — see docs/42). Advisory; reconciling is the supervised `task b2:apply`. No MR rule — see the credential note below |

> **Vault reads on merge-request pipelines.** `OP_SERVICE_ACCOUNT_TOKEN` **must
> be masked and protected** — this pipeline is written for that posture, and it
> is a project setting nothing here can assert (see the closing paragraph).
> Protected means absent on merge-request refs: no job can read the vault with
> code from the branch under review, so everything in this pipeline that
> resolves an `op://` reference is main-only.
>
> Two consequences are deliberate:
>
> - **`terraform-plan` gives no MR preview.** Its `merge_request_event` rule is
>   kept rather than deleted — it is inert while the protection stands, and the
>   preview returns by itself if the variable is ever unprotected. The accepted
>   cost is that a Cloudflare change is first seen in `deploy-terraform`'s apply
>   on main; `task terraform:cloudflare-plan` is the local substitute, and the
>   deploy job's plan output is the human checkpoint.
> - **`pr-agent-review` gates on `$OPENAI__KEY`,** not on the 1Password token —
>   its own credentials are CI variables (`secrets_source: env`), so it never
>   needed the vault, and gating on a variable that no longer exists on MR refs
>   would have silently deleted the job.
>
> The four advisory drift plans do not run on MRs at all: their real detector is
> the schedule and their applies are supervised, so the MR run bought nothing
> against the exposure (`authentik-drift-plan` alone read ~13 vault secrets,
> every OIDC client secret among them, and `unifi-drift-plan` reads the gateway
> API key plus every WLAN pre-shared key).
>
> Protection is a **project setting**, not something `.gitlab-ci.yml` can assert.
> If Settings → CI/CD → Variables ever shows the token unprotected, this whole
> argument is void — re-protect it rather than reasoning around it.

#### Test Stage
| Job | Triggers | Description |
|-----|----------|-------------|
| `integration-tests` | main / web / scheduled `full-test` only (`ansible/integration-tests/**`, `ansible/molecule/**`, `ansible/playbooks/**`, `ansible/requirements.yml`, `group_vars/all.yml`, the retry script) | The five-stack matrix — `base-infrastructure`, `cert-distribution`, `dns-stack`, `mail-stack`, `storage-stack` — converged against the collection's roles by FQCN. Requires Docker-in-Docker. all.yml IS a trigger (the converges `vars_files` it), and `ansible/molecule/**` is too (the converges import its shared prep) |
| `python-tests` | scripts/**, all.yml, ansible/playbooks/**, ansible/integration-tests/**, docs/**, README.md, CLAUDE.md, .claude/**, `Taskfile.yml` plus `taskfiles/**/*`, .gitlab-ci.yml | pytest on the scripts/ suite. Beyond the script unit tests it carries the doc gates in `test_doc_inventories.py`: every documented `ansible-playbook --tags <tag>` must reach a real tag (a bogus tag exits 0 having done nothing), and every `` `task <ns>:<name>` `` in docs/ + the agent files must exist in the Taskfile `includes:` tree (resolved through `scripts/taskfile_tree.py`, so `taskfiles/<ns>.yml` definitions count). Library job (`/ci/test/python-tests.yml`) with weisssrv's full path list |
| `test-aggregate-integration` | main pushes | Status-only fan-in over the integration matrix (`artifacts: false`), so `validation-gate` depends on one job instead of five |

**The role-scenario Molecule matrix does not live here any more.** The roles are
in `weisssrv.infra`, so their 43 scenarios run in `weisssrv-lib`'s pipeline
against the collection itself. What weisssrv keeps is the part only weisssrv can
test: the five multi-role **integration stacks** that converge real playbook
shapes against the pinned collection. Consequences worth knowing:

- A role regression is caught by the **library** pipeline, before a tag exists to
  consume. This repo's gate is the collection pin plus the integration stacks.
- The integration matrix still runs on `main`, on web pipelines, and on the
  scheduled `full-test` canary, not on every MR.

The job pulls its images from the library at the pinned ref:
`molecule-ci:$WEISSSRV_LIB_REF` as the job image, `molecule-test:$WEISSSRV_LIB_REF`
pulled and locally re-tagged `:latest` for the scenarios' platform literal
(`pre_build_image: true`, so Molecule never pulls it itself). It then installs
`ansible-galaxy collection install -r ansible/requirements.yml` — the library
image bakes only the Galaxy collections, not `weisssrv.infra`.

Two prerequisites live outside this repo: weisssrv must be on weisssrv-lib's
CI/CD **job-token allowlist** (or the cross-project registry pull 403s), and the
library release must have published both image tags.

**Registry pull-through cache.** The job's fresh DinD daemon pulls the
molecule-test image through the in-cluster cache
(`registry-cache.registry-cache.svc:5000`, `kubernetes/apps/registry-cache`,
docs/27) with a timeout-bounded fallback to the direct registry — a cache outage
only loses the warm hit, never breaks CI.

**DinD bridge MTU.** The daemon runs with `--mtu=1420`, the pod interface MTU on
flannel over WireGuard; the 1500 default black-holes large TLS frames and reads
as a connection reset inside the job's containers. `--mtu` covers only the
default bridge, so the service also passes
`--default-network-opt=bridge=com.docker.network.driver.mtu=1420` for the
user-defined networks the integration scenarios declare.

> **Note:** The bounded re-run with jitter on transient DinD flakes lives in
> `scripts/molecule-retry.sh`. The job also `needs:` `ansible-lint`/`yaml-lint`
> (`optional: true`) so a fast lint failure short-circuits it before it consumes
> privileged-runner capacity.
>
> **Note:** Integration tests require Docker-in-Docker and a runner with
> `privileged = true`. All weisssrv jobs run on the **infrastructure runner**
> (`gitlab-runner-privileged` Helm release, tag: `infrastructure`). The runner is
> Flux-managed at `kubernetes/apps/gitlab-runner-privileged/release.yaml`.

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

The indirection is not decoration. A job that declares `needs:` is no longer
ordered by stage, and GitLab **drops** an `optional: true` entry whose job the
rules excluded. A deploy job whose only needs were optional lint jobs therefore
ends up with an empty needs set and starts immediately, before those jobs run
and regardless of whether they later fail. Pointing every deploy at one job that
always exists is what keeps the needs set non-empty and the stage order real.

`validation-gate` must therefore be created for every pipeline that creates a
deploy job: a deploy job needing a gate that was not created fails pipeline
**creation**, so keep the two rule sets in step when you change either.

Ansible/Terraform deploy jobs depend on `validation-gate` as a required `needs`.
**Kubernetes workloads are not gated by CI** — Flux reconciles from git regardless of
pipeline state. CI's job for k8s is to validate (`flux-lint`, `kubeconform`,
the versions-configmap sync check in `check-generated-files`). Flux reconciliation itself is push-triggered via the GitLab
agent's Flux integration (poll is the fallback — see docs/29-flux-operations.md).

#### Deploy Stage - Terraform
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-terraform` | terraform/cloudflare/** on main | Apply Cloudflare DNS (fails closed if the saved plan deletes any resource; `ALLOW_DNS_DELETE=1` overrides). weisssrv applies on merge; weisssrv-cluster-template gates the same job behind `when: manual`, a deliberate difference for an operator who does not own the zone — the fail-closed delete count is what replaces the button here |

#### Deploy Stage - Ansible Infrastructure

> **Deploy triggers after the collection migration.** There are no role paths in
> this repo to key on, so every `deploy-*` job triggers on three things:
> **`ansible/requirements.yml`** (the collection pin — a library bump redeploys
> everything), **the playbook(s) it runs**, and **its inventory inputs**
> (`hosts.yml`, the relevant `group_vars`/`host_vars`, and `all.yml` where the
> job consumes pins from it). `scripts/check-deploy-coverage.sh` enforces the
> playbook and inventory halves, and additionally asserts that every job in the
> `deploy` stage lists `ansible/requirements.yml` — a job that misses it silently
> stops redeploying on collection bumps. Each job's `rules:`/`changes:` block in
> `.gitlab-ci.yml` is authoritative; the **Triggers** column below lists the
> playbook/inventory half only, since the requirements pin is common to all.
> The gate credits literal `<inventory_dir>/<path>.yml` entries only, so a
> `host_vars/**` glob confers no coverage: a newly added `host_vars/<host>.yml`
> reds the pipeline until the exact path is named in the matching `deploy-*`
> job's `changes:` list, or given an entry with a reason in
> `scripts/deploy-coverage.conf`.

| Job | Triggers (beyond `ansible/requirements.yml`) | Description |
|-----|----------|-------------|
| `deploy-ansible-base` | ansible/playbooks/base.yml, all.yml | Deploy base packages, SSH, users. all.yml is a trigger so host-side pins consumed here (e.g. `alloy_host_version`) auto-deploy on a version bump |
| `deploy-ansible-proxmox` | ansible/playbooks/site.yml (`--limit proxmox`, tag-scoped), group_vars/proxmox.yml, hosts.yml | Deploy Proxmox host config (qol, tailscale, mail). Deliberately NOT all.yml — its tailscale/plex pins apply via the manual maintenance jobs |
| `deploy-ansible-firewall` | ansible/playbooks/site.yml (`--tags proxmox_firewall`), ansible/inventories/prod/** | Deploy Proxmox firewall rules. The ipset render reads the ENTIRE inventory (every host's IP + memberships), so any inventory change re-deploys |
| `deploy-ansible-dns` | ansible/playbooks/dns.yml, all.yml, group_vars/dns.yml, host_vars/dns-0{1,2}.yml, hosts.yml | Deploy DNS stack (includes postfix). all.yml is a trigger so pins like `unbound_exporter_version` auto-deploy; dns.yml group/host vars carry the rewrites + upstreams |
| `deploy-ansible-storage` | ansible/playbooks/storage.yml, all.yml, host_vars/pve-nas-01.yml, hosts.yml | Deploy storage services. all.yml is a trigger so pins like `restic_version`/`rclone_version`/`zfs_exporter_version` auto-deploy |
| `deploy-ansible-mail` | ansible/playbooks/site.yml (`--limit mail`), group_vars/mail.yml, host_vars/smtp-relay.yml, hosts.yml | Deploy SMTP relay |
| `deploy-ansible-certs` | ansible/playbooks/site.yml (`--limit dns --tags acme_certs`), host_vars/dns-01.yml, hosts.yml | Deploy certificate distribution (dns-01.yml carries the pinned target host keys) |

#### Deploy Stage - Ansible Applications
| Job | Triggers | Description |
|-----|----------|-------------|
| `deploy-plex` | ansible/playbooks/plex.yml, group_vars/plex_servers.yml, host_vars/plex.yml, hosts.yml | Deploy Plex LXC container |
| `deploy-gitlab` | ansible/playbooks/gitlab.yml, group_vars/gitlab_servers.yml, group_vars/all.yml | Deploy GitLab VM and application |
| `deploy-nextcloud` | ansible/playbooks/nextcloud.yml, group_vars/nextcloud_servers.yml, hosts.yml, all.yml | Deploy the Nextcloud VM (.156) docker-compose stack (docs/35) |
| `deploy-immich` | ansible/playbooks/immich.yml, group_vars/immich_servers.yml, hosts.yml, all.yml | Deploy the Immich VM (.157) docker-compose stack (docs/36) |
| `deploy-immich-ml` | ansible/playbooks/immich-ml.yml, host_vars/immich-ml.yml, hosts.yml, all.yml | Deploy the Immich ML LXC (.158) — version lockstep with `deploy-immich` |
| `deploy-home-assistant-config` | ansible/playbooks/home-assistant.yml, host_vars/home.yml, hosts.yml | Deploy Home Assistant configuration |

#### K3s Platform and Applications: Flux-Managed

**All Kubernetes workloads deploy via Flux, not CI jobs.** Every platform
component (the `kubernetes/infrastructure/` stages: sources → crds → controllers
→ configs → observability) and every application listed in
`kubernetes/apps/kustomization.yaml` is reconciled by Flux from `kubernetes/` on
every `git push` to `main`. CI only validates (`flux-lint`, `kubeconform`,
`check-generated-files`).

#### Verify Stage

A **separate stage after `deploy`**, not deploy-stage jobs with `needs:` on the
deploys. In GitLab a job that declares any `needs:` starts as soon as those needs
complete and ignores stage order, and a need that was *created and then failed*
blocks the dependent job (`optional: true` only tolerates a need that was never
created). So both jobs here carry **no `needs:` at all** — stage ordering alone
sequences them — plus `when: always`, which is what makes verification run after
a failed deploy. Verification has to run even when a deploy job fails, or a
partial change set ships unverified.

`when: always` is relative to the **whole pipeline**, not just the deploy stage.
On a main pipeline that matches a deploy job's `changes:` but fails earlier at
lint/validate/test, `validation-gate` blocks the deploys and `deploy-verify`
still runs — verifying a deployment that was never attempted. It is read-only, so
nothing breaks, but expect a second, unrelated failure surface on an already-red
pipeline.

| Job | Triggers | Description | Blocks pipeline? |
|-----|----------|-------------|------------------|
| `deploy-gitlab-verify` | Inherits deploy-gitlab's rules verbatim via `!reference` (`ansible/requirements.yml`, ansible/playbooks/gitlab.yml, group_vars/gitlab_servers.yml, group_vars/all.yml) — Ansible-only; the k3s ingress/runner manifests are Flux-managed and do **not** trigger this job. | GitLab smoke tests (HTTP readiness, container registry, SSH port 22). | Yes |
| `deploy-verify` | The union of every `deploy-*` job's own `changes:` filter — one `!reference [deploy-<x>, rules]` per deploy job, `deploy-immich` included, so adding a deploy job means adding its `!reference` here — plus `kubernetes/**/*`, on pushes to main. That union is per-playbook/per-inventory subsets, **not** a blanket `ansible/**`, and carries no `scripts/**` filter (a change only under `scripts/` runs no deploy job). Since every deploy job lists `ansible/requirements.yml`, a collection-pin bump always runs the full verify. | Runs `scripts/deploy-verify.sh`: server-side dry-run validates rendered manifests against cluster API, triggers Flux reconciliation (fails on timeout), checks all nodes `Ready`, asserts zero Flux resources `Ready=false`, checks ExternalSecret readiness (hard failure on steady-state, warning during bootstrap), verifies GitLab HTTP. | Yes — fails the pipeline on any issue |
| `deploy-verify-hosts` | `deploy-verify`'s rules verbatim via `!reference`, so both halves of verification exist for the same pipelines. | Runs `ansible/playbooks/postflight.yml` with `postflight_exercise_sync=false` — the host-side read-only probes `deploy-verify` does not cover: SSH reachability, root filesystem headroom, unit state (asserted from `systemctl is-active`, never started), pool and mount health across the managed fleet. | Yes — a host left unreachable or a pool left degraded must not read as a warning |

A **cancelled** main pipeline is the one case the verify stage does not cover.
Cancelling cancels pending jobs rather than running them, so `deploy-verify`
never executes and a partially-applied deploy stage goes unverified. Recovery is
to re-run the pipeline, or `task infra:deploy` — not to assume convergence.

> **Note:** Both jobs block. `deploy-verify` fails the pipeline on any
> `NotReady` node, non-Ready Flux resource, or GitLab outage;
> `deploy-gitlab-verify` no longer carries `allow_failure`.

#### Maintenance Stage
All manual buttons except `version-bump-bot`, which is the one **scheduled** job
in this stage (see [Version bump bot](#version-bump-bot) below).

| Job | Description |
|-----|-------------|
| `version-bump-bot` | Weekly (`SCHEDULE_TYPE=version-bump`): bumps the version pins and keeps one open bot MR in sync with them. Never merges |
| `maintenance-k3s-provision` | Provision k3s VMs and deploy cluster |
| `maintenance-update-packages` | OS package updates on all hosts (base, k3s, app VMs) with auto-reboot |
| `maintenance-update-applications` | Application updates: AdGuard Home, Tailscale, Plex |
| `maintenance-update-k3s-nodes` | K3s node rolling update: cordon, upgrade, restart. It deliberately does **not** drain — reboots are kured's job |
| `maintenance-proxmox-ha` | Proxmox HA configuration |
| `maintenance-home-assistant-restart` | Restart Home Assistant after config deployment |
| `maintenance-run-all` | Wrapper that runs the six maintenance ops in sequence via `scripts/maintenance-run-with-verify.sh` (verify always runs after) |
| `maintenance-verify` | Post-maintenance cluster health validation (fails on critical issues) |

## Pipeline Triggers

| Trigger | Runs |
|---------|------|
| Merge request | Lint, validate (including the credential-free `check-deploy-playbooks`; drift plans excluded and `terraform-plan`'s rule inert), test (integration matrix excluded), security, AI review — no deploy, no verify |
| Push to main | Full validation including the integration matrix, then `validation-gate`, the path-gated deploys, and the `verify` stage (which runs regardless of the deploy stage's outcome) |
| Scheduled | Version checking, secret detection, and the six advisory drift checks — `tailscale-drift-plan`, `authentik-drift-plan`, `unifi-drift-plan`, `unifi-settings-drift`, `b2-drift-plan` and `cluster-drift-plan`. Their schedule rule carries no `$OP_SERVICE_ACCOUNT_TOKEN` guard: a guard would delete the detector when the credential is revoked or rescoped, so a missing token reds the job instead. All other jobs (lint, validate, test, ai-review, gate, deploy, maintenance) are excluded — **except** two `SCHEDULE_TYPE`-scoped opt-ins: `SCHEDULE_TYPE=full-test` also runs `integration-tests` as an external-dependency canary (catches upstream image/package breakage between code changes), and `SCHEDULE_TYPE=version-bump` runs `version-bump-bot` (below). |
| Manual (web) | Lint, validate, test stages only. AI review, deploy, gate, and maintenance jobs are excluded. Security (`secret_detection`) runs if branch is `main`. |

The branch name is written **literally** as `main` in every rule. GitLab does
not expand variables inside a quoted `rules:if`, so `$CI_DEFAULT_BRANCH` would
be compared as text and match nothing. "Fixing" the literal silently stops every
deploy and every library default-branch job; renaming the default branch means
changing these rules and passing the library templates' matching input.

## Deployment Pipeline

### Auto-Deploy Behavior

When a merge request is merged to `main`:

1. **Validation stages run first** (lint, validate, test, security)
2. **Validation gate blocks Ansible/Terraform deploys** — the `validation-gate`
   job in the `gate` stage must pass before any Ansible/Terraform deploy job can
   start. It depends on `secret_detection` and the two `test-aggregate-*` fan-ins
   as **required** dependencies, and on every path-filtered lint/validate job
   (`check-generated-files`, `check-repo-policies`, `lint-docs-links`, …) as
   `optional: true`. Path-filtered jobs that were not created are skipped, but
   `secret_detection` and any path-filtered job that *was* created must succeed
   or all Ansible/Terraform deployments are blocked. The advisory drift plans are
   deliberately excluded, like `pr-agent-review`.
3. **Only changed components deploy** (path-based triggers on Ansible/Terraform jobs)
4. **Kubernetes workloads reconcile via Flux** — independent of CI. Reconciliation
   is push-triggered via the GitLab agent's Flux integration (seconds after a push);
   the 1-minute GitRepository poll remains as fallback (see docs/29-flux-operations.md).
5. **The `verify` stage runs last, unconditionally** — `when: always` and no
   `needs:`, so a failed deploy is still verified (see [Verify Stage](#verify-stage))
6. **Machine reboots require manual approval** (maintenance stage)

### Deployment Categories

| Category | Mechanism | Jobs / Action | Auto-Deploy | Manual Approval |
|----------|-----------|---------------|-------------|-----------------|
| Terraform | CI job | `deploy-terraform` | Yes | No (blocked on record deletion) |
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

### Merge serialization

Two settings together give deploys their ordering guarantee:

1. **`workflow.auto_cancel.on_new_commit`** is `none` on `main` and
   `interruptible` on MR refs. A merged pipeline therefore always runs to
   completion; rapid merges queue instead of cancelling each other.
2. **Per-target `resource_group`s.** Every Ansible deploy job declares its own
   group (`deploy-ansible-base`, `deploy-plex`, …) rather than one repo-wide
   group, so different targets still deploy in parallel while the same target
   never has two pipelines on it at once.

Ordering depends on each group's **process mode being `oldest_first`**, which is
a project setting with no representation in `.gitlab-ci.yml`. It is applied
through the API, and a recreated or restored project comes back at the default
`unordered`, which lets queued deploys run out of submission order. Check every
group after a project restore —
[docs/17](17-disaster-recovery.md) § GitLab project state that is not in git
carries it as a restore step:

```bash
# List the groups and their process modes
glab api "projects/:id/resource_groups"

# Set (or restore) oldest_first for one group
glab api --method PUT "projects/:id/resource_groups/deploy-ansible-base" \
  -f process_mode=oldest_first
```

What this deliberately does **not** buy is whole-pipeline atomicity: once the
first merge's `deploy-ansible-base` releases its group, the second merge's can
start while the first pipeline's per-app deploys are still running — and `base`
targets every host, so two Ansible runs can meet on one box. A single repo-wide
group would close that at the cost of serialising the app-deploy fan-out inside
every pipeline; per-target was chosen knowingly.

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
  `infrastructure-sources` → `infrastructure-crds` → `infrastructure-controllers`
  → `infrastructure-configs`, which then reconciles
  `infrastructure-observability` and `apps` in parallel
- `helm-controller` upgrades HelmReleases; `kustomize-controller` applies Kustomizations
- **All Secrets** are created by `external-secrets`' `ExternalSecret` CRs that
  reference 1Password item titles via the ClusterSecretStore `onepassword-homelab`
  (Connect provider). CI does not inject any secrets into k8s.
- Version substitutions flow from the `cluster-versions` ConfigMap
  (`kubernetes/infrastructure/sources/versions-configmap.yaml`, generated from
  `all.yml`). The versions-sync check in `check-generated-files` fails the pipeline if the
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
| `OP_SERVICE_ACCOUNT_TOKEN` | Variable | Yes | Yes | 1Password service account token. **Protected**, so it is absent on merge-request refs and every `op://` consumer in this pipeline is main-only. The accepted cost is that `terraform-plan` no longer previews on MRs (`task terraform:cloudflare-plan` is the substitute) — see the vault-reads-on-MR note under the validate stage |
| `VERSION_BUMP_BOT_TOKEN` | Variable | Yes | Yes | GitLab PAT (`api` + `write_repository`) used by `version-bump-bot` to push its branch and manage its MR. **Only** required if the version-bump schedule exists; without it that job errors out and nothing else is affected. `CI_JOB_TOKEN` cannot substitute — it cannot push, and the Merge requests API is read-only for job tokens. Store the value in 1Password too (see below) so it is rotatable from the same place as everything else |

### Optional Variables

| Variable | Type | Protected | Masked | Description |
|----------|------|-----------|--------|-------------|
| `GH_API_TOKEN` | Variable | No | Yes | GitHub API token for version checker rate limits (check-versions.py accepts either `GH_API_TOKEN` or `GITHUB_TOKEN`; CI sets `GH_API_TOKEN` as the canonical name) |
| `GITLAB__PERSONAL_ACCESS_TOKEN` | Variable | No | Yes | Project access token (`weisssrv-review-bot`, Developer + `api`) used by `pr-agent-review` to post review comments and apply its effort label. Developer rather than Reporter because Reporter cannot label. Cannot push. Deliberately a CI variable, not a 1Password fetch: the job's script comes from the branch under review |
| `OPENAI__KEY` | Variable | No | Yes | LLM API key for `pr-agent-review`. Same reasoning as above — never fetched from 1Password in that job |
| `GITLAB_API_TOKEN` | Variable | No | Yes | Project access token (`weisssrv-bot`, Reporter + `api`) used by the `version-check` job to post its report comment. Without it the job still writes the report and skips the comment |

> These are unprotected on purpose: the jobs that read them run on merge
> requests, where `OP_SERVICE_ACCOUNT_TOKEN` is deliberately absent. Keep them
> scoped to the minimum role that works, since an MR pipeline's `.gitlab-ci.yml`
> comes from the branch under review.

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
# Assign, then export: `export VAR=$(...)` returns export's status, so a failed
# vault read would leave the variable empty and pass silently.
TF_VAR_cloudflare_api_token=$(op read "op://Homelab/Cloudflare Terraform Token/credential")
TF_VAR_cloudflare_account_id=$(op read "op://Homelab/Cloudflare Terraform Token/username")
export TF_VAR_cloudflare_api_token TF_VAR_cloudflare_account_id
```

**Ansible deploy jobs:**
```bash
# SSH key for Ansible (placed on disk outside of op run — ssh-agent reads it)
op read "op://Homelab/SSH Key/private key" > ~/.ssh/id_ed25519
chmod 600 ~/.ssh/id_ed25519

# Run Ansible via op run so inline `op://...` env refs are injected at runtime
op run -- ansible-playbook -i ansible/inventories/prod ansible/playbooks/site.yml
```

**Kubernetes secrets (no CI involvement)**:

Kubernetes Secrets are not created by CI. External Secrets Operator watches
`ExternalSecret` CRs in the cluster and syncs their values from 1Password (via
the `onepassword-homelab` ClusterSecretStore, Connect provider). The only bootstrap
secrets in the cluster are `external-secrets/op-credentials` and
`external-secrets/onepassword-connect-token`, created once during initial setup.
See `task flux:bootstrap-onepassword` for instructions and `docs/29-flux-operations.md`.

### 1Password items the pipeline reads

Items a CI job performs an `op read` for (see
[docs/15-credential-rotation.md](./15-credential-rotation.md) "Required 1Password
Items" for the complete list of items referenced by ExternalSecrets in the cluster):

| Item | Fields | Used By |
|------|--------|---------|
| SSH Key | `private key` | Ansible deployments |
| Cloudflare Terraform Token | `credential`, `username` | Terraform `terraform-plan` / `deploy-terraform` |
| Tailscale OAuth | `client id`, `credential` | `tailscale-drift-plan` (read-only ACL drift plan) |
| UniFi Controller | `url`, `api-key` | `unifi-drift-plan` (read-only network drift plan) |
| WiFi TheRevengers, WiFi Panopticon, WiFi kugel-tikka-masala, WiFi DunderMiffLAN | `password` | `unifi-drift-plan` — the four SSID pre-shared keys the plan needs to compare WLANs |
| GitLab API Token | `credential` | `deploy-gitlab` (`GITLAB_ADMIN_API_TOKEN`) and `task gitlab:deploy` — an **instance-admin** PAT |
| GitHub Token | `credential` | `version-bump-bot` (via `task maintenance:update-all-versions`, which runs its checker under `op run`) |
| GitLab Version Bump Bot Token | `credential` | `version-bump-bot` — the item of record for the `VERSION_BUMP_BOT_TOKEN` CI variable, which is what the job actually reads |

> `pr-agent-review` reads **no** vault item: it runs `secrets_source: env` and
> takes `GITLAB__PERSONAL_ACCESS_TOKEN` (the `weisssrv-review-bot` project access
> token) and `OPENAI__KEY` from masked CI variables, because the job's script
> comes from the branch under review. The **OpenAI API Key** item's consumer is
> Mealie, in-cluster (docs/15).
>
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

### Version checking

`version-check` is advisory: it reports available pin updates and comments on the
MR. It opens no MRs — that is the bump bot below. It needs no schedule of its
own, because it runs on every merge request and push, as a manual button on web
pipelines, and on **every** scheduled pipeline.

The project has two schedules, both on `main`:

| Schedule | Cron | Timezone | Scope |
|---|---|---|---|
| Nightly integration canary (full-test) | `47 2 * * *` | UTC | `SCHEDULE_TYPE=full-test` — also runs `integration-tests` as an external-dependency canary |
| Weekly version bump MR | `0 10 * * 1` | America/Los_Angeles | `SCHEDULE_TYPE=version-bump` — also runs `version-bump-bot` |

Both also run `version-check`, secret detection and the four advisory drift
plans.

A drift exit 2 leaves the pipeline **passed with warnings**, and GitLab sends no
mail for a warned pipeline. Nothing tells you about drift unless you watch the
scheduled pipeline yourself in **Settings → CI/CD → Schedules** or wire a
pipeline-event webhook.

### Version bump bot

`version-bump-bot` (maintenance stage) turns the weekly report into a reviewable
MR. It is a deliberate fork of the library's
`/ci/maintenance/version-bump-bot.yml`, and the only library job this repo
defines locally. The library job exposes no `variables` / `before_script` /
`rules` inputs and this bot needs all three: its own pin pathspec, the
1Password and go-task installs, and a `SCHEDULE_TYPE`-scoped trigger.
Re-declaring an included job's name to add them is the silent-override trap.
The tri-state exit handling in step 1 is the other divergence, so the fork is
limited to those two things. It runs the same command an operator runs by hand —
`task maintenance:update-all-versions`, which rewrites the pins in
`ansible/inventories/prod/group_vars/all.yml` and regenerates
`kubernetes/infrastructure/sources/versions-configmap.yaml` — and then hands the
working tree to `scripts/version-bump-mr.py`.

Step 1 of the job handles three exit states, because `check-versions.py
--update-all` exits 0 whether or not it wrote a bump and 2 only on a real error.
On 0 the job carries on, and go-task has already run `flux:sync-versions`. On a
non-zero exit that still wrote bumps it ships them, but regenerates the versions
ConfigMap itself, since go-task stopped before that step and the bot MR would
otherwise fail its own `check-generated-files`. On a non-zero exit with no bumps it
re-raises, because step 2 would read that as "all up to date" and close a
still-valid bot MR.

**Every outcome is idempotent. It never merges, and it never commits anything
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
including `check-generated-files`, which is what proves the two files stay in sync.

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
2. **Create the schedule.** The `SCHEDULE_TYPE=version-bump` variable is what
   scopes the bot to it, so it cannot fire on the nightly canary:
   - **CI/CD > Schedules > New schedule**
   - **Description**: `Weekly version bump MR`
   - **Interval pattern**: `0 10 * * 1` (Monday 10am, `America/Los_Angeles`)
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
     --form cron_timezone="America/Los_Angeles" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

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
# Run all linters (mirrors the CI lint stage)
task lint

# Individual checks
task ansible:lint
task terraform:validate
task kubernetes:lint
```

### Testing Locally

Role scenarios live in `weisssrv-lib` — run them there. What this repo tests
locally is the integration stacks (Docker required):

```bash
# All five integration stacks
task ansible:test-integration

# One stack
task ansible:test-integration-dns
```

### Terraform

```bash
# Format code
cd terraform/cloudflare
terraform fmt

# Validate syntax
terraform validate

# Plan changes (requires 1Password)
task terraform:cloudflare-plan
```

## Secret Detection

CI secret detection scans the pipeline's **commit range only**:
`SECRET_DETECTION_HISTORIC_SCAN` stays false. The full-history pass is a
`SECRET_DETECTION_HISTORIC_SCAN=true` variable on the security pipeline
schedule.

A one-time local full-history audit is
`gitleaks detect --source . --config .gitleaks.toml --log-opts='--all'`. Record
the date and outcome here once it is run.

GitLab native secret detection scans for:
- API keys and tokens
- Private keys
- Passwords in code
- Cloud provider credentials

Scanning scope:
- `docs/*.md` is scanned normally — content-shape allowlists in
  `.gitleaks.toml` cover the placeholder forms docs use (`op://` references,
  `[your-...]` placeholders, anchored fake fixture tokens)
- The integration-test tree is scanned by every rule EXCEPT the
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
- `.github/workflows/ci-disabled.yml` is an inert `on: {}` placeholder — it can
  never trigger, not even via `workflow_dispatch`

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

### Integration Tests Failing

A failing **role** scenario is a `weisssrv-lib` problem — reproduce it there, fix
the collection, cut a tag, then bump `ansible/requirements.yml` here. The steps
below are for the five integration stacks this repo owns.

1. Check Docker-in-Docker is available:
   - All weisssrv jobs run on the infrastructure runner (`gitlab-runner-privileged` Helm release, tag: `infrastructure`)
   - This runner has `privileged = true` which enables Docker-in-Docker
   - To change runner config: edit `kubernetes/apps/gitlab-runner-privileged/release.yaml`,
     commit, push; Flux reconciles within ~1 min (or `task flux:reconcile` to force).
   - Check runner pods: `kubectl get pods -n gitlab-runner-privileged -l release=gitlab-runner-privileged` (the privileged runner has its own namespace)

2. Run the stack locally:
   ```bash
   task ansible:test-integration-dns
   ```

3. Keep containers for debugging:
   ```bash
   MOLECULE_OPTS="--destroy=never" task ansible:test-integration-dns
   ```

4. If the failure is a missing role or an unresolved variable, confirm the
   collection is installed at the pinned tag:
   `ansible-galaxy collection list weisssrv.infra`.

### Molecule container dies at prepare: "Too many open files"

The container is created, then systemd PID 1 inside it exits with
`Failed to create control group inotify object: Too many open files`.
`fs.inotify.max_user_instances` is a per-UID ceiling, and containers share the
host user namespace, so every uid-0 container draws on the same pool. The kernel
default of 128 is exhausted on a container-dense node and the next
systemd-in-Docker container cannot start. Running the workload as a non-root
user puts it on its own pool. It is intermittent, hits at any concurrency, and
clusters on one node at a time.

The `k3s` role pins `fs.inotify.max_user_instances = 8192` and
`fs.inotify.max_user_watches = 1048576` on every node via
`/etc/sysctl.d/90-k3s-inotify.conf` (`k3s_inotify_*` in the collection's role
defaults). Check a node with
`ssh <k3s-node> cat /proc/sys/fs/inotify/max_user_instances`; if it shows 128,
re-run `task k3s:deploy` to re-assert the drop-in.

### Secret Detection False Positives

If secret detection flags a false positive:

1. Check if it's a real secret (never commit secrets)
2. Add a scoped allowlist entry to `.gitleaks.toml` (passed through by
   `.gitlab/secret-detection-ruleset.toml` — see *Scanning scope* above). Prefer
   a content-shape or rule-scoped allowlist over a path exclusion, and never
   override `SECRET_DETECTION_EXCLUDED_PATHS` inline in `.gitlab-ci.yml`: that
   turns the gate off for a whole tree with no record of why.

## Related documentation

- [docs/16-next-steps.md](./16-next-steps.md) — the roadmap, including the open CI items
- [docs/15-credential-rotation.md](./15-credential-rotation.md) — the canonical 1Password inventory
- [docs/29-flux-operations.md](./29-flux-operations.md) — what happens after a merge to `main`
- [docs/27-gitlab-deployment.md](./27-gitlab-deployment.md) — the GitLab instance and its runners

## External references

- [GitLab CI/CD Documentation](https://docs.gitlab.com/ee/ci/)
- [GitLab Secret Detection](https://docs.gitlab.com/ee/user/application_security/secret_detection/)
- [1Password Service Accounts](https://developer.1password.com/docs/service-accounts/)
- [Molecule Testing](https://molecule.readthedocs.io/)
