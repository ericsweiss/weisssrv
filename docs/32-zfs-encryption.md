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

The model is **per-dataset encryption roots, not pool-level**. The tank /
ssd pool roots stay plaintext; individual child datasets that hold
sensitive data are each their own encryption root (the dataset name equals
its `encryptionroot`), all sharing the one per-pool passphrase. New
datasets are created encrypted from the start (§2a); the datasets that
predated the rollout were migrated in place via `zfs send | zfs recv` +
`zfs change-key`.

At boot `zfs-load-key.sh` enumerates the encryption roots under the pool
(`zfs get -r encryptionroot <pool>`, keeping the rows where name == value)
and loads the per-pool passphrase into each one individually. It does
**not** use `zfs load-key -r <pool>`: `-r` reads the passphrase from stdin
only once, so with more than one encryption root it satisfies the first and
fails the rest with "encryption failure". Inheriting children (datasets
that are not their own root) are unlocked automatically when their root is
loaded. The role's per-pool entry in `zfs_encryption_pools` (e.g.
`name: tank`) therefore covers every encrypted descendant without the pool
root itself being encrypted.

### Encrypted (post-rollout)

All encrypted datasets are their own encryption root (Model B), so the
dataset names — and therefore every `/dev/zvol` path, Proxmox storage
reference, and NFS export path — are unchanged by encryption.

- **NAS (`pve-nas-01`)**:
  - `tank/share` (LAN file share) — migrated via send/recv.
  - `tank/proxmox` (Proxmox VM backup target, ~3T referenced) — migrated.
  - `tank/backups` (765G) — migrated via send/recv.
  - `tank/nextcloud-data` — created encrypted; holds the Nextcloud VM's
    passthrough zvols (docs/35).
  - `tank/immich-data` — created encrypted; holds the Immich photo library
    zvol (docs/36).
  - `ssd/appdata` and its children (`authentik` + `authentik/postgres` are
    their own roots; `gitlab`, `loki`, `mealie`, `prometheus` inherit from
    `ssd/appdata`) — every app PV / DB. Migrated one at a time.
  - `ssd/databases` — migrated.
  - `ssd/pve` (`vm-153-disk-0` + `vm-153-cloudinit`, the GitLab VM disks) —
    migrated; the Proxmox storage `ssd` reference is unchanged.
  - `ssd/k3s-etcd` — own root; holds the off-node k3s etcd snapshot copies
    (docs/17). Created encrypted at activation (the copies are full cluster
    state, so they must not land plaintext on the NAS).

  GitLab's backup tarball lands on the NFS mount `/mnt/backups-offsite`
  (= `tank/backups/apps/gitlab`), which is an encryption root, so it is
  encrypted at rest and offsite-eligible (docs/17, docs/42). Proxmox VM
  snapshots of the GitLab VM land in `tank/proxmox` (covered above).

### Not encrypted (by design)

- **The media domain** — `tank/media` (the ~14.5T Plex library) plus all of
  `nvme` (`nvme/media` hot tier, `nvme/fast` transcode scratch, `nvme/pve`
  ephemeral images). `/mnt/media` is a mergerfs union of `nvme/media` +
  `tank/media`, so the two tiers are one logical store. Media is
  non-sensitive (LAN-trust is fine); the only way to encrypt the 14.5T
  `tank/media` tier is a multi-day `zfs send | zfs recv` (there is no
  in-place ZFS encryption) that has destabilized this host under sustained
  load, and AES would tax the streaming/transcode hot path for no at-rest
  benefit. Encryption also breaks `zfs send` to off-pool replicas without
  `-w`.
- `tank/pve` — ephemeral Proxmox VM/LXC images. (`tank/proxmox` is in
  the encrypted list above because it holds VM backup tarballs that
  contain persistent app state.)
