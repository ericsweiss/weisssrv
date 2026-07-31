# Offsite Backup (restic → Backblaze B2) + Encrypted Swap

Nightly **offsite** backup of the high-value estate to **Backblaze B2** using
**restic** (client-side encryption) over **rclone**, plus the supporting changes
that make it work: a consolidated logical-dump landing zone, vzdump right-sizing,
and encrypted swap on the Proxmox hosts.

This is the *offsite* layer. The *local* DR layers are unchanged:

| Layer | Mechanism | Where |
|---|---|---|
| Local dataset encryption | ZFS native `aes-256-gcm` | tank/ssd (docs/32) |
| Local replication (DR copy) | `archive-backupctl` raw `zfs send -w` | archive pool (docs/06) |
| **Offsite (this doc)** | **restic client-side ciphertext** | **Backblaze B2** |
| Bare-metal VM recovery | vzdump images | tank/proxmox (local + archive) |

## Architecture

`restic_offsite` runs on **pve-nas-01** only, chained `OnSuccess=` after
`archive-backup.service` so B2 uploads the **consistent point-in-time** a
known-good archive replication just produced (a `07:15` fallback timer + an
in-script freshness guard cover the rare skipped-OnSuccess path). restic never
reads the live datasets — for each source it binds the newest `archsync-*`
snapshot at a **stable path** so its parent-snapshot optimization re-reads only
changed files instead of re-hashing the whole set each night.

- **File-walkable datasets** (`tank/backups`, `tank/share`, `ssd/appdata`,
  `ssd/databases`, `ssd/k3s-etcd`) — `mount --bind -o ro` the
  `.zfs/snapshot/archsync-*` subtree at `/mnt/restic-src/<name>`.
- **File-bearing data zvols** (`tank/immich-data/disk`,
  `tank/nextcloud-data/disk`) — a file walk can't see a live zvol, so the control
  script **clones** the newest `archsync-*` snapshot to a throwaway sibling zvol
  and mounts its ext4 read-only (`ro,noload`) at `/run/restic-offsite/<name>`.
  An **EXIT trap** unmounts + destroys every clone so a crashed run never strands
  one. **This closes the immich-photos / nextcloud-user-files offsite gap** — the
  ~2 TB photo library and NC user files now ride into B2.

### Freshness guard

A run **aborts** (metric `success=0`, no upload) if any source's newest
`archsync-*` snapshot is older than `restic_offsite_freshness_max_age_h`
(default 26h) — B2 must never upload a stale tree. Because B2 chains after a
successful archive run, a fresh snapshot is the norm.

**Archive ↔ offsite coupling.** The two tiers are coupled by this guard, by
design. restic reads only the `archsync-*` snapshots the archive run produced,
so if the `archive` pool is **exported or faulted** — e.g. you physically pull
the archive drive — new `archsync-*` snapshots stop advancing, the freshness
guard sees a newest snapshot older than 26h and **aborts** (no B2 upload), and
`ResticOffsiteStale` is the signal that the offsite tier has paused. This is the
intended behaviour: pulling the archive drive pauses B2 too, so B2 never uploads
a tree the local archive tier did not just certify. Restoring the archive pool
(re-import / replace the drive) resumes both on the next nightly chain.

## Coverage

| Live data | Offsite path | In B2? |
|---|---|---|
| authentik/mealie postgres (zvol) | `*-pg-dump` → `tank/backups/apps/<app>` | YES |
| gitlab repos+DB+secrets/config | `gitlab-backup` tar + `gitlab-secrets.json` + `gitlab.rb` → `tank/backups/apps/gitlab` | YES [^gitlab-tar] |
| immich/nextcloud postgres (zvol) | `*-backup` pg_dump → `tank/backups/apps/<app>` | YES |
| plex `/config` (ssd/appdata) | appdata walk (60G cache/metadata excluded) | YES (DB/prefs) |
| all appdata `/config` dirs | appdata walk | YES |
| **immich photo library** (`tank/immich-data/disk`) | zvol clone → file walk | **YES** |
| **nextcloud user files** (`tank/nextcloud-data/disk`) | zvol clone → file walk | **YES** |
| off-node etcd snapshots (`ssd/k3s-etcd`) | direct walk | YES |
| tank/share, tank/backups legacy | direct walk | YES |
| prometheus/loki (zvol) | — | NO (own retention, huge) |
| whole-VM images (`tank/proxmox`) | — | NO (local + archive DR only) |
| **`/etc/pve` (PVE cluster identity)** | `pve-cluster-backup.timer` tar → `tank/backups/apps/pve-cluster` | **YES** |
| tank/media | — | NO (huge, non-sensitive; Samba/local) |
| `nvme/media` (hot download tier) | — | NO (staging only; media-mover tiers it to the equally-unbacked `tank/media`) |

