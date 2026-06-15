# Home Assistant Deployment Guide

This guide covers deploying Home Assistant OS (HAOS) as a VM on Proxmox with Traefik ingress and Authentik SSO integration.

## Overview

Home Assistant runs as a dedicated VM using Home Assistant OS (HAOS), which is a purpose-built Linux distribution optimized for Home Assistant. Unlike other VMs in this infrastructure that use Debian cloud images with cloud-init, HAOS uses its own disk image and onboarding process.

### Architecture

```
Internet
    |
    v
Cloudflare (home.ericsweiss.com)
    |
    v
Router Port Forward (443 -> 192.168.0.100)
    |
    v
Traefik Public VIP (192.168.0.100)
    |
    +-- IngressRoute (home.ericsweiss.com) --+
                                              |
Internal LAN                                  |
    |                                         |
    v                                         v
AdGuard Home (home.esweiss.com -> .101)   Home Assistant VM
    |                                     192.168.0.154:8123
    v                                         ^
Traefik Internal VIP (192.168.0.101)          |
    |                                         |
    +-- IngressRoute (home.esweiss.com) ------+
```

### VM Specifications

| Resource | Value |
|----------|-------|
| **VM ID** | 154 |
| **Hostname** | home |
| **IP Address** | 192.168.0.154 |
| **Proxmox Host** | pve-prec-01 |
| **Storage** | local-lvm |
| **CPU Cores** | 4 |
| **RAM** | 8 GB |
| **Disk** | 64 GB |
| **OS** | Home Assistant OS |

## Prerequisites

### 1. Verify Infrastructure

```bash
# Verify k3s cluster is running
export KUBECONFIG=~/.kube/config-k3s
kubectl get nodes

# Verify Traefik is serving
curl -k https://192.168.0.100  # Should get Traefik 404
curl -k https://192.168.0.101  # Should get Traefik 404

# Verify DNS is configured
dig home.esweiss.com @192.168.0.150
# Should return 192.168.0.101 (Traefik internal VIP)
```

### 2. Download HAOS Image

Download the latest Home Assistant OS image for Proxmox:

```bash
# SSH to Proxmox host
ssh pve-nas-01

# Download HAOS image (check https://www.home-assistant.io/installation/alternative for latest version)
cd /var/lib/vz/template/iso
wget https://github.com/home-assistant/operating-system/releases/download/17.0/haos_ova-17.0.qcow2.xz

# Extract the image
xz -d haos_ova-17.0.qcow2.xz

# Verify
ls -la haos_ova-17.0.qcow2
```

