# ZFS Native Encryption

## Overview

Selected ZFS datasets are encrypted with native ZFS encryption (AES-256-GCM)
to defeat **disk-leaves-the-building** scenarios: RMA, disposal, theft of
an offline drive. The mechanism does *not* protect against running-system
theft — anyone with root on a powered-on host can extract the unlock
credentials and decrypt the data.

Pool keys are stored as `passphrase`-format ZFS keys. At boot, each
Proxmox host pulls its passphrase from 1Password Connect (running HA in
the cluster, exposed at `connect.esweiss.com` via internal Traefik) and
calls `zfs load-key`. If Connect is unreachable, the operator unlocks
manually via SSH using the same passphrase from the 1Password mobile
app.

## What is encrypted, what isn't

The model is **dataset-level encryption, not pool-level**. The tank /
ssd / nvme pool roots stay plaintext; individual child datasets that
hold sensitive data are encryption roots, recreated and populated via
`zfs send | zfs recv` (per pool §2a procedure below). At boot
`zfs-load-key.sh` runs `zfs load-key -r <pool>` which descends into
every encryption root within the pool, so the role's per-pool entry in
`zfs_encryption_pools` (e.g. `name: tank`) covers all encrypted
descendants without requiring the pool root itself to be encrypted.

### Encrypted (post-rollout)

- **NAS (`pve-nas-01`)**:
  - `tank/share` (LAN file share) — recreated encrypted, data migrated
    via send/recv.
  - `tank/proxmox` (Proxmox VM backup target) — recreated encrypted.
  - `tank/nextcloud-data` — to be created encrypted during rollout
    (Nextcloud not yet deployed).
  - `tank/immich-data` — to be created encrypted during rollout
    (Immich not yet deployed).
  - `ssd/appdata/{authentik,gitlab,loki,mealie,prometheus}` — every app
    PV / DB. Migrated one at a time via `zfs send | zfs recv`.
  - `nvme/*` — recreated encrypted.

  GitLab's backup tarball lives on the VM's root disk
  (`/var/opt/gitlab/backups`) and is out of scope for ZFS encryption;
  Proxmox VM snapshots of the GitLab VM land in `tank/proxmox` (covered
  above).

### Not encrypted (by design)

- `tank/media` — public-domain or licensed media; LAN-trust is fine
  and encryption breaks `zfs send` to off-pool replicas without `-w`.
- `tank/pve` — ephemeral Proxmox VM/LXC images. (`tank/proxmox` is in
  the encrypted list above because it holds VM backup tarballs that
  contain persistent app state.)
- `archive` pool — separate LUKS effort tracked alongside the failing
  archive-pool drive replacement. See `docs/06-zfs.md`.
- **Compute nodes' `local-ssd` pools (5×)** — host the k3s VM disks
  (servers + agents) and HA-managed LXC subvols. Encrypting these
  would create an unrecoverable cold-boot deadlock: Connect runs in
  k3s, k3s VMs live on `local-ssd`, and `local-ssd` would need
  Connect to unlock. Threat coverage instead comes from the drive-wipe
  SOP in `docs/15-credential-rotation.md` (every drive that leaves a
  compute host is wiped or destroyed) plus k3s native
  `secrets-encryption` for etcd at-rest. The residual exposure —
  drive theft from a powered-down host on the LAN — is accepted as
  low-probability for a homelab.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ pve-nas-01    (boot)                                              │