- `archive` pool — the pool itself is plaintext, but the eight replicated
  datasets (`archive/{share,backups,nextcloud-data,proxmox,immich-data,appdata,
  databases,k3s-etcd}`) now arrive as **raw** `zfs send -w` streams from their
  encrypted tank/ssd sources (`archive-backupctl`), so that backup data is
  encrypted at rest under the source's own key — archive never loads a key, and
  a restore needs `zfs load-key`. (`ssd/appdata` holds zvol children — the app
  DB/data volumes — so the per-dataset re-seed receive uses `-o readonly=on` for
  volumes, since a per-dataset `-o mountpoint`/`canmount` override on a zvol
  receive is rejected; the `-R` initial/incremental paths avoid this because
  `zfs receive -o` applies the override only to the stream's top-level (filesystem)
  dataset, never to the zvol descendants.) (Sending the now-encrypted sources non-raw is impossible with
  `-R`: ZFS rejects sending an encrypted dataset with properties unless raw.)
  This gives the replicated data native at-rest encryption — raw `zfs send -w`
  replication IS the at-rest protection for those eight archive datasets (the
  archive pool itself loads no key); only archive data outside those eight
  datasets stays plaintext. Raw replication
  preserves the source's compression (ZFS compresses before it encrypts), so the
  encrypted archive copies are the same size as the sources — not larger. The
  one-time raw re-seed copies only each dataset's **current snapshot**, not its
  full history: `zfs send -R` would replicate the source's entire snapshot set
  (incl. zfs-auto-snap churn the archive doesn't keep — e.g. `tank/proxmox`
  carries ~46 snapshots vs the ~9 `archsync` the archive retains, ~7.8T vs
  ~3.1T) and overflow the pool. The `archsync` history rebuilds forward from the
  re-seed via the normal incrementals. **Never `zfs load-key` + mount an
  `archive/<dataset>` in place** — it dirties the raw incremental chain and
  forces a full re-seed; to read a backup, restore it to a `*-restore-*` clone
  (`archive-backupctl restore <target>`) and load the key there.
- **Compute nodes' `local-ssd` pools (5×)** — host the k3s VM disks
  (servers + agents) and HA-managed LXC subvols. Encrypting these
  would create an unrecoverable cold-boot deadlock: Connect runs in
  k3s, k3s VMs live on `local-ssd`, and `local-ssd` would need
  Connect to unlock.

  **What keeps Connect off an encrypted disk is a design rule, not
  scheduling: no k3s node boots from an encrypted drive — by plan.** Every
  server and agent root disk sits on an unencrypted pool (`local-ssd` on the
  five compute hosts, `local-lvm` on pve-nas-01, see below), so wherever the
  scheduler places Connect — its HelmRelease selects `esweiss.com/cpu: modern`
  and spreads replicas with a *preferred* anti-affinity (a required one would
  leave a replica Pending on three eligible nodes) — no placement can land it
  behind an unlock. Encrypting any node's root pool would re-create the
  deadlock even though nothing in the manifests would complain, so a new node
  inherits the rule with its storage. The controls for the residual **drive theft from a
  powered-down host** threat are the drive-wipe SOP in
  `docs/15-credential-rotation.md` (every drive that leaves a compute host is
  wiped or destroyed) plus physical access control; the residual exposure is
  accepted as low-probability for a homelab. Note that k3s
  `secrets-encryption` does **not** meaningfully protect against that
  drive-theft scenario: its AES key is stored unencrypted at
  `/var/lib/rancher/k3s/server/cred/encryption-config.json`, on the same
  unencrypted `local-ssd` as the etcd datastore — so a stolen powered-down
  server disk yields both the encrypted Secrets and the key that decrypts them.
  Its real value is limited to cases where an etcd *snapshot* is separated from
  the key, and those off-node snapshots already sit on the ZFS-encrypted
  `ssd/k3s-etcd` dataset (above).

  **Blast radius of one stolen compute drive** — etcd is not the whole story, and
  the drive-wipe SOP has to be scoped to all of it:

  | On the pool | What it holds |
  |---|---|
  | `subvol-150` / `subvol-160` (dns-01 on pve-prec-01, dns-02 on pve-opt-03) | The `*.esweiss.com` wildcard **private key** at `/opt/AdGuardHome/certs/privkey.pem` — which can impersonate every internal service, the exact reasoning `host_vars/dns-01.yml` uses to keep it off the k3s agents — plus the AdGuard admin credential |
  | `subvol-151` (smtp-relay on pve-opt-01) | `/etc/postfix/sasl_passwd` with the Gmail app password, and the local sasldb |
  | `vm-154-disk-0` (Home Assistant OS) | Every home-automation integration token and long-lived access token in `.storage` |
  | k3s server/agent disks | The etcd datastore, `encryption-config.json`, the server token and the cluster CA (above) |

  Rotating after a lost or unwiped compute drive therefore means the wildcard
  cert, the Gmail app password and the HA tokens as well as the k3s material —
  see `docs/15-credential-rotation.md`.

