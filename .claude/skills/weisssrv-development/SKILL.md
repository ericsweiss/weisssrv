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
shorthand for `ansible/inventories/prod/…`.

Canonical remote: GitLab (`git.ericsweiss.com/eric/weisssrv`). GitHub is a
read-only mirror. `CLAUDE.md` is the top-level fact source; `README.md` owns the
docs index and the repo map; `task --list` owns the command set.

**This repo is the cluster instance, not the whole system.** The Ansible roles,
the CI job templates, and two vendored scripts come from `eric/weisssrv-lib` at
pinned refs (`CLAUDE.md` § Repo family). Changing any of them is a two-repo flow.

## Invariants (violating any breaks prod or fails review)

- **Never push to `main`.** Every change — even a one-line hotfix — ships via a
  feature branch + GitLab MR. Flux and CI act on `main` only after merge.
- **Keep feature branches local until the MR is ready, and confirm with the user
  before pushing.** Do not open an MR unprompted.
- **No secrets in git.** Host tooling: `op://Homelab/<Item>/<field>` refs in the
  `secrets:` dict of `group_vars/all.yml`. In-cluster: `ExternalSecret` with
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
  --fix` (rewrites every literal `ref:`) → the collection `version:` in
  `ansible/requirements.yml` → re-vendor the byte-identical copies
  (`scripts/check-lib-pins.py`, `scripts/version-bump-mr.py`) → any inventory
  change the collection's `MIGRATING.md` requires for renamed or emptied
  variables. Never patch a vendored script or a role locally: the next re-vendor
  reverts it. Never re-add an included CI job inline — a same-named local job
  silently overrides the include.
- **Never automate ZFS pool create/destroy** — pools are manual. Ansible sets
  properties, creates zvols and mounts only.
- **Versions are single-sourced** in `group_vars/all.yml`. After editing a pin,
  run `task flux:sync-versions` and commit **both** `all.yml` and
  `kubernetes/infrastructure/sources/versions-configmap.yaml`. Never hand-edit
  the ConfigMap. Manifests reference `${snake_case_version}` placeholders.
- **Every namespace carries an ingress default-deny.** The
  `kubernetes/components/netpol-baseline` component is the standard mechanism;
  the two exceptions (`download-clients`, whose local policy covers ingress and
  egress, and `flux-system`, whose upstream manifests ship their own) are
  documented where they occur. Egress allowlists are per-app and their
  reserved-CIDR except-lists must stay identical.
- **Ansible conventions** (FQCN, snake_case + role-prefixed vars, `no_log: true`
  on any secret-touching task, handler + service patterns, the `--tags` caveat)
  are canonical in `ansible/README.md` § "Code conventions".
- **No AI/Claude attribution anywhere** — commits, code, comments, docs, MR text.
- **Fold available version bumps into any MR revision** (never open a revision
  just for bumps). Read the *pipeline's* `version-check` job output each round —
  not a local run — since the local cache can be stale.
- **MR test sections describe what WAS done** — never unchecked checkboxes.
- **The skill rides along with the change.** If a change alters a workflow, gate,
  invariant, or canonical-doc pointer, update `SKILL.md` / the matching
  `references/` file in the same MR. CI checks only the skill's relative links
  and the `task <ns>:<name>` references it names; nothing validates its prose.

## Decision tree — what am I changing?

| Change type | Read first | Canonical docs |
|---|---|---|
| Kubernetes app (dir under `kubernetes/apps/`) | `references/add-k8s-app.md` | `docs/29-flux-operations.md` (Adding a New App), `docs/33-autoscaling.md` |
| New Proxmox VM / LXC app | `references/add-vm-app.md` | `docs/27-gitlab-deployment.md` (worked example), `docs/06-zfs.md`, `docs/11-firewall.md`, `docs/17-disaster-recovery.md`, `docs/18-bootstrap-new-systems.md` |
| **Ansible role behaviour** (tasks, templates, defaults) | the role in `weisssrv-lib` — **not here** | the collection README + `MIGRATING.md`; back here for the pin bump (§ Invariants) |
| Inventory / playbook / host or guest sizing | `ansible/README.md` (layout + conventions) + `CLAUDE.md` § Ansible roles | `docs/01-overview.md`, `docs/18-bootstrap-new-systems.md`, the role's README in the collection |
| Terraform (Cloudflare DNS / Tailscale ACL / Authentik SSO) | `terraform/<module>/README.md` + neighbours | `docs/08-dns.md`, `docs/05-tailscale.md`, `docs/40-authentik-terraform.md` (Authentik apply is a supervised manual `op run -- terraform apply`) |
| CI pipeline (`.gitlab-ci.yml`, `.gitlab/ci/`) | `CLAUDE.md` § Repo family — check whether the job is included from the library before editing | `docs/13-ci-cd.md` (§ Shared CI library), the library's `docs/INCLUDE-CONTRACT.md` |
| `scripts/` | `scripts/README.md` — it marks which scripts are vendored or dual-maintained with the library | `docs/13-ci-cd.md` |
| `docker/` images (molecule + app builds) | the image's own directory | `docs/13-ci-cd.md`, `ansible/TESTING.md` |
| Version bump / upgrade / maintenance | `references/maintenance-upgrades.md` | `docs/12-runbooks.md`, `docs/16-next-steps.md` |
| Debug / incident response | `references/debugging.md` | `docs/29-flux-operations.md`, `docs/12-runbooks.md`, `docs/32-zfs-encryption.md`, `docs/34-bond-mac-flapping.md` |
| Cluster access / secrets / kubeconfig | `references/cluster-access.md` | `docs/15-credential-rotation.md`, `docs/29-flux-operations.md` |
| Storage layout, backups, restores | `references/add-vm-app.md` (zvol + backup steps) | `docs/06-zfs.md`, `docs/44-storage-bootstrap.md`, `docs/42-offsite-backup.md`, `docs/17-disaster-recovery.md` |
| DNS / firewall / observability wiring for a new service | `references/add-k8s-app.md` or `add-vm-app.md` | `docs/08-dns.md`, `docs/11-firewall.md`, `docs/31-observability.md` |

Every new service is mandatory-observed: logs (in-cluster automatic / the
collection's `alloy_host` role for VMs), metrics (exporter or native `/metrics` +
ServiceMonitor/PodMonitor + a scrape NetworkPolicy allow), a down/stale alert
rule, and a blackbox probe for user-facing endpoints. The reference files spell
out the per-change-type checklist.

## Pre-MR gates (run from the worktree root)

- `task lint` — the local lint aggregate; run it for every change. The `lint:`
  task's command list in `Taskfile.yml` is the source of truth for the current
  set (ansible-lint, terraform fmt/validate-local, `flux:lint`, the Python
  script tests + ruff, shellcheck, yamllint, and the coverage / sync /
  version-pin / netpol-parity policy gates).
- Playbooks or inventory touched → install the pinned collection first
  (`ansible-galaxy install -r ansible/requirements.yml --force`), then
  `task ansible:lint`; `ansible-playbook --syntax-check` catches an FQCN or role
  name that no longer resolves. Integration scenarios: `task ansible:test-integration`
  (Docker required) — see `references/debugging.md` for local caveats.
- Versions touched → `task flux:sync-versions`, commit both files (else
  the versions-sync check in `repo-sync-checks` reds the pipeline).
- `hosts.yml` touched → `task hosts:sync`, commit `scripts/hosts.env`.
- `kubernetes/` touched → `task flux:lint` (kustomize build + envsubst with zero
  unsubstituted `${...}` + kubeconform + helm-template). Optionally preview with
  `task flux:dev-apply -- kubernetes/apps/<app>` (reverted next reconcile).
- Prometheus/alert rules touched → `task lint:prometheus-config` (promtool and
  amtool must be on PATH; it also runs inside `task lint`).
- A new guest or deploy target needs its `deploy-*` CI job wiring; the
  coverage-check scripts inside `task lint` fail when it is missing.

## CI, review loop, and merge

- An MR pipeline runs the **path-filtered** lint/validate/test/security/ai-review
  gates only — **no deploy on MRs**. A gate runs only if its paths changed.
- `pr-agent-review` (advisory, `allow_failure`) posts findings within minutes of
  the lint jobs finishing; it is not created on tokenless/fork MRs (expected, not
  a failure). `version-check` is soft-fail and only comments when it has a token.
- **Run the review loop to convergence before raising the MR**: self-review /
  the AI reviewer / any requested reviewer, address every *valid* finding, repeat
  until no valid findings remain. Then push and confirm before opening the MR.
- After merge to `main`: Flux reconciles `kubernetes/` (~1 min; `task
  flux:reconcile` forces it) and the `deploy-*` jobs run behind the main-only
  validation gate. Verify with `task flux:status` / `task flux:verify` (k8s),
  `task infra:verify` (base), `task k3s:status`.

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
