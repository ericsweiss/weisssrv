#!/usr/bin/env bash
# Bootstrap a fresh Proxmox host for Ansible management: create the admin user
# with passwordless sudo and deploy an SSH public key. Run from a workstation
# against a host that only has root SSH access; no arguments prints usage.

set -euo pipefail

HOST_IP="${1:-}"
SSH_PUBLIC_KEY="${2:-}"
DEFAULT_ADMIN_USER='eric'
ADMIN_USER="${3:-$DEFAULT_ADMIN_USER}"

if [[ -z "$HOST_IP" ]] || [[ -z "$SSH_PUBLIC_KEY" ]]; then
    cat <<USAGE
Usage: $0 <host-ip> '<ssh-public-key>' [user]

Examples:
  $0 10.0.10.103 "\$(cat ~/.ssh/id_ed25519.pub)"
  $0 10.0.10.103 "\$(op read 'op://Homelab/SSH Key/public key')"

The user defaults to $DEFAULT_ADMIN_USER, the ansible_user this inventory uses.
This script will:
  1. Install sudo if the image lacks it
  2. Create the user with passwordless sudo
  3. Set a console password for the user
  4. Append the SSH public key to its authorized_keys
  5. Verify SSH and sudo access
USAGE
    exit 1
fi

case "$ADMIN_USER" in
    root|*[!a-z0-9_-]*|"")
        echo "ERROR: '$ADMIN_USER' is not a usable admin username" >&2
        exit 1
        ;;
esac

echo "Bootstrapping Proxmox host $HOST_IP for user '$ADMIN_USER'"
read -r -p "Continue? (y/N) " -n 1 REPLY
echo
case "$REPLY" in
    y|Y) ;;
    *) echo "Aborted."; exit 1 ;;
esac

echo ""
echo "=== Console password for '$ADMIN_USER' ==="
read -r -s -p "Password: " ADMIN_PASSWORD
echo
read -r -s -p "Confirm password: " ADMIN_PASSWORD_CONFIRM
echo

if [[ "$ADMIN_PASSWORD" != "$ADMIN_PASSWORD_CONFIRM" ]]; then
    echo "ERROR: passwords do not match" >&2
    exit 1
fi
if [[ -z "$ADMIN_PASSWORD" ]]; then
    echo "ERROR: password cannot be empty" >&2
    exit 1
fi

# base64 so a password containing $, `, quotes or backslashes cannot be
# interpreted as shell by the remote heredoc.
ADMIN_PASSWORD_B64=$(printf '%s' "$ADMIN_PASSWORD" | base64)

echo ""
echo "=== Step 1: creating '$ADMIN_USER' on $HOST_IP (root password prompt follows) ==="
echo ""

# CRITICAL: the heredoc is unquoted so $ADMIN_PASSWORD_B64 expands locally, keeping the
# secret out of process arguments on both machines. Every remote variable must therefore
# be escaped (\$) or it is expanded here instead of on the target.
# shellcheck disable=SC2087  # Unquoted heredoc is intentional for variable expansion
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@${HOST_IP}" bash << REMOTE_SCRIPT
set -euo pipefail

ADMIN_USER="$ADMIN_USER"
ADMIN_PASSWORD_B64="$ADMIN_PASSWORD_B64"

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

    # Move a repo file to .disabled, refusing to clobber a pre-existing .disabled so
    # restore_repos re-enables exactly the files this script moved.
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

if ! id "\$ADMIN_USER" &>/dev/null; then
    useradd -m -s /bin/bash "\$ADMIN_USER"
    echo "Created user '\$ADMIN_USER'"
else
    echo "User '\$ADMIN_USER' already exists"
fi

# base64 keeps the password out of the remote command line.
ADMIN_PASSWORD=\$(echo "\$ADMIN_PASSWORD_B64" | base64 -d)
echo "\$ADMIN_USER:\$ADMIN_PASSWORD" | chpasswd
unset ADMIN_PASSWORD
echo "Password set for user '\$ADMIN_USER'"

if ! grep -q "^sudo:" /etc/group; then
    groupadd sudo
fi
usermod -aG sudo "\$ADMIN_USER"