│   systemd: zfs-import.target                                      │
│      |                                                            │
│      v                                                            │
│   zfs-load-key@tank.service ─┐                                    │
│   zfs-load-key@ssd.service   │  Each unit runs:                   │
│   zfs-load-key@nvme.service  ├─ zfs-load-key.sh <pool>            │
│      ...                     │     - read /etc/onepassword-connect/token │
│                              │     - HTTPS GET connect.esweiss.com/v1/...│
│                              │     - parse passphrase from response      │
│                              │     - `zfs load-key <pool>` via stdin     │
│                              │     (mount handled by zfs-mount.service)  │
│      |                                                            │
│      v                                                            │
│   zfs-mount.service                                               │
│      |                                                            │
│      v                                                            │
│   zfs.target (pools available)                                    │
│      |                                                            │
│      v                                                            │
│   pve.service / qemu / lxc / k3s VMs ...                          │
└──────────────────────────────────────────────────────────────────┘
                                        │ HTTPS, TLS 1.3, lan-only
                                        v
              ┌──────────────────────────────────────────┐
              │ k3s cluster                              │
              │   IngressRoute connect.esweiss.com       │
              │     -> Service onepassword-connect:8080  │
              │     -> Pod (replicas: 2, anti-affinity)  │
              └──────────────────────────────────────────┘
```

## 1Password items

In vault `Homelab`, create one item per pool using the **Password**
template:

| Item title | Pool | Field name | Field value |
|------------|------|------------|-------------|
| `ZFS Pool tank Passphrase` | tank | `passphrase` | random ≥ 32 chars |
| `ZFS Pool ssd Passphrase` | ssd | `passphrase` | random ≥ 32 chars |
| `ZFS Pool nvme Passphrase` | nvme | `passphrase` | random ≥ 32 chars |

Generate with `op item generate-password --length 64`.

(No `local-ssd` entry — compute hosts' pools are intentionally not
encrypted. See "What is encrypted, what isn't" above.)

Plus one shared item for the Connect access token used by all hosts:

| Item title | Field name | Source |
|------------|------------|--------|
| `ZFS Encryption Connect Token` | `credential` | `op connect token create weisssrv-zfs --server <id> --vaults Homelab` |

The token only grants Connect read access for the Homelab vault — same
exposure as if a Connect server admin role were leaked.

## Rollout procedure

### Step 1: Bootstrap the host-side mechanism (no-op until pools exist)

The role can be deployed safely before any pool is actually encrypted:
it installs the script, the systemd unit template, and the Connect
token; nothing runs until `zfs_encryption_pools` is non-empty for that
host AND `zfs_encryption_bootstrap_only` is `false`.

```bash
# Make sure Connect HA + the connect.esweiss.com IngressRoute are
# already deployed; the relevant manifests live in
# kubernetes/infrastructure/controllers/onepassword-connect/ (HelmRelease
# + replicas:2 + PDB) and kubernetes/infrastructure/configs/onepassword-connect-{certificate,ingress}.yaml.
task flux:status

# Generate Connect token + place in 1P
op connect token create weisssrv-zfs --server <connect-id> --vaults Homelab
# -> paste output into "ZFS Encryption Connect Token" / credential

# Bootstrap the role on every Proxmox host:
task zfs:encrypt-bootstrap
```

After this step:
- `/usr/local/sbin/zfs-load-key.sh` and unit template deployed on every
  Proxmox host (so the mechanism is in place before anything is
  encrypted)
- `/etc/onepassword-connect/token` (mode 0400) is deployed only on
  hosts whose `host_vars/<host>.yml` declares a non-empty
  `zfs_encryption_pools`. Compute hosts (intentionally not encrypted)
  don't receive the token even after bootstrap — the role gates the
  copy on the per-host pools list to keep the token's blast radius
  scoped to hosts that will actually use it
- No pools changed; no per-pool services enabled

### Step 2: Encrypt a pool (per pool, one at a time)

ZFS native encryption requires either:
- A new dataset created with `encryption=on` and a key, with data
  migrated in via `zfs send | zfs recv`, OR
- An entire pool re-created (only viable for compute `local-ssd` since
  contents are replicated/rebuildable).

#### 2a. NAS dataset migration (per app on `ssd/appdata/<app>`)

For each child dataset (authentik, gitlab, loki, mealie, prometheus):

```bash
APP=authentik
SRC=ssd/appdata/$APP
ENC=ssd/appdata-enc/$APP