[^gitlab-tar]: "YES" is a claim about the *tarball*, not about the directory
    being non-empty. `gitlab.rb` and `gitlab-secrets.json` are re-copied there
    nightly, so an empty-of-tarballs landing zone still looks fresh to anything
    that only checks mtimes. That is exactly how four days of total offsite loss
    went unnoticed (`gitlab.rb`'s relocated `backup_path` never reached the Rails
    config because a `notify: Reconfigure gitlab` was lost). Guards added since:
    the `gitlab` role compares the EFFECTIVE Rails backup path and re-runs the
    reconfigure itself; the NAS artifact collector matches a per-app glob
    (`*_gitlab_backup.tar`), not any file; and `BackupArtifactEmpty` fires on any
    `*_backup_last_size_bytes == 0`. **Audit artefact NAMES, not just mtimes.**

### The logical-dump landing zone (`tank/backups/apps`)

Every zvol-backed app lands a compact **logical dump** under
`tank/backups/apps/<app>` so it rides the file walk into B2 (a file walk can't
see a live zvol's block device). Each app has its **own** NFS export
`/export/backups-apps/<app>` (bind_source `tank/backups/apps/<app>`), scoped to
that app's sole writer — per-app isolation: no client can read another app's
dumps (the GitLab secrets tarball is reachable only from .153, the Authentik
credential DB dump only from the k3s pg-dump CIDRs, and so on):

- **k3s pg-dump PVs** (authentik, mealie) mount `…:/backups-apps/<app>` by
  hostname with `xprtsec=tls` (export requires TLS; agents + servers CIDRs only).
- **App VMs** (gitlab .153, immich .157, nextcloud .156) mount their
  `…:/backups-apps/<app>` with `xprtsec=tls` — each runs tlshd via the
  `nfs_tls` role, and each export **requires** TLS from exactly its one client.
- **HAOS** (.154) is the one flow that is **plaintext on the wire**: a
  non-co-resident appliance that cannot run tlshd, so its
  `…:/backups-apps/home-assistant` write crosses the LAN in the clear. This is
  a **documented, accepted risk** (the .154 plaintext-NFS exception, docs/24) —
  its export is scoped to .154 only and the data is still `aes-256-gcm` at
  rest; HAOS simply has no way to complete the TLS handshake.

Every export **all_squashes** its writes to `1000:2000` (the 02775 subdirs), so
the git user's tarball, the root-run secrets copy, the postgres dumps, and HAOS
all land writable regardless of in-guest UID. The per-app subdirs are created by
`nas_storage` as each export's bind source (no manual prereq). Legacy machine
backups under `tank/backups` stay **unexported** — restic still captures them
via the direct dataset walk (it reads the snapshots locally and needs no export
at all).

## Security — three independent at-rest layers

1. **Local:** dumps land on `tank/backups` (`aes-256-gcm`); the vzdump target
   `tank/proxmox` is likewise `aes-256-gcm`.
2. **Archive:** `archive-backupctl` sends **raw encrypted streams** (`zfs send
   -w`) — encrypted-at-rest blobs, no key loaded.
3. **Offsite:** B2 holds **restic client-side ciphertext** (repo password =
   the `restic_repo_password` field). SSE-B2 is a redundant extra. rclone
   deletes by **hiding**; the B2 lifecycle (scripts/b2-bucket-drift.py) expires hidden versions
   at 30 days, so a capability-restricted key (no `deleteFiles`) still prunes.

## Operations — `restic-offsitectl`

Run as root on pve-nas-01 (source the env as the systemd unit does):

```bash
set -a; . /etc/restic-offsite/env; set +a
restic-offsitectl status         # timer/service + snapshots + repo stats
restic-offsitectl snapshots      # list restic snapshots
restic-offsitectl verify         # restic check (structure + subset of packs)
restic-offsitectl verify --full  # restic check --read-data (reads ALL data)
# restic-offsite-verify.timer runs `verify --auto-subset` every Sunday 12:00 —
# a rotating 1/12 read-data check (ISO week % 12), so the whole repo is re-read
# against bit-rot every ~12 weeks at bounded egress. There is no traditional
# "re-baseline": restic is content-addressed, every snapshot is logically a
# full, and the nightly forget --prune continuously repacks.
restic-offsitectl run            # run now (freshness-guarded + already-uploaded guard)
restic-offsitectl run --force    # …ignoring the already-uploaded guard
restic-offsitectl prune          # restic forget --prune (GFS retention)
```

**Two triggers, one run per generation.** `archive-backup.service` carries
`OnSuccess=restic-offsite.service` (the primary trigger) and
`restic-offsite.timer` fires at 07:15 (the fallback for a skipped OnSuccess).
Both fire every night, so `cmd_run` short-circuits when the last successful run
already covers the newest `archsync-*` source snapshot **and** every source is
present and fresh — logging `already-uploaded` and exiting 0 without touching the
metrics. Before that guard, the estate ran two full `backup` + `forget --prune`
cycles a night, and the second prune discarded the chain-verified snapshot in
favour of the fallback one. The freshness condition is load-bearing: a source
that is stale or missing (i.e. archsync itself failed) must NOT be skipped — it
falls through to the freshness guard and aborts loudly with `success=0`.

**Restore** (see also docs/17):

```bash
# Restore one source's latest snapshot into /mnt/restore/restic/<name>/<ts>
restic-offsitectl restore appdata
# Or a specific file, ad hoc:
restic restore latest --target /tmp/rtest --include /mnt/restic-src/appdata/sonarr/config.xml
```

GFS retention: `keep-daily 3, keep-weekly 2, keep-monthly 3, keep-yearly 1`.
This offsite depth (3/2/3/1) is **deliberately shallower** than the local
archive tier (`KEEP_RECENT 3` + `KEEP_MONTHLY 6`, `archive-backupctl`, docs/06):
offsite depth is cost-driven (B2 $/TB-mo), and the deeper monthly history already
lives on the local `archive` pool — both tiers coexist rather than B2 mirroring
the full archive retention. `--keep-last 5` is a WORM-ish floor on top, and
`--group-by host` is pinned (restic's default is `host,paths`, which forks a new
retention group whenever the source list changes and freezes the old group's
snapshots forever).

**Retention ceiling.** The bucket has no Object Lock, so past the hide-lifecycle
window a `forget` is unrecoverable — and both the keep policy and `--group-by`
change which snapshots are expendable (pinning to `host` *merges* groups, so
snapshots that were frozen become collectable in one step). `restic-offsitectl`
therefore dry-runs the exact policy before the destructive pass and refuses to
prune when the delete set exceeds `restic_offsite_forget_max_remove` (3):

```bash
restic-offsitectl prune                 # guarded: refuses a delete set > 3
restic-offsitectl prune --max-remove 7  # accept a deliberate, inspected delete set
```

A refusal fails the nightly run (the backup itself already landed; retention
needs an operator decision, and keeping the run red is the only way to say so).
Inspect with `restic-offsitectl snapshots` before raising the ceiling.

### Metrics / alerts

`/var/lib/node_exporter/restic_offsite.prom`
(`restic_offsite_last_run_success`, `…_last_success_timestamp_seconds`,
`…_last_run_duration_seconds`, `…_repo_size_bytes`, `…_snapshot_total_bytes`) and
`restic_offsite_verify.prom` (`…_last_verify_success`, `…_last_verify_timestamp_seconds`).
Alerts `ResticOffsiteFailed` / `ResticOffsiteStale` (kube-prometheus-stack).

The **NAS-side** `backup_artifact_last_mtime_seconds{app}` collector stats the
newest file matching that app's **artefact glob** (`nas_backup_artifact_apps[].pattern`)
under `tank/backups/apps/<app>` — the independent "a RESTORABLE dump landed
offsite-eligible" signal (alert `BackupArtifactStale`), distinct from the VM/k8s
wrappers' own "the dump ran" metrics. The glob is not cosmetic: while it matched
any file, GitLab's nightly `gitlab.rb`/`gitlab-secrets.json` copies kept the
alert green through four days in which no tarball reached the landing zone.

`BackupArtifactEmpty` closes the same class from the wrapper side: it fires on
any `*_backup_last_size_bytes == 0`, i.e. a run that reported success while
producing no artefact, regardless of what the freshness signals say.

### Restore drills

`restic check` proves the repository's structure and checksums. It does **not**
prove that a restored `pg_dump` replays, that `gitlab-backup restore` runs, or
that the encrypted HAOS tars decrypt with the key in 1Password. Integrity is not
restorability, and only a drill closes that gap.

**Quarterly drill (documented, ~1 h).** Restore one artefact per app class and
actually replay it:

1. `restic-offsitectl restore backups` — pull the logical-dump tree.
2. **Postgres class** — take the newest `authentik-*.sql.gz`, replay into a
   throwaway database (`gunzip -c … | psql -d drill_tmp`), confirm the schema
   and a row count, drop it.
3. **GitLab class** — confirm the newest `*_gitlab_backup.tar` unpacks and its
   `backup_information.yml` names the expected GitLab version. (A full
   `gitlab-backup restore` needs a scratch VM; unpack-and-inspect is the
   quarterly floor, full restore the annual one.)
4. **Home Assistant class** — confirm the newest `Automatic_backup_*.tar`
   decrypts with `backup_encryption_key` from the "Home Assistant API Token"
   1Password item. **If that field is empty, the HA tars are unrecoverable** —
   fix it before anything else.
5. **File class** — restore one known file out of `appdata` and diff it against
   the live copy.
6. Record the result: `pve-cluster-backup`-style textfile metric
   `backup_restore_drill_last_success_seconds` (write it by hand from the drill,
   `date +%s`), so a ~100-day staleness alert can page when the drill lapses —
   mirroring the `restic_offsite_last_verify_*` pattern.

The only positive restorability evidence on record so far is the one-time
post-merge smoke test below (2026-07-23: a single-file restore that came back
byte-identical). That is a start, not a drill.

## Cost

The planning estimate assumed a ~2 TB immich photo library; that was the sparse
**zvol ceiling**, not the footprint. Measured on 2026-07-25, once the tier had
been running for three nights:

| Component | planned | **measured** |
|---|---|---|
| tank/backups (legacy + apps dumps) + share + appdata configs + dumps | ~0.8 TB | ~0.6 TB (≈75% of it legacy machine backups) |
| immich photo library + nextcloud user files (zvol clones) | ~2 TB | **23 GB + 1.9 GB** |
| **Total stored (`restic_offsite_repo_size_bytes`, raw-data)** | ~2.5–2.8 TB | **621 GB** |

B2 storage $6/TB-mo ⇒ **≈$3.70/month** at the measured footprint (the
~$15–25/month figure was derived from the 2 TB ceiling). Egress is free within
3× stored/month, and this is far inside the **user-approved ~$48/month budget
envelope**. (vzdump images stay excluded — nightly-fresh, poorly dedupable
`.zst`.) Re-measure from the metric rather than re-deriving from zvol sizes:
`restic_offsite_repo_size_bytes` is on the Backup — Nightly Jobs dashboard.

Note the *shape* of the footprint: `tank/backups` is dominated by immutable
legacy machine backups (Amy-Laptop-Old 2022, Desktop-Backup 2021, …) that are
re-walked nightly. If the bill ever matters, moving those to a separate cold
prefix with its own retention is the biggest single lever.

### Effective restore depth

GFS is `keep-daily 3 / keep-weekly 2 / keep-monthly 3 / keep-yearly 1`, plus a
`keep-last 5` floor (`restic_offsite_keep_last`). The floor exists because the
bucket has **no Object Lock** (`scripts/b2-bucket-drift.py` sets
`defaultRetention: {mode: None}`) and the lifecycle expires hidden versions at 30
days: with the calendar buckets alone, corruption that persists three days walks
every daily restore point out of the window. The documented monthly/yearly depth
only materialises as the repo ages — a freshly-seeded repo has days of history,
not months, whatever the policy says. `restic-offsitectl snapshots` shows the
truth. Object Lock (governance mode) on the restic prefix remains the stronger
answer and is the recommended next step if the threat model tightens.

## Nightly-chain right-sizing (vzdump)

- **Exclude the 9 k3s VMs** from vzdump (`exclude: [202-207,222,223,227]`): they
  are IaC-rebuildable cattle (all persistent data on `backup=0` zvols, covered by
  archive + pg-dumps + B2); keeping OS-disk images also risks a **stale-member
  etcd restore** that can corrupt quorum. Saves ~200 G/night of dump I/O.
- **bwlimit 30720 → 61440** (30 → 60 MiB/s): the read-pinned window proved 30 was
  the binding constraint, and the 2026-07-13 incident's drivers (CI DinD on the
  NAS agent, memory/swap thrash) are fixed. The pve-nas-01 slice now clears
  ~03:30→~04:45, before media-mover (06:00) and archive (06:30), so
  `tank/proxmox` is no longer deferred.