**Note**: Check [Home Assistant releases](https://github.com/home-assistant/operating-system/releases) for the latest version. The version number (17.0) will change.

## Phase 1: VM Provisioning (Manual)

HAOS cannot use the standard `proxmox_vm` Ansible role because:
1. It uses a pre-built qcow2 image, not a cloud image
2. It has its own configuration system (no cloud-init)
3. Network is configured via HAOS onboarding, not cloud-init

### Step 1: Create the VM

```bash
# SSH to pve-prec-01
ssh pve-prec-01

# Create VM with proper settings
qm create 154 \
  --name home \
  --memory 8192 \
  --cores 4 \
  --net0 virtio,bridge=vmbr0 \
  --ostype l26 \
  --agent enabled=1 \
  --bios ovmf \
  --machine q35 \
  --efidisk0 local-lvm:1,format=raw,efitype=4m,pre-enrolled-keys=1 \
  --scsihw virtio-scsi-pci

# Import the HAOS disk
qm importdisk 154 /var/lib/vz/template/iso/haos_ova-17.0.qcow2 local-lvm

# Attach the disk
qm set 154 --scsi0 local-lvm:vm-154-disk-1

# Resize disk to 64GB
qm resize 154 scsi0 64G

# Set boot order
qm set 154 --boot order=scsi0

# Configure autostart (order=50 aligns with boot strategy: DNS 10 → SMTP 20 → k3s srv 30 → k3s agt 40 → apps 50)
qm set 154 --onboot 1 --startup order=50,up=10

# Add to resource pool (optional)
pvesh set /pools/apps-public -vms 154
```

### Step 2: Start the VM

```bash
# Start the VM
qm start 154

# Watch the console (Proxmox web UI or noVNC)
# HAOS will boot and display its IP address after DHCP
```

### Step 3: Configure Static IP

HAOS will initially get a DHCP address. Configure static IP via the console:

1. Open Proxmox web UI
2. Navigate to pve-prec-01 > 154 (home) > Console
3. Wait for HAOS to boot (shows `ha>` prompt)
4. Configure network:

```bash
# At the ha> prompt:
network info
# NOTE: The interface name varies by VM hardware configuration.
# Use the interface name shown by 'network info' above (e.g., enp0s18, enp6s18).
network update <interface> --ipv4-method static --ipv4-address 192.168.0.154/24 --ipv4-gateway 192.168.0.1 --ipv4-nameserver 192.168.0.150
```

5. Reboot to apply:

```bash
host reboot
```

### Step 4: Verify Connectivity

```bash
# From your laptop
ping 192.168.0.154

# Verify HTTP port
curl -s http://192.168.0.154:8123 | head -5
# Should show HTML content
```

## Phase 2: Deploy Kubernetes Resources

The IngressRoute, ClusterIP Service, and manual EndpointSlice (pointing at
the HAOS VM's IP) for Home Assistant live in `kubernetes/apps/vm-ingress/`
(the consolidated "non-k8s services" bundle alongside Plex, AdGuard, GitLab
VM, router). Resources land in the `default` namespace
(services) and `traefik` namespace (middleware); `vm-ingress` is a folder
name, not a Kubernetes namespace. Flux reconciles them automatically once
the files are committed.

To deploy or update Home Assistant routing, edit
`kubernetes/apps/vm-ingress/home-assistant.yaml` (or `services-default.yaml`
/ `middleware.yaml` as appropriate), commit, and push:

```bash
vim kubernetes/apps/vm-ingress/home-assistant.yaml
git add kubernetes/apps/vm-ingress/home-assistant.yaml
git commit -m "Update Home Assistant ingress"
git push

# Flux reconciles within ~1 minute; force immediately:
task flux:reconcile

# Verify (resources are in the `default` namespace, NOT `vm-ingress`)
kubectl get ingressroute -A | grep home
kubectl get service home-assistant-backend -n default
kubectl get endpointslice -n default | grep home-assistant-backend
```

### Verify Traefik Routing

```bash
# Test internal access
curl -k https://home.esweiss.com
# Should redirect to Home Assistant onboarding

# Test external access (if port forwarding is configured)
curl -k https://home.ericsweiss.com
```

## Phase 3: Home Assistant Initial Setup

### Step 1: Access Onboarding

1. Open browser to `https://home.esweiss.com`
2. Complete the onboarding wizard:
   - Create your user account
   - Set location (for weather, sunrise/sunset)
   - Configure units (imperial/metric)
   - Skip integrations for now

### Step 2: Restore Backup (If Applicable)

If restoring from a backup:

1. On the onboarding screen, click **"Restore from backup"** instead of creating a new account
2. Upload your backup file (`.tar` format)
3. Wait for restore to complete
4. Log in with your restored credentials

### Step 3: Configure HTTP Settings

After initial setup, configure Home Assistant to work properly behind Traefik:

1. Go to Settings > System > Network
2. Under "Home Assistant URL", set:
   - **Internal URL**: `https://home.esweiss.com`
   - **External URL**: `https://home.ericsweiss.com`

3. Edit `/config/configuration.yaml` via File Editor add-on or SSH:

```yaml
# Add to configuration.yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 192.168.0.0/24      # Local LAN (includes Traefik)
    - 10.42.0.0/16        # k3s pod network
    - 10.43.0.0/16        # k3s service network
```

4. Restart Home Assistant (Settings > System > Restart)

## Phase 4: Download/Media Access (Optional)

Home Assistant can directly access download clients and media managers using high-priority bypass routes that skip Authentik SSO.

### Overview

Home Assistant bypass routes allow direct API access to:
- `tv.esweiss.com` - Sonarr
- `movies.esweiss.com` - Radarr
- `music.esweiss.com` - Lidarr
- `nzbget.esweiss.com` - NZBGet
- `qbittorrent.esweiss.com` - qBittorrent

Apps that do **not** need HA bypass routes (no Home Assistant integration):
- `prowlarr.esweiss.com` - Prowlarr
- `pulsarr.esweiss.com` - Pulsarr

**How it works:**
- High-priority IngressRoutes match both hostname AND Home Assistant's IP (192.168.0.154)
- These routes bypass Authentik SSO but keep other security middlewares (HSTS, IP whitelist)
- Regular routes with SSO remain active for browser access
- Uses Traefik's routing priority to prefer HA bypass over SSO routes

### Step 1: Deploy HA Bypass Routes

Bypass routes are part of `kubernetes/apps/download-clients/ingress-routes-ha-bypass.yaml`
and Flux-managed alongside the rest of the downloads stack. To update them, edit that
file, commit, and push.

```bash
# Verify (should show 5 bypass routes: sonarr, radarr, lidarr, nzbget, qbittorrent)
kubectl get ingressroute -n downloads | grep ha-bypass
```

### Step 2: Test Access

**From Home Assistant (should work without authentication):**
```bash
ssh root@192.168.0.154 -p 22222
curl -sk https://tv.esweiss.com/api/v3/system/status
# Should return JSON with Sonarr system info
```

**From your laptop (should require Authentik login):**
```bash
curl -sk https://tv.esweiss.com
# Will be redirected to Authentik for authentication
```

### Step 3: Configure Home Assistant Integrations

In Home Assistant, use the standard URLs for integrations:
- **Sonarr**: `https://tv.esweiss.com` + API key
- **Radarr**: `https://movies.esweiss.com` + API key
- **Lidarr**: `https://music.esweiss.com` + API key
- **NZBGet**: `https://nzbget.esweiss.com` + credentials
- **qBittorrent**: `https://qbittorrent.esweiss.com` + credentials

**Important**: Disable SSL verification in Home Assistant integrations if using Let's Encrypt certificates (some integrations have issues with cert validation).

## Phase 5: Operational Configuration

### Configuration Management with Ansible & 1Password

Home Assistant configuration is managed via Ansible with 1Password secret injection for consistency with other infrastructure.

**Step 1: Deploy Configuration**

Home Assistant has two independent surfaces that are managed separately:

1. **HA configuration** (`configuration.yaml`, `secrets.yaml`, etc.) — managed by the
   `home_assistant` Ansible role, deployed via SCP to the HAOS VM.
2. **Traefik ingress** (`home-assistant.yaml`, middlewares, `ExternalName` Service) —
   managed by Flux under `kubernetes/apps/vm-ingress/`.

**Deploy/update HA configuration (Ansible):**

```bash
# Deploy configuration (regenerates configuration.yaml + secrets.yaml from templates)
task home-assistant:deploy-config

# Restart to apply
task home-assistant:restart-after-config
```

**Deploy/update ingress (Flux):**

Edit `kubernetes/apps/vm-ingress/home-assistant.yaml`, commit, push. Flux reconciles.
`task flux:reconcile` triggers immediate sync.

This will:
1. Read secrets from 1Password via environment variables (resolved by `op run`)
2. Generate `configuration.yaml` and `secrets.yaml` from Jinja2 templates
3. Deploy via SCP to Home Assistant VM (192.168.0.154:22222)
4. Validate configuration using `ha core check`
5. Clean up temporary files

**How secrets work:**
- Task defines environment variables with 1Password references
- `op run` resolves them at runtime
- Ansible templates use `lookup('ansible.builtin.env', 'VAR_NAME')` to access values

**Step 2: Verify SMTP Notifications**

After deployment, test notifications via Developer Tools > Services:

```yaml
service: notify.smtp_notify
data:
  message: "Home Assistant SMTP test from codified config"
  title: "Test Notification"
```

**Configuration Files:**

Templates are version-controlled in the Ansible role:
- `ansible/roles/home_assistant/templates/configuration.yaml.j2` - Main configuration
- `ansible/roles/home_assistant/templates/secrets.yaml.j2` - Secrets template
- `ansible/roles/home_assistant/defaults/main.yml` - Default variables
- `ansible/playbooks/home-assistant.yml` - Deployment playbook

**Making Changes:**

1. Edit templates in `ansible/roles/home_assistant/templates/`
2. Update variables in `defaults/main.yml` if needed
3. Commit to Git
4. Deploy configuration: `task home-assistant:deploy-config`. Ingress lives in Flux; edit `kubernetes/apps/vm-ingress/home-assistant.yaml` + `services-default.yaml`, commit, push.
5. Restart: `task home-assistant:restart-after-config`

**Direct Playbook Execution:**

If you prefer to run Ansible directly:

```bash
op run --env-file=<(echo "") -- ansible-playbook ansible/playbooks/home-assistant.yml
```

**Manual Configuration Alternative:**

If you prefer manual setup, secrets are available via 1Password CLI:

```bash
op read "op://Homelab/SMTP Relay Auth/username"
op read "op://Homelab/SMTP Relay Auth/password"
op read "op://Homelab/Email Config/root_alias"
```

### NFS Media Mount

Home Assistant can access the unified media library (mergerfs) via NFS for browsing media files, album art, etc.

The NFS export is already configured on pve-nas-01 for 192.168.0.154 as read-only:
```yaml
# In ansible/inventories/prod/host_vars/pve-nas-01.yml (nfs_exports -> /export/media)
- spec: "192.168.0.154/32"
  options: "ro,sync,no_subtree_check,root_squash,fsid=20"
```

**Mount NFS in Home Assistant:**

```bash
# SSH to Home Assistant
ssh root@192.168.0.154 -p 22222

# Create mount point
mkdir -p /mnt/media

# Add to fstab for persistence (NFSv4 path relative to fsid=0 root export)
echo "pve-nas-01.esweiss.com:/media /mnt/media nfs ro,defaults,timeo=900,retrans=5,_netdev 0 0" >> /etc/fstab

# Mount
mount -a

# Verify
df -h | grep media
ls /mnt/media/
# Should show: downloads/ library/
```

**Note**: The mount is read-only (`ro`) since Home Assistant only needs to browse media, not write to it. The NFSv4 path is `/media` (relative to the fsid=0 root at `/export`), not `/export/media`.

**NFS transport is plaintext for HAOS — by necessity, not oversight.** The
rest of the cluster mounts `/export/media` with `xprtsec=tls` (NFSv4 over
kernel-TLS, by hostname `pve-nas-01.esweiss.com` so the `*.esweiss.com` cert
verifies; see docs/07 Transport Security), but HAOS cannot:

- The Home Assistant Supervisor hardcodes its NFS mount options
  (`supervisor/mounts/mount.py`, `NFSMount.options` returns
  `softerr,timeo=100,retrans=2` plus `ro`/`port=`) and exposes no free-form
  mount-options field — there is no way to add `xprtsec=tls`.
- HAOS ships no `tlshd` (the ktls-utils handshake daemon) and the locked
  appliance image can't have it installed, so even the hand-edited `/etc/fstab`
  mount above couldn't complete a TLS handshake. HAOS 17.x's kernel (6.12)
  *does* meet the kTLS bar, but the userspace daemon is the blocker.

The `/export/media` k3s client lines are **permissive** (`xprtsec=none:tls`):
they advertise TLS but still accept plaintext, and the k3s pods drive the
encrypted mount via their own `xprtsec=tls`. The `.154` line omits `xprtsec`
entirely, so HAOS's plaintext mount is accepted on the same export. Do **not**
add `xprtsec` to the `.154` line in `host_vars/pve-nas-01.yml` — HAOS has no
`tlshd` and would be locked out of its media mount. Note that even with
permissive exports, HAOS must keep mounting by hostname
(`pve-nas-01.esweiss.com`, as the fstab line above does) — it works for HAOS
because the mount is plaintext, but the hostname is also what a TLS client
needs (the `*.esweiss.com` cert has no IP SAN). The media is non-sensitive and
the read-only mount sits behind the LAN-trust boundary, so plaintext is an
accepted exception (tracked in docs/16). SMB3 (which is encrypted) was
considered and rejected to avoid adding a second protocol + credential path
for one browse mount.

### Backup Configuration

**Step 1: Create NFS Mount for Backups** (Optional but Recommended)

Home Assistant backups should be stored on NFS for durability.

1. **On pve-nas-01**, ensure backup directory exists:

```bash
ssh pve-nas-01
mkdir -p /mnt/ssd/appdata/home-assistant/backups
chown 1000:1000 /mnt/ssd/appdata/home-assistant/backups
chmod 755 /mnt/ssd/appdata/home-assistant/backups
```

2. **Verify NFS export** (should already be configured):

```bash
# Check /etc/exports on pve-nas-01
grep appdata /etc/exports
# Should include: /mnt/ssd/appdata 192.168.0.0/24(rw,sync,no_subtree_check,no_root_squash)
```

3. **Mount NFS in Home Assistant**:

Install "Samba share" or "SSH & Web Terminal" add-on, then:

```bash
ssh root@192.168.0.154 -p 22222

# Create mount point
mkdir -p /mnt/backups

# Add to fstab (persistent across reboots)
echo "pve-nas-01.esweiss.com:/mnt/ssd/appdata/home-assistant/backups /mnt/backups nfs defaults,timeo=900,retrans=5,_netdev 0 0" >> /etc/fstab

# Mount
mount -a

# Verify
df -h | grep backups
```

**Step 2: Configure Automatic Backups**

1. Go to **Settings > System > Backups**
2. Click **Settings** (gear icon)
3. Configure:
   - **Automatic backups**: Enabled
   - **Days to keep**: 7
   - **Backup folder**: `/mnt/backups` (if NFS mounted)

**Step 3: Manual Backup**

Create your first backup:
1. Go to **Settings > System > Backups**
2. Click **Create Backup**
3. Select what to include (Full backup recommended)
4. Name it appropriately (e.g., `pre-production-2026-01`)

### SSH Access Configuration

**Step 1: Install SSH Add-on**

1. Go to **Settings > Add-ons**
2. Click **Add-on Store**
3. Search for **Terminal & SSH** or **SSH & Web Terminal**
4. Install the add-on

**Step 2: Configure SSH**

1. Open the add-on
2. Go to **Configuration**
3. Set:
   - **Port**: `22222` (avoid conflict with Proxmox host SSH)
   - **Username**: `root` (or create custom user)
   - **Password**: Set a strong password
   - **Authorized keys**: Add your SSH public key (optional)

4. Start the add-on and enable "Start on boot"

**Step 3: Test SSH Access**

```bash
# From your laptop
ssh root@192.168.0.154 -p 22222

# Or use the task command
task home-assistant:console
```

### Update Strategy

**Step 1: Enable Update Notifications**

1. Go to **Settings > System > Updates**
2. Enable notifications for updates

**Step 2: Update Process**

Before updating:
1. **Create a backup** (Settings > System > Backups)
2. **Review release notes** at https://www.home-assistant.io/blog/
3. **Check for breaking changes**

To update:
1. Go to **Settings > System > Updates**
2. Click **Update** next to Home Assistant Core/OS/Supervisor
3. Wait for update to complete
4. Verify system stability

**Important**: Always backup before major version updates (e.g., 2025.12 → 2026.1)

## Phase 6: Authentik SSO Integration

Home Assistant uses SSO via the `hass-openid` custom integration, providing unified authentication with Authentik across your homelab. SSO is the sole login method (`block_login: true`), consistent with the Authentik-first authentication pattern used by other homelab services (Mealie, Bar Assistant, download clients).

> **Note**: The `home_assistant` Ansible role requires OIDC credentials. If you need to deploy without SSO temporarily (e.g., during initial setup before Authentik is configured), set `block_login: false` in the configuration template and provide placeholder values for the OIDC environment variables.

### Architecture

**OIDC Method** (Recommended):
- Uses the `hass-openid` custom integration (https://github.com/cavefire/hass-openid)
- Integrates directly into Home Assistant's login page
- No changes to Traefik IngressRoutes required
- Local login can be enabled as fallback (`block_login: false`) or disabled (`block_login: true`)

**Important Notes**:
- The `hass-openid` integration is community-maintained, not official Home Assistant
- Users must be pre-created in Home Assistant before their first SSO login
- The username in Home Assistant must exactly match the `preferred_username` claim from Authentik
- With `block_login: true`, only SSO login is available; set to `false` if local login fallback is desired

### Prerequisites

1. **Authentik** is running at `https://auth.ericsweiss.com`
2. **Home Assistant** is accessible at `https://home.esweiss.com` and `https://home.ericsweiss.com`
3. **HACS** (Home Assistant Community Store) is installed (for installing custom integrations)
4. **1Password item** `Home Assistant SSO` exists in Homelab vault with fields:
   - `authentik-client-id`
   - `authentik-client-secret`

### Step 1: Install HACS (If Not Already Installed)

HACS is required to install the `hass-openid` custom integration.

```bash
# SSH to Home Assistant
task home-assistant:console

# Install HACS
wget -O - https://get.hacs.xyz | bash -

# Exit and restart Home Assistant
exit
task home-assistant:vm-restart
```

After restart:
1. Go to **Settings -> Devices & Services -> Add Integration**
2. Search for **HACS**
3. Follow the setup wizard (requires GitHub account)

### Step 2: Install hass-openid Custom Integration

**Via HACS (Recommended):**

1. In Home Assistant, go to **HACS -> Integrations**
2. Click three dots menu (top right) -> **Custom repositories**
3. Add repository: `https://github.com/cavefire/hass-openid`
4. Category: **Integration**
5. Click **Add**
6. Find **OpenID Connect** in the list and click **Download**
7. Restart Home Assistant:
   ```bash
   task home-assistant:vm-restart
   ```

**Manual Installation (Alternative):**

```bash
# SSH to Home Assistant
task home-assistant:console

# Download and install manually
cd /config
mkdir -p custom_components
cd custom_components
wget https://github.com/cavefire/hass-openid/releases/latest/download/hass-openid.zip
unzip hass-openid.zip -d openid
rm hass-openid.zip

# Exit and restart
exit
task home-assistant:vm-restart
```

### Step 3: Configure Authentik

#### Create OAuth2 Provider

1. Log into Authentik at `https://auth.ericsweiss.com`
2. Navigate to **Applications -> Providers**
3. Click **Create**
4. Select **OAuth2/OpenID Provider**
5. Configure:

| Field | Value |
|-------|-------|
| **Name** | `Home Assistant` |
| **Authorization flow** | `default-authorization-flow` (implicit-consent) |
| **Client type** | `Confidential` |
| **Client ID** | (auto-generated - **COPY THIS**) |
| **Client Secret** | (auto-generated - **COPY THIS**) |
| **Redirect URIs/Origins (Regex)** | See below |
| **Signing Key** | Select any available certificate |
| **Scopes** | `openid`, `email`, `profile` |

**Redirect URIs/Origins (Regex)**:
```
https://home\.ericsweiss\.com/auth/openid/callback$
https://home\.esweiss\.com/auth/openid/callback$
```

6. Click **Finish**
7. **Save the Client ID and Client Secret** - you'll need them for 1Password

#### Create Authentik Application

1. Navigate to **Applications -> Applications**
2. Click **Create**
3. Configure:

| Field | Value |
|-------|-------|
| **Name** | `Home Assistant` |
| **Slug** | `home` |
| **Provider** | `Home Assistant` (select the provider created above) |
| **Launch URL** | `https://home.ericsweiss.com` |

4. Click **Create**

#### Optional: Bind User Group

If you want to restrict access:

1. Create a group: **Directory -> Groups -> Create** (`home-assistant-users`)
2. Add users to the group
3. Open the `Home Assistant` application
4. Go to **Policy / Group / User Bindings**
5. Click **Bind existing policy/group/user**
6. Select **Group** and choose `home-assistant-users`
7. Click **Create**

### Step 4: Store Credentials in 1Password

```bash
# Sign in to 1Password
eval $(op signin)

# Create or update the Home Assistant SSO item
# Use the 1Password UI to add/update the item with:
# - Item name: Home Assistant SSO
# - Field: authentik-client-id (paste Client ID from Authentik)
# - Field: authentik-client-secret (paste Client Secret from Authentik)
```

Alternatively, create via CLI:
```bash
op item create --category=password --title="Home Assistant SSO" \
  --vault=Homelab \
  authentik-client-id="<paste-client-id-here>" \
  authentik-client-secret="<paste-client-secret-here>"
```

### Step 5: Deploy Configuration

The configuration templates are already updated in the `home_assistant` Ansible role. Deploy:

```bash
# Ensure 1Password is authenticated
eval $(op signin)

# Verify secrets are accessible
op read "op://Homelab/Home Assistant SSO/authentik-client-id"
op read "op://Homelab/Home Assistant SSO/authentik-client-secret"

# Create a snapshot before deployment (recommended)
task home-assistant:snapshot NAME=pre-sso DESC="Before SSO integration"

# Deploy configuration (generates configuration.yaml + secrets.yaml with OIDC config)
task home-assistant:deploy-config

# Restart Home Assistant to apply
task home-assistant:restart-after-config
```

This will:
1. Generate `configuration.yaml` with the `openid:` block
2. Generate `secrets.yaml` with `oidc_client_id` and `oidc_client_secret`
3. Deploy both files to Home Assistant via SCP
4. Validate configuration using `ha core check`
5. Restart Home Assistant

### Step 6: Pre-Create Users in Home Assistant

**Critical**: OIDC users must exist in Home Assistant before their first SSO login.

1. Log into Home Assistant at `https://home.esweiss.com` with your existing local account
2. Go to **Settings -> People -> Users**
3. Click **Add User**
4. Configure:
   - **Username**: Must match the Authentik username exactly (the `preferred_username` claim)
   - **Password**: Set a temporary password (required during creation, not needed for SSO login)
   - **Administrator**: Enable if the user should have admin access
5. Click **Create**

Repeat for each Authentik user who needs access.

### Step 7: Test SSO Login

1. Open `https://home.esweiss.com` in a private/incognito browser window
2. You should see the Home Assistant login page with a new **"OpenID/OAuth2 authentication"** button
3. Click the SSO button
4. You'll be redirected to Authentik at `https://auth.ericsweiss.com`
5. Log in with your Authentik credentials
6. After authentication, you'll be redirected back to Home Assistant and logged in
7. Verify the correct user account is active (check profile icon)

### Step 8: Test External Domain

1. Open `https://home.ericsweiss.com` in a browser
2. Click the **OpenID/OAuth2 authentication** button
3. Verify the Authentik login flow works correctly

### Step 9: Verify Login Behavior

With `block_login: true`, only the SSO login button is displayed. To re-enable local login as a fallback:

1. Edit `ansible/roles/home_assistant/templates/configuration.yaml.j2`
2. Set `block_login: false`
3. Redeploy: `task home-assistant:deploy-config && task home-assistant:restart-after-config`

### Troubleshooting

#### "Invalid redirect URI" Error

**Cause**: Authentik provider redirect URIs don't match.

**Fix**: Verify the Authentik provider has both redirect URIs:
```
https://home\.ericsweiss\.com/auth/openid/callback$
https://home\.esweiss\.com/auth/openid/callback$
```

#### "Invalid client" Error

**Cause**: Client ID or secret mismatch between Authentik and Home Assistant.

**Fix**:
1. Verify 1Password has the correct values:
   ```bash
   op read "op://Homelab/Home Assistant SSO/authentik-client-id"
   op read "op://Homelab/Home Assistant SSO/authentik-client-secret"
   ```
2. Compare with Authentik provider settings
3. Redeploy configuration: `task home-assistant:deploy-config`

#### "User not found" Error

**Cause**: User doesn't exist in Home Assistant.

**Fix**: Pre-create the user (see Step 6) with matching username.

#### SSO Button Not Appearing

**Cause**: `hass-openid` integration not installed or configuration not loaded.

**Fix**:
1. Verify custom component is installed:
   ```bash
   task home-assistant:console
   ls -la /config/custom_components/openid
   ```
2. Check Home Assistant logs:
   ```bash
   cat /config/home-assistant.log | grep -i openid
   ```
3. Verify `configuration.yaml` has the `openid:` block:
   ```bash
   cat /config/configuration.yaml | grep -A 10 openid
   ```
4. Restart Home Assistant: `task home-assistant:vm-restart`

#### Configuration Check Failed

**Cause**: Syntax error in `configuration.yaml` or missing secrets.

**Fix**:
1. Check Ansible deployment output for errors
2. Manually verify secrets exist:
   ```bash
   task home-assistant:console
   cat /config/secrets.yaml | grep oidc
   ```
3. Run configuration check:
   ```bash
   ha core check
   ```

### Maintenance

#### Updating hass-openid

Check for updates via HACS:
1. Go to **HACS -> Integrations**
2. Find **OpenID Connect**
3. If an update is available, click **Update**
4. Restart Home Assistant

#### Rotating OIDC Credentials

To rotate the client secret:

1. In Authentik, navigate to the `Home Assistant` provider
2. Click **Regenerate Secret**
3. Copy the new secret
4. Update 1Password:
   ```bash
   op item edit "Home Assistant SSO" authentik-client-secret="<new-secret>"
   ```
5. Redeploy configuration:
   ```bash
   task home-assistant:deploy-config
   task home-assistant:restart-after-config
   ```

### Configuration Reference

The SSO configuration is managed by:
- **Templates**: `ansible/roles/home_assistant/templates/configuration.yaml.j2`, `secrets.yaml.j2`
- **Defaults**: `ansible/roles/home_assistant/defaults/main.yml`
- **Taskfile**: `home-assistant:deploy-config` task with 1Password env vars
- **1Password**: `Home Assistant SSO` item in Homelab vault

**Generated `configuration.yaml` section:**
```yaml
openid:
  client_id: !secret oidc_client_id
  client_secret: !secret oidc_client_secret
  configure_url: "https://auth.ericsweiss.com/application/o/home/.well-known/openid-configuration"
  scope: "openid profile email"
  username_field: "preferred_username"
  block_login: true
```

**Important fields**:
- `configure_url`: Uses the `home` slug from the Authentik application
- `username_field`: Maps to Authentik's `preferred_username` claim
- `block_login: true`: Only SSO login is displayed (set to `false` for local login fallback)

## Verification

### Test Access

```bash
# Internal access
curl -k https://home.esweiss.com

# External access
curl -k https://home.ericsweiss.com
```

Both should return the Home Assistant login page.

### Test WebSocket Connectivity

Home Assistant uses WebSockets for real-time updates. Verify they work:

1. Open browser developer tools (F12)
2. Navigate to Network tab
3. Go to `https://home.esweiss.com`
4. Look for WebSocket connections to `/api/websocket`
5. Status should be 101 (Switching Protocols)

## Troubleshooting

### VM Won't Boot

```bash
# Check VM status
ssh pve-prec-01 "qm status 154"

# Check VM config
ssh pve-prec-01 "qm config 154"

# View console output
# Use Proxmox web UI Console viewer
```

### Network Not Working

```bash
# At HAOS console (ha> prompt)
network info

# Check DNS resolution
ping -c 3 google.com
```

### Traefik 502 Bad Gateway

```bash
# Verify Home Assistant is running
curl -v http://192.168.0.154:8123

# Check EndpointSlice
kubectl get endpointslice home-assistant-backend -o yaml

# Check Traefik logs
kubectl logs -n traefik -l app.kubernetes.io/name=traefik -f
```

### WebSocket Connection Failed

Verify the `home-assistant-headers` middleware is applied (it lives in `kubernetes/apps/vm-ingress/middleware.yaml` but all middlewares declare `metadata.namespace: traefik`):

```bash
kubectl get middleware home-assistant-headers -n traefik -o yaml
```

Ensure `configuration.yaml` has:
```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 192.168.0.0/24
```

## Maintenance

### Backup Home Assistant

Home Assistant manages its own backups:

1. Settings > System > Backups
2. Create a full backup
3. Download backup file for off-site storage

Consider storing backups on NAS:
- Mount NFS share in Home Assistant
- Configure backup location to NFS path

### Update Home Assistant

Updates are managed through the Home Assistant UI:

1. Settings > System > Updates
2. Click update notification
3. Follow prompts

### VM Snapshots

For major changes, take a Proxmox snapshot first:

```bash
ssh pve-prec-01 "qm snapshot 154 pre-update --description 'Before HA update'"

# To restore if needed:
ssh pve-prec-01 "qm rollback 154 pre-update"
```

## DNS Configuration Reference

DNS is already configured. For reference:

### AdGuard Home (Internal)

In `ansible/inventories/prod/group_vars/dns.yml`:
```yaml
adguard_rewrites:
  - domain: "home.esweiss.com"
    answer: "192.168.0.101"  # Traefik internal VIP
  - domain: "home-direct.esweiss.com"
    answer: "192.168.0.154"  # Direct access (bypasses Traefik)
```

### Cloudflare (External)

Managed by external-dns via IngressRoute annotation:
```yaml
annotations:
  external-dns.alpha.kubernetes.io/target: ericsweiss.com
```

This creates: `home.ericsweiss.com -> ericsweiss.com -> (your public IP via DDNS)`

## Firewall Configuration Reference

The `home` host is already in the inventory with proper firewall configuration:

```yaml
# In hosts.yml
home:
  ansible_host: 192.168.0.154
  firewall_ipsets:
    - core-cluster
    - nfs_clients
```

This allows:
- Communication with all core infrastructure
- NFS access to NAS storage

No additional firewall rules are needed.

## Related Documentation

- `docs/19-k3s-deployment.md` - K3s cluster and platform services
- `docs/23-recipes-sso-setup.md` - Authentik SSO configuration patterns
- `docs/08-dns.md` - DNS architecture and AdGuard Home configuration
- [Home Assistant Installation](https://www.home-assistant.io/installation/alternative)
- [HAOS CLI](https://www.home-assistant.io/common-tasks/os/)