# visudo -cf BEFORE install: a syntax error anywhere in sudoers locks every sudo
# user out of the host, and this is the only admin path in. Both rules go to
# /etc/sudoers.d so neither is an unvalidated append to /etc/sudoers.
install_sudoers() {
    local rule=\$1 dest=\$2 tmp
    tmp=\$(mktemp)
    printf '%s\\n' "\$rule" > "\$tmp"
    if ! visudo -cf "\$tmp"; then
        rm -f "\$tmp"
        echo "ERROR: refusing to install an invalid \$dest" >&2
        exit 1
    fi
    install -m 440 -o root -g root "\$tmp" "\$dest"
    rm -f "\$tmp"
}

install_sudoers '%sudo   ALL=(ALL:ALL) ALL' /etc/sudoers.d/00-sudo-group
install_sudoers "\$ADMIN_USER ALL=(ALL) NOPASSWD: ALL" "/etc/sudoers.d/\$ADMIN_USER"
echo "Configured passwordless sudo for \$ADMIN_USER"

mkdir -p "/home/\$ADMIN_USER/.ssh"
chmod 700 "/home/\$ADMIN_USER/.ssh"
chown "\$ADMIN_USER:\$ADMIN_USER" "/home/\$ADMIN_USER/.ssh"

echo ""
echo "User setup complete. SSH key deployment next..."
REMOTE_SCRIPT

echo ""
echo "=== Step 2: Deploying SSH public key ==="

# Unquoted heredoc: $SSH_KEY_B64 expands locally; remote vars are escaped (\$).
SSH_KEY_B64=$(printf '%s' "$SSH_PUBLIC_KEY" | base64)
# shellcheck disable=SC2087  # Unquoted heredoc is intentional for variable expansion
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@${HOST_IP}" bash << DEPLOY_KEY
set -euo pipefail

ADMIN_USER="$ADMIN_USER"
SSH_KEY_B64="$SSH_KEY_B64"
SSH_KEY=\$(echo "\$SSH_KEY_B64" | base64 -d)
AUTH="/home/\$ADMIN_USER/.ssh/authorized_keys"
# Append if absent; never truncate — the host may carry other admin keys.
touch "\$AUTH"
if grep -qxF "\$SSH_KEY" "\$AUTH"; then
    echo "SSH key already present in \$AUTH (no change)"
else
    printf '%s\n' "\$SSH_KEY" >> "\$AUTH"
    echo "SSH key appended to \$AUTH"
fi
chmod 600 "\$AUTH"
chown "\$ADMIN_USER:\$ADMIN_USER" "\$AUTH"
echo "Authorized keys now present: \$(grep -c '^[^#[:space:]]' "\$AUTH")"
DEPLOY_KEY

echo ""
echo "=== Step 3: Verifying SSH access as $ADMIN_USER ==="

sleep 2

if ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10 "${ADMIN_USER}@${HOST_IP}" "echo 'SSH access as ${ADMIN_USER}: SUCCESS'"; then
    echo ""
    echo "=== Step 4: Verifying sudo access ==="
    # Capture stderr and tolerate a non-zero exit so a misconfigured remote sudo
    # (or a BatchMode no-tty prompt) doesn't abort under `set -e` before the
    # diagnostic below can run; the captured message lands in the error string.
    REMOTE_SUDO_WHOAMI=$(ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10 "${ADMIN_USER}@${HOST_IP}" "sudo whoami" 2>&1) || true
    if [[ "$REMOTE_SUDO_WHOAMI" == "root" ]]; then
        echo "Sudo access: SUCCESS (sudo whoami returned 'root')"
    else
        echo "ERROR: Sudo access failed (got '$REMOTE_SUDO_WHOAMI' instead of 'root')"
        exit 1
    fi

    cat <<DONE

Bootstrap COMPLETE for $HOST_IP. User '$ADMIN_USER' now has a console
password, passwordless sudo and SSH key authentication.

Next steps:
  1. Create the local-ssd ZFS pool (docs/26-multi-node-implementation.md)
  2. Add the host to ansible/inventories/prod/hosts.yml
  3. Run: task infra:base -- --limit <hostname>
DONE
else
    cat >&2 <<FAILED

ERROR: SSH access as $ADMIN_USER FAILED.

Troubleshooting:
  1. Check that your SSH private key is loaded: ssh-add -l
  2. Verify the public key matches: ssh-keygen -lf ~/.ssh/id_ed25519.pub
  3. Check permissions on the remote host:
     ssh root@$HOST_IP 'ls -la /home/$ADMIN_USER/.ssh/'
FAILED
    exit 1
fi