**Re-measure after deploy** (one full 03:30 window): pve-nas-01 slice done by
~04:45 with `ProxmoxHostIOPressure` quiet; `archive_backup_dataset_deferred_runs{dataset="tank/proxmox"}`
back to 0. Only then consider a further raise (80+ deliberately deferred).

## Encrypted swap (Proxmox hosts)

`encrypted_swap` deploys **dm-crypt plain-mode, random-key** swap on all six
bare-metal hosts via `/etc/crypttab` (`cryptswap ← /dev/pve/swap`, `/dev/urandom`
key, `aes-xts-plain64`/`size=512` = AES-256-XTS) + `/etc/fstab`
(`/dev/mapper/cryptswap`). A fresh key each boot ⇒ on-disk swap unrecoverable
after a reboot.

Activation is **deferred to the next reboot** on all six hosts — there is no
live (running-host) switchover. On reboot `systemd-cryptsetup` opens and
`mkswap`s the mapper from crypttab and the `nofail` fstab mapper line swaps it
on; the boot finalize unit then drops the retained plaintext line. Existing
plaintext swap keeps running until then, so the deploy and a normal activation
reboot have **no swapless window** (the encrypted_swap README documents one rare
boot-race exception that can leave a host swapless until the next reboot,
surfaced by the `NASSwapGone` alert). **Reboot to activate** (compute hosts on
their next kured reboot; reboot
`pve-nas-01` when convenient). `swap-clean` is device-agnostic
(`swapoff -a`/`swapon -a`) and works transparently post-reboot; its pre-flight
skips the cycle while the mapper is still pending (see the encrypted_swap README).

