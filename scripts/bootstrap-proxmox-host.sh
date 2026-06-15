#!/bin/bash
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

# Prompt for password (hidden input)
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

# Encode password in base64 to safely pass through SSH without shell injection
# This prevents passwords containing $, `, ', ", \, etc. from being interpreted
ERIC_PASSWORD_B64=$(printf '%s' "$ERIC_PASSWORD" | base64)

echo ""
echo "=== Step 1: Creating user 'eric' on $HOST_IP ==="
echo "You will be prompted for the root password..."
echo ""

# Create user and configure sudo
# The base64-encoded password is embedded directly in the script via unquoted heredoc.
# This expands $ERIC_PASSWORD_B64 locally before sending, keeping the secret out of
# process arguments on both machines. All remote variables must be escaped (\$).
# shellcheck disable=SC2087  # Unquoted heredoc is intentional for variable expansion
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@${HOST_IP}" bash << REMOTE_SCRIPT
set -euo pipefail

# Password embedded via heredoc expansion (not visible in process list)
ERIC_PASSWORD_B64="$ERIC_PASSWORD_B64"

echo "Checking for existing user 'eric'..."

# Install sudo if not present (required for Proxmox hosts)
if ! command -v sudo &>/dev/null; then
    echo "Installing sudo package..."

    # Track which repos we disable so we only re-enable those (not pre-existing .disabled files)
    DISABLED_BY_US=""

    # Cleanup function to restore disabled repos on exit (success or failure)
    # This ensures the host is never left with repos disabled if something fails
    restore_repos() {
        for disabled_file in \$DISABLED_BY_US; do
            if [ -f "\$disabled_file" ]; then
                original_name="\${disabled_file%.disabled}"
                mv "\$disabled_file" "\$original_name" 2>/dev/null || true
            fi
        done
    }

    # Set trap to restore repos on exit (covers both success and failure paths)
    trap restore_repos EXIT

    # Disable enterprise repos temporarily (they require subscription)
    # Handle both legacy .list format and modern .sources format (Proxmox 9+ / Debian Trixie)
    for repo_file in /etc/apt/sources.list.d/pve-enterprise.list \\
                     /etc/apt/sources.list.d/ceph.list \\
                     /etc/apt/sources.list.d/pve-no-subscription.list; do
        if [ -f "\$repo_file" ]; then
            mv "\$repo_file" "\$repo_file.disabled" 2>/dev/null || true
            DISABLED_BY_US="\$DISABLED_BY_US \$repo_file.disabled"
        fi
    done

    # Also check for any .sources files (new Debian format)
    for sources_file in /etc/apt/sources.list.d/*.sources; do
        if [ -f "\$sources_file" ] && grep -q "enterprise.proxmox.com" "\$sources_file" 2>/dev/null; then
            mv "\$sources_file" "\$sources_file.disabled" 2>/dev/null || true
            DISABLED_BY_US="\$DISABLED_BY_US \$sources_file.disabled"
        fi
    done

    # Update package lists - capture output to check for real errors
    # Temporarily disable errexit to capture exit code before it triggers script exit
    set +e
    APT_OUTPUT=\$(apt-get update 2>&1)
    APT_RC=\$?
    set -e
    if [ \$APT_RC -ne 0 ]; then
        # Check if failure is only from enterprise repos (expected without subscription)
        if echo "\$APT_OUTPUT" | grep -qE "enterprise.proxmox.com|ceph.com.*enterprise"; then
            echo "Note: Enterprise repos failed (subscription required) - continuing"
        else
            echo "ERROR: apt-get update failed with unexpected error:"
            echo "\$APT_OUTPUT"
            exit 1
        fi
    fi

    # Install sudo
    if apt-get install -y sudo; then
        echo "sudo installed successfully"
    else
        echo "ERROR: Failed to install sudo"
        exit 1
    fi

    # Note: repos are restored automatically by the EXIT trap (restore_repos function)
fi

# Create user eric if not exists
if ! id eric &>/dev/null; then
    useradd -m -s /bin/bash eric
    echo "Created user 'eric'"
else
    echo "User 'eric' already exists"
fi

# Set password for eric user
# Decode password from base64 to avoid shell injection vulnerabilities
ERIC_PASSWORD=\$(echo "\$ERIC_PASSWORD_B64" | base64 -d)
echo "eric:\$ERIC_PASSWORD" | chpasswd
unset ERIC_PASSWORD  # Clear from memory
echo "Password set for user 'eric'"

# Add to sudo group (create group if needed)
if ! grep -q "^sudo:" /etc/group; then
    groupadd sudo
fi
usermod -aG sudo eric

# Ensure sudo group has proper permissions in sudoers
if ! grep -q "^%sudo" /etc/sudoers; then
    echo "%sudo   ALL=(ALL:ALL) ALL" >> /etc/sudoers
fi

# Configure passwordless sudo for eric
echo 'eric ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/eric
chmod 440 /etc/sudoers.d/eric
echo "Configured passwordless sudo for eric"

# Create .ssh directory
mkdir -p /home/eric/.ssh
chmod 700 /home/eric/.ssh
chown eric:eric /home/eric/.ssh

echo ""
echo "User setup complete. SSH key deployment next..."
REMOTE_SCRIPT

echo ""
echo "=== Step 2: Deploying SSH public key ==="

# Deploy the SSH key
# The base64-encoded key is embedded directly in the script via unquoted heredoc.
# This expands $SSH_KEY_B64 locally before sending, keeping the secret out of
# process arguments on both machines. All remote variables must be escaped (\$).
SSH_KEY_B64=$(printf '%s' "$SSH_PUBLIC_KEY" | base64)
# shellcheck disable=SC2087  # Unquoted heredoc is intentional for variable expansion
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@${HOST_IP}" bash << DEPLOY_KEY
SSH_KEY_B64="$SSH_KEY_B64"
SSH_KEY=\$(echo "\$SSH_KEY_B64" | base64 -d)
echo "\$SSH_KEY" > /home/eric/.ssh/authorized_keys
chmod 600 /home/eric/.ssh/authorized_keys
chown eric:eric /home/eric/.ssh/authorized_keys
echo "SSH key deployed to /home/eric/.ssh/authorized_keys"
DEPLOY_KEY

echo ""
echo "=== Step 3: Verifying SSH access as eric ==="

# Wait a moment for SSH to recognize the key
sleep 2

# Test SSH as eric (should not prompt for password)
if ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10 "eric@${HOST_IP}" "echo 'SSH access as eric: SUCCESS'"; then
    echo ""
    echo "=== Step 4: Verifying sudo access ==="
    REMOTE_SUDO_WHOAMI=$(ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10 "eric@${HOST_IP}" "sudo whoami")
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
