#!/usr/bin/env bash
# Bootstrap a fresh Proxmox host for Ansible management
#
# This script creates user 'eric' with passwordless sudo and deploys
# the SSH public key from 1Password. Run from your workstation against
# new Proxmox hosts that only have root SSH access.
#
# Usage:
#   ./scripts/bootstrap-proxmox-host.sh <host-ip> '<ssh-public-key>'
#
# Or with 1Password CLI:
#   ./scripts/bootstrap-proxmox-host.sh 192.168.0.103 "$(op read 'op://Homelab/SSH Key/public key')"
#
# Prerequisites:
#   - Proxmox VE installed on target host
#   - Network connectivity to target host
#   - Root password for target host (prompted during execution)

set -euo pipefail

HOST_IP="${1:-}"
SSH_PUBLIC_KEY="${2:-}"

if [[ -z "$HOST_IP" ]] || [[ -z "$SSH_PUBLIC_KEY" ]]; then
    echo "Usage: $0 <host-ip> '<ssh-public-key>'"
    echo ""
    echo "Examples:"
    echo "  $0 192.168.0.103 'ssh-ed25519 AAAAC3NzaC1... eric@MacBookPro.esweiss.com'"
    echo "  $0 192.168.0.103 \"\$(op read 'op://Homelab/SSH Key/public key')\""
    echo ""
    echo "This script will:"
    echo "  1. Create user 'eric' with passwordless sudo"
    echo "  2. Set password for eric user"
    echo "  3. Deploy SSH public key for eric"
    echo "  4. Verify SSH access works"
    exit 1
fi

echo "============================================"
echo "Bootstrapping Proxmox host at $HOST_IP"
echo "============================================"
echo ""
echo "This script will:"
echo "  1. Create user 'eric' with passwordless sudo"
echo "  2. Set password for eric user"
echo "  3. Deploy SSH public key for eric"
echo "  4. Verify SSH access works"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

echo ""
echo "=== Password Setup ==="
echo "Enter password for user 'eric' (will be set on $HOST_IP)"
echo "This password will be used for local console access and sudo."
echo ""

read -r -s -p "Password: " ERIC_PASSWORD
echo
read -r -s -p "Confirm password: " ERIC_PASSWORD_CONFIRM
echo

if [[ "$ERIC_PASSWORD" != "$ERIC_PASSWORD_CONFIRM" ]]; then
    echo ""
    echo "ERROR: Passwords do not match!"
    exit 1
fi

if [[ -z "$ERIC_PASSWORD" ]]; then
    echo ""
    echo "ERROR: Password cannot be empty!"
    exit 1
fi

# base64 so a password containing $, `, quotes or backslashes cannot be
# interpreted as shell by the remote heredoc.
ERIC_PASSWORD_B64=$(printf '%s' "$ERIC_PASSWORD" | base64)

echo ""
echo "=== Step 1: Creating user 'eric' on $HOST_IP ==="
echo "You will be prompted for the root password..."
echo ""

# The base64-encoded password is embedded directly in the script via unquoted heredoc.
# This expands $ERIC_PASSWORD_B64 locally before sending, keeping the secret out of
# process arguments on both machines. All remote variables must be escaped (\$).
# shellcheck disable=SC2087  # Unquoted heredoc is intentional for variable expansion
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@${HOST_IP}" bash << REMOTE_SCRIPT
set -euo pipefail

ERIC_PASSWORD_B64="$ERIC_PASSWORD_B64"

echo "Checking for existing user 'eric'..."

