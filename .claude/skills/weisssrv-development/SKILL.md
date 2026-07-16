---
name: weisssrv-development
description: >-
  Use when working in the weisssrv homelab IaC repo (Proxmox + Ansible +
  Terraform + k3s/Flux) — developing or changing features, adding a Kubernetes
  app / Ansible role / VM, deploying, debugging or managing the cluster, running
  maintenance or version upgrades, or incident response. Establishes the
  non-negotiable workflow (branch+MR, never push main, no secrets, Flux owns
  k8s, single-source versions), the pre-MR gate commands, the change-type
  decision tree, and where every canonical doc lives. Read this before making
  ANY change in this repo.
---

# weisssrv development

Homelab Infrastructure-as-Code: Proxmox + Ansible + Terraform + k3s/Flux GitOps.
This skill is the operating map — it points at the canonical docs and encodes the
workflow/gates that live nowhere else. It does not restate doc content; open the
referenced files. All paths below are repo-relative.

Canonical remote: GitLab (`git.ericsweiss.com/eric/weisssrv`). GitHub is a
read-only mirror. `CLAUDE.md` is the top-level fact source; `README.md` owns the
roles table and docs index; `task --list` owns the command set.

## Invariants (violating any breaks prod or fails review)

- **Never push to `main`.** Every change — even a one-line hotfix — ships via a
  feature branch + GitLab MR. Flux and CI act on `main` only after merge.
- **Keep feature branches local until the MR is ready, and confirm with the user
  before pushing.** Do not open an MR unprompted.
- **No secrets in git.** Host tooling: `op://Homelab/<Item>/<field>` refs in the
  `secrets:` dict of `ansible/inventories/prod/group_vars/all.yml`. In-cluster:
  `ExternalSecret` with `remoteRef.key` = 1Password item **title**,
  `remoteRef.property` = **field**, store `onepassword-homelab`. New 1P items go
  in `docs/15-credential-rotation.md`.
- **Flux owns all of `kubernetes/`.** No `kubectl apply -k` / `helm upgrade` as
  ongoing ops. Only exceptions: `task flux:dev-apply -- <path>` (reverted next
  reconcile), the two bootstrap secrets, Ansible-provisioned objects outside
  `kubernetes/`.
- **Never automate ZFS pool create/destroy** — pools are manual. Ansible sets
  properties and mounts zvols only.
- **Versions are single-sourced** in `group_vars/all.yml`. After editing a pin,
  run `task flux:sync-versions` and commit **both** `all.yml` and
  `kubernetes/infrastructure/sources/versions-configmap.yaml`. Never hand-edit
  the ConfigMap. Manifests reference `${snake_case_version}` placeholders.
- **Ansible conventions** (FQCN, snake_case + role-prefixed vars, `no_log: true`
  on any secret-touching task, handler + service patterns) are canonical in
  `CLAUDE.md` § "Code Conventions" — follow that section, do not reinvent.
- **No AI/Claude attribution anywhere** — commits, code, comments, docs, MR text.
- **Fold available version bumps into any MR revision** (never open a revision
  just for bumps). Read the *pipeline's* `version-check` job output each round —
  not a local run — since the local cache can be stale.
- **MR test sections describe what WAS done** — never unchecked checkboxes.

## Decision tree — what am I changing?

| Change type | Read first | Canonical docs |
|---|---|---|
| Kubernetes app (dir under `kubernetes/apps/`) | `references/add-k8s-app.md` | `docs/29-flux-operations.md` (Adding a New App), `docs/33-autoscaling.md` |
| New Proxmox VM / LXC app | `references/add-vm-app.md` | `docs/27-gitlab-deployment.md` (GitLab example), `docs/06-zfs.md`, `docs/11-firewall.md`, `docs/17-disaster-recovery.md`, `docs/18-bootstrap-new-systems.md` |
| Ansible role / base infra | `CLAUDE.md` § Ansible Roles + Code Conventions | `docs/18-bootstrap-new-systems.md`, the role's README |
| Terraform (Cloudflare DNS / Tailscale ACL) | `terraform/<module>/` + neighbours | `docs/08-dns.md`, `docs/05-tailscale.md` |
| Version bump / upgrade / maintenance | `references/maintenance-upgrades.md` | `docs/12-runbooks.md`, `docs/16-next-steps.md` |
| Debug / incident response | `references/debugging.md` | `docs/29-flux-operations.md`, `docs/12-runbooks.md`, `docs/32-zfs-encryption.md`, `docs/34-bond-mac-flapping.md` |
| Cluster access / secrets / kubeconfig | `references/cluster-access.md` | `docs/15-credential-rotation.md`, `docs/29-flux-operations.md` |
| DNS / firewall / observability wiring for a new service | `references/add-k8s-app.md` or `add-vm-app.md` | `docs/08-dns.md`, `docs/11-firewall.md`, `docs/31-observability.md` |

Every new service is mandatory-observed: logs (in-cluster automatic / `alloy_host`
for VMs), metrics (exporter or native `/metrics` + ServiceMonitor/PodMonitor +
scrape NetworkPolicy), a down/stale alert rule in the kube-prometheus-stack
groups, and a blackbox probe for user-facing endpoints. The reference files spell
out the per-change-type checklist.

## Pre-MR gates (run from the worktree root)

- `task lint` — the full local mirror of the CI lint stage (ansible-lint,
  terraform fmt/validate, `flux:lint`, `scripts:test`, shellcheck, yamllint,
  coverage-checks, flux-version-pin). Run this for every change.
- Ansible role touched → `task ansible:test -- <role...>` (omit the args to run
  every scenario; the `--` is required for go-task to pass roles through). Needs
  Docker; CI runs the molecule matrix regardless — see `references/debugging.md`
  for local caveats.
  A new role also needs a `deploy-*` CI job + molecule-matrix entry; `task lint`
  runs the coverage-check scripts that enforce this.
- Versions touched → `task flux:sync-versions`, commit both files (else
  `flux-versions-sync` reds the pipeline).
- `hosts.yml` touched → `task hosts:sync`, commit `scripts/hosts.env`.
- `kubernetes/` touched → `task flux:lint` (kustomize build + envsubst with zero
  unsubstituted `${...}` + kubeconform + helm-template). Optionally preview with
  `task flux:dev-apply -- kubernetes/apps/<app>` (reverted next reconcile).
- Prometheus/alert rules touched → `task lint:prometheus-config` (promtool/amtool;
  NOT part of the top-level `task lint`).

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
- `references/add-vm-app.md` — new Proxmox VM/LXC app (the full VM checklist,
  encryption vmids, firewall, backups, cert distribution, ingress, DNS).
- `references/debugging.md` — symptom → entry point (Flux, pods, NFS, DNS, certs,
  nodes, backups, reboot-safety, local molecule caveats).
- `references/maintenance-upgrades.md` — version-bump flow, node/full upgrades,
  reboots, MetalLB hold, Helm CRD lifecycle trap.
- `references/cluster-access.md` — kubeconfig, KUBECONFIG, ssh naming, `op`
  wrappers, 1Password conventions, Tailscale.
