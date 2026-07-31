# Ansible

Configuration management for the six Proxmox hosts, the LXC/VM guests, and the
k3s node VMs. Everything *inside* the k3s cluster is Flux's, not Ansible's.

| Path | What lives there |
|---|---|
| `inventories/prod/hosts.yml` | Host definitions, groups, VM/LXC sizing, `vm_additional_disks` (zvols) |
| `inventories/prod/group_vars/all.yml` | Single source of truth for every version pin + the `secrets:` `op://` references |
| `inventories/prod/group_vars/<group>.yml`, `host_vars/<host>.yml` | Group/host overrides |
| `playbooks/` | Entry points (`site.yml` is the fan-out; per-area playbooks mirror the `deploy-*` CI jobs) |
| `roles/` | One role per service — the **Ansible Roles** table in the repo `README.md` is the source of truth for the list |
| `molecule/base.yml` | Shared molecule driver/provisioner config inherited by every scenario |
| `TESTING.md` | The molecule testing infrastructure: scenario coverage table, how to run, how to add a scenario |

Deployment commands are `task infra:*` / `task k3s:*` / the per-app namespaces —
run `task --list`. Every task is idempotent and safe to re-run.

## Code conventions

These are repo-wide rules; `CLAUDE.md`, `AGENTS.md` and `.cursorrules` point
here rather than restating them.

- **Fully-qualified collection names (FQCN)** — `ansible.builtin.apt`, not `apt`.
  Partly enforced by `ansible-lint` (`profile: production`) via `task lint`.
- **snake_case** for all variables; role-specific vars are prefixed with the role
  name (e.g. `adguard_http_port`). Var precedence (low→high):
  vars written **inside `hosts.yml`** (a group's `vars:` block) →
  `group_vars/all.yml` → `group_vars/<group>.yml` → host vars inside `hosts.yml`
  → `host_vars/<host>.yml`. Note where the inventory FILE sits: a group `vars:`
  block in `hosts.yml` is the LOWEST of those, so an override written there is
  beaten by anything in `group_vars/`. Put a group-scoped override that must
  hold in `group_vars/<group>.yml` (see `group_vars/services.yml`).
- **`no_log: true` on any task that handles a secret** (renders a password into a
  file, passes a credential, …). `ansible-lint` does **not** fully catch this —
  it matches known password module params, not the template-writes-a-secret
  pattern — so apply it by hand. Secrets themselves are `op://Homelab/...`
  references in the `secrets:` dict; never literals.
- **Handler pattern**: a config-changing task `notify:`s a handler that does the
  `state: restarted`; handlers live in `handlers/main.yml`. When a readiness
  probe must observe the restarted process, `meta: flush_handlers` before it.
- **Service pattern**: install packages → create a system user (`system: true`,
  `shell: /usr/sbin/nologin`) → deploy the systemd unit template
  (`notify: [Reload systemd, Restart <svc>]`) → enable + start.
- **Tags**: playbooks tag roles at the `roles:` level (see `site.yml`); a task may
  add finer tags (the `base` role tags its SSH/user tasks `ssh` / `users`).
  `--tags <x>` silently matches nothing and exits 0 when `<x>` is not a declared
  tag, so verify with `ansible-playbook <playbook> --list-tasks --tags <x>`
  before publishing a command in a runbook.
- **Follow existing patterns** — mirror the closest neighbouring role instead of
  inventing a new shape.

## Testing

- `task ansible:test -- <role...>` runs the molecule scenarios (the `--` is
  required for go-task to pass the role names through); omit the roles to run
  every scenario. Needs Docker.
- Every role in `roles/` ships a molecule scenario, and CI enforces that a new
  role has both a scenario in the matrix and a `deploy-*` job
  (`scripts/check-molecule-matrix-coverage.sh`, `scripts/check-deploy-coverage.sh`,
  both run by `task lint`).
- Scenario-by-scenario coverage, container caveats and the local-run gotchas:
  [TESTING.md](TESTING.md).

## Secrets

Host-side tooling resolves `op://Homelab/<Item>/<field>` references at runtime
via `op run`. The full three-consumer model (Ansible/Terraform, in-cluster ESO,
CI) is in [../docs/15-credential-rotation.md](../docs/15-credential-rotation.md)
§ Secrets model, and the item inventory in the same file.