- **pve-nas-01's `local-lvm`** (LVM-thin pool in VG `pve`, carved out of
  partition 3 of the Proxmox boot NVMe — not ZFS, no dm-crypt layer; only
  `/dev/pve/swap` is mapped through `cryptswap`). It carries two guests: VM 202
  (k3s-agt-nas-01) and VM 222
  (k3s-srv-nas-01). VM 222 is an **etcd quorum member**, so the same cold-boot
  reasoning as the compute pools applies with more force: it must never sit
  behind an unlock, or cluster quorum would wait on the NAS. Note pve-nas-01 has
  no `local-ssd` pool (the storage id is defined for the five compute hosts
  only), and that VM 202 appears in `zfs_encryption_guest_vmids` because its
  passthrough data zvols live on the encrypted `ssd/appdata` — that list is
  start ordering, not a claim about a root disk.

  CT 152 (plex) and CT 158 (immich-ml) carry no quorum constraint, so their
  rootfs lives on the encrypted `ssd` pool (`proxmox_lxc_storage: ssd` in
  their host_vars; each migrated off `local-lvm` once with
  `pct move-volume <id> rootfs ssd --delete`, stopped). The trade accepted
  with the move is the unlock dependency: both CTs are in
  `zfs_encryption_guest_ctids`, started by pve-start-encrypted-guests after
  the unlock rather than by pve-guests at boot. CT 152's sensitive `/config`
  was already a bind from the encrypted `ssd/appdata/plex`; the move closes
  the remaining rootfs gap for both.

## Architecture

Key-load is deliberately OFF the early-boot critical path. The box
always boots to ssh/Tailscale on plaintext alone; encrypted data, nfsd's
encrypted exports, and encrypted-storage guests converge LATE and async.

```
┌──────────────────────────────────────────────────────────────────┐
│ pve-nas-01 (boot)                                                 │
│   zfs-import.target ─> zfs-mount.service (EARLY): `zfs mount -a`  │
│       mounts PLAINTEXT datasets, SKIPS still-locked ones, exit 0  │
│       ─> local-fs.target ─> multi-user.target  ✓ ssh + Tailscale  │
│          UP (sshd/tailscaled on unencrypted root — never gated)   │
│                                                                    │
│   network-online.target                                            │
│      |                                                            │
│      v                                                            │
│   zfs-load-key@{tank,ssd}.service  (After=network-online; retries)│
│      - read /etc/onepassword-connect/token                        │
│      - HTTPS GET connect.esweiss.com/v1/... ; parse passphrase    │
│      - zfs load-key into each encryption root  (NO mount here)    │
│      |                                                            │
│      v                                                            │
│   zfs-mount-encrypted.service  (LATE anchor; WantedBy=multi-user, │
│      retries forever until all keystatus=available, then          │
│      `zfs mount -a`)  ── never Before any boot target             │
│      |                         |                                  │
│      v                         v                                  │
│   nfs-server (drop-in:    pve-start-encrypted-guests.service:     │
│   After=+RequiresMountsFor   qm/pct start the gated guest list    │
│   encrypted exports)         (VM 222/etcd is NOT gated — early)   │
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

Generate with `op item generate-password --length 64`.

(No `local-ssd` entry — compute hosts' pools are intentionally not
encrypted. See "What is encrypted, what isn't" above.)

Plus one shared item for the Connect access token used by all hosts:

| Item title | Field name | Source |
|------------|------------|--------|
| `ZFS Encryption Connect Token` | `credential` | `op connect token create weisssrv-zfs --server <id> --vaults Homelab` |

**Scope caveat.** That token grants Connect read access to the **whole**
`Homelab` vault, while it only ever needs the two pool-passphrase items — a
plaintext file at `/etc/onepassword-connect/token` (0400 root) on every
encryption host, so a host compromise reads the vault, not just the
passphrases. The role takes the vault as an input
(`zfs_encryption_connect_vault`, default `Homelab`), so narrowing it is a
1Password-side change plus that variable: put the passphrase items in their own
vault, grant the **Connect server** access to it (a token cannot reach a vault
its server was not granted), mint a token scoped to it, and set the variable.
Until that is done, treat the token as vault-equivalent — the same exposure as a
leaked Connect admin credential.

## Rollout procedure

### Step 1: Deploy the host-side mechanism (no-op until pools exist)

The role can be deployed safely before any pool is actually encrypted:
it installs the script, the systemd unit template, and the Connect
token; nothing runs until `zfs_encryption_pools` is non-empty for that
host.

```bash
# Make sure Connect HA + the connect.esweiss.com IngressRoute are
# already deployed; the relevant manifests live in
# kubernetes/infrastructure/controllers/onepassword-connect/ (HelmRelease
# + replicas:2 + PDB) and kubernetes/infrastructure/configs/onepassword-connect-{certificate,ingress}.yaml.
task flux:status

