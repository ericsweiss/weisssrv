# Ansible Role Testing with Molecule

This document describes the Molecule-based testing infrastructure for weisssrv Ansible roles.

## Overview

Molecule tests run each role inside a Docker container (Debian Trixie with systemd) to verify
that tasks execute correctly, configurations are deployed as expected, and services start properly.

**Tested roles (15 roles, 16 scenarios):**

| Role | Scenarios | What it tests | Container notes |
|------|-----------|--------------|-----------------|
| base | default | Package install, user/sudoers, authorized_keys, timezone, DNS template | Skips SSH/DNS config in containers |
| qol | default | Oh My Zsh, neovim/Vundle, zsh plugins, shell change | Plugin config validated |
| adguard_home | default | Binary install, systemd service, user/group, HTTP port, config content | Architecture-aware download |
| adguard_sync | default | Binary download, systemd timer, config generation | Binary may not run on ARM hosts |
| acme_certs | default | acme.sh install, SSH keys, cert reload script | Pre-installs via git |
| tailscale | default | Apt repo, package install, systemd service | Uses test version from molecule.yml |
| nas_storage | default | NFS exports, Samba config, SMART monitoring | Mock ZFS mount points |
| k3s | default, agent | Server/agent config, kube-vip manifest, labels/taints | Skips binary install |
| plex | default | Apt repo, package, systemd override, user groups | Skips GPU drivers and service |
| unbound | default | Package install, config, DoT forwarders, listening port | DNS resolution may fail |
| postfix_null_client | default | Postfix config, SASL credentials, aliases, mailname | Uses test credentials |
| smtp_relay | default | Gmail relay config, dual SASL, TLS dirs, SASL database | Mock TLS certs |
| gitlab | default | VM-side GitLab install config (Omnibus, ports, SSO, registry) | Skips actual Omnibus run |
| zvol_mount | default | Loopback→by-id symlink, mkfs, fstab by-UUID, mount | Loopback replaces Proxmox SCSI zvol |
| nic_tuning | default | sysctl.d drop-in for ip_forward, per-NIC ethtool cfg with `logger` guard, idempotence | Uses loopback interface + ansible.posix.sysctl |

## Prerequisites

```bash
# Python packages
pip install molecule molecule-docker

# Ansible collections (already in requirements.yml)
ansible-galaxy install -r ansible/requirements.yml

# Docker must be running
docker info
```

## Running Tests

### Quick start

```bash
# Run all role tests
task ansible:test

# Or directly
cd ansible && ./test-all-roles.sh

# Test a specific role
task ansible:test -- k3s

# Test multiple specific roles
cd ansible && ./test-all-roles.sh unbound smtp_relay
```

### Individual role testing

```bash
# Full test cycle (create -> converge -> verify -> destroy)
cd ansible/roles/unbound
python3 -m molecule test

# Just converge (apply role) without destroying
python3 -m molecule converge

# Run verification only (after converge)
python3 -m molecule verify

# Keep container running after test (for debugging)
python3 -m molecule test --destroy=never

# Shell into test container for debugging
docker exec -it <container-name> bash

# Destroy test container
python3 -m molecule destroy
```

### Keeping containers for debugging

```bash
# Run tests but keep containers
MOLECULE_OPTS="--destroy=never" ./test-all-roles.sh

# Shell into a container
docker exec -it k3s-server-test bash
docker exec -it adguard-test bash
docker exec -it unbound-test bash

# Check service status inside container
docker exec k3s-server-test systemctl status AdGuardHome
docker exec unbound-test journalctl -u unbound --no-pager

# Clean up when done
cd ansible/roles/<role>
python3 -m molecule destroy
```

## Container Testing Considerations

### What works in containers

- Package installation (apt)
- File/template deployment
- Systemd services (using privileged mode + systemd image)
- User/group creation
- Service port binding
- Configuration validation

### What does NOT work in containers

- **Hardware clock (hwclock)**: The `community.general.timezone` module calls hwclock, which
  fails in containers. The base role detects Docker and uses a symlink-only timezone method.
- **DNS resolution**: Container may not have outbound DNS. Tests that require DNS resolution
  use `ignore_errors: true` with informational output.
- **ZFS operations**: No ZFS kernel module in containers. The nas_storage test uses mock mount
  points and skips actual ZFS operations.
- **k3s binary**: The k3s binary requires kernel-level features (cgroups v2, namespaces). Tests
  verify config deployment only via `skip_k3s_install: true`.
- **Block devices**: No real block devices in containers. Disk mounting tasks are skipped.

