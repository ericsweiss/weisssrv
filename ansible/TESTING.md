# Ansible testing

## What is tested where

| Layer | Lives in | Runs |
|---|---|---|
| Per-role Molecule scenarios | **weisssrv-lib**, beside each role in `ansible_collections/weisssrv/infra/roles/<role>/molecule/` | that repo's `molecule-tests` matrix |
| Multi-role integration stacks | this repo, `ansible/integration-tests/` | `task ansible:test`, and the `integration-tests` matrix job |
| Static analysis | this repo | `task ansible:lint` (ansible-lint + yamllint over the whole `ansible/` tree — inventory and the shared `molecule/` prep included) |
| Production verification | this repo, `playbooks/postflight.yml` | `task infra:verify` |

A role's own behaviour is proven where the role lives; a change to one is
released as a library tag and adopted here by bumping `requirements.yml`. What
this repo proves is the composition: that the roles this site actually runs
together still converge together, against the pinned collection.

The four files under `ansible/molecule/` are vendored from weisssrv-lib's
`molecule-shared/`. Fix one upstream and re-vendor it here; a local edit is
lost on the next re-vendor.

## Prerequisites

```bash
pip install -r ../requirements.txt   # molecule + molecule-plugins[docker], pinned
docker info                          # Docker must be running
```

The scenarios pull the published `molecule-test` image from weisssrv-lib's
registry. Override it for a local build:

```bash
export MOLECULE_TEST_IMAGE=registry.git.ericsweiss.com/eric/weisssrv-lib/molecule-test:v0.18.0
```

The collection itself is installed by molecule's `galaxy` dependency step from
`ansible/requirements.yml`, so a scenario always exercises the pinned tag — not
whatever happens to be in the operator's collections path.

## Running

```bash
task ansible:test                       # every stack
task ansible:test-integration-dns       # one stack (also -mail, -base, -storage, -certs)

# By hand, from the stack directory:
cd ansible/integration-tests/dns-stack
molecule test                           # full cycle
molecule converge                       # apply only
molecule verify                         # assertions only, after a converge
molecule login -h dns-01                # shell into a container
molecule destroy
```

`molecule test --destroy=never` keeps the containers for debugging; `molecule
destroy` cleans up.

## The stacks

A scenario cannot override anything `inventories/prod/group_vars/all.yml`
defines. The converge play loads all.yml through `vars_files`, which outranks
both inventory group_vars and the play's own `vars:`. Steer such a key through
the scenario's `provisioner.env` instead, where all.yml reads it from the
environment. A key all.yml does not define can live in either place.

### DNS stack — `integration-tests/dns-stack/`

`unbound` + `adguard_home` + `adguard_sync` across two servers (dns-01 primary,
dns-02 replica) on a shared Docker network.

Asserted: Unbound installs, runs and resolves over DoT; the converge-supplied
`unbound_forwarders` actually reach the template (a negative check on a
default-only forwarder catches a rename silently falling back); AdGuard Home
installs, starts and answers on :3000; the codified `adguard_home_rewrites`
are reconciled onto the primary through the role's API path; the sync unit and
timer are configured and enabled on the primary.

Not asserted: DNS on :53 through AdGuard, and full sync operation — the role
skips the resolv.conf switch in-container, so AdGuard's own resolution path is
not representative. `postflight.yml` covers both against the real resolvers.

### Mail stack — `integration-tests/mail-stack/`

`smtp_relay` + `postfix_null_client`: one relay, two clients.

Asserted: the relay's `main.cf` renders from the role defaults merged with the
scenario's deltas; the SASL password files exist with the right modes on relay
and clients; the null clients are loopback-only; a client reaches the relay on
:25 and gets an SMTP banner; the alias map rewrites root to the admin address.

Not asserted: real SASL auth, delivery to an upstream smarthost, or STARTTLS —
the credentials are mock. Real mail flow is verified in production.

### Base infrastructure — `integration-tests/base-infrastructure/`

`base` + `qol` + `tailscale` on two hosts. Asserted: packages, admin user and
sudoers, timezone, the zsh/neovim configuration, and that the Tailscale package
lands and the unit is installed and enabled. `tailscale up` self-skips because
the scenario sets no `TAILSCALE_AUTH_KEY`, which is what makes it runnable in a
container. The daemon's runtime state is deliberately not asserted.

### Storage stack — `integration-tests/storage-stack/`

`nas_storage` + a Samba client. Asserted: the rendered `/etc/exports` (including
a production-shaped entry with `bind_source`, export-level `xprtsec=tls`, fsid
and `all_squash` mapping), the Samba configuration and shares, the smartd setup,
and share listing from the client.

Not asserted: NFS or CIFS client mounts — Docker containers have no kernel NFS
support and CIFS needs `CAP_SYS_ADMIN`. Server-side render and Samba access are
the boundary.

### Certificate distribution — `integration-tests/cert-distribution/`