## Step 0 — prerequisites (supervised, before the deploy)

Out-of-band / supervised, one-time steps this change set depends on; run them
**before** `deploy-ansible-storage`, which the checklist below assumes done.
(This change ships the offsite-backup layer alongside the Homarr app and the
Hermes OIDC cutover, so all three sets of prerequisites are gathered here.)

1. **1Password items present** (`docs/15-credential-rotation.md`) — both already
   exist; confirm before applying:
   - **B2 Archive Backup** — `b2_key_id`, `b2_application_key`,
     `restic_repo_password` (the restic repo password — **keep an OFFLINE copy;
     losing it makes the entire offsite repo undecryptable**).
   - **Homarr SSO** — the Homarr OIDC credentials (docs/41), consumed by **both**
     ESO (`homarr-secrets`) and `terraform/authentik` so they can never disagree.
2. **Mint the capability-restricted B2 key (at-merge, not post-merge).**
   Hide-only prune needs only `writeFiles`, so the restricted key ships **with**
   the deploy rather than as a later swap:
   ```bash
   b2 key create --bucket weisssrv-backup weisssrv-restic-offsite \
     listBuckets,listFiles,readFiles,writeFiles,readBucketEncryption   # no deleteFiles
   ```
   Store the returned key id / application key in **NEW fields** on the
   **B2 Archive Backup** item: `restic_key_id` / `restic_application_key` —
   do **NOT** replace the master `b2_key_id` / `b2_application_key` fields.
   The two consumers deliberately use different keys (capability split):
   - **restic/rclone** (storage deploy + `restic-offsitectl`) consume the
     `restic_*` fields — hide-only, no `deleteFiles`, tamper-resistant.
   - **scripts/b2-bucket-drift.py** (the `b2-drift-plan` CI job + the
     supervised `task b2:apply`) keeps the bucket-settings key on
     `b2_key_id`/`b2_application_key` — reconciling bucket lifecycle / SSE /
     retention needs bucket-management capabilities (`writeBuckets`,
     `readBucketRetentions` et al.) that the restricted key deliberately
     lacks. (This script replaced the terraform/b2 module: the Backblaze
     terraform provider's read path returns empty attributes against B2's
     current API, so terraform plan reported a permanent phantom diff.)
   restic/rclone keep working — they only ever hide.