### Container skip flags

These variables control container-specific behavior in roles:

| Variable | Role | Effect |
|----------|------|--------|
| `skip_ssh_config` | base | Skips SSH hardening tasks |
| `skip_dns_config` | base | Skips DNS resolver configuration |
| `skip_timezone_config` | base | Skips timezone configuration entirely |
| `skip_k3s_install` | k3s | Skips binary install, service start, and node labeling |
| `skip_gpu_drivers` | plex | Skips Intel GPU driver installation |
| `skip_plex_service` | plex | Skips Plex service startup (handlers too) |
| `ssh_authorized_keys: []` | base | Empty list skips authorized_keys deployment |

The base role auto-detects containers (`ansible_facts['virtualization_type']` in `docker`, `lxc`,
`container`, `podman`) to use a container-compatible timezone method (symlink instead of hwclock).

### Architecture considerations

Running on Apple Silicon (ARM64/aarch64):

- The `adguard_home` role auto-detects architecture and downloads the correct binary
  (arm64 on Apple Silicon, amd64 on Intel/production servers)
- k3s binary install is skipped entirely in containers (tests config only)
- All other roles install from apt which handles architecture automatically

## Test Structure

Each testable role has this directory structure:

```
ansible/roles/<role>/
  molecule/
    default/
      molecule.yml    # Container config, platform, provisioner vars
      converge.yml    # Pre-tasks + role execution
      verify.yml      # Assertions to validate the role worked
  meta/
    main.yml          # Galaxy metadata (role_name, namespace required)
```

### molecule.yml key settings

```yaml
driver:
  name: docker
platforms:
  - name: <role>-test
    image: ghcr.io/hifis-net/debian-systemd:trixie  # Systemd-enabled Debian
    pre_build_image: true
    privileged: true      # Required for systemd
    volumes:
      - /sys/fs/cgroup:/sys/fs/cgroup:rw  # cgroups for systemd
    command: /lib/systemd/systemd          # PID 1 = systemd
provisioner:
  env:
    ANSIBLE_ROLES_PATH: ../../..  # So dependent roles can be found
```

### Galaxy metadata requirement

Every role with molecule tests must have `role_name` and `namespace` in `meta/main.yml`:

```yaml
galaxy_info:
  role_name: my_role_name
  namespace: weisssrv
```

Without these, molecule's role discovery fails with `InvalidPrerequisiteError`.

## Adding Tests for a New Role

1. Create the molecule directory structure:

```bash
mkdir -p ansible/roles/<role>/molecule/default
```

2. Ensure `meta/main.yml` has `role_name` and `namespace`:

```yaml
galaxy_info:
  role_name: <role_name>
  namespace: weisssrv
  author: Eric Weiss
  # ...
```

3. Create `molecule.yml` with container config and test variables
4. Create `converge.yml` with pre-tasks (apt cache, prerequisites) and role invocation
5. Create `verify.yml` with assertions that validate the role's output
6. Test incrementally:

```bash
cd ansible/roles/<role>
python3 -m molecule create    # Start container
python3 -m molecule converge  # Run role
python3 -m molecule verify    # Check results
python3 -m molecule destroy   # Cleanup
```

### Tips for writing tests

- **Mock secrets**: Use test values in `molecule.yml` group_vars. Never reference 1Password.
- **Mock infrastructure**: Create mock TLS certs, directories, etc. in converge pre_tasks.
- **Environment variables**: Set test env vars in `provisioner.env` in molecule.yml.
- **Use set_fact for bcrypt hashes**: Molecule's YAML parser chokes on `$2a$...` strings.
  Set them via `ansible.builtin.set_fact` in converge pre_tasks instead.
- **Skip hardware-dependent tasks**: Add `when: not (skip_X | default(false))` guards
  for tasks that require hardware (block devices, hwclock, etc.).
- **Verify configuration, not runtime**: Focus assertions on file content, permissions,
  and service state rather than functional behavior (DNS resolution, mail delivery).

## Roles NOT tested (and why)

| Role | Reason |
|------|--------|
| proxmox_firewall | Requires Proxmox API (pve-firewall commands) |
| proxmox_vm | Requires Proxmox API (qm commands, cloud-init) |
| proxmox_lxc | Requires Proxmox API (pct commands) |
| proxmox_ha | Requires Proxmox cluster (ha-manager, replication) |
| home_assistant | Requires HAOS VM (SSH/SCP based management) |
| resolv_conf | Shared helper role (exercised transitively by base + adguard_home tests) |
| nic_tuning-integration | Wiring with `base` playbook is covered by `integration-tests/base-infrastructure` |