# Generate Connect token + place in 1P
op connect token create weisssrv-zfs --server <connect-id> --vaults Homelab
# -> paste output into "ZFS Encryption Connect Token" / credential

# Deploy the role on every Proxmox host. On a host with an empty
# zfs_encryption_pools this deploys the mechanism only (no token, no
# enabled units); on a host with pools it also activates the boot units.
task zfs:encrypt
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
- No pools changed; no per-pool services enabled on hosts with an empty
  pool list

### Step 2: Encrypt data

There is no in-place ZFS encryption: a dataset is either **created
encrypted**, or its data is copied into an encrypted dataset. Prefer the
former — encryption is baked into dataset creation, not bootstrapped on
afterward.

#### 2a. New datasets — create encrypted from the start (preferred)

When adding a dataset to a pool already in the encrypted set (`tank`,
`ssd`), create it as its own encryption root with the per-pool passphrase.
There is no separate "migrate later" step:

```bash
# Paste the passphrase from 1P "ZFS Pool <pool> Passphrase" at the prompt.
sudo zfs create \
  -o encryption=aes-256-gcm \
  -o keyformat=passphrase \
  -o keylocation=prompt \
  -o mountpoint=/mnt/ssd/appdata/<app> \
  ssd/appdata/<app>
```

The dataset's name equals its `encryptionroot`, so the boot unit's
per-root loop (see Architecture) unlocks it automatically — nothing else to
configure. Record it in docs/06's pool layout, and if it backs a VM zvol,
in `vm_additional_disks` in `hosts.yml`.

#### 2b. Migrating an existing plaintext dataset (reference)

This is how the pre-rollout datasets were encrypted **in place while
preserving their names** (so `/dev/zvol` paths, Proxmox storage refs, and
NFS export paths did not change — "Model B"). One dataset at a time, with
the consumer stopped.

