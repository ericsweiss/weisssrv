---
name: weisssrv-development
description: >-
  Use when working in the weisssrv homelab IaC repo (Proxmox + Ansible +
  Terraform + k3s/Flux) — developing or changing features, adding a Kubernetes
  app / Ansible role / VM, deploying, debugging or managing the cluster, running
  maintenance or version upgrades, or incident response. Establishes the
  non-negotiable workflow (branch+MR, never push main, no secrets, Flux owns
  k8s, single-source versions, roles live in weisssrv-lib), the pre-MR gate
  commands, the change-type decision tree, and where every canonical doc lives.
  Read this before making ANY change in this repo.
---

# weisssrv development

Homelab Infrastructure-as-Code: Proxmox + Ansible + Terraform + k3s/Flux GitOps.
This skill is the operating map — it points at the canonical docs and encodes the
workflow/gates that live nowhere else. It does not restate doc content; open the
referenced files. Paths are repo-relative, with two shorthands: anything starting
`references/` is relative to this skill directory
(`.claude/skills/weisssrv-development/`), and `group_vars/…` / `host_vars/…` are
shorthand for `ansible/inventories/prod/`.

Canonical remote: GitLab (`git.ericsweiss.com/eric/weisssrv`). GitHub is a
read-only mirror. `CLAUDE.md` is the top-level fact source; `README.md` owns the
docs index and the repo map; `task --list` owns the command set.

**This repo is the cluster instance, not the whole system.** The Ansible roles,
the CI job templates, and a set of vendored files come from `eric/weisssrv-lib`
at pinned refs (`CLAUDE.md` § Repo family). Changing any of them is a two-repo
flow. Never assume a file is repo-owned: `scripts/README.md`'s **Origin** column
and this repo's own `scripts/vendored-manifest.yml` (enforced by
`scripts/test_vendored_byte_identity.py`) are the inventory of record — never a
list or a count written anywhere else.

