# Disaster Recovery

This document covers disaster recovery scenarios for rebuilding the homelab
from backups / git. It is split into three tracks you may need to combine:

1. **Storage** — rebuild ZFS pools + datasets on the NAS (this doc's original scope).
2. **k3s cluster** — bring up the 9-node k3s cluster from Ansible.
3. **Flux GitOps + secrets encryption** — bootstrap Flux, restore ESO, rotate k3s secrets-encryption keys.

The three tracks correspond to the deployment order in
`docs/19-k3s-deployment.md` and `docs/29-flux-operations.md`; DR is those
guides applied to empty hardware.

## Storage Bootstrap

### When to Use This

Use the storage bootstrap playbook for these scenarios:

1. **Initial NAS Setup** - Brand new hardware, no existing ZFS pools
2. **Complete Hardware Failure** - Replacing NAS server entirely
3. **Disaster Recovery** - Rebuilding from backup after catastrophic failure
4. **Adding New Storage Pools** - Expanding storage with new ZFS pools

**DO NOT use this for:**
- Normal deployments (use `task deploy:all` instead)
- Adding new datasets to existing pools (use ZFS commands directly)
- Configuration changes only (use `task deploy:storage`)

## Storage Safety Guarantees

The storage management in this infrastructure is designed to be non-destructive:

**What storage tasks will NOT do:**
- Create or destroy ZFS pools
- Create or destroy ZFS datasets
- Format any disks
- Delete any files
- Overwrite existing data

**What storage tasks WILL do:**
- Set ZFS dataset properties (metadata only - compression, mountpoint, etc.)
- Mount existing filesystems (MergerFS, NFS bind mounts)
- Configure services (NFS exports, Samba shares)
- Install and configure monitoring (SMART, backup jobs)

### Safe vs Destructive Operations

**Safe Operations** (automated via Ansible):
- Setting ZFS properties (compression, atime, recordsize)
- Creating mount points and bind mounts
- Configuring NFS exports and Samba shares
- Moving data between tiers (media mover)
- Creating backups

**Destructive Operations** (require manual intervention):
- Creating ZFS pools (must be done manually)
- Destroying ZFS datasets
- Formatting disks
- Deleting data permanently

All storage tasks are idempotent and will fail safely if expected resources don't exist rather than creating them automatically.

---

## Overview

The storage bootstrap process consists of 7 phases:

1. **Pre-flight Checks** - Verify target host and display warnings
2. **Infrastructure Detection** - Scan for existing ZFS pools, datasets, directories
3. **Action Planning** - Determine what needs to be created
4. **Interactive Confirmation** - User approval required before proceeding
5. **Dataset Creation** - Create missing ZFS datasets
6. **Directory Setup** - Create bind mounts, export structure
7. **Service Configuration** - Configure NFS, Samba, MergerFS
8. **Verification** - Validate everything is working

---

## Prerequisites

Before running the bootstrap playbook:

### 1. Hardware Requirements
- Physical disks installed and detected by Linux
- Sufficient RAM for ZFS (minimum 8GB recommended)
- Network connectivity

### 2. ZFS Pools Must Be Created First

**CRITICAL**: The playbook does NOT create ZFS pools. You must create them manually first.

#### Why Manual Pool Creation?

ZFS pool creation requires:
- Choosing RAID level (raidz, raidz2, raidz3, mirror, stripe)
- Selecting specific disks by ID
- Setting pool-level properties (ashift, etc.)
- Making decisions about redundancy vs capacity

These decisions are too critical and hardware-specific to automate safely.

#### Creating ZFS Pools

For detailed instructions, see `docs/06-zfs.md`. Quick example:

```bash
# List available disks
ls -la /dev/disk/by-id/

# Create pool 'tank' with raidz2 (dual parity)
zpool create -o ashift=12 \
             -O acltype=posixacl \
             -O compression=zstd \
             -O normalization=formD \
             -O relatime=on \
             -O xattr=sa \
             -m /mnt/tank \
             tank raidz2 \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA18WD1 \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0RDDB \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0EJDZ \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0ZZ1R \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0HR34 \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0FEGJ

# Verify pool creation
zpool status tank
zpool list -v tank
```

**Required Pools for pve-nas-01:**
- `tank` - Main storage pool (HDDs in raidz2)
- Any other pools defined in `host_vars/pve-nas-01.yml`

### 3. Ansible Environment

Ensure your Ansible environment is set up:

```bash
# Install Ansible collections
cd ansible
ansible-galaxy collection install -r requirements.yml

# Verify 1Password authentication
op account get

# Export required secrets
export SSH_PUBLIC_KEY=$(op read "op://Homelab/SSH Key/public key")
export SAMBA_NAS_PASSWORD=$(op read "op://Homelab/Samba NAS User/password")
```

---

## Running Storage Bootstrap

### Step 1: Verify Current State

Before bootstrapping, understand what exists:

```bash
# Check for existing ZFS pools
ssh pve-nas-01 'zpool list'

# Check for existing datasets
ssh pve-nas-01 'zfs list'

# Check directory structure
ssh pve-nas-01 'ls -la /mnt/ /export/'
```

### Step 2: Run Bootstrap Playbook

```bash
cd ansible

# Run bootstrap (interactive mode - will prompt for confirmation)
ansible-playbook playbooks/bootstrap/storage-bootstrap.yml --limit pve-nas-01
```

### Step 3: Review Detection Results

The playbook will display:

```
INFRASTRUCTURE DETECTION RESULTS

ZFS Kernel Module: LOADED

Existing ZFS Pools:
  - tank

Existing ZFS Datasets:
  tank:
    - tank
    - tank/media
    - tank/share
    (or none found)

Critical Directories:
  /mnt/tank: EXISTS
  /mnt/ssd: MISSING
  /export: MISSING
```

### Step 4: Review Required Actions

```
REQUIRED ACTIONS

ZFS Pools to Create:
  WARNING: tank
  (or: All pools exist)

ZFS Datasets to Create:
  WARNING: tank/media
  WARNING: tank/share
  WARNING: tank/downloads
  (or: All datasets exist)

Directories to Create:
  - /export (if missing)
  - Bind mount targets under /export/
  - Bind source directories (if missing)

Services to Configure:
  - NFS exports (/etc/exports)
  - Samba shares (smb.conf)
  - MergerFS union mounts
```

### Step 5: Confirm or Abort

If pools are missing:
```
CRITICAL WARNING

The following ZFS pools need to be created:
  tank

ZFS POOL CREATION IS NOT AUTOMATED

You must manually create ZFS pools first.

TASK [Abort if pools need creation]
fatal: [pve-nas-01]: FAILED! => {
    "msg": "ZFS pools must be created manually first. See warning above."
}
```

**Action**: Create pools manually, then re-run playbook.

If datasets need creation:
```
CONFIRMATION REQUIRED

This playbook will create the following ZFS datasets:
  - tank/media
  - tank/share
  - tank/downloads
  - tank/proxmox
  - tank/pve

WARNING: This operation will:
  - Create ZFS datasets with configured properties
  - Set mountpoints, compression, recordsize, etc.
  - NOT destroy any existing data
  - NOT format any existing filesystems

Existing datasets will be SKIPPED (safe).

Do you want to proceed? [yes/NO]:
```

**Type exactly:** `yes` (lowercase, then press Enter)

### Step 6: Monitor Execution

The playbook will:

1. **Create ZFS Datasets**
   ```
   TASK [Create missing ZFS datasets]
   changed: [pve-nas-01] => (item=tank/media)
   changed: [pve-nas-01] => (item=tank/share)
   changed: [pve-nas-01] => (item=tank/downloads)
   ```

2. **Create Directory Structure**
   - `/export` (NFS root)
   - Bind mount targets (`/export/media`, `/export/share`, etc.)
   - Bind sources (`/mnt/tank/media`, `/mnt/ssd/appdata`, etc.)

3. **Configure Services**
   - NFS exports → `/etc/exports`
   - Samba shares → `/etc/samba/smb.conf`
   - MergerFS mounts → `/etc/fstab`
   - Media mover → systemd timer

4. **Verify Configuration**
   - ZFS datasets mounted
   - NFS exports active
   - MergerFS unions created

### Step 7: Review Completion Status

```
STORAGE BOOTSTRAP COMPLETE

ZFS Datasets Mounted:
  tank                     yes
  tank/media              yes
  tank/share              yes
  tank/downloads          yes
  tank/proxmox            yes
  tank/pve                yes

NFS Exports:
  Export list for pve-nas-01:
  /export           192.168.0.102,192.168.0.200/29
  /export/media     192.168.0.200/29,192.168.0.154
  /export/share     192.168.0.200/29
  /export/appdata   192.168.0.200/29

MergerFS Mounts:
  /mnt/media  /mnt/nvme/media:/mnt/tank/media

Next steps:
  1. Run postflight verification: task deploy:verify
  2. Review ZFS pool health: zpool status
  3. Check SMART disk health: smartctl -H /dev/disk/by-id/...
```

---

## Post-Bootstrap Verification

### 1. Run Comprehensive Verification

```bash
task deploy:verify
```

This will check:
- ZFS pool health (tank, ssd, nvme, archive - no DEGRADED/FAULTED)
- ZFS datasets mounted correctly
- MergerFS unions active
- NFS exports responding
- Samba shares configured
- SMART disk health (17 disks: 6 HDD tank + 3 SSD + 4 NVMe + 4 HDD archive)
- Backup jobs configured
- Media mover timer active

### 2. Verify ZFS Pool Health

```bash
ssh pve-nas-01 'zpool status -v'
```

Expected output:
```
  pool: tank
 state: ONLINE
config:

        NAME                                      STATE     READ WRITE CKSUM
        tank                                      ONLINE       0     0     0
          raidz2-0                                ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA18WD1     ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA0RDDB     ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA0EJDZ     ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA0ZZ1R     ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA0HR34     ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA0FEGJ     ONLINE       0     0     0

errors: No known data errors
```

### 3. Verify Dataset Properties

```bash
ssh pve-nas-01 'zfs get compression,recordsize,atime,mountpoint tank/media'
```

Should match configured values in `host_vars/pve-nas-01.yml`.

### 4. Test NFS Mounts

From a client machine:

```bash
# Show NFS exports
showmount -e pve-nas-01

# Test mount
sudo mount -t nfs4 192.168.0.102:/media /mnt/test
ls -la /mnt/test
sudo umount /mnt/test
```

### 5. Test Samba Access

From a client machine:

```bash
# List shares
smbclient -L //pve-nas-01 -U nas

# Connect to share
smbclient //pve-nas-01/share -U nas
```

---

## Common Scenarios

### Scenario 1: Brand New NAS (Fresh Install)

**Starting Point**: Proxmox installed, disks connected, no ZFS pools

**Procedure**:
1. Create ZFS pools manually (see above)
2. Run storage bootstrap playbook
3. Confirm dataset creation
4. Verify with `task deploy:verify`

### Scenario 2: Disaster Recovery (Pool Intact, Datasets Lost)

**Starting Point**: ZFS pool exists but datasets were accidentally destroyed

**Procedure**:
1. Run storage bootstrap playbook (will detect existing pool)
2. Confirm creation of missing datasets
3. Restore data from backups (if needed)
4. Verify with `task deploy:verify`

### Scenario 3: Hardware Replacement (Complete Rebuild)

**Starting Point**: New hardware, need to restore from backup

**Procedure**:
1. Install Proxmox on new hardware
2. Install physical disks
3. Create ZFS pools (same names as before)
4. Run storage bootstrap playbook
5. Restore ZFS datasets from backup:
   ```bash
   # Receive ZFS snapshot from backup
   zfs receive tank/media < /path/to/backup/tank-media-snapshot.zfs
   ```
6. Run storage bootstrap to configure services
7. Verify with `task deploy:verify`

### Scenario 4: Adding New Storage Pool

**Starting Point**: Existing NAS operational, adding new pool

**Procedure**:
1. Install new physical disks
2. Create new ZFS pool manually
3. Add pool configuration to `host_vars/pve-nas-01.yml`
4. Run storage bootstrap playbook (will only create new datasets)
5. Verify with `task deploy:verify`

---

## Safety Features

The storage bootstrap playbook includes multiple safety features:

### 1. Detection Before Action
- Scans for existing pools and datasets
- Shows exactly what will be created
- Never modifies existing infrastructure

### 2. Interactive Confirmation
- Requires user to type "yes" to proceed
- Displays clear warnings about what will happen
- Aborts if confirmation not given

### 3. Idempotent Operations
- Safe to run multiple times
- Skips existing infrastructure
- Only creates what's missing

### 4. No Destructive Operations
- Never creates ZFS pools (manual only)
- Never destroys datasets
- Never formats disks
- Never deletes data
- Only creates missing datasets
- Only creates directories
- Only configures services

### 5. Clear Error Messages
- Fails fast if pools are missing
- Provides actionable guidance
- Links to relevant documentation

---

## Troubleshooting

### Problem: Playbook aborts with "ZFS pools must be created manually"

**Cause**: Required ZFS pools don't exist

**Solution**:
1. Create pools manually (see `docs/06-zfs.md`)
2. Verify pools exist: `zpool list`
3. Re-run bootstrap playbook

### Problem: Dataset creation fails with "dataset already exists"

**Cause**: Dataset exists but wasn't detected

**Solution**:
1. Check dataset list: `zfs list`
2. Verify dataset name matches configuration
3. If dataset exists, playbook should skip it (report as bug if not)

### Problem: Permission denied errors during directory creation

**Cause**: Parent directory doesn't exist or insufficient permissions

**Solution**:
1. Verify ZFS datasets are mounted: `zfs get mounted`
2. Check mount points exist: `ls -la /mnt/`
3. Verify playbook running with `become: true`

### Problem: NFS exports not visible to clients

**Cause**: Firewall rules, NFS server not running, or exports misconfigured

**Solution**:
1. Check NFS server: `systemctl status nfs-kernel-server`
2. Verify exports: `exportfs -v`
3. Check firewall rules
4. Test from NAS: `showmount -e localhost`

---

## Rollback Procedures

### If Dataset Creation Fails Mid-Way

**Situation**: Some datasets created, others failed

**Rollback**:
```bash
# List datasets that were created
zfs list

# Destroy newly created datasets (if needed)
zfs destroy tank/problematic-dataset

# Re-run bootstrap playbook
```

**Note**: Only destroy datasets if they were just created and contain no data.

### If Service Configuration Fails

**Situation**: Datasets created successfully, but NFS/Samba config failed

**Rollback**:
1. ZFS datasets are safe (no rollback needed)
2. Fix configuration issue
3. Re-run bootstrap playbook (will skip dataset creation)

### If You Need to Start Over

**Nuclear Option**: Remove all created infrastructure

**WARNING**: This destroys all data!

```bash
# Export and destroy all NFS/Samba configs
sudo systemctl stop nfs-kernel-server smbd nmbd
sudo mv /etc/exports /etc/exports.old
sudo mv /etc/samba/smb.conf /etc/samba/smb.conf.old

# Unmount MergerFS
sudo umount /mnt/media
sudo umount /export/media

# Destroy datasets (DATA LOSS!)
sudo zfs destroy -r tank/media
sudo zfs destroy -r tank/share
# ... repeat for all datasets

# Destroy pools (DATA LOSS!)
sudo zpool destroy tank

# Now you can start completely fresh
```

---

## Checklist

Use this checklist when performing disaster recovery:

### Pre-Bootstrap
- [ ] Physical disks installed and detected (`ls /dev/disk/by-id/`)
- [ ] Sufficient RAM available (8GB+ for ZFS)
- [ ] Network connectivity working
- [ ] SSH access to NAS established
- [ ] 1Password authenticated (`op account get`)
- [ ] Required secrets exported
- [ ] ZFS kernel module loaded (`lsmod | grep zfs`)
- [ ] ZFS pools created manually (`zpool list`)

### During Bootstrap
- [ ] Reviewed infrastructure detection results
- [ ] Confirmed required actions make sense
- [ ] Provided explicit confirmation ("yes")
- [ ] Monitored dataset creation
- [ ] Reviewed service configuration output
- [ ] Checked for errors in playbook output

### Post-Bootstrap
- [ ] Ran `task deploy:verify` successfully
- [ ] Verified ZFS pool health (`zpool status`)
- [ ] Checked dataset properties (`zfs get all`)
- [ ] Tested NFS exports (`showmount -e`)
- [ ] Tested Samba shares (`smbclient -L`)
- [ ] Verified MergerFS mounts (`findmnt -t fuse.mergerfs`)
- [ ] Checked SMART disk health
- [ ] Reviewed backup job configuration
- [ ] Verified media mover timer active

---

## k3s cluster recovery (after hardware loss)

If the k3s cluster itself is gone (all three server VMs lost, corrupt etcd
that can't be restored from snapshot, etc.):

```bash
# 1. Bring up VMs + k3s from Ansible (idempotent; respects existing state).
task k3s:provision-vms
task k3s:deploy

# 2. Fetch the fresh kubeconfig.
task k3s:kubeconfig

# 3. Verify the cluster is Ready.
task k3s:status
```

After step 3 you have a bare k3s cluster with kube-vip + MetalLB pool ready,
but no Flux, no app workloads. Proceed to the Flux recovery section next.

The previous etcd snapshot (if available) contained a snapshot of every
Secret value at snapshot time — but restoring from etcd is uncommon in our
topology. The canonical "restore" path is: reinstall k3s fresh → bootstrap
Flux → let ESO re-sync every Secret from 1Password. The only state NOT in
git is the ZFS zvols that back Postgres (Authentik, Mealie) and the GitLab
repo storage — those are independently backed up via ZFS snapshots /
replication and must be restored before the consuming workload comes up
(see `docs/06-zfs.md`).

## Flux + ESO recovery

After the k3s cluster is healthy:

```bash
# 1. Install flux CLI locally if needed.
task flux:install-cli

# 2. Create the 1Password SDK bootstrap Secret (the ONE hand-managed Secret).
task flux:bootstrap-onepassword

# 3. Optionally pre-delete pre-existing Secrets on a partial recovery.
# If the cluster was rebuilt from scratch (fresh k3s install), nothing
# to delete — skip this step. If you kept the cluster but are re-pointing
# Flux at a clean repo, see docs/29-flux-operations.md §
# "First-time Flux bootstrap: delete pre-existing manually-created Secrets".

# 4. Bootstrap Flux itself. This pushes a commit to the configured branch
# (default main) with the Flux components manifests. The bootstrap token
# is read from 1Password item "Flux GitLab PAT".
task flux:bootstrap

# 5. Watch reconciliation. Expect 5-15 min for first convergence
# (HelmRepository pulls → controllers install → CRDs registered →
# configs reconcile → apps reconcile).
task flux:status
task flux:verify
```

## k3s secrets-encryption reencrypt (scheduled follow-up)

The k3s deployment config enables secrets-encryption, but the initial
re-encryption of on-disk etcd secrets has not yet been performed on the
live cluster. This is a staggered per-server operation that requires
server restarts and is appropriately deferred to a maintenance window
rather than run under DR time pressure.

When ready to perform the reencrypt:

```bash
# On each k3s server node, one at a time, with monitoring between steps:
#   k3s-srv-nas-01, k3s-srv-laptop-01, k3s-srv-prec-01
ssh k3s-srv-nas-01

# Prepare — serializes a new encryption key.
sudo k3s secrets-encrypt prepare

# Reencrypt etcd contents with the new key. This is the work-doing step.
sudo k3s secrets-encrypt reencrypt

# Confirm status shows "reencrypt_finished" before moving to the next server.
sudo k3s secrets-encrypt status
```

See https://docs.k3s.io/cli/secrets-encrypt for the full procedure. Track
in `docs/16-next-steps.md`.

## Related Documentation

- `docs/06-zfs.md` - ZFS administration, pool creation, maintenance
- `docs/07-fileservices.md` - NFS, Samba, MergerFS detailed configuration
- `docs/12-runbooks.md` - Operational procedures
- `docs/19-k3s-deployment.md` - k3s cluster deployment (full workflow)
- `docs/29-flux-operations.md` - Flux day-2 ops (suspend/resume, rotation, rollback)
- `ansible/roles/nas_storage/` - Storage role implementation

---

## Emergency Contacts / Resources

If you need help during disaster recovery:

1. **ZFS Documentation**: https://openzfs.github.io/openzfs-docs/
2. **Proxmox Forums**: https://forum.proxmox.com/
3. **This Repository**: Check issues for similar scenarios
4. **Your Backups**: Always verify you have recent backups before major changes

---

**Last Updated**: 2026-04-16