```bash
SRC=ssd/appdata/authentik          # existing plaintext dataset
ENCPARENT=ssd/enc                  # temporary encrypted staging root
NAME=$(basename "$SRC")
MNT=$(zfs get -H -o value mountpoint "$SRC")

# 1. Stop the consumer so the data is quiescent.
kubectl scale deploy/authentik-server -n authentik --replicas=0

# 2. Snapshot.
sudo zfs snapshot -r "${SRC}@enc-migrate"

# 3. Encrypted staging root (once per pool), keyed with the per-pool
#    passphrase from 1P "ZFS Pool <pool> Passphrase".
sudo zfs list "$ENCPARENT" 2>/dev/null || sudo zfs create \
  -o encryption=aes-256-gcm -o keyformat=passphrase -o keylocation=prompt \
  -o mountpoint=none -o canmount=off "$ENCPARENT"

# 4. send | recv into the staging root, EXCLUDING the encryption property so
#    the copy INHERITS the parent's key (and is therefore encrypted).
#    Without `-x encryption`, `send -R` replays the source's encryption=off
#    and you get an UNENCRYPTED copy — the most common mistake here.
sudo zfs send -R "${SRC}@enc-migrate" | \
  sudo zfs recv -x encryption "${ENCPARENT}/${NAME}"

# 5. Make the copy its OWN encryption root so it can be renamed out of the
#    staging parent (an inheriting child cannot be renamed across roots).
sudo zfs change-key -o keyformat=passphrase -o keylocation=prompt \
  "${ENCPARENT}/${NAME}"      # paste the same per-pool passphrase

# 6. Preserve the name: park the plaintext original, promote the encrypted
#    copy into its place, restore the mountpoint, clear readonly.
sudo zfs set readonly=off -r "${ENCPARENT}/${NAME}"
sudo zfs rename "${SRC}" "${SRC}-pre-enc"
sudo zfs rename "${ENCPARENT}/${NAME}" "${SRC}"
sudo zfs set mountpoint="${MNT}" "${SRC}"
sudo zfs mount "${SRC}"

# 7. Bring the consumer back and verify it works against the encrypted data.
kubectl scale deploy/authentik-server -n authentik --replicas=1

# 8. Only after verifying, destroy the parked plaintext copy + its snapshot.
sudo zfs destroy -r "${SRC}-pre-enc"
```

> **NFS submounts:** if the dataset is exported, redo the `/export` bind
> (`mount --rbind`) AFTER the dataset is read-write — `mount --rbind`
> captures the read-only flag at bind time. Never `systemctl stop` an
> `export-*.mount` unit; stopping it cascades to `nfs-server`. Always
> confirm `systemctl is-active nfs-server` after any storage surgery.
>
> **Large datasets:** a multi-hour `send | recv` over SSH will drop; run it
> server-side under `nohup`/`systemd-run`. Sustained multi-TB encrypted
> send/recv has destabilized this host — stage big migrations and avoid
> encrypting the 14.5T media tier (see "Not encrypted").

### Step 3: Activate per-pool boot units

For each host that now has at least one encrypted pool, set
`zfs_encryption_pools` in its `host_vars/<host>.yml` and re-run
`task zfs:encrypt`:

```yaml
# ansible/inventories/prod/host_vars/pve-nas-01.yml
zfs_encryption_pools:
  - name: tank
    item: "ZFS Pool tank Passphrase"
  - name: ssd
    item: "ZFS Pool ssd Passphrase"
```

```bash
task zfs:encrypt -- --limit pve-nas-01
```

This enables `zfs-load-key@<pool>.service` for each listed pool. A
reboot test should now show keys auto-loaded; `zfs get keystatus
<pool>` returns `available`.

### Step 4: Verify and rotate