# 1. Stop the consumer (k3s pod, LXC, or VM)
kubectl scale deploy/$APP -n $APP --replicas=0
# (or the equivalent for non-k3s consumers)

# 2. Take a clean snapshot
sudo zfs snapshot -r ${SRC}@encrypt-migrate

# 3. Create an encrypted parent if it doesn't exist
sudo zfs list ssd/appdata-enc 2>/dev/null || \
  sudo zfs create \
    -o encryption=on \
    -o keyformat=passphrase \
    -o keylocation=prompt \
    ssd/appdata-enc
# (key prompted; paste from 1P "ZFS Pool ssd Passphrase" — the same
#  passphrase will be used for every encrypted dataset on this pool
#  because they all inherit the wrapping key)

# 4. Send|recv into the encrypted parent
sudo zfs send -R ${SRC}@encrypt-migrate | \
  sudo zfs recv -F ${ENC}

# 5. Swap mountpoints
sudo zfs unmount ${SRC}
sudo zfs set mountpoint=$(zfs get -H -o value mountpoint ${SRC}) ${ENC}
sudo zfs rename ${SRC} ${SRC}-pre-encryption
sudo zfs rename ${ENC} ${SRC}

# 6. Bring the consumer back up
kubectl scale deploy/$APP -n $APP --replicas=1

# 7. After verifying the app works, destroy the unencrypted source
#    AND its snapshot.
sudo zfs destroy -r ${SRC}-pre-encryption
```

### Step 3: Activate per-pool boot units

For each host that now has at least one encrypted pool, set
`zfs_encryption_pools` in its `host_vars/<host>.yml` and re-run the
playbook without `bootstrap_only`:

```yaml
# ansible/inventories/prod/host_vars/pve-nas-01.yml
zfs_encryption_pools:
  - name: tank
    item: "ZFS Pool tank Passphrase"
  - name: ssd
    item: "ZFS Pool ssd Passphrase"
  - name: nvme
    item: "ZFS Pool nvme Passphrase"
```

```bash
task zfs:encrypt -- --limit pve-nas-01
```

This enables `zfs-load-key@<pool>.service` for each listed pool. A
reboot test should now show keys auto-loaded; `zfs get keystatus
<pool>` returns `available`.

### Step 4: Verify and rotate

```bash
# Verify auto-load works
sudo zfs unload-key -a   # only safe if you're prepared to remount
sudo systemctl restart 'zfs-load-key@*.service'
sudo zfs get keystatus tank ssd nvme