## Idempotency Testing

All molecule tests include idempotency verification by default. The test sequence runs `converge` twice
and verifies that the second run produces no changes. This catches tasks that are not properly
idempotent (e.g., always reporting "changed" even when no changes are needed).

**Exception:** The cert-distribution integration test intentionally skips idempotence testing
because SSH keys are regenerated on each test run (the role deploys dummy keys that are
overwritten with working keys for the test environment).

The test sequence is:
```yaml
scenario:
  test_sequence:
    - dependency
    - cleanup
    - destroy
    - syntax
    - create
    - prepare
    - converge
    - idempotence    # Runs converge again, fails if any tasks report "changed"
    - verify
    - cleanup
    - destroy
```

If a role fails idempotency testing, check for:
- Tasks using `changed_when: true` or missing `changed_when`
- Tasks that modify files unconditionally
- Shell/command tasks without proper `creates:` or `changed_when:` guards

## Integration Tests

Integration tests verify multi-role interactions using multi-container Docker networks.

**Current Status: Implemented**

Five integration test scenarios are implemented in `ansible/integration-tests/`:

1. **DNS Stack** - unbound + adguard_home + adguard_sync (2 DNS servers with sync)
2. **Mail Stack** - smtp_relay + postfix_null_client (relay server + 2 clients)
3. **Base Infrastructure** - base + qol + tailscale (foundation stack on 2 hosts)
4. **Storage Stack** - nas_storage + Samba client (server + SMB client, NFS server-only)
5. **Certificate Distribution** - acme_certs with SSH distribution (1 cert server + 2 clients)

### Running Integration Tests

```bash
# Run all integration tests
task ansible:test-integration

# Run specific integration test
task ansible:test-integration-dns
task ansible:test-integration-mail
task ansible:test-integration-base
task ansible:test-integration-storage
task ansible:test-integration-certs

# Run directly with molecule
cd ansible/integration-tests/dns-stack
molecule test
```

**Version Management:** Integration tests automatically use production versions from `ansible/inventories/prod/group_vars/all.yml` via `vars_files` in each converge.yml. This ensures tests always use the same versions as production deployments, maintaining a single source of truth.

### DNS Stack Integration Test

**Tests:** unbound + adguard_home + adguard_sync

**Scenario:** Two DNS servers (dns-01 primary, dns-02 replica) on a shared Docker network.

**What IS tested:**
- Unbound DoT recursive resolver installation and configuration on both servers
- AdGuard Home binary installation and systemd service
- AdGuard Home configuration file deployment (AdGuardHome.yaml)
- Web UI accessibility on port 3000
- adguardhome-sync service configuration and timer setup
- Cross-server connectivity (replica can reach primary)

**What is NOT tested (due to AdGuard Home setup wizard requirement):**
- DNS listening on port 53 (requires completing interactive setup wizard first)
- DNS query resolution via AdGuard Home (tested via Unbound directly instead)
- Full adguardhome-sync operation (sync config deployed, but API auth not exercised because AdGuard Home runs in "setup mode" until wizard is completed)

**Note:** Production validation should use `postflight.yml` which tests real instances.

**Location:** `ansible/integration-tests/dns-stack/`

### Mail Stack Integration Test

**Tests:** smtp_relay + postfix_null_client

**Scenario:** One relay server + two mail clients on a shared Docker network.

**What IS tested:**
- SMTP relay server Postfix configuration (relayhost to Gmail SMTP)
- SASL password file existence and permissions (on both relay and clients)
- Postfix null client configuration (loopback-only mode)
- Client-to-relay SMTP transport connectivity (TCP port 25, SMTP banner)
- Virtual alias file configuration

**What is NOT tested (would require real Gmail credentials):**
- SASL authentication between clients and relay (mock credentials used)
- Actual mail delivery to Gmail SMTP
- TLS negotiation (STARTTLS)
- Mail queue processing and delivery

**Note:** Full mail flow is validated manually in production by sending test emails.

**Location:** `ansible/integration-tests/mail-stack/`

### Base Infrastructure Integration Test

**Tests:** base + qol + tailscale

**Scenario:** Two hosts with full base infrastructure stack.

**What it tests:**
- Base role (packages, user, sudoers, timezone)
- QoL role (Oh My Zsh, neovim, fzf, ripgrep)
- Tailscale VPN setup
- User shell configuration integration
- Developer tools functionality

**Location:** `ansible/integration-tests/base-infrastructure/`