**Template first.** Site data (IPs, hostnames, VM IDs, the app estate, inventory
values, this cluster's docs) belongs here. Anything generic belongs upstream
first: code goes to `eric/weisssrv-lib` (roles, CI job templates, Terraform
modules, vendored gates), and repo shape goes to
`eric/weisssrv-cluster-template` (directory layout, a gate every cluster wants,
a doc skeleton, a Taskfile pattern); tenant-repo shape goes to
`eric/weisssrv-app-template`. weisssrv is the source the cluster template was
extracted from — there is no `.copier-answers.yml` here and it is never
re-rendered — so a generic change landed only here is permanent drift. Land it
in the template (and the library where the code lives) as well, in the same
program of work. The decision-tree rows below name the counterpart for each
change type.

## Invariants (violating any breaks prod or fails review)

- **Never push to `main`.** Every change — even a one-line hotfix — ships via a
  feature branch + GitLab MR. Flux and CI act on `main` only after merge.
- **Keep feature branches local until the MR is ready, and confirm with the user
  before pushing.** Do not open an MR unprompted.
- **No secrets in git.** Host tooling: `op://Homelab/<Item>/<field>` refs in the
  Taskfile's per-task `env:` blocks (and the equivalent CI job variables), with
  `docs/15-credential-rotation.md` as the item inventory. In-cluster:
  `ExternalSecret` with
  `remoteRef.key` = 1Password item **title**, `remoteRef.property` = **field**,
  store `onepassword-homelab`. New 1P items go in
  `docs/15-credential-rotation.md`.
- **Flux owns all of `kubernetes/`.** No `kubectl apply -k` / `helm upgrade` as
  ongoing ops. Only exceptions: `task flux:dev-apply -- <path>` (reverted next
  reconcile), the two bootstrap secrets, Ansible-provisioned objects outside
  `kubernetes/`.
- **There are no Ansible roles in this repo.** They ship in the `weisssrv.infra`
  collection (weisssrv-lib), pinned in `ansible/requirements.yml`; playbooks
  address them as `weisssrv.infra.<role>`, and the role molecule scenarios and
  their CI matrix live in the library too. A role change is a library MR + tag,
  then a pin bump here (below). What this repo owns is site data: inventory,
  playbooks, Taskfile/CI wiring.
- **Bumping weisssrv-lib is one atomic set of edits**, all in the same MR:
  `variables.WEISSSRV_LIB_REF` in `.gitlab-ci.yml` → `scripts/check-lib-pins.py
  --fix` (rewrites every literal `ref:`) → `scripts/check-molecule-image-pin.py
  --fix` (the `molecule-test:<tag>` fallbacks in the integration scenarios and
  `ansible/TESTING.md`; CI overrides the image, so a stale one only breaks a
  local run) → the collection `version:` in `ansible/requirements.yml` → the
  Terraform module `?ref=` pins, one per root under `terraform/`, bumped by hand
  because `check-lib-pins.py` reads only the `include:` block and
  `ansible/requirements.yml` (`scripts/test_site_configs.py` holds every root's
  ref equal to `WEISSSRV_LIB_REF`) → re-vendor **every** file this repo's
  `scripts/vendored-manifest.yml` lists (never a hand-remembered subset;
  `python3 <lib>/scripts/check-vendored-copies.py --manifest
  scripts/vendored-manifest.yml` lists and gates them) →
  `scripts/check-role-default-flips.py --from <installed tag> --to <new tag>`,
  declaring or `--allow`-ing every boolean flip it reports (no pipeline runs it:
  it needs both tags side by side) → any inventory change the
  collection's `MIGRATING.md` requires for renamed or emptied variables. Never
  patch a vendored file or a role locally: the next re-vendor reverts it. Never
  re-add an included CI job inline — a same-named local job silently overrides
  the include.
- **Never automate ZFS pool create/destroy** — pools are manual. Ansible sets
  properties, creates zvols and mounts only.
- **Versions are single-sourced** in `group_vars/all.yml`. After editing a pin,
  run `task flux:sync-versions` and commit **both** `all.yml` and
  `kubernetes/infrastructure/sources/versions-configmap.yaml`. Never hand-edit
  the ConfigMap. Manifests reference `${snake_case_version}` placeholders.
- **Cluster identity is single-sourced too**, in the sibling
  `kubernetes/infrastructure/sources/cluster-config.yaml` (hand-edited, not
  generated). Manifests spell domains, CIDRs and VIPs as `${cluster_*}`
  placeholders — `app.${cluster_internal_domain}`,
  `${cluster_metallb_internal_vip}` — and every stage after `sources`
  substitutes from BOTH ConfigMaps. `scripts/check-cluster-literals.py` fails a
  hard-coded value and cross-checks the ConfigMap against the Ansible inventory.
  Literals stay ONLY where a tool parses them before Flux substitutes:
  NetworkPolicy `ipBlock` CIDRs, `observability/rules/`, escaped regex
  spellings, and per-guest/per-node addresses. The ConfigMap header is canonical.
- **Every namespace carries an ingress default-deny.** The
  `kubernetes/components/netpol-baseline` component is the standard mechanism;
  the two exceptions (`downloads`, whose local policy covers ingress
  and egress; `flux-system`, whose upstream manifests ship their own) are listed
  canonically in `docs/29-flux-operations.md`
  § Network policy exceptions. `scripts/check-default-deny-coverage.py` is the
  `flux:lint` gate: it fails any namespace owning a workload without a
  namespace-wide ingress deny unless that namespace carries a reasoned entry in
  its `EXEMPT_NAMESPACES` — today only `flux-system`, since `downloads`
  satisfies the invariant outright. `kube-system` is NOT an exception: its deny and its
  whole allow set live together in
  `kubernetes/infrastructure/configs/kube-system-policies/`, and a new
  kube-system workload must add its allow there in the same commit. Three sibling components
  ship the recurring EGRESS rules — `netpol-egress-{dns,apiserver,public}` — but
  each is a whole policy selecting the whole namespace, so use one only where the
  rule really is namespace-wide; an app-scoped policy keeps its rule inline
  (`kubernetes/components/README.md`). The remaining copies of the reserved-CIDR
  except-lists must stay identical, which
  `scripts/check-netpol-except-parity.py` enforces.
- **Ansible conventions** (FQCN, snake_case + role-prefixed vars, `no_log: true`
  on any secret-touching task, handler + service patterns, the `--tags` caveat)
  are canonical in `ansible/README.md` § "Code conventions".
- **No AI/Claude attribution anywhere** — commits, code, comments, docs, MR text.
- **Comments describe the current state, briefly, in the present tense.** Three
  content lines per block is the ceiling. A block guarding a trap that causes an
  outage may open `CRITICAL:` and run to eight; that marker is for outages, not
  for emphasis and not for a long file header. No dates, MR or issue numbers,
  no "used to / previously", no incident narrative, prior values or rejected
  alternatives. Runbook and rationale detail moves to the owning doc or README.
  `task lint:comment-length` enforces the ceiling and the marker.
- **Fold available version bumps into any MR revision** (never open a revision
  just for bumps). Read the *pipeline's* `version-check` job output each round —
  not a local run — since the local cache can be stale.
- **MR test sections describe what WAS done** — never unchecked checkboxes.
- **The skill rides along with the change.** If a change alters a workflow, gate,
  invariant, or canonical-doc pointer, update `SKILL.md` / the matching
  `references/` file in the same MR. CI checks only the `task <ns>:<name>` names
  (`scripts/test_doc_inventories.py`) and the backticked repo paths it cites
  (`scripts/check-skill-refs.py`, proven against the tree by
  `scripts/test_check_skill_refs.py`); nothing validates its prose.

## Decision tree — what am I changing?

| Change type | Read first | Canonical docs |
|---|---|---|
| Kubernetes app (dir under `kubernetes/apps/`) | `references/add-k8s-app.md` | `docs/29-flux-operations.md` (Adding a New App), `docs/33-autoscaling.md` |
| Platform piece under `kubernetes/infrastructure/` | the sibling stage's dir — stage by dependency, not by kind: `sources/` (HelmRepository + the versions ConfigMap), `crds/` (CRDs the controllers below reference), `controllers/` (platform HelmReleases), `configs/` (CRs needing those CRDs), `observability/` | `docs/29-flux-operations.md`, `docs/31-observability.md`; `kubernetes/clusters/weisssrv/infrastructure-*.yaml` owns the `dependsOn` order |
| Alert rule, dashboard, exporter or scrape target (no new app) | `references/add-k8s-app.md` § Alert rules — canonical wherever the rule comes from: a standalone `PrometheusRule` under `kubernetes/infrastructure/observability/rules/`, registered in `rules/kustomization.yaml`, with a MANDATORY `annotations.runbook_url` that `scripts/check-runbook-anchors.py` resolves against a real docs heading, and a promtool unit test in `scripts/prometheus-rule-tests/` (or an `UNTESTED` entry in `scripts/test_prometheus_rule_coverage.py`). A new ServiceMonitor needs its scrape NetworkPolicy allow | `docs/31-observability.md`, `docs/12-runbooks.md` (the page a `runbook_url` points at) |
| Tenant onboarding (`kubernetes/clusters/weisssrv/tenants/`) | `docs/30-multi-repo-onboarding.md` | the tenant repo is GENERATED with `copier copy` from `eric/weisssrv-app-template` (never forked), and ships its own pre-rendered `docs/ONBOARDING.md` — ask for that page |
| k3s layer itself (nodes, kube-vip, etcd, VM sizing) | `references/add-vm-app.md` (guest mechanics) + `hosts.yml` | `docs/19-k3s-deployment.md`, `docs/25-multi-node-expansion.md` |
| GPU workload / passthrough | `references/add-k8s-app.md` § Scheduling | `docs/43-gpu-passthrough.md` |
| New Proxmox VM / LXC app | `references/add-vm-app.md` | `docs/35-nextcloud.md` + `docs/36-immich.md` (current-shape worked examples), `docs/27-gitlab-deployment.md` (the Omnibus/registry case), `docs/06-zfs.md`, `docs/11-firewall.md`, `docs/17-disaster-recovery.md`, `docs/18-bootstrap-new-systems.md` |
| **Ansible role behaviour** (tasks, templates, defaults) | the role in `weisssrv-lib` — **not here** | the collection README + `MIGRATING.md`; back here for the pin bump (§ Invariants) |
| Inventory / playbook / host or guest sizing | `ansible/README.md` (layout + conventions) + `CLAUDE.md` § Ansible roles | `docs/01-overview.md`, `docs/18-bootstrap-new-systems.md`, the role's README in the collection |
| Terraform | `terraform/<module>/README.md` + neighbours — the four roots deploy differently: **cloudflare AUTO-APPLIES on merge** (`deploy-terraform`, `-auto-approve`), **tailscale** is plan-in-CI + supervised manual apply, **authentik** and **unifi** are supervised manual apply only. **all four** are thin callers of `weisssrv-lib//terraform/modules/{cloudflare-zone,tailscale-acl,authentik-sso,unifi-network}` at a hand-bumped `?ref=` holding only site data, and `check-lib-pins.py` does not cover module sources — `scripts/test_site_configs.py` holds the refs equal to `WEISSSRV_LIB_REF`. All four refs pin the same release as `WEISSSRV_LIB_REF`. `prevent_destroy` (authentik objects; unifi networks + zones) and the unbound-application precondition are **module-side** and a consumer cannot remove them: a rename needs a `moved {}` block, a removal needs `terraform state rm` first. **unifi** additionally has a codified-vs-UI split — device/port config, mDNS reflection and policy ordering are UI steps the provider cannot express, so read docs/46 before assuming a network change belongs in HCL | `docs/08-dns.md`, `docs/05-tailscale.md`, `docs/40-authentik-terraform.md`, `docs/46-unifi-network.md` |
| SSO account or group membership (a person, not an app) | `terraform/authentik/users.tf` header + `groups.tf` | `docs/40-authentik-terraform.md` § Managed users. `task authentik:add-user -- <username> --name "…" --email …` scaffolds the entry and prints the 1Password snippet. `users.tf` holds LOGIN NAMES ONLY — display name and email live solely in the "Authentik User Identities" 1P item injected as `TF_VAR_user_identities`, because this repo mirrors to public GitHub. Membership goes on the group in `groups.tf`, never on the user. A PRE-EXISTING account also needs an `import {}` block in `terraform/authentik/imports.tf` landing in the SAME apply. Every user carries module-side `prevent_destroy`: rename with a `moved {}` block, never delete and recreate. Supervised `terraform:authentik-plan` then `:authentik-apply` — never the UI |
| CI pipeline (`.gitlab-ci.yml`, `.gitlab/ci/`) | `CLAUDE.md` § Repo family — check whether the job is included from the library before editing | `docs/13-ci-cd.md` (§ Shared CI library), the library's `docs/INCLUDE-CONTRACT.md` |
| `scripts/` | `scripts/README.md` — its **Origin** column says whether the file is local, vendored, or a declared fork | `docs/13-ci-cd.md` |
| `docker/` app build images (hermes-agent, camofox-browser) | the image's own directory — the molecule test/CI images ship from weisssrv-lib, not here | `docs/13-ci-cd.md`, `ansible/TESTING.md` |
| Version bump / upgrade / maintenance | `references/maintenance-upgrades.md` | `docs/12-runbooks.md`, `docs/16-next-steps.md` |
| Debug / incident response | `references/debugging.md` | `docs/29-flux-operations.md`, `docs/12-runbooks.md`, `docs/32-zfs-encryption.md`, `docs/34-bond-mac-flapping.md` |
| Cluster access / secrets / kubeconfig | `references/cluster-access.md` | `docs/15-credential-rotation.md`, `docs/29-flux-operations.md` |
| Storage layout, backups, restores | `references/add-vm-app.md` (zvol + backup steps) | `docs/06-zfs.md`, `docs/44-storage-bootstrap.md`, `docs/42-offsite-backup.md`, `docs/17-disaster-recovery.md` |
| DNS / firewall / observability wiring for a new service | `references/add-k8s-app.md` or `add-vm-app.md` | `docs/08-dns.md`, `docs/11-firewall.md`, `docs/31-observability.md` |
| VLAN / gateway / wireless change (who may reach what, before any host rule) | `terraform/unifi/README.md` — the estate is segmented one zone per VLAN with inter-zone default-deny; the Proxmox sets mirror it (`admin_lan` ⊂ `lan_clients` ⊂ `dns_clients`) | `docs/46-unifi-network.md`, `docs/11-firewall.md`, `docs/01-overview.md` |

### Where the generic half of a change goes

| Change type | Upstream home |
|---|---|
| Ansible role behaviour, a CI job template, a Terraform module, a vendored gate | `eric/weisssrv-lib` — then bump the pins here |
| A k8s app shape, a component, a stage layout, a repo-local gate, a doc skeleton, a Taskfile pattern | `eric/weisssrv-cluster-template` |
| Anything a tenant repo needs | `eric/weisssrv-app-template` |
| Inventory values, hostnames, IPs, VM ids, the app estate, this cluster's docs and alerts | weisssrv only — never ported |

Every new service is mandatory-observed: logs (in-cluster automatic / the
collection's `alloy_host` role for VMs), metrics (exporter or native `/metrics` +
ServiceMonitor/PodMonitor + a scrape NetworkPolicy allow), a down/stale alert
rule, and a blackbox probe for user-facing endpoints. The reference files spell
out the per-change-type checklist.

## Pre-MR gates (run from the worktree root)

- `task lint` — the local lint aggregate; run it for every change. The root
  `lint:` task's command list in `Taskfile.yml` is the source of truth for the
  current set (ansible-lint, terraform fmt/validate-local, `flux:lint`, the
  gates in `taskfiles/lint.yml`, and `scripts:test`, which runs every
  `scripts/test_*.py`). A new gate therefore enters `task lint` either as a
  `taskfiles/lint.yml` command or as a pytest suite that exercises it against
  the real tree — `check-skill-refs.py`, `check-molecule-image-pin.py` and
  `check-tenant-traefik-isolation.py` are enforced the second way, by their
  pytest suites.
- **The Taskfile is an `includes:` tree.** `Taskfile.yml` holds the top-level
  tasks and one file per namespace lives under `taskfiles/<ns>.yml`; task names
  are unchanged, so `flux:lint` is the task `lint:` in `taskfiles/flux.yml` and
  a cross-namespace call carries a leading colon (`:flux:sync-versions`).
  Anything that walks task names goes through `scripts/taskfile_tree.py`, not
  the root file's own `tasks:` dict.
- Playbooks or inventory touched → install the pinned collection first
  (`ansible-galaxy install -r ansible/requirements.yml --force`), then
  `task ansible:lint`; `ansible-playbook --syntax-check` catches an FQCN or role
  name that no longer resolves. Integration scenarios: `task ansible:test-integration`
  (Docker required) — see `references/debugging.md` for local caveats.
- Versions touched → `task flux:sync-versions`, commit both files (else
  the versions-sync check in `check-generated-files` reds the pipeline).
- `hosts.yml` touched → `task hosts:sync`, commit `scripts/hosts.env`.
- `kubernetes/` touched → `task flux:lint` (kustomize build + envsubst with zero
  unsubstituted `${...}` + kubeconform + helm-template). Optionally preview with
  `task flux:dev-apply -- kubernetes/apps/<app>` (reverted next reconcile).
  - **Flux's envsubst reads bash modifier forms as variable names** — `${conf%/*}`,
    a bare `${1}` — and kustomize-controller runs with
    `StrictPostBuildSubstitutions=true`, so an undefined name fails the whole
    Kustomization. Escape them `$${...}` or assemble the value at runtime;
    `task flux:lint` renders through `flux envsubst --strict`, the authority.
- Prometheus/alert rules touched → `task lint:prometheus-config` (promtool and
  amtool must be on PATH; it also runs inside `task lint`).
- A new guest or deploy target needs its `deploy-*` CI job wiring; the
  coverage-check scripts inside `task lint` fail when it is missing.
- `task lint`'s script tests need a **weisssrv-lib checkout** (sibling
  `../weisssrv-lib`, or `$WEISSSRV_LIB_PATH`) — the vendored-copy gate never
  skips. `references/cluster-access.md` § Local toolchain has the full list.