3. **Supervised B2 bucket reconcile** (not terraform — `task b2:apply` runs
   `scripts/b2-bucket-drift.py --apply`, which replaced the `terraform/b2`
   module for the reason given above):
   - **`task b2:apply`** — reconcile the `weisssrv-backup` bucket settings
     (allPrivate / SSE-B2 / lifecycle) after reviewing `task b2:drift`. The
     30-day hidden-version lifecycle rule is what lets the restricted key's
     hide-only prune reclaim space.
4. **Supervised terraform apply** (plan-reviewed manual apply, no
   `-auto-approve`):
   - **`terraform/authentik`** — the Hermes dashboard OIDC cutover **and** the
     Homarr OIDC objects (provider / application / `homarr-admins` group /
     binding), docs/40.
5. **Homarr external record** — `dashboard.ericsweiss.com` is
   **external-dns-managed** (auto-created when Flux reconciles the Homarr
   ingress; there is **no** manual terraform/cloudflare step). Just confirm it
   resolves once reconciled (docs/41).
6. **Verify the NAS swap-line spelling** — the encrypted_swap fstab edits and
   crypttab source key off `encrypted_swap_source_device` (`/dev/pve/swap`).
   Confirm `grep swap /etc/fstab` on pve-nas-01 uses exactly that spelling
   (not `UUID=`/`/dev/mapper/pve-swap`); if it differs, set the var to match
   in host_vars first. A mismatch fails SAFE (the plaintext line is simply
   never retired — swap stays up, unencrypted, unfinalized) but wastes the
   activation reboot.