### Storage Stack Integration Test

**Tests:** nas_storage + Samba client

**Scenario:** One NAS server + one Samba client on a shared network.

**What it tests:**
- NFS server configuration and exports (server-side only)
- Samba server configuration and shares
- SMART monitoring setup
- Samba client connectivity and share listing

**Limitations (Docker):**
- NFS client mounts are NOT tested (Docker containers lack kernel NFS support)
- CIFS/SMB mounts are NOT tested (requires CAP_SYS_ADMIN and kernel modules)
- The test validates server configuration and Samba share access, but cannot verify NFS client read/write operations

**Location:** `ansible/integration-tests/storage-stack/`

### Certificate Distribution Integration Test

**Tests:** acme_certs with SSH-based distribution

**Scenario:** One certificate server + two client hosts receiving certificates.

**What it tests:**
- acme.sh installation and configuration
- Mock certificate generation
- SSH key setup for distribution
- Certificate distribution to multiple hosts
- Client certificate directory setup

**Location:** `ansible/integration-tests/cert-distribution/`

### When to Run Integration Tests

- **Before major infrastructure changes** - Validate multi-role interactions
- **When debugging production issues** - Reproduce cross-service problems
- **During code review** - Ensure changes don't break integrations
- **CI/CD** - Automatic testing via GitLab CI when relevant roles change

## Manual Testing Checklist

### Pre-Deployment

- Run `task ansible:lint` (ansible-lint + yamllint)
- Run `task deploy:check` (dry-run mode)
- Verify 1Password secrets are available: `op whoami`
- Review changes in git diff

### Post-Deployment

- Run `task deploy:verify` (verification playbook)
- Check service status: `systemctl status <service>`
- Verify logs: `journalctl -u <service> -n 50`
- Test functionality (curl, dig, ping, etc.)
- Run `task collect-state` (cluster snapshot)

### Role-Specific Manual Tests

**DNS (AdGuard + Unbound):**
```bash
dig @192.168.0.150 google.com          # DNS resolution
dig @127.0.0.1 -p 5335 google.com     # DoT via Unbound
curl -I http://192.168.0.150:3000      # AdGuard web UI
```

**K3s:**
```bash
kubectl get nodes                       # Cluster health
kubectl get pods -A                     # All pods
curl -k https://192.168.0.161:6443     # API VIP
```

**NAS:**
```bash
showmount -e pve-nas-01                # NFS exports
smbclient -L //pve-nas-01 -N          # Samba shares
df -h /tank/media/unified             # Mergerfs mount
```

## CI/CD Integration

### GitHub Actions Workflows (DISABLED — historical reference only)

> **Status**: GitLab is now the canonical CI/CD. `.github/workflows/*.yml`
> files are retained in the repo but disabled. See `.gitlab-ci.yml` for the
> active pipeline definitions and `docs/13-ci-cd.md` for the current jobs
> and rules. This section is kept for anyone investigating the legacy
> GitHub Actions setup.

The legacy workflows were:

| Workflow | File | Purpose |
|----------|------|---------|
| Lint | `.github/workflows/lint.yml` | Ansible syntax, ansible-lint, terraform fmt, yamllint |
| Molecule | `.github/workflows/molecule.yml` | Molecule tests for changed roles (matrix-based) |
| Integration Tests | `.github/workflows/integration-tests.yml` | Multi-role integration tests (triggered by role changes) |
| Terraform | `.github/workflows/terraform.yml` | Terraform format, init, validate |
| Kubernetes | `.github/workflows/kubernetes.yml` | kubeconform validation, Helm values linting |

### Molecule CI Workflow

The `molecule.yml` workflow uses smart change detection to only test roles that have changed:

```yaml
# Only tests roles with changes in their directory
filters: |
  base: 'ansible/roles/base/**'
  k3s: 'ansible/roles/k3s/**'
  # ...
```

Features:
- Matrix-based parallel testing (one job per role)
- Change detection via `dorny/paths-filter`
- Manual trigger via `workflow_dispatch` tests all roles
- K3s has both `default` and `agent` scenarios in matrix

### Integration Tests CI Workflow

The `integration-tests.yml` workflow runs multi-role integration tests when relevant roles change:

```yaml
# Triggers integration tests based on role changes
filters:
  dns-stack:
    - 'ansible/roles/unbound/**'
    - 'ansible/roles/adguard_home/**'
    - 'ansible/roles/adguard_sync/**'
  mail-stack:
    - 'ansible/roles/smtp_relay/**'
    - 'ansible/roles/postfix_null_client/**'
  # ...
```