`acme_certs` with SSH distribution: one cert server, two clients. Certificate
issuance is mocked (a real CA + wildcard leaf are generated locally), so what is
exercised is everything after issuance: key generation, the pinned-host-key push
path, the forced-command receiver, the sudoers entry, and the per-target reload.

### Not covered by any stack

Two lifecycles have no composition coverage here. The k3s lifecycle
(`proxmox_vm` -> k3s server/agent -> `kube_vip`) and the Ansible-provisioned
guests (gitlab, nextcloud, immich, immich_ml, plex, home_assistant, windows)
appear in no stack. Their per-role scenarios in weisssrv-lib are render and
contract scenarios by design — they set `k3s_skip_install`, `gitlab_skip_install`,
`nextcloud_skip_install`, `immich_skip_install`, `immich_ml_skip_install`,
`docker_engine_skip_install` and `compose_app_skip_install` — so the install and
composition halves are proven only against production, by
`playbooks/postflight.yml` (`task infra:verify`) and the per-guest `:verify`
tasks.

## Idempotence

Four of the five stacks run `idempotence` in their `test_sequence`: converge
runs twice and the second pass must report zero changes.

**`cert-distribution` is the exception** and omits the step deliberately: the
scenario generates a fresh SSH keypair per run and re-injects it via `add_host`,
so the key deployment is genuinely changed on every converge.

A stack that starts failing idempotence usually points at a task in the
*collection* with a missing `changed_when`, an unconditional file write, or a
shell command with no `creates:` guard — fix it in weisssrv-lib.

## Container caveats

Works in a systemd container: package installs, users and groups, file and
template rendering, systemd unit management, most service starts, Samba.

Does not: ZFS (no kernel modules), NFS client mounts, real block devices,
`tailscale up`, anything needing a real network peer. Roles expose skip flags
for exactly these — `nas_storage_skip_zfs_operations`,
`nas_storage_skip_nfs_reload`, `nas_storage_skip_smartd_service`,
`base_skip_ssh_config`, `base_skip_dns_config` — and the scenarios set them
with a comment saying why.

Note the architecture: the containers are amd64. On an arm64 workstation Docker
must have binfmt/qemu emulation available, and some scenarios are slow enough
that CI is the practical arbiter.

## Expected negative-path failures

No stack declares one today. If you add a scenario that drives a guard task to
failure inside `block`/`rescue`, the junit callback records the raw failure even
though the rescue handles it — list the task name in
`molecule/default/expected-junit-failures.txt` and
`scripts/sanitize-junit-expected-failures.py` (CI's last step, and only on a
molecule success) downgrades exactly those entries. Undeclared failures stay
red, and `scripts/molecule-retry.sh` clears the junit directory between attempts
so a transient first try never uploads alongside a passing retry.

A negative case must also prove that the guard is what failed. A `rescue:` that
only logs catches every failure in the block, the sentinel task included, so
write it the way the collection's role scenarios do: end the block with
`set_fact: <case>_failed: false`, set it true in the `rescue` under
`when: ansible_failed_task.name == '<the guard task name>'`, and assert the fact
from a task outside the block with `| default(false)`.

Each line of the file is a case-sensitive substring matched against the junit
testcase name, and declares one testcase. A line ending in ` ::<n>` declares
that it matches exactly n of them, where `<n>` is a positive integer. Blank
lines and `#` comments are ignored.

Keep every pattern narrow. CI passes `--strict`, so a declaration that matches
no testcase, or a number of them other than its ` ::<n>` count, fails the job
instead of warning. A renamed or deleted guard is then a finding, a pattern that
stops firing cannot sit there unnoticed, and a broad pattern cannot green-wash a
real failure elsewhere in the scenario. Without `--strict` the count is only a
cap and a mismatch is a warning, so declare the number the run records rather
than a margin.

Count what the run records, not what the scenario reads like. The junit callback
writes one testcase per task per host, so a guard that fires on two platforms
counts twice. A scenario whose `test_sequence` includes `idempotence` replays
converge, so a converge-driven guard counts twice unless its task or an
enclosing block carries the `molecule-idempotence-notest` tag. A case driven
under `ignore_errors: true` is recorded as passed, so it is never observed and
must not be declared.

## Pre-deployment checklist

- `task ansible:lint`
- `task infra:check` (dry-run)
- `op whoami` (1Password secrets resolvable)
- `task ansible:test` when a change touches the roles' composition

## Post-deployment

`task infra:verify` runs `playbooks/postflight.yml`: SSH reachability and disk
headroom on every managed host, service state and a live DNS query on both
resolvers, the relay's SASL configuration, ZFS pool health, mounts, SMART and
NFS/Samba on the NAS, the certificate-distribution SSH path from dns-01, and
`xprtsec=tls` on every live NAS mount from a k3s node.

## References

- [Molecule](https://molecule.readthedocs.io/)
- [molecule-plugins (docker driver)](https://github.com/ansible-community/molecule-plugins)
- weisssrv-lib: the collection README and `MIGRATING.md` for the role-side
  contract, and that repo's own testing docs for the per-role scenarios
