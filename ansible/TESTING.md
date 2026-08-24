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

## Prerequisites

```bash
pip install -r ../requirements.txt   # molecule + molecule-plugins[docker], pinned
docker info                          # Docker must be running
```

The scenarios pull the published `molecule-test` image from weisssrv-lib's
registry. Override it for a local build:

```bash
export MOLECULE_TEST_IMAGE=registry.git.ericsweiss.com/eric/weisssrv-lib/molecule-test:v0.13.2
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
and unit land. `tailscale up` self-skips because the scenario sets no
`TAILSCALE_AUTH_KEY`, which is what makes it runnable in a container.

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

## Idempotence

Four of the five stacks run `idempotence` in their `test_sequence`: converge
runs twice and the second pass must report zero changes.

**`cert-distribution` is the exception** and omits the step deliberately: the
scenario generates a fresh SSH keypair per run and re-injects it via `add_host`,
so the key deployment is genuinely changed on every converge.

A stack that starts failing idempotence usually points at a task in the
*collection* with a missing `changed_when`, an unconditional file write, or a
shell command with no `creates:` guard — fix it in weisssrv-lib.

## Expected negative-path failures and the junit report

If a scenario drives a guard task to failure inside `block`/`rescue` to prove
the guard fires, the Ansible junit callback records the RAW task failure even
though the rescue handles it, which would leave red entries in a green
pipeline's report. No stack declares one today — the declaration file below is
absent and the sanitize step is a no-op — so this is the contract to follow when
adding the first. Two mechanisms keep the report truthful:

1. **Declared downgrades** — a scenario lists its expected failing task names in
   `molecule/default/expected-junit-failures.txt`, and CI's last script step
   (`scripts/sanitize-junit-expected-failures.py`, which runs only when molecule
   SUCCEEDED) replaces exactly those entries with a system-out note. Undeclared
   failures stay red, and a failed job keeps its raw report. Add the task name to
   the declaration file when you add a negative-path exercise.
2. **Retry hygiene** — `scripts/molecule-retry.sh` clears the junit directory
   between attempts, so a transient first attempt no longer uploads red
   testcases alongside the passing retry.

Job status remains the arbiter; the report agrees with it.

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