```bash
# Verify auto-load works (a cold reboot is the real test; this re-runs the
# units, which no-op on already-loaded roots):
sudo systemctl restart 'zfs-load-key@*.service'
sudo zfs get -r keystatus tank ssd   # every encryption root => available

# Rotate the passphrase (yearly, or after suspected exposure). Each dataset is
# its own encryption root (Model B) and the plaintext pool root is NOT a key
# holder, so change-key every encryption root in the pool with the new
# passphrase — `zfs change-key tank` alone would fail ("not an encryption
# root"):
for root in $(zfs get -H -t filesystem,volume -o name,value -r encryptionroot tank \
                | awk -F'\t' '$1==$2{print $1}'); do
  sudo zfs change-key -o keyformat=passphrase -o keylocation=prompt "$root"
done
# Update "ZFS Pool tank Passphrase" in 1P with the new value.
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
   `zfs-load-key@{tank,ssd}.service` queries Connect. If Connect
   (or its DNS, or its Traefik route) hasn't come up yet, the unit
   retries continuously under `Restart=on-failure` +
   `RestartSec=30s` until it succeeds. (`StartLimitBurst=60` over
   `StartLimitIntervalSec=3600s` is set, but each ExecStart attempt
   can take ~240s in the worst case for sequential vault+item
   Connect calls, so the burst rarely trips — the unit retries
   indefinitely rather than reaching a clean `failed` state. See
   "Recovery is only needed if Connect itself can't come up"
   below for the operator path.)
5. Keys load → `zfs-mount-encrypted.service` (the late anchor) mounts the
   encrypted datasets on its next retry → nfsd (drop-in, ordered after it)
   serves the real datasets, and `pve-start-encrypted-guests.service` starts
   the gated guests (below). `k3s-srv-nas-01` (VM 222, etcd) is NOT gated — it
   has no encrypted disk and starts early on its normal onboot path, so cluster
   quorum never waits on this NAS's unlock. **Boot itself never waits on any of
   this** — the box reached multi-user (ssh + Tailscale) back at step 4's
   plaintext mount.

### The gated guest list

Which guests wait for the unlock is inventory, not code:
`zfs_encryption_guest_vmids` and `zfs_encryption_guest_ctids` in
`ansible/inventories/prod/host_vars/pve-nas-01.yml`. Read them there rather than
from any list in prose — today they are VMs 202 (k3s-agt-nas-01), 153 (gitlab),
156 (nextcloud), 157 (immich), 155 (windows) and CT 152 (plex).

Two rules govern the list:

- **Membership means "has a disk or passthrough zvol on an encrypted dataset"**,
  not "has an encrypted root". VM 202's root is on `local-lvm`; it is here
  because its data zvols live on `ssd/appdata`.
- **Order is start order, and Windows (155) is deliberately last.** It is also
  the reason VM 155 carries `onboot=0`: it must not be started by Proxmox before
  the unlock, and this unit is what starts it afterwards. Removing it from the
  list stops it auto-starting — do **not** "fix" that by setting `onboot=1`
  (docs/39).

Recovery is only needed if Connect itself can't come up — e.g. all
three k3s server VMs are offline, or external-secrets namespace is
broken, or DNS for `connect.esweiss.com` is broken. In that case,
manually unlock pve-nas-01's pools using the passphrases from the
1Password mobile app:

Each dataset is its own encryption root (Model B), all sharing the per-pool
passphrase; load the passphrase into every root (the plaintext pool root is
not a key holder, so `zfs load-key tank` would fail). Manually, pasting each
pool's passphrase from the 1Password mobile app when prompted:

```bash
ssh pve-nas-01
for pool in tank ssd; do
  read -rsp "Passphrase for $pool: " POOL_PASS; echo
  for root in $(zfs get -H -t filesystem,volume -o name,value -r encryptionroot "$pool" \
                  | awk -F'\t' '$1==$2{print $1}'); do
    printf '%s\n' "$POOL_PASS" | sudo zfs load-key "$root"
  done
done
```

(No `zfs mount -a` needed — `zfs-mount-encrypted.service` mounts on its next
30s retry once the keys are present.)

Or scripted from a workstation with `op signin` active (the passphrase is piped
over stdin, so it never lands in a remote process's argv):

```bash
#!/usr/bin/env bash
set -euo pipefail
for pool in tank ssd; do
  op read "op://Homelab/ZFS Pool ${pool} Passphrase/passphrase" \
    | ssh pve-nas-01 "pass=\$(cat); for root in \$(zfs get -H -t filesystem,volume -o name,value -r encryptionroot ${pool} | awk -F'\t' '\$1==\$2{print \$1}'); do printf '%s\n' \"\$pass\" | sudo zfs load-key \"\$root\"; done"
