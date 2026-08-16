# Ansible

Configuration management for the six Proxmox hosts, the LXC/VM guests, and the
k3s node VMs. Everything *inside* the k3s cluster is Flux's, not Ansible's.

**There is no `roles/` directory.** Every role lives in the `weisssrv.infra`
collection in [weisssrv-lib](https://git.ericsweiss.com/eric/weisssrv-lib),
pinned in `requirements.yml`; playbooks address roles by FQCN
(`weisssrv.infra.base`). This repo is the *site*: inventory, playbook
composition, and the integration tests that exercise the roles together.

| Path | What lives there |
|---|---|
| `requirements.yml` | The `weisssrv.infra` pin (a release tag) + the galaxy collections it needs. Bumping the platform is a bump of `version:` here |
| `inventories/prod/hosts.yml` | Host definitions, groups, VM/LXC sizing, `vm_additional_disks` (zvols) |
| `inventories/prod/group_vars/all.yml` | Single source of truth for every version pin |
| `inventories/prod/group_vars/<group>.yml`, `host_vars/<host>.yml` | Group/host overrides — and, since the roles are generic, all the site data they used to default to |
| `playbooks/` | Entry points (`site.yml` is the fan-out; per-area playbooks mirror the `deploy-*` CI jobs) |
| `integration-tests/` | Multi-role molecule stacks (DNS, mail, base, storage, certs) |
| `molecule/` | Shared prepare/warm-up tasks the integration-test scenarios import |
| `TESTING.md` | What this repo tests, how to run it, and what moved to the library |

Deployment commands are `task infra:*` / `task k3s:*` / the per-app namespaces —
run `task --list`. Every task is idempotent and safe to re-run.

## Changing a role

Roles are not edited here. The workflow is:

1. Change the role in `weisssrv-lib` (its molecule scenario lives with it).
2. Merge there and cut a tag.
3. Bump `version:` in `requirements.yml` and land the matching inventory
   changes in the SAME merge request.

Step 3 is not optional when the role's variables changed. Every role variable
carries its role's prefix, and every lookup is `| default(...)` — so a name left
un-renamed does not raise, it silently takes the role default. The collection's
`MIGRATING.md` carries the complete old → new map, the variables whose *value*
(not name) is now empty, and the inputs that are asserted.

To iterate against an unmerged library change, point `version:` at a branch,
re-run `ansible-galaxy install -r requirements.yml --force`, and change it back
to a tag before opening the merge request.

## Code conventions

These are repo-wide rules; `CLAUDE.md`, `AGENTS.md` and `.cursorrules` point
here rather than restating them.

- **Fully-qualified names** — `ansible.builtin.apt`, not `apt`;
  `weisssrv.infra.base`, not `base`. Enforced by `ansible-lint`
  (`profile: production`) via `task lint`.
- **snake_case** for all variables; role variables carry the role's name as a
  prefix (`adguard_home_http_port`). A handful of conventionally
  inventory-wide names (`admin_user`, `timezone`, `dns_servers`,
  `internal_domain`, `vm_additional_disks`, …) are aliased by the roles and keep
  their bare form — the collection README's alias table is the authoritative
  list. Var precedence (low→high): vars written **inside `hosts.yml`** (a
  group's `vars:` block) → `group_vars/all.yml` → `group_vars/<group>.yml` →
  host vars inside `hosts.yml` → `host_vars/<host>.yml`. Note where the
  inventory FILE sits: a group `vars:` block in `hosts.yml` is the LOWEST of
  those, so an override written there is beaten by anything in `group_vars/`.
  Put a group-scoped override that must hold in `group_vars/<group>.yml` (see
  `group_vars/services.yml`).
- **`no_log: true` on any task that handles a secret** (renders a password into a
  file, passes a credential, …). `ansible-lint` does **not** fully catch this —
  it matches known password module params, not the template-writes-a-secret
  pattern — so apply it by hand. Secrets themselves are `op://Homelab/...`
  references in the invoking `Taskfile.yml` task's `env:` block, mirrored by the
  matching CI job's `variables:` (`task secrets:show` prints the live set);
  never literals, and never in the inventory. See
  `docs/15-credential-rotation.md` § Secrets model.
- **Tags**: playbooks tag roles at the `roles:` level (see `site.yml`); a task may
  add finer tags (the `base` role tags its SSH/user tasks `ssh` / `users`).
  `--tags <x>` silently matches nothing and exits 0 when `<x>` is not a declared
  tag, so verify with `ansible-playbook <playbook> --list-tasks --tags <x>`
  before publishing a command in a runbook.
- **Follow existing patterns** — mirror the closest neighbouring playbook or
  inventory block instead of inventing a new shape. The handler and service
  patterns the roles follow are documented in the collection's own README.
- **Cross-cutting roles are listed twice on purpose.** `node_exporter_host`,
  `alloy_host` and `nfs_tls` are deployed by `site.yml` in dedicated plays AND
  by each app playbook, so a standalone `task <app>:deploy` does not leave
  metrics, log shipping or tlshd behind. Each app playbook marks them with
  `# Also in site.yml; listed here so a standalone deploy stays in sync.`

## Testing

- `task ansible:lint` lints the whole `ansible/` tree — `playbooks/`,
  `integration-tests/`, `inventories/prod/` and the shared `molecule/` prep —
  with `.ansible-lint` excluding the collection installed under
  `.ansible-home/collections`.
- `task ansible:test` runs the integration-test stacks (needs Docker).
- Per-role molecule scenarios run in weisssrv-lib, against the role. Details and
  container caveats: [TESTING.md](TESTING.md).

## Secrets

Host-side tooling resolves `op://Homelab/<Item>/<field>` references at runtime
via `op run`. The full three-consumer model (Ansible/Terraform, in-cluster ESO,
CI) is in [../docs/15-credential-rotation.md](../docs/15-credential-rotation.md)
§ Secrets model, and the item inventory in the same file.