# Rotate passphrase (yearly, or after suspected exposure):
sudo zfs change-key -o keylocation=prompt tank   # paste new passphrase
# Update the 1P item with the new value
# Other hosts pick up the new value at next reboot or:
sudo systemctl restart zfs-load-key@tank.service
```

## Cold-cluster recovery

In the normal cold-boot case **no operator intervention is needed**.
The chain is:

1. Compute hosts (`pve-laptop-01`, `pve-opt-0{1,2,3}`, `pve-prec-01`)
   power on. Their `local-ssd` pools are unencrypted, so ZFS mounts
   without a key. k3s VMs boot, etcd reaches quorum, the cluster
   becomes ready.
2. DNS LXCs (`dns-01`, `dns-02` — HA-managed by Proxmox HA on
   compute hosts; also unencrypted `local-ssd`-backed) come up.
   They serve the AdGuard rewrite that resolves
   `connect.esweiss.com` to the in-cluster Traefik VIP.
3. Connect (`replicas: 2` in `external-secrets` namespace) gets
   scheduled and serves at `connect.esweiss.com` via Traefik.
4. `pve-nas-01` powers on. Each
   `zfs-load-key@{tank,ssd,nvme}.service` queries Connect. If Connect
   (or its DNS, or its Traefik route) hasn't come up yet, the unit
   retries continuously under `Restart=on-failure` +
   `RestartSec=30s` until it succeeds. (`StartLimitBurst=60` over
   `StartLimitIntervalSec=3600s` is set, but each ExecStart attempt
   can take ~240s in the worst case for sequential vault+item
   Connect calls, so the burst rarely trips — the unit retries
   indefinitely rather than reaching a clean `failed` state. See
   the "Cold-cluster recovery" section below for the operator
   path if Connect itself never comes up.)
5. Pools unlock → `zfs-mount.service` succeeds → Proxmox / k3s
   workloads on pve-nas-01 (gitlab, k3s-srv-nas-01, k3s-agt-nas-01,
   plex LXC) come up.

Recovery is only needed if Connect itself can't come up — e.g. all
three k3s server VMs are offline, or external-secrets namespace is
broken, or DNS for `connect.esweiss.com` is broken. In that case,
manually unlock pve-nas-01's pools using the passphrases from the
1Password mobile app:

```bash
ssh pve-nas-01
sudo zfs load-key tank      # paste from "ZFS Pool tank Passphrase"
sudo zfs load-key ssd       # paste from "ZFS Pool ssd Passphrase"
sudo zfs load-key nvme      # paste from "ZFS Pool nvme Passphrase"
sudo zfs mount -a
```

Or scripted from a workstation with `op signin` active:

```bash
#!/usr/bin/env bash
PASSPHRASE_TANK=$(op read "op://Homelab/ZFS Pool tank Passphrase/passphrase")
PASSPHRASE_SSD=$(op read "op://Homelab/ZFS Pool ssd Passphrase/passphrase")
PASSPHRASE_NVME=$(op read "op://Homelab/ZFS Pool nvme Passphrase/passphrase")

ssh pve-nas-01 "echo '$PASSPHRASE_TANK' | sudo zfs load-key tank && \
                echo '$PASSPHRASE_SSD'  | sudo zfs load-key ssd && \
                echo '$PASSPHRASE_NVME' | sudo zfs load-key nvme && \
                sudo zfs mount -a"