done
```

After the keys load, the late retrying units converge on their own —
`zfs-mount-encrypted.service` mounts the datasets, then nfsd and
`pve-start-encrypted-guests.service` follow. To avoid waiting for the 30s
retry cadence, nudge them once:

```bash
ssh pve-nas-01 "sudo systemctl start zfs-mount-encrypted.service pve-start-encrypted-guests.service"
```

(Once Connect is back up, the units resume auto-unlock on subsequent reboots —
no operator action needed.)

### No residual failed state to clear

Unlike the old `RequiredBy=zfs-mount.service` design, a locked boot no longer
puts `zfs-mount.service` into `failed` (the early mount just skips the locked
datasets and exits 0). After a manual `zfs load-key`, nothing needs a
`reset-failed`: the next `zfs-mount-encrypted.service` retry sees
`keystatus=available` and mounts. If you want to confirm convergence:

```bash
ssh pve-nas-01
systemctl is-active zfs-mount-encrypted.service     # active once mounted
zfs get -H -o value mounted ssd/appdata tank/share  # yes
ss -ltn 'sport = :2049'                              # nfsd listening
qm status 153; qm status 202; pct status 152         # running
```

### Leftover `zfs-mount.service.requires/` symlinks (one-time check)

The `RequiredBy=zfs-mount.service` design left `zfs-load-key@<pool>.service`
symlinks in `/etc/systemd/system/zfs-mount.service.requires/`. **The role no
longer sweeps that directory** — it stopped creating the dependency, but it does
not remove symlinks a previous revision installed, and a surviving one restores
the ordering cycle this whole design exists to break (`zfs-mount.service` fails
at boot, and with it the guest starts that depend on the mounts).

Verified clean on all six Proxmox hosts on **2026-08-10**; the one leftover
(an empty directory on pve-nas-01) was removed. It is not self-healing, so
re-check after rebuilding or re-imaging a host, or after restoring an older
`/etc`:

```bash
ansible proxmox -m shell -a \
  'ls -1 /etc/systemd/system/zfs-mount.service.requires/ 2>/dev/null || echo CLEAN'
# Anything listed: rm the symlink, then `systemctl daemon-reload`.
```

## Threat model recap

| Threat | Protected? |
|--------|-----------|
| Stolen offline drive (RMA, disposal, theft from rack) | Yes — `tank`/`ssd` are ZFS-encrypted; `archive`'s eight replicated datasets are raw-encrypted under their source keys (`nvme` and `tank/media` are plaintext by design) |
| Stolen offline drive bundled with stolen Proxmox host (no LAN) | Yes (Connect token unusable without LAN reach to `connect.esweiss.com`) |
| Stolen running NAS still on the same LAN | No (running root extracts both token and key) |
| Compromised root via remote exploit on running host | No (same as above) |
| Compromised k3s pod (e.g. ESO controller) | Limited (Connect NetworkPolicy restricts ingress to specific service endpoints at L3/L4; any pod that already reaches Connect can still issue vault reads) |

For the running-system threat, a TPM-sealed Proxmox root with measured
boot would be the next step. Tracked in `docs/16-next-steps.md` as a
follow-up project.

## Out of scope: host swap

Swap is **not** a ZFS dataset and is not handled by this role. All six Proxmox
hosts run dm-crypt plain-mode AES-256-XTS swap with a random key regenerated on
every boot (`encrypted_swap` role → `/dev/mapper/cryptswap`), which needs no
1Password key material and no unlock step. Details and the activation-reboot
caveat: `docs/42-offsite-backup.md` § Encrypted swap and
weisssrv-lib `ansible_collections/weisssrv/infra/roles/encrypted_swap/README.md`; the at-rest posture table in
`docs/06-zfs.md` carries the summary row.

## Related documentation

- ZFS native encryption: <https://openzfs.github.io/openzfs-docs/man/master/8/zfs-load-key.8.html>
- 1Password Connect API: <https://developer.1password.com/docs/connect/connect-api-reference>
- weisssrv-lib `ansible_collections/weisssrv/infra/roles/zfs_encryption/README.md`
- `kubernetes/infrastructure/controllers/onepassword-connect/{networkpolicy,pdb}.yaml`
- `kubernetes/infrastructure/configs/onepassword-connect-{certificate,ingress}.yaml`
