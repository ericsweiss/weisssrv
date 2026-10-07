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
first (docs/06); see [docs/17 § Storage safety](17-disaster-recovery.md#storage-safety).
It is idempotent, detection-first and confirmation-gated: it detects what
exists, asks for confirmation, then creates only the missing datasets,
directories and service config, and verifies them.

---

## Prerequisites

Before running the bootstrap playbook the disks must be installed and visible
to Linux (`ls /dev/disk/by-id/`), and the NAS wants at least 8 GB of RAM for
ZFS.

### 1. ZFS Pools Must Be Created First

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

### 2. Ansible Environment

Ensure your Ansible environment is set up:

```bash
# Install Ansible collections
ansible-galaxy collection install -r ansible/requirements.yml

# Verify 1Password authentication
op account get
```

The two secrets the playbook needs (`SSH_PUBLIC_KEY`, `SAMBA_NAS_PASSWORD`) are
injected by the task wrapper below — do not export them into your shell.

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

# Confirm the declared cipher matches every encryption root. host_vars declares
# aes-256-gcm as a plaintext-recreation guard; a mismatch fails the deploy loudly.
ssh pve-nas-01 'zfs get -r -t filesystem encryption tank ssd'
```

### Step 2: Run Bootstrap Playbook

```bash
# Runs the playbook under `op run --`, injecting SSH_PUBLIC_KEY and
# SAMBA_NAS_PASSWORD from 1Password (interactive — prompts for confirmation)
task disaster-recovery:storage-bootstrap
```

Fallback, if the Taskfile is unavailable: export `SSH_PUBLIC_KEY` and
`SAMBA_NAS_PASSWORD` by hand, then

```bash
ansible-playbook -i ansible/inventories/prod \
  ansible/playbooks/bootstrap/storage-bootstrap.yml --limit pve-nas-01
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
  ... (the dataset list comes from the `datasets:` blocks of
       `nas_storage_zfs_pools` in host_vars/pve-nas-01.yml — shape-only output)
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
  - tank/proxmox
  - tank/pve
  ... (as declared in host_vars/pve-nas-01.yml)

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
   ... (one item per `nas_storage_zfs_pools[].datasets` entry)
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
  tank/proxmox            yes
  tank/pve                yes
  ... (one line per declared dataset)

NFS Exports:
  Export list for pve-nas-01:
  ... (the live export set, its client scoping and its TLS requirement are
       described under § Post-Bootstrap Verification — `nas_storage_exports`
       is the source)

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

## Adding a dataset to a live NAS

`nas_storage` **never creates datasets** — declaring one it cannot find is a hard
`DATASET_MISSING` failure on the next NAS deploy. So a new entry in
`nas_storage_zfs_pools[].datasets` is always paired with this sequence, run as
root on pve-nas-01 **before** the deploy. `tank/backups/apps` is the worked
example: it was a plain directory under `tank/backups` holding live per-app
dumps, and `zfs create` refuses a non-empty mountpoint.

```bash
# 1. Stop whatever writes there (in-cluster dump CronJobs, archsync/restic
#    timers, the gitlab-backup timer on .153). A dump landing mid-move is lost.

# 2. Move existing content aside — same pool, so this is a rename, not a copy.
mv /mnt/tank/backups/apps /mnt/tank/backups/apps.pre-dataset

# 3. Create it. Encryption is INHERITED from the tank/backups encryption root:
#    do NOT pass -o encryption, and never `zfs set encryption` on an existing
#    dataset (it errors, by design — that is the plaintext-recreation guard).
zfs create -o mountpoint=/mnt/tank/backups/apps \
           -o atime=off -o compression=zstd \
           -o com.sun:auto-snapshot=false \
           tank/backups/apps

# 4. Move the content back and restore ownership to the exports' all_squash pair.
mv /mnt/tank/backups/apps.pre-dataset/* /mnt/tank/backups/apps/
rmdir /mnt/tank/backups/apps.pre-dataset
chown -R 1000:2000 /mnt/tank/backups/apps

# 5. Prove it is a mounted dataset — this is exactly what the exports'
#    bind_source_check tests, and a bare directory passes nothing.
mountpoint -q /mnt/tank/backups/apps && echo "OK: dataset mounted"
zfs get -o property,value encryption,keystatus,mountpoint tank/backups/apps
```

Only then `task infra:deploy -- --limit pve-nas-01 --tags nas_storage`, restart
the writers, and confirm the next nightly dumps land. The dataset is listed in
docs/06's `tank` dataset table.

**Every established mount goes stale across the move — server binds first.**
Bind mounts capture the source tree at mount time, so a rename or a staging-dir
move leaves `/export/backups-apps/<app>` serving a deleted filesystem
(`findmnt` shows the source suffixed `//deleted`) even though fstab already
names the new path. Client mount RPCs then hang on the hard mount and freeze an
Ansible play mid-task. Re-point the server first — per app,
`umount -l /export/backups-apps/<app> && mount /export/backups-apps/<app>`, then
`sudo exportfs -ra` — then lazy-unmount the same paths on any guest that already
had them mounted (the gitlab, nextcloud and immich landing mounts) and let the
role remount them on its next converge. The role cannot detect this: mountpoint
present plus fstab entry present reads as converged.

### Encrypted datasets

A dataset that is its own encryption root is created with the key material, and
the passphrase comes from 1Password (`ZFS Pool ssd Passphrase`). `ssd/k3s-etcd`
is the worked example:

```bash
zfs create -o encryption=aes-256-gcm -o keyformat=passphrase \
           -o keylocation=prompt -o mountpoint=/mnt/ssd/k3s-etcd \
           ssd/k3s-etcd
```

The dataset name equals its `encryptionroot`, so the `zfs-load-key@ssd` boot
loop unlocks it with the pool's own passphrase ([docs/32](32-zfs-encryption.md)).

The plaintext-recreation guard (`encryption:` on an entry in
`nas_storage_zfs_pools`) now covers every declared encryption root:
`tank/proxmox`, `tank/nextcloud-data`, `tank/immich-data`, `tank/backups/apps`,
`ssd/appdata` and `ssd/k3s-etcd`. It does not cover `tank/share`, which is not
an encryption root.

---

## Common Scenarios

Every variant runs the same bootstrap playbook. What differs is the setup
before it and the restore after it.

| Starting point | What differs from the standard run |
|---|---|
| Fresh install — Proxmox up, disks connected, no pools | Create the pools by hand first ([docs/06](06-zfs.md)), then the standard run |
| Pool intact, datasets lost | The standard run detects the pool and creates only the missing datasets; restore data per [docs/17 § Restore Procedures](17-disaster-recovery.md#restore-procedures) |
| Hardware replacement | Install Proxmox and the disks, recreate the pools with the SAME names and the exact geometry in [docs/06](06-zfs.md), then the standard run; restore datasets per [docs/17 § Restore Procedures](17-disaster-recovery.md#restore-procedures) — `sudo archive-backupctl restore <target>` from the archive replica if the archive pool survived, otherwise `sudo restic-offsitectl restore <source>` from B2 ([docs/42](42-offsite-backup.md)). Both tools are role-shipped, so on truly fresh hardware they exist only after the base deploy has run |
| New pool on a live NAS | Create the pool, add it to `host_vars/pve-nas-01.yml`, then the standard run creates only its datasets |

Finish every variant with `task infra:verify`.

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

There is no scripted teardown, and none should be added. Tearing a pool down is
a deliberate, out-of-band act performed by hand with the pool geometry in
[docs/06](06-zfs.md) in front of you; the bootstrap playbook neither creates nor
destroys pools.

---

## Related documentation

- [docs/17-disaster-recovery.md](17-disaster-recovery.md) — full disaster-recovery tracks and restore procedures
- [docs/06-zfs.md](06-zfs.md) — pool creation commands, dataset inventory, encryption
- [docs/07-fileservices.md](07-fileservices.md) — NFS/Samba export configuration
- [docs/32-zfs-encryption.md](32-zfs-encryption.md) — encryption roots and boot-time unlock
