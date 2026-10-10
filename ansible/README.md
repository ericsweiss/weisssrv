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
| `inventories/prod/group_vars/<group>.yml`, `host_vars/<host>.yml` | Group/host overrides, and the site data the generic roles require |
| `playbooks/` | Entry points (`site.yml` is the fan-out; per-area playbooks mirror the `deploy-*` CI jobs) |
| `playbooks/tasks/` | Task files several playbooks include: the check-mode reachability guard, the PVE cluster probe, and the cert-target re-seed |
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
un-renamed does not raise, it silently takes the role default. Each role's
README in the collection lists every variable it reads, its default, and which
inputs the role asserts.

To iterate against an unmerged library change, point `version:` at a branch,
re-run `ansible-galaxy install -r requirements.yml --force`, and change it back
to a tag before opening the merge request.

## Guest playbooks and the cert-distribution key

`weisssrv.infra.base` rewrites `authorized_keys`, so a re-provisioned guest
loses the `acme_certs` distribution key pinned there. Each guest that is a cert
target (`plex.yml`, `immich.yml`, `nextcloud.yml`, `gitlab.yml`) ends with a
play on the `dns_primary` group (dns-01, the cert authority) that includes
`tasks/_reseed-cert-target.yml` with `reseed_cert_target_host` set to that
guest. `base.yml` and `mail.yml` include the same task over a loop — every
`acme_certs_distribution_targets` entry, and the `mail` group's. Adding a new
cert-target guest means adding the same play, and
`scripts/test_cert_reseed_coverage.py` fails one that applies base without it.
`dns-02` needs none: `dns.yml` already runs `acme_certs` in full. Details are
in `docs/15-credential-rotation.md`.

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
  references in the invoking task's `env:` block (the owning
  `taskfiles/<ns>.yml`, or the root `Taskfile.yml`), mirrored by the matching CI
  job's `variables:` (`task secrets:show` prints the live set); never literals,
  and never in the inventory. See
  `docs/15-credential-rotation.md` § Secrets model.
- **Tags**: playbooks tag roles at the `roles:` level (see `site.yml`); a task may
  add finer tags (the `base` role tags its SSH/user tasks `ssh` / `users`).
  `--tags <x>` silently matches nothing and exits 0 when `<x>` is not a declared
  tag, so verify with `ansible-playbook <playbook> --list-tasks --tags <x>`
  before publishing a command in a runbook.
- **Follow existing patterns** — mirror the closest neighbouring playbook or
  inventory block instead of inventing a new shape. The handler and service
  patterns the roles follow are documented in the collection's own README.
- **Cross-cutting roles are listed twice on purpose.** `node_exporter_host` and
  `alloy_host` are deployed by `site.yml` in dedicated plays AND by each app
  playbook, so a standalone `task <app>:deploy` does not leave metrics or log
  shipping behind. Each app playbook marks them with `# Shared with site.yml;
  ...`, and `scripts/test_shared_role_playbook_sync.py` fails when the two
  disagree about either role on any host.
- **`nfs_tls` is narrower.** It runs where a host mounts pve-nas-01's exports
  over `xprtsec=tls` — the Proxmox hosts, the k3s nodes and pve-nas-01 itself as
  the server. The sync gate does not cover it, so add it by hand when a new
  guest needs a TLS mount.

- **`ansible.cfg` sizing and trust model.** `forks = 15` is sized to the CI
  runner build container's memory cap (`kubernetes/apps/gitlab-runner/release.yaml`);
  raise the two together. There is no fact-caching backend on purpose, so facts
  are gathered once per run and never outlive it. `host_key_checking` stays on
  with `StrictHostKeyChecking=accept-new`: after a legitimate host rebuild, run
  `ssh-keygen -R <host>` and let accept-new record the new key. Never set
  `host_key_checking=False`.

## Testing

- `task ansible:lint` lints the whole `ansible/` tree — `playbooks/`,
  `integration-tests/`, `inventories/prod/` and the shared `molecule/` prep —
  with `.ansible-lint` excluding the collection installed under
  `.ansible-home/collections`.
- `task ansible:test` runs the integration-test stacks (needs Docker).
- Per-role molecule scenarios run in weisssrv-lib, against the role. Details and
  container caveats: [TESTING.md](TESTING.md).
- `task infra:verify` runs `playbooks/postflight.yml`. It is read-only apart
  from one deliberate exception: it triggers a single `adguardhome-sync` run,
  because starting the unit is the only way to prove the sandbox can start.
  That run pushes dns-01's config to dns-02, so it reports as changed. Pass
  `-e postflight_exercise_sync=false` to skip it.

## Secrets

Host-side tooling resolves `op://Homelab/<Item>/<field>` references at runtime
via `op run`. The full three-consumer model (Ansible/Terraform, in-cluster ESO,
CI) is in [../docs/15-credential-rotation.md](../docs/15-credential-rotation.md)
§ Secrets model, and the item inventory in the same file.