- `pre-commit install` once per clone wires the local backstop (gitleaks,
  yamllint, `check-taskfile.sh`, `check-doc-links.py`) — the hook set is
  `.pre-commit-config.yaml`.

## CI, review loop, and merge

- **Commit subjects are Conventional Commits** — `type(scope): summary`, types in
  use `feat|fix|chore|docs|refactor|ci|test`, with the merge commit carrying the
  `(#eric/weisssrv!NNN)` MR reference GitLab appends. Nothing enforces this
  (there is no commitlint); the history is the contract. No AI attribution in any
  commit or MR text.
- **MRs are opened on GitLab**, against `main`, from a pushed feature branch —
  `glab mr create` or the web UI. The MR description carries the change summary,
  the deploy plan, and a test section describing what WAS run.
- An MR pipeline runs the **path-filtered** lint/validate/test/security/ai-review
  gates only — **no deploy on MRs**. A gate runs only if its paths changed. The
  closest thing to a pre-merge deploy signal is `check-deploy-playbooks`: it is
  credential-free by construction (an MR job must never hold prod credentials),
  so it proves the collection pin installs, every deploy
  job's playbook exists and every `--tags` selection reaches a real task —
  nothing that needs a live host.
- `pr-agent-review` (advisory, `allow_failure`) posts findings within minutes of
  the lint jobs finishing; it is not created on tokenless/fork MRs (expected, not
  a failure). `version-check` is soft-fail and only comments when it has a token.
