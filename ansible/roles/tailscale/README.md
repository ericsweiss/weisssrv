# Tailscale Role

Installs and configures Tailscale VPN on managed hosts. Handles apt repository setup, version pinning, authentication, and subnet route advertisement.

## What This Role Manages

### Installation
- Tailscale GPG key download and verification
- Tailscale apt repository configuration
- Pinned version installation (from group_vars)
- Tailscaled daemon enablement and start

### Network Configuration
- IP forwarding enablement (for subnet routers only)
- Automatic status checking
- Authentication with auth key from 1Password
- Route advertisement (optional)
- DNS and route acceptance (configured per host)

### Authentication
- Automatic `tailscale up` with auth key
- Idempotent (only authenticates if not already running)
- Support for additional flags (--operator, --ssh, etc.)
- Manual authentication fallback with helpful command output

## Configuration

### Default Variables

```yaml
# Enable Tailscale
tailscale_enabled: true

# Version pinning (from group_vars/all.yml)
tailscale_version: "1.94.1"

# Route acceptance (CRITICAL: false prevents routing loops)
tailscale_accept_routes: false

# DNS acceptance (false - use homelab DNS infrastructure)
tailscale_accept_dns: false

# Subnet advertisement (for subnet routers only)
tailscale_advertise_routes: []  # e.g., ["192.168.0.0/24"]

# Additional flags
tailscale_additional_flags: []  # e.g., ["--operator=eric", "--ssh"]
```

### 1Password Secrets

```yaml
secrets:
  tailscale_auth_key: "op://Homelab/Tailscale Auth Key/credential"
```

Auth key is resolved at runtime via 1Password CLI and passed as environment variable.

### Host-Specific Configuration

**Proxmox Subnet Routers** (advertise homelab network):

```yaml
# In host_vars/pve-nas-01.yml
tailscale_advertise_routes:
  - "192.168.0.0/24"
tailscale_additional_flags:
  - "--operator=eric"
  - "--ssh"
```

**Regular Hosts** (default behavior):

```yaml
# No special configuration needed - uses defaults
tailscale_accept_routes: false
tailscale_accept_dns: false
```

## Deployment

```bash
# Deploy to Proxmox hosts
ansible-playbook ansible/playbooks/site.yml --tags tailscale

# Deploy to specific host
ansible-playbook ansible/playbooks/site.yml --limit pve-nas-01 --tags tailscale

# Manual authentication if needed
ssh pve-nas-01
sudo tailscale up --accept-routes=false --accept-dns=false
```

## Architecture

### Subnet Router Model

Proxmox hosts advertise the homelab network (192.168.0.0/24) to Tailscale:

```
Tailscale Network (100.64.0.0/10)
        │
        ├─ pve-nas-01 (subnet router)
        │  └─ Advertises: 192.168.0.0/24
        │
        ├─ pve-opt-03 (subnet router)
        │  └─ Advertises: 192.168.0.0/24
        │
        └─ External devices
           └─ Can reach homelab via subnet routers
```

### Critical Configuration

**IMPORTANT**: `tailscale_accept_routes: false` on subnet routers prevents routing loops:

```
Without this setting:
  Homelab → Tailscale → Homelab (LOOP!)

With this setting:
  Homelab traffic stays local
  Tailscale traffic uses VPN
```

## Task Flow

```
1. Create /usr/share/keyrings directory
2. Check if Tailscale GPG key exists
3. Download GPG key (if not present)
4. Add Tailscale apt repository
5. Install Tailscale (pinned version)
6. Enable and start tailscaled service
7. Enable IP forwarding (if advertising routes)
8. Check Tailscale status (JSON)
9. Authenticate with tailscale up (if needed):
   ├─ Use auth key from environment (1Password)
   ├─ Set --accept-routes flag
   ├─ Set --accept-dns flag
   ├─ Set --advertise-routes (if configured)
   └─ Add additional flags (if configured)
10. Display authentication status
```

## Files

- `tasks/main.yml` - Main task orchestration
- `defaults/main.yml` - Default variables

## Dependencies

None - runs independently.

## Security

- GPG key verified before repository use
- Auth key stored in 1Password (never in git)
- Auth key not logged (`no_log: true`)
- Pinned version prevents unexpected updates
- IP forwarding only enabled when needed

## Idempotency

- GPG key download checks for existence first
- Repository addition is idempotent
- Package installation allows downgrades (version pinning)
- Authentication only runs if BackendState != "Running"
- IP forwarding only enabled if advertising routes

## Operational Notes

### Version Updates

Update version in `group_vars/all.yml`:

```yaml
tailscale_version: "1.94.1"
```

Then run:

```bash
task maintenance:update-applications
```

This will:
1. Check current version
2. Upgrade if different from target
3. Restart tailscaled service
4. Verify new version

### Manual Authentication

If automatic authentication fails:

```bash
# The role will output the exact command to run
sudo tailscale up \
  --accept-routes=false \
  --accept-dns=false \
  --advertise-routes=192.168.0.0/24 \
  --operator=eric \
  --ssh
```

### Subnet Router Setup

To make a Proxmox host a subnet router:

1. Configure in inventory:
   ```yaml
   tailscale_advertise_routes:
     - "192.168.0.0/24"
   ```

2. Run the role (IP forwarding will be enabled automatically)

3. Approve route in Tailscale admin console:
   - Go to https://login.tailscale.com/admin/machines
   - Find the machine
   - Click "Review route settings"
   - Approve the advertised routes

### Troubleshooting

**Check Tailscale status:**
```bash
tailscale status
tailscale status --json
```

**Check IP forwarding:**
```bash
sysctl net.ipv4.ip_forward
```

**View Tailscale logs:**
```bash
journalctl -u tailscaled -f
```

**Re-authenticate:**
```bash
sudo tailscale down
sudo tailscale up [flags]
```