## Post-merge checklist (supervised, first night)

1. `deploy-ansible-storage` reconciles the per-app exports, `restic_offsite`,
   vzdump right-sizing. `nas_storage` auto-creates `tank/backups/apps/{authentik,
   mealie,gitlab,immich,nextcloud,home-assistant}` as the exports' bind sources.
2. **Deploy the three app VMs** — `task gitlab:deploy`, `task immich:deploy`,
   `task nextcloud:deploy` (in any order, after step 1): each installs tlshd
   (`nfs_tls`), mounts its per-app export over TLS, and (gitlab) activates the
   new backup service+timer. These plays are NOT part of site.yml, so the
   storage deploy alone does not wire them.
3. **Trigger every dump once, now** — don't wait for the first scheduled night.
   This validates each chain end-to-end AND seeds the metrics the new alerts
   watch, so `BackupArtifactStale` / `ResticOffsiteStale` (whose `absent()` /
   zero-mtime arms fire until a first artifact exists) go green today instead
   of paging through the first night:
   - k8s dumps: `kubectl create job --from=cronjob/authentik-pg-dump -n authentik
     authentik-pg-dump-seed` (same for `mealie-pg-dump` in `recipes`);
   - VM wrappers: `systemctl start gitlab-backup.service` (gitlab),
     `systemctl start immich-backup.service` / `nextcloud-backup.service` on
     their VMs; HA: trigger a native backup from the UI step below;
   - `/etc/pve`: `systemctl start pve-cluster-backup.service` on pve-nas-01
     (its landing dir is created by the same deploy, so its
     `backup_artifact_last_mtime_seconds{app="pve-cluster"}` series starts at 0
     and would trip the staleness arm about an hour after deploy otherwise);
   - then `systemctl start backup-artifact-collector.service` on pve-nas-01 and
     confirm every `backup_artifact_last_mtime_seconds{app=...}` series is fresh.
   - **Check artefact NAMES, not just mtimes.** The collector now matches a
     per-app glob, so `ls -lt /mnt/tank/backups/apps/*/` should show a real dump
     per app — a directory holding only `gitlab.rb` + `gitlab-secrets.json` is
     the failure this whole guard exists for. `BackupArtifactEmpty` covers the
     wrapper side (`*_backup_last_size_bytes == 0`).
