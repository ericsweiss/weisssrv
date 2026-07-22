# Tailscale Role

Installs and configures Tailscale VPN on managed hosts. Handles apt repository setup, version pinning, authentication, and subnet route advertisement.

## What This Role Manages

### Installation
- Tailscale GPG key download to a staging path, fingerprint verification, then
  install into `/usr/share/keyrings/`
- Tailscale apt repository configuration
- Pinned version installation (from group_vars)
- Tailscaled daemon enablement and start

### Network Configuration
- IP forwarding enablement for subnet routers, persisted in
  `/etc/sysctl.d/99-tailscale-ip-forward.conf` plus a tailscaled
  `ExecStartPost` drop-in (Proxmox bridge init can reset the value after
  systemd-sysctl at boot)
- Automatic status checking
- Authentication with auth key from 1Password
- Route advertisement (optional)
- DNS and route acceptance (configured per host)

### Authentication
- Automatic `tailscale up` with auth key
- Idempotent (only authenticates if not already running)
- Preference reconciliation on every run via `tailscale set` (accept-routes,
  accept-dns, advertise-routes) for already-authenticated nodes
- Support for additional flags (--operator, --ssh, etc.) — applied only by the
  initial `tailscale up`
- Manual authentication fallback with helpful command output

## Configuration

### Default Variables

```yaml
# Enable Tailscale
tailscale_enabled: true

# Version pinning — group_vars/all.yml is the source of truth for the value
tailscale_version: "…"   # see group_vars/all.yml

# Route acceptance (CRITICAL: false prevents routing loops)
tailscale_accept_routes: false

# DNS acceptance (false - use homelab DNS infrastructure)
tailscale_accept_dns: false

# Subnet advertisement (for subnet routers only)
tailscale_advertise_routes: []  # e.g., ["192.168.0.0/24"]

# ACL tags to advertise (least-privilege tailnet policy). The tag must exist in
# tagOwners in terraform/tailscale/policy.hujson before a host can adopt it.
tailscale_advertise_tags: []  # e.g., ["tag:subnet-router"]

# Strict tag adoption (see below). Default false = best-effort.
tailscale_tags_require_adoption: false

# Additional flags
tailscale_additional_flags: []  # e.g., ["--operator=eric", "--ssh"]
```

`tailscale_advertise_tags` is a first-class var (like `tailscale_advertise_routes`)
adopted **only** via the reconcile `tailscale set --advertise-tags` task — it is
deliberately **not** passed to the initial `tailscale up` (a tag on `up` would
hard-fail a first-time join while the live ACL has no `tagOwners` entry, or
trigger a reauth `up` cannot complete non-interactively). It is applied
best-effort on running hosts: the first transition of a user-owned device to a
tag-owned identity needs an interactive reauthentication (a Tailscale platform
behavior), so that one-time migration is a supervised step — see
`terraform/tailscale/README.md`. Deploy order matters: apply the ACL (which
defines `tagOwners` and the tag-based route auto-approver) **before** running
this role so the tag can be adopted.

`tailscale_tags_require_adoption` controls how a failed tag reconcile is treated:

- **`false` (default)** — best-effort. A non-zero `tailscale set --advertise-tags`
  does not fail the play (the pre-cutover / automatic-pipeline state where "needs
  reauth" is expected, so the pipeline stays green). The reconcile's debug task
  still prints `rc` + `stderr`, so an unexpected error is visible, not swallowed.
- **`true`** — strict. Any non-zero rc fails the play. Set this **only** for the
  supervised adoption step, run *after* the ACL defines `tagOwners`
  (`-e tailscale_tags_require_adoption=true`), so a host that silently fails to
  adopt the tag is caught instead of leaving the lockdown half-applied.

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

All six Proxmox hosts advertise the homelab network (192.168.0.0/24) to
Tailscale (`tailscale_advertise_routes` in `group_vars/proxmox.yml`), so any
one of them can carry remote access — real failover, not a single-host SPOF:

```
Tailscale Network (100.64.0.0/10)
        │
        ├─ pve-nas-01 ─┐
        ├─ pve-opt-01  │
        ├─ pve-opt-02  ├─ subnet routers, each advertising 192.168.0.0/24
        ├─ pve-opt-03  │  (auto-approved via terraform/tailscale autoApprovers)
        ├─ pve-prec-01 │
        ├─ pve-laptop-01 ┘
        │
        └─ External devices
           └─ Can reach homelab via any subnet router
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
2. Download GPG key to a staging file (re-fetched every run)
3. Verify the primary key fingerprint, then install into /usr/share/keyrings
4. Add Tailscale apt repository
5. Install Tailscale (pinned version)
6. Enable and start tailscaled service
7. Enable IP forwarding (if advertising routes):
   ├─ sysctl.d drop-in (99-tailscale-ip-forward.conf)
   └─ tailscaled ExecStartPost drop-in (re-asserts after boot bridge init)
8. Check Tailscale status (JSON)
9. Authenticate with tailscale up (if needed):
   ├─ Use auth key from environment (1Password)
   ├─ Set --accept-routes flag
   ├─ Set --accept-dns flag
   ├─ Set --advertise-routes (if configured)
   └─ Add additional flags (if configured)
       (--advertise-tags is NOT passed here — see step 11)
10. Reconcile preferences on already-running nodes (tailscale set: routes/DNS)
11. Reconcile advertised ACL tags on already-running nodes via
    `tailscale set --advertise-tags` (best-effort by default; may need a
    supervised reauth on first tag adoption; strict when
    tailscale_tags_require_adoption=true)
12. Display authentication status
```

## Files

- `tasks/main.yml` - Main task orchestration
- `defaults/main.yml` - Default variables
- `handlers/main.yml` - tailscaled reload/restart handler

## Dependencies

None - runs independently.

## Security

- GPG key fingerprint-verified in a staging path before it is installed into
  the trusted keyring location (a tampered download never becomes trusted)
- Auth key stored in 1Password (never in git)
- Auth key not logged (`no_log: true`)
- Pinned version prevents unexpected updates
- IP forwarding only enabled when needed

## Idempotency

- GPG key is re-downloaded and verified every run (picks up upstream key
  rotation); the keyring file only reports changed when its content changes
- Repository addition is idempotent
- Package installation allows downgrades (version pinning)
- Authentication only runs if BackendState != "Running"; `tailscale set`
  reconciles preference drift on every run
- IP forwarding only enabled if advertising routes

## Operational Notes

### Version Updates

Update version in `group_vars/all.yml`:

```yaml
tailscale_version: "…"   # current pin lives in group_vars/all.yml
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

3. Route approval: `192.168.0.0/24` advertisements auto-approve via the
   `autoApprovers` block in the tailnet ACL managed in `terraform/tailscale`
   (see that module's README). Routes outside the auto-approved set still
   need manual approval at https://login.tailscale.com/admin/machines.

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