```

(Once Connect is back up, the per-pool systemd units will resume
auto-unlock on subsequent reboots — no further operator action needed.)

### Clearing residual failed state after manual unlock

`zfs-load-key@<pool>.service` is `RequiredBy=zfs-mount.service`, so a
sequence of failed key-load attempts before manual recovery also
leaves `zfs-mount.service` in `failed`. (The unit retries
indefinitely under `Restart=on-failure` rather than tripping
`StartLimitBurst`, but each individual transition through `failed`
is recorded.) After the manual unlock above, the unit's
`RemainAfterExit=yes` means it stays in `active`, but the prior
failed state must be cleared explicitly so a later
`systemctl daemon-reload` or service restart doesn't re-trip on the
residual:

```bash
ssh <host>
sudo systemctl reset-failed "zfs-load-key@<pool>.service" zfs-mount.service
sudo systemctl start zfs-mount.service       # idempotent if already mounted
sudo systemctl status "zfs-load-key@<pool>.service"  # active (exited) expected
```

Repeat for each pool you unlocked manually on that host.

## LUKS rolling conversion (archive pool)

The `archive` pool predates the encryption hardening work — four
ST6000NM0024 drives with ~3 years of power-on hours and a thermal history
that disqualifies them from "create a new ZFS-native encrypted pool
from scratch." The rolling-conversion path lets us encrypt the existing
pool one drive at a time without destroying it, layering LUKS underneath
each vdev. Implementation lives in `ansible/roles/luks_archive` — sibling
to `zfs_encryption`, same Connect plumbing, different unlock primitive.

### Prerequisites

- All four archive vdevs in `ONLINE` state with no pending resilver and
  no `errors:` in `zpool status -v archive`.
- 1Password item "LUKS Archive Passphrase" created and populated with a
  random ≥32-character passphrase. Same passphrase is used for all four
  LUKS containers (threat model: drives stolen as a group).
- `task luks-archive:bootstrap` has been run at least once. Confirms the
  script + Connect token + unit template are deployed:
  ```
  ssh pve-nas-01 ls /usr/local/sbin/luks-archive-open.sh \
                     /etc/systemd/system/archive-luks-open@.service \
                     /etc/onepassword-connect/token
  ```
- Recent off-NAS backup of anything irreplaceable. Each per-drive
  resilver in the loop below briefly drops the pool to "no redundancy"
  (raidz1 minus one drive); a second drive coughing during that window
  ends the pool.

### Conversion order

`luks_archive_devices` in `host_vars/pve-nas-01.yml` lists `archive-{1..4}`.
Convert `archive-{2,3,4}` (the surviving original drives) first, finishing
with `archive-1` (the recently-resilvered replacement). That order avoids
burning an extra resilver cycle on the new drive — if we did `archive-1`
first, we'd resilver it once into the LUKS layer NOW and again later
when the other three flip over.

### Per-drive procedure

Repeat the block below four times, replacing `archive-N` and the by-id
on each pass. Stay on a single drive until its resilver completes —
parallelizing kills the pool.

```bash
# EDIT BOTH lines on every iteration — N is used only for the mapper
# name; BYID is used for every disk-side operation. Forgetting to
# update BYID will luksFormat the WRONG drive (the previous iteration's
# survivor that's already been converted, undoing the prior work).
# Pairs to use, in order:
#   N=2 / BYID=ata-ST6000NM0024-1HT17Z_Z4D1JCL6   (sde, archive-2)
#   N=3 / BYID=ata-ST6000NM0024-1HT17Z_Z4D1RQSM   (sdf, archive-3)
#   N=4 / BYID=ata-ST6000NM0024-1HT17Z_Z4D1JQBA   (sdg, archive-4)
#   N=1 / BYID=ata-SEAGATE_ST6000NM0024_Z4D2BDD2  (sdd, archive-1, last)
N=2
BYID=ata-ST6000NM0024-1HT17Z_Z4D1JCL6

# 1. Offline the chosen survivor. Pool drops to "no parity" — confirm
#    no other drive is showing read/CKSUM errors before proceeding.
sudo zpool status archive
sudo zpool offline archive "$BYID"

# 2. Wipe ZFS labels so they don't survive luksFormat. Without this,
#    if the host reboots between luksFormat and `zpool replace`, the
#    next `zpool import -d /dev/disk/by-id` could re-attach the
#    now-LUKS-headed drive to the pool via its still-readable ZFS
#    label, causing a pool-side checksum storm and complicating
#    recovery. wipefs makes the label disappear before we layer on
#    the LUKS header.
sudo wipefs -a "/dev/disk/by-id/$BYID"

# 3. Format the device. luks2 + aes-xts-plain64 + 256-bit data key
#    + argon2id KDF (with sha256 as its internal hash) — same parameters
#    cryptsetup picks by default on bookworm/trixie but pinned here so
#    the runbook stays reproducible across cryptsetup versions and
#    matches the README's short summary. Paste the passphrase from 1P
#    "LUKS Archive Passphrase" twice.
sudo cryptsetup luksFormat \
    --type luks2 \
    --cipher aes-xts-plain64 \
    --key-size 256 \
    --hash sha256 \
    --pbkdf argon2id \
    "/dev/disk/by-id/$BYID"

# 4. Open the container. Paste passphrase once more.
sudo cryptsetup open "/dev/disk/by-id/$BYID" "archive-${N}"
ls -l "/dev/mapper/archive-${N}"   # confirm the mapper exists

# 5. Replace the offline vdev with the LUKS-backed mapper.
sudo zpool replace archive "$BYID" "/dev/mapper/archive-${N}"

# 6. Wait for resilver. ~6-9h per drive at full pool size. Cluster
#    has passwordless sudo for eric so `watch -n 60 sudo` doesn't
#    re-prompt; substitute a polling loop if running elsewhere.
watch -n 60 sudo zpool status archive