- **Run the review loop to convergence before raising the MR**: self-review /
  the AI reviewer / any requested reviewer, address every *valid* finding, repeat
  until no valid findings remain. Then push and confirm before opening the MR.
- After merge to `main`: Flux reconciles `kubernetes/` (~1 min; `task
  flux:reconcile` forces it) and the `deploy-*` jobs run behind the main-only
  validation gate, followed by a separate **`verify` stage** that runs
  `when: always` — a failed deploy job no longer skips verification, so read
  `deploy-verify` even (especially) on a red pipeline. Verify locally with
  `task flux:status` / `task flux:verify` (k8s), `task infra:verify` (base),
  `task k3s:status`.
- **Merges are serialized per deploy target, not per pipeline.**
  `auto_cancel.on_new_commit` is `none` on `main`, so a merged pipeline always
  runs to completion, and each Ansible deploy job holds its own
  `resource_group`. Ordering depends on each group's `oldest_first` process
  mode — a project API setting with no representation in `.gitlab-ci.yml` that a
  recreated project silently reverts to `unordered`. What this does NOT buy is
  whole-pipeline atomicity: a second merge's `deploy-ansible-base` can start
  while the first pipeline's per-app deploys are still running, and `base`
  targets every host. Let a main pipeline finish before merging the next MR that
  touches the same targets, and never hand-run a deploy task while its CI deploy
  job is in flight — `docs/13-ci-cd.md` § Merge serialization.

## Reference files

- `references/add-k8s-app.md` — new app under `kubernetes/apps/` (exemplar,
  storage/NAS mechanics, netpol/VPA/cert/ingress, OIDC).
- `references/add-vm-app.md` — new Proxmox VM/LXC app (the full guest checklist,
  encryption vmids, firewall, backups, cert distribution, ingress, DNS).
- `references/debugging.md` — symptom → entry point (Flux, pods, NFS, DNS, certs,
  nodes, backups, reboot-safety, local molecule caveats).
- `references/maintenance-upgrades.md` — version-bump flow, the library bump,
  node/full upgrades, reboots, MetalLB hold, Helm CRD lifecycle trap.
- `references/cluster-access.md` — kubeconfig, KUBECONFIG, ssh naming, `op`
  wrappers, 1Password conventions, Tailscale.