4. First restic run (watch it): `restic-offsitectl run` — confirm repo init,
   freshness guard passes, zvol clones mount + tear down, snapshot created,
   metrics written.
4. **Restore smoke test:** restore a single file byte-identical.
5. `restic-offsitectl verify` — `restic check` passes.
6. Re-verify the vzdump window (§ above).
7. Confirm the relocated dumps land on `tank/backups/apps/*` (`backup_artifact_last_mtime_seconds` fresh).
8. **Verify the restricted key prunes** (it was minted at-merge in Step 0, not
   swapped in here): a `restic-offsitectl prune` completes — the hide-only
   forget/prune succeeds with **no** `deleteFiles` capability.
9. **B2 spend check** after ~a week: read `restic_offsite_repo_size_bytes` off
   the Backup — Nightly Jobs dashboard rather than re-deriving from zvol sizes
   (measured 621 GB / ≈$3.70 mo on 2026-07-25, vs the ~2.5–2.8 TB planning
   estimate that assumed a full 2 TB photo library); confirm the lifecycle is
   expiring hidden versions at 30 days.
10. **Reboot pve-nas-01 to activate encrypted swap** (timing is convenience,
    not emergency): on the defer path the plaintext swap line is retained
    alongside the mapper line, and swap-clean's pre-flight skips its cycle
    while the mapper is pending — so there is **no swapless window**; until
    the reboot, swap simply remains **unencrypted**. After the reboot,
    verify the cutover: `swapon --show` lists `/dev/mapper/cryptswap` (and
    nothing else), and the plaintext `/dev/pve/swap` fstab line is commented
    out (the finalize unit's work). If the mapper did not come up, check
    `systemctl status systemd-cryptsetup@cryptswap` — the plaintext swap
    stays active as the fallback and `NASSwapGone` guards the zero-swap case.

### One-time Home Assistant native-backup UI step (offsite for HA)

vzdump already captures the whole HAOS VM image (local + archive DR). For a
**granular offsite** HA restore path, point HAOS's native scheduled backup at the
NFS target this MR provisions:

1. Home Assistant → **Settings → System → Storage → Add network storage**:
   - Name: `nas_backup` (the field allows only alphanumerics and underscores), Usage: **Backup**
   - Server: `192.168.0.102` (pve-nas-01), Protocol: **NFS**, NFS version **4**
   - Remote share path: `/backups-apps/home-assistant`
   (HAOS mounts NFS plaintext — the documented .154 exception, docs/24.)
2. **Settings → System → Backups → Automatic backups**: schedule a backup and set
   its **location** to the `nas_backup` network storage.
3. **Save the backup encryption key offsite**: automatic backups are
   `protected: true` (encrypted); download the emergency kit (Backups → ⋮) and
   store the key as `backup_encryption_key` on the **Home Assistant API Token**
   1Password item — without it the offsite tars are undecryptable in DR.

Its freshness is then covered by the NAS-side `BackupArtifactStale`
(`app="home-assistant"`) mtime alert. If deferred, HA offsite coverage remains
"whole-VM image, local only" — acceptable.

## See also

- `docs/17-disaster-recovery.md` — restic/B2 restore procedures.
- `docs/06-zfs.md`, `docs/32-zfs-encryption.md` — local layers.
- `docs/15-credential-rotation.md` — the `B2 Archive Backup` 1Password item.
- `docs/12-runbooks.md` — backup-and-recovery runbook entries.