# 7. After resilver completes: verify the new vdev is ONLINE and the
#    pool is clean before moving to the next drive.
sudo zpool status -v archive       # all four vdevs ONLINE, no errors
```

### After all four are LUKS-backed

```bash
# 1. Flip the host_vars from bootstrap mode to active mode and activate
#    the four archive-luks-open@<name>.service instances. The role
#    probes `cryptsetup isLuks` on each listed device — if any device
#    is still plaintext (e.g. you stopped after three drives), the
#    play fails loudly rather than enabling a unit that would block
#    next boot.
task luks-archive:apply

# 2. Verify the units are enabled and ordered correctly:
ssh pve-nas-01 'systemctl list-dependencies zfs-import-cache.service \
                | grep archive-luks-open'

# 3. Reboot pve-nas-01 to validate the cold-boot path end-to-end. Pool
#    should import automatically with no manual intervention.
sudo reboot
# After reboot:
sudo zpool status archive          # ONLINE, all four /dev/mapper/archive-N
sudo systemctl status 'archive-luks-open@archive-*.service'
                                   # all four: active (exited)
```

### Rollback (any step before step #7 of a given drive)

If something goes sideways mid-conversion on a single drive, the safe
recovery is to re-attach the original plaintext drive:

```bash
# If you reached step 5 and the resilver is in-flight, abort it first.
# `zpool replace` adds a transient `replacing-N` mirror vdev; detaching
# the replacement while resilver is mid-stream returns "no such device"
# unless we wait for ZFS to settle the vdev state. ~10-30s typical.
sudo zpool status archive | grep -A2 replacing- || true
sleep 30   # let the replace transaction quiesce

sudo cryptsetup close archive-${N}                    # if you opened it
sudo zpool detach archive /dev/mapper/archive-${N}    # if you got to replace
sudo wipefs -a "/dev/disk/by-id/$BYID"                # clear LUKS header
sudo zpool online archive "$BYID"                     # rejoin as plaintext
sudo zpool clear archive
```

The other three drives still hold the data via raidz1 parity — losing
the offline drive's plaintext content is recoverable as long as no
SECOND drive faults during the resilver back.

## Threat model recap

| Threat | Protected? |
|--------|-----------|
| Stolen offline drive (RMA, disposal, theft from rack) | Yes [^archive-rollout] |
| Stolen offline drive bundled with stolen Proxmox host (no LAN) | Yes (Connect token unusable without LAN reach to `connect.esweiss.com`) |
| Stolen running NAS still on the same LAN | No (running root extracts both token and key) |
| Compromised root via remote exploit on running host | No (same as above) |
| Compromised k3s pod (e.g. ESO controller) | Limited (Connect NetworkPolicy restricts ingress to specific service endpoints at L3/L4; any pod that already reaches Connect can still issue vault reads) |

For the running-system threat, a TPM-sealed Proxmox root with measured
boot would be the next step. Tracked in `docs/16-next-steps.md` as a
follow-up project.

[^archive-rollout]: For the **archive** pool specifically, this row is
    "Yes" only AFTER all four drives have completed the LUKS rolling
    conversion above. During the in-progress window — typically ~24-32
    hours between starting drive 1 and finishing drive 4 — drives that
    haven't been converted yet are still plaintext. `tank`, `ssd`, and
    `nvme` are encrypted at the ZFS layer from the start, so this
    caveat doesn't apply to them.

## References

- ZFS native encryption: <https://openzfs.github.io/openzfs-docs/man/master/8/zfs-load-key.8.html>
- 1Password Connect API: <https://developer.1password.com/docs/connect/connect-api-reference>
- `ansible/roles/zfs_encryption/README.md`
- `kubernetes/infrastructure/controllers/onepassword-connect/{networkpolicy,pdb}.yaml`
- `kubernetes/infrastructure/configs/onepassword-connect-{certificate,ingress}.yaml`