Features:
- Smart change detection - only runs affected integration tests
- Matrix-based parallel execution
- Manual trigger via `workflow_dispatch` runs all integration tests
- Tests multi-container scenarios with Docker networks
- Validates cross-role configuration and service dependencies

### Kubernetes Validation Workflow

The `kubernetes.yml` workflow validates Kubernetes manifests and Helm values:

1. **kubeconform**: Validates YAML against Kubernetes API schemas
   - Includes CRD schemas from datreeio/CRDs-catalog
   - Ignores values.yaml and kustomization.yaml files

2. **Helm template**: Validates Helm values render correctly
   - Tests Traefik, MetalLB, cert-manager, Authentik values

3. **yamllint**: Basic YAML syntax validation

## Kubernetes Testing

### Manifest Validation

All Kubernetes manifests are validated with `kubeconform`:

```bash
# Local validation
kubeconform -summary -strict \
  -ignore-missing-schemas \
  -kubernetes-version 1.35.0 \
  kubernetes/apps/**/*.yaml
```

### Flux + Helm Values Testing

All Helm values are now inlined into `HelmRelease.spec.values` under
`kubernetes/apps/*/release.yaml` and `kubernetes/infrastructure/controllers/*/release.yaml`.
Validation is done by rendering the full Flux Kustomization tree with
`kustomize build` and then running `kubeconform` on the output (which
covers both HelmReleases and every other CR).

```bash
task flux:lint
# flux:lint iterates every Flux Kustomization under kubernetes/clusters/weisssrv/,
# loads cluster-versions ConfigMap values via envsubst (so ${var} typos are caught),
# then runs kubeconform against the rendered output. It also builds the cluster
# root kustomization to verify all references are valid.
```

The CI `flux-lint` job (`.gitlab-ci.yml`) mirrors `task flux:lint` and runs on
every MR against `kubernetes/**` or `all.yml` changes. `flux-versions-sync`
additionally verifies that `cluster-versions` ConfigMap is in sync with
`ansible/inventories/prod/group_vars/all.yml`.

## Production Verification

### Postflight Playbook

The `postflight.yml` playbook provides comprehensive production health checks:

```bash
# Run after any deployment
task deploy:verify
```

Verifies:
- SSH connectivity to all hosts
- Disk space on root partitions
- Service status (DNS, mail, NAS, k3s)
- ZFS pool health (ONLINE, no DEGRADED/FAULTED)
- NFS/Samba exports active
- SMART disk health
- Certificate distribution SSH access
- K3s persistent storage mounts

### Future: Automated Smoke Tests

A self-hosted runner would enable automated production verification:

```yaml
# .github/workflows/smoke-tests.yml (future)
on:
  schedule:
    - cron: '0 6 * * *'  # Daily at 6 AM

jobs:
  smoke-tests:
    runs-on: self-hosted
    steps:
      - name: DNS health check
        run: dig @192.168.0.150 google.com +short

      - name: K3s cluster health
        run: kubectl get nodes

      - name: Service endpoint checks
        run: curl -sf https://auth.esweiss.com/health/
```

## Testing Pyramid

The testing strategy follows a pyramid structure:

```
                    /\
                   /  \
                  / E2E \        <- Production smoke tests (postflight.yml)
                 /------\
                /        \
               / Integr.  \      <- Multi-role integration tests (5 scenarios)
              /------------\
             /              \
            /   Unit Tests   \   <- Individual role molecule tests (15 roles, 16 scenarios)
           /------------------\
          /                    \
         /    Static Analysis   \  <- ansible-lint, kubeconform, terraform validate
        /------------------------\
```

**Test Coverage:**
- **Static Analysis:** ansible-lint, yamllint, kubeconform, terraform validate
- **Unit Tests:** 15 roles with 16 Molecule scenarios (including idempotency)
- **Integration Tests:** 5 multi-role scenarios testing cross-service interactions
- **E2E Tests:** Production verification via postflight.yml playbook

## References

- [Molecule Documentation](https://molecule.readthedocs.io/)
- [molecule-docker Plugin](https://github.com/ansible-community/molecule-plugins)
- [Jeff Geerling - Testing Ansible Roles](https://www.jeffgeerling.com/blog/2018/testing-your-ansible-roles-molecule)
- [kubeconform](https://github.com/yannh/kubeconform)
- [GitHub Actions Workflow Syntax](https://docs.github.com/en/actions/reference/workflow-syntax-for-github-actions)
