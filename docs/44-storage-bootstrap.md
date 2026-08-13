# NAS Storage Bootstrap

This is the operator manual for
`ansible/playbooks/bootstrap/storage-bootstrap.yml`, the
interactive playbook that lays out ZFS datasets, the export tree, and the file
services on pve-nas-01. It is one leg of disaster recovery — see
[docs/17](17-disaster-recovery.md) for the full DR picture, and
[docs/06](06-zfs.md) for pool geometry.

## When to use this

Use the storage bootstrap playbook for:

1. **Initial NAS setup** — brand new hardware, no existing ZFS pools
2. **Complete hardware failure** — replacing the NAS server entirely
3. **Disaster recovery** — rebuilding after catastrophic failure
4. **Adding a new storage pool** — expanding storage

**Do not use it for:**

- Normal deployments — use `task infra:deploy`
- Adding datasets to existing pools — use ZFS commands directly
- Configuration-only changes — use `task storage:deploy`

The playbook never creates or destroys ZFS **pools**. Create those by hand
first (docs/06); see [docs/17 § Storage safety guarantees](17-disaster-recovery.md#storage-safety-guarantees).

## Bootstrap phases

The storage bootstrap process consists of 8 phases:

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

**Use the canonical pool-creation commands in
[docs/06-zfs.md](06-zfs.md#pool-details) — do not hand-transcribe them here.**
The real `tank` pool is `raidz2` **plus** a `special mirror` (2x NVMe metadata)
**and** an L2ARC `cache` vdev; a `zpool create ... tank raidz2 <disks>` without
those two vdevs would rebuild a materially different, slower pool. docs/06 is the
single source of truth for every pool's exact geometry and by-id device list.

> **DANGER — `nvme` lives on partition 4 of the Proxmox BOOT disk.** On
> pve-nas-01 the Samsung 990 PRO 4TB carries the BIOS boot partition, the ESP,
> and the `pve` LVM volume group (root, swap, and the local-lvm guest disks) on
> `p1`-`p3`; only `p4` is the ZFS member. `zpool create ... nvme
> /dev/disk/by-id/nvme-Samsung_SSD_990_PRO_4TB_<serial>` against the **whole
> device** destroys the hypervisor you just installed. Use the `-part4` suffix,
> exactly as docs/06 § NVMe Pool Creation writes it, and confirm with
> `zpool status nvme` that the vdev reads `...-part4`. This matters most in the
> DR path, where docs/17 § Total site loss installs Proxmox onto this same disk
> immediately before pool creation.

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
  - ssd
  - nvme
  - archive

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
  1. Run postflight verification: task infra:verify
  2. Review ZFS pool health: zpool status
  3. Check SMART disk health: smartctl -H /dev/disk/by-id/...
```

---

## Post-Bootstrap Verification

### 1. Run Verification

```bash
task infra:verify
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

After `nfs_tls` hardening the `/export/{media,share,appdata}` exports are scoped
to the k3s CIDRs (.200/29, .220/29) with `xprtsec=tls` **required** (plaintext is
rejected); `/export/media` also has a plaintext read-only entry for HAOS (.154).
General LAN clients are intentionally not exported to, so a plaintext mount by IP
from a laptop fails by design (and a TLS mount by IP fails too — the
`*.esweiss.com` cert has no IP SAN). Verify from an authorized k3s agent, mounting
**by hostname over TLS**:

```bash
# From a k3s agent (an authorized CIDR with tlshd running)
sudo mount -t nfs4 -o xprtsec=tls pve-nas-01.esweiss.com:/media /mnt/test
ls -la /mnt/test
sudo umount /mnt/test

# Or simply confirm the existing PV-backed pods are Running (they mount the same
# exports over TLS): kubectl get pods -A | grep -Ev 'Running|Completed'
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
4. Verify with `task infra:verify`

### Scenario 2: Disaster Recovery (Pool Intact, Datasets Lost)

**Starting Point**: ZFS pool exists but datasets were accidentally destroyed

**Procedure**:
1. Run storage bootstrap playbook (will detect existing pool)
2. Confirm creation of missing datasets
3. Restore data from backups (if needed)
4. Verify with `task infra:verify`

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
7. Verify with `task infra:verify`

### Scenario 4: Adding New Storage Pool

**Starting Point**: Existing NAS operational, adding new pool

**Procedure**:
1. Install new physical disks
2. Create new ZFS pool manually
3. Add pool configuration to `host_vars/pve-nas-01.yml`
4. Run storage bootstrap playbook (will only create new datasets)
5. Verify with `task infra:verify`

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
- [ ] Ran `task infra:verify` successfully
- [ ] Verified ZFS pool health (`zpool status`)
- [ ] Checked dataset properties (`zfs get all`)
- [ ] Tested NFS exports (`showmount -e`)
- [ ] Tested Samba shares (`smbclient -L`)
- [ ] Verified MergerFS mounts (`findmnt -t fuse.mergerfs`)
- [ ] Checked SMART disk health
- [ ] Reviewed backup job configuration
- [ ] Verified media mover timer active

## Related documentation

- [docs/17-disaster-recovery.md](17-disaster-recovery.md) — full disaster-recovery tracks and restore procedures
- [docs/06-zfs.md](06-zfs.md) — pool creation commands, dataset inventory, encryption
- [docs/07-fileservices.md](07-fileservices.md) — NFS/Samba export configuration
- [docs/32-zfs-encryption.md](32-zfs-encryption.md) — encryption roots and boot-time unlock
