# Ansible Role Testing with Molecule

This document describes the Molecule-based testing infrastructure for weisssrv Ansible roles.

## Overview

Molecule tests run each role inside a Docker container (Debian Trixie with systemd) to verify
that tasks execute correctly, configurations are deployed as expected, and services start properly.

**Tested roles (12 roles, 13 scenarios):**

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
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 python3 -m molecule test

# Just converge (apply role) without destroying
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 python3 -m molecule converge

# Run verification only (after converge)
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 python3 -m molecule verify

# Keep container running after test (for debugging)
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 python3 -m molecule test --destroy=never

# Shell into test container for debugging
docker exec -it <container-name> bash

# Destroy test container
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 python3 -m molecule destroy
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
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 python3 -m molecule destroy
```

## Important: ANSIBLE_ALLOW_BROKEN_CONDITIONALS

**This environment variable is required for all molecule commands.** The `test-all-roles.sh`
script and the Taskfile `ansible:test` task set it automatically.

The reason: `molecule-docker`'s internal `create.yml` playbook contains a broken conditional:

```yaml
# molecule-docker/playbooks/create.yml line 14
when: (lookup('env', 'HOME'))
```

Ansible 2.20+ requires boolean conditionals, but this evaluates to a string. The
`ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1` flag allows this legacy behavior. This is an upstream
issue with `molecule-docker`, not with our roles.

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
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 python3 -m molecule create    # Start container
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 python3 -m molecule converge  # Run role
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 python3 -m molecule verify    # Check results
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 python3 -m molecule destroy   # Cleanup
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
| home_assistant | Requires HAOS VM (SSH/SCP based management) |

## Integration Tests Strategy

Integration tests verify multi-role interactions (e.g., dns-stack with dns-01 + dns-02, mail-stack).

**Current Status: Deferred**

Unit tests for individual roles provide sufficient coverage. Integration tests would require:

1. Multi-container scenarios with Docker networks
2. Real service dependencies (e.g., two AdGuard instances for sync testing)
3. Longer test timeouts and more complex teardown

**Recommended approach for future integration tests:**

```yaml
# molecule.yml for dns-stack integration scenario
platforms:
  - name: dns-01
    groups: [dns, dns_primary]
    networks:
      - name: dns-stack
  - name: dns-02
    groups: [dns, dns_replica]
    networks:
      - name: dns-stack
```

This would allow testing:
- adguard_sync from dns-01 to dns-02
- DNS failover scenarios
- Split-horizon DNS validation

**When to implement integration tests:**
- When adding complex multi-host features
- When debugging production issues that unit tests miss
- When preparing for major infrastructure changes

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

## References

- [Molecule Documentation](https://molecule.readthedocs.io/)
- [molecule-docker Plugin](https://github.com/ansible-community/molecule-plugins)
- [Jeff Geerling - Testing Ansible Roles](https://www.jeffgeerling.com/blog/2018/testing-your-ansible-roles-molecule)