if ! command -v sudo &>/dev/null; then
    echo "Installing sudo package..."

    # Track which repos we disable so we only re-enable those (not pre-existing
    # .disabled files). Use an array so paths are iterated element-by-element
    # rather than re-split on whitespace.
    DISABLED_BY_US=()

    restore_repos() {
        # DISABLED_BY_US is always declared above, so "\${arr[@]}" expands to
        # nothing when empty under set -u (bash >= 4.4; PVE hosts run bash 5.x).
        for disabled_file in "\${DISABLED_BY_US[@]}"; do
            if [ -f "\$disabled_file" ]; then
                original_name="\${disabled_file%.disabled}"
                mv "\$disabled_file" "\$original_name" 2>/dev/null || true
            fi
        done
    }

    trap restore_repos EXIT

    # Move a repo file to .disabled, but REFUSE to clobber a pre-existing
    # .disabled (a repo someone disabled before us) — overwriting it would
    # corrupt its content and make restore_repos later restore the wrong file.
    # Only record what we actually moved so restore_repos re-enables exactly ours.
    disable_repo_file() {
        local repo_file="\$1"
        local disabled_file="\$repo_file.disabled"
        if [ -e "\$disabled_file" ]; then
            echo "ERROR: refusing to overwrite existing \$disabled_file while disabling \$repo_file" >&2
            exit 1
        fi
        if mv "\$repo_file" "\$disabled_file" 2>/dev/null; then
            DISABLED_BY_US+=("\$disabled_file")
        fi
    }

    # Enterprise repos need a subscription, so apt-get update fails against
    # them; disable both the legacy .list and the .sources shapes for the
    # duration and let restore_repos put them back.
    for repo_file in /etc/apt/sources.list.d/pve-enterprise.list \\
                     /etc/apt/sources.list.d/ceph.list \\
                     /etc/apt/sources.list.d/pve-no-subscription.list; do
        if [ -f "\$repo_file" ]; then
            disable_repo_file "\$repo_file"
        fi
    done

    for sources_file in /etc/apt/sources.list.d/*.sources; do
        if [ -f "\$sources_file" ] && grep -q "enterprise.proxmox.com" "\$sources_file" 2>/dev/null; then
            disable_repo_file "\$sources_file"
        fi
    done

    # errexit off so the rc can be inspected instead of ending the script.
    set +e
    APT_OUTPUT=\$(apt-get update 2>&1)
    APT_RC=\$?
    set -e
    if [ \$APT_RC -ne 0 ]; then
        if echo "\$APT_OUTPUT" | grep -qE "enterprise.proxmox.com|ceph.com.*enterprise"; then
            echo "Note: Enterprise repos failed (subscription required) - continuing"
        else
            echo "ERROR: apt-get update failed with unexpected error:"
            echo "\$APT_OUTPUT"
            exit 1
        fi
    fi

    if apt-get install -y sudo; then
        echo "sudo installed successfully"
    else
        echo "ERROR: Failed to install sudo"
        exit 1
    fi

fi

if ! id eric &>/dev/null; then
    useradd -m -s /bin/bash eric
    echo "Created user 'eric'"
else
    echo "User 'eric' already exists"
fi

# base64 keeps the password out of the remote command line.
ERIC_PASSWORD=\$(echo "\$ERIC_PASSWORD_B64" | base64 -d)
echo "eric:\$ERIC_PASSWORD" | chpasswd
unset ERIC_PASSWORD  # Clear from memory
echo "Password set for user 'eric'"

if ! grep -q "^sudo:" /etc/group; then
    groupadd sudo
fi
usermod -aG sudo eric

if ! grep -q "^%sudo" /etc/sudoers; then
    echo "%sudo   ALL=(ALL:ALL) ALL" >> /etc/sudoers
fi

# visudo -cf BEFORE install: a syntax error anywhere in /etc/sudoers.d locks
# every sudo user out of the host, and this is the only admin path in.
SUDOERS_TMP=\$(mktemp)
echo 'eric ALL=(ALL) NOPASSWD: ALL' > "\$SUDOERS_TMP"
if ! visudo -cf "\$SUDOERS_TMP"; then
    rm -f "\$SUDOERS_TMP"
    echo "ERROR: refusing to install an invalid /etc/sudoers.d/eric" >&2
    exit 1
fi
install -m 440 -o root -g root "\$SUDOERS_TMP" /etc/sudoers.d/eric
rm -f "\$SUDOERS_TMP"
echo "Configured passwordless sudo for eric"

mkdir -p /home/eric/.ssh
chmod 700 /home/eric/.ssh
chown eric:eric /home/eric/.ssh

echo ""
echo "User setup complete. SSH key deployment next..."
REMOTE_SCRIPT

echo ""
echo "=== Step 2: Deploying SSH public key ==="

# The base64-encoded key is embedded directly in the script via unquoted heredoc.
# This expands $SSH_KEY_B64 locally before sending, keeping the secret out of
# process arguments on both machines. All remote variables must be escaped (\$).
SSH_KEY_B64=$(printf '%s' "$SSH_PUBLIC_KEY" | base64)
# shellcheck disable=SC2087  # Unquoted heredoc is intentional for variable expansion
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@${HOST_IP}" bash << DEPLOY_KEY
set -euo pipefail

SSH_KEY_B64="$SSH_KEY_B64"
SSH_KEY=\$(echo "\$SSH_KEY_B64" | base64 -d)
AUTH=/home/eric/.ssh/authorized_keys
# Append-if-absent, never truncate. A re-run against a host that has since
# gained a second admin key, a CI deploy key, or the dns-01 cert-distribution
# key must not destroy them — and the verification step below would still
# report SUCCESS, because the key it tests with is the survivor.
touch "\$AUTH"
if grep -qxF "\$SSH_KEY" "\$AUTH"; then
    echo "SSH key already present in \$AUTH (no change)"
else
    printf '%s\n' "\$SSH_KEY" >> "\$AUTH"
    echo "SSH key appended to \$AUTH"
fi
chmod 600 "\$AUTH"
chown eric:eric "\$AUTH"
echo "Authorized keys now present: \$(grep -c '^[^#[:space:]]' "\$AUTH")"
DEPLOY_KEY

echo ""
echo "=== Step 3: Verifying SSH access as eric ==="

sleep 2

if ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10 "eric@${HOST_IP}" "echo 'SSH access as eric: SUCCESS'"; then
    echo ""
    echo "=== Step 4: Verifying sudo access ==="
    # Capture stderr and tolerate a non-zero exit so a misconfigured remote sudo
    # (or a BatchMode no-tty prompt) doesn't abort under `set -e` before the
    # diagnostic below can run; the captured message lands in the error string.
    REMOTE_SUDO_WHOAMI=$(ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10 "eric@${HOST_IP}" "sudo whoami" 2>&1) || true
    if [[ "$REMOTE_SUDO_WHOAMI" == "root" ]]; then
        echo "Sudo access: SUCCESS (sudo whoami returned 'root')"
    else
        echo "ERROR: Sudo access failed (got '$REMOTE_SUDO_WHOAMI' instead of 'root')"
        exit 1
    fi

    echo ""
    echo "============================================"
    echo "Bootstrap COMPLETE for $HOST_IP"
    echo "============================================"
    echo ""
    echo "User 'eric' configured with:"
    echo "  - Password set (for local console access)"
    echo "  - Passwordless sudo enabled"
    echo "  - SSH key authentication enabled"
    echo ""
    echo "Next steps:"
    echo "  1. Create local-ssd ZFS pool (see docs/26-multi-node-implementation.md)"
    echo "  2. Update ansible/inventories/prod/hosts.yml"
    echo "  3. Run: task infra:base -- --limit <hostname>"
    echo ""
else
    echo ""
    echo "============================================"
    echo "ERROR: SSH access as eric FAILED!"
    echo "============================================"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check that your SSH private key is loaded: ssh-add -l"
    echo "  2. Verify the public key matches: ssh-keygen -lf ~/.ssh/id_ed25519.pub"
    echo "  3. Check permissions on remote host:"
    echo "     ssh root@$HOST_IP 'ls -la /home/eric/.ssh/'"
    exit 1
fi
