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
    (`*_gitlab_backup.tar`), not any file; `BackupArtifactEmpty` fires on any
    wrapper-side `*_backup_last_size_bytes == 0`, and `BackupArtifactZeroBytes`
    on the NAS-side `backup_artifact_last_size_bytes == 0` (an artefact that
    landed with a fresh mtime and no content). `gitlab-secrets.json` is covered
    separately by `GitLabBackupSecretsMissing` — it is excluded from both the
    tarball and the artefact glob, so nothing else sees it.
    **Audit artefact NAMES, not just mtimes.**

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
# a rotating 1/12 read-data check driven by a PERSISTED cursor
# (restic_offsite_verify_group in restic_offsite_verify.prom), advanced only on
# success. A wall-clock form (ISO week % 12) would skip a group for a full cycle
# whenever a week failed or the timer did not run; the cursor retries it.
# There is no traditional "re-baseline": restic is content-addressed, every
# snapshot is logically a full, and the nightly forget --prune continuously
# repacks.
restic-offsitectl run            # run now (freshness-guarded + already-uploaded guard)
restic-offsitectl run --force    # …ignoring the already-uploaded guard
restic-offsitectl prune          # restic forget --prune (GFS retention)
restic-offsitectl unlock         # drop a stale lock left by a crashed run
restic-offsitectl drill          # restore-drill on demand (see Restore drills)
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

GFS retention: `keep-daily 7, keep-weekly 2, keep-monthly 3, keep-yearly 1`.
This offsite depth (7/2/3/1) is **deliberately shallower** than the local
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

A ceiling refusal does **not** fail the nightly run: the snapshot already landed,
so the run exits 0 (`restic_offsite_last_run_success 1`) and says so through
`restic_offsite_retention_blocked 1` + `restic_offsite_last_prune_success 0`
(alert `ResticOffsitePruneBlocked`, 48h). The CLI exits **90** on a refusal. What
*does* fail the run is an unusable dry-run or a `forget --prune` that crashed —
those leave `retention_blocked 0`, so `ResticOffsitePruneFailed` and
`ResticOffsiteFailed` are the ones that fire. Inspect with
`restic-offsitectl snapshots` before raising the ceiling.

### Repository locks (rc=11)

An interrupted run leaves a repository lock in the repo. A **shared** lock lets
plain backups keep succeeding, so snapshots keep landing while every *exclusive*
operation — `forget`/`prune` and `check` — fails with **rc=11**, reported as
`repository lock` in the journal. That is what happened on 2026-07-27: a crashed
run's lock silently disabled retention and integrity verification for ~14 days
while backups looked healthy, and the resulting alerts went unactioned for six
days because "backups are landing" reads as fine.

The durable fix ships in the role: every restic invocation carries
`--retry-lock` (`restic_offsite_retry_lock`, 15m), and a **pre-flight reaper**
removes a lock owned by a PID that is dead **on this host** and older than
`restic_offsite_stale_lock_min_age_h` (6h), logging loudly when it does.

`restic-offsitectl unlock` runs the same reaper on demand and is deliberately
opinionated: it exits **non-zero when locks remain**, naming why each was
declined (held by another host, PID still alive here, unparseable timestamp,
inside the staleness window). A repository it cannot read at all reports that as
its own verdict rather than "no locks" — an unreachable bucket or a rotated repo
password must not read as success. So a human still unlocks by hand only when
the reaper declined and the holder is genuinely gone (e.g. the run died on
another host).

### Metrics / alerts

`/var/lib/node_exporter/restic_offsite.prom`: `restic_offsite_last_run_success`,
`…_last_run_duration_seconds`, `…_last_success_timestamp_seconds`,
`…_last_backup_success`, `…_last_backup_timestamp_seconds`,
`…_last_prune_success`, `…_retention_blocked`, `…_retention_pending_removals`,
`…_repo_size_bytes`, `…_snapshot_total_bytes`.
`restic_offsite_verify.prom`: `…_last_verify_success`,
`…_last_verify_timestamp_seconds`, `…_verify_group`, `…_verify_groups`.
`backup_restore_drill.prom`: `backup_restore_drill_last_success_seconds`,
`…_last_run_seconds`, `…_files_compared`.

Alerts (all in `kubernetes/infrastructure/observability/rules/scripts.yaml`):
`ResticOffsiteFailed` / `ResticOffsiteFailedProlonged`, `ResticOffsiteStale` /
`ResticOffsiteStaleCritical`, `ResticOffsiteVerifyFailed`,
`ResticOffsiteVerifyStale` / `ResticOffsiteVerifyStaleCritical`,
`ResticOffsitePruneBlocked`, `ResticOffsitePruneFailed`,
`ResticOffsiteRepoShrank`, `BackupRestoreDrillStale`,
`BackupRestoreDrillNeverRan`.

The **NAS-side** `backup_artifact_last_mtime_seconds{app}` collector stats the
newest file matching that app's **artefact glob** (`nas_storage_backup_artifact_apps[].pattern`)
under `tank/backups/apps/<app>` — the independent "a RESTORABLE dump landed
offsite-eligible" signal (alert `BackupArtifactStale`), distinct from the VM/k8s
wrappers' own "the dump ran" metrics. The glob is not cosmetic: while it matched
any file, GitLab's nightly `gitlab.rb`/`gitlab-secrets.json` copies kept the
alert green through four days in which no tarball reached the landing zone.

Size and companion coverage, because a fresh mtime is not a restorable backup:

- `BackupArtifactEmpty` — wrapper side, any `*_backup_last_size_bytes == 0`
  (the run reported success while producing no artefact).
- `BackupArtifactZeroBytes` — NAS side, `backup_artifact_last_size_bytes == 0`
  with a non-zero mtime, i.e. a real file that is empty. This is the only arm
  that covers authentik, mealie and home-assistant, which emit no wrapper-side
  size metric at all.
- `BackupArtifactCompanionMissing` — `backup_artifact_companion_present == 0`
  or `…_companion_size_bytes == 0`, for files declared in
  `nas_storage_backup_artifact_apps[].companions`: files required to RESTORE an
  artefact but deliberately kept out of its glob.

  The collector emits `backup_artifact_companion_*` **only** for declared
  companions, so this alert covers exactly what the inventory declares and is
  inert if nothing does. Today that is the gitlab entry
  (`host_vars/pve-nas-01.yml`), declaring `gitlab-secrets.json` and `gitlab.rb`
  — the two files `gitlab-backup` excludes from the tarball and that
  `gitlab-backup-run.sh` copies into the landing zone separately, on a step that
  can fail on its own. `scripts/check-backup-artifact-apps.py` fails the lint
  stage if the rule exists while no app declares a companion (and vice versa),
  so it can never ship as coverage that cannot fire.
- `GitLabBackupSecretsMissing` — `gitlab_backup_secrets_present == 0` or
  `gitlab_backup_secrets_size_bytes == 0`, written by the GitLab wrapper.
  `gitlab-secrets.json` is excluded from the tarball *and* from the artefact
  glob, so without this alert an unrestorable backup looks perfectly healthy.
  This is the **VM-side** witness (the wrapper's own view); the companion arm
  above is the **NAS-side** one (what actually reached `tank/backups/apps`).
  They disagree exactly when the copy is lost in transit, which is the failure
  neither could see alone.

### Restore drills

`restic check` proves the repository's structure and checksums. It does **not**
prove that a restored `pg_dump` replays, that `gitlab-backup restore` runs, or
that the encrypted HAOS tars decrypt with the key in 1Password. Integrity is not
restorability, and only a drill closes that gap.

#### The automated quarterly drill

`backup-restore-drill.timer` on pve-nas-01 (shipped by the `restic_offsite`
role, `Persistent=true`, gated by `restic_offsite_restore_drill_enabled`) runs
`restic-offsitectl drill`: it samples a handful of files out of the newest
snapshot, restores just those, and byte-compares each against the ZFS snapshot
subtree it came from — immutable, so any difference is corruption rather than
churn. A narrow but real end-to-end proof (repo → decrypt → restore → identical
bytes) at negligible cost. Run it on demand with `restic-offsitectl drill` or
`systemctl start backup-restore-drill.service`, and read the journal with
`journalctl -t backup-restore-drill`.

**What a pass actually proves is decided by the sampler's bounds**, all of them
role variables (defaults shown; this host overrides one, below):

| Variable | Default | Why it exists |
|---|---|---|
| `restic_offsite_restore_drill_sample_files` / `_max_bytes` | 5 files / 16 MiB | egress is billed, and volume proves nothing extra here — repo-wide bit-rot is the rotating deep verify's job |
| `restic_offsite_restore_drill_min_bytes` | 4096 | size **floor**. Without it the sampler takes the estate's globally smallest files — one- and two-byte marker files — and the drill passes having proven essentially no bytes |
| `restic_offsite_restore_drill_min_sources` | 1 | **coverage floor**. Candidates are bucketed per file source and drawn **round-robin**, so one source cannot dominate the sample; a drill that covered fewer sources than this **fails** |

Only `restic_offsite_sources` (file sources) count toward coverage: a zvol
source's filesystem is mounted only during a run, so between runs it has no
comparand and is never drillable. A requirement above the number of configured
sources is clamped with a log line rather than wedging the drill permanently.
The journal prints the per-source breakdown (`sampled <src>=<n> …`, sources
covered, candidates under the floor), so what a pass proved is readable after
the fact. A sampled path containing a glob metacharacter is skipped with a
logged note — restic would treat it as a pattern, and the resulting MISSING
would be a sampler artefact rather than a real failure.

On this cluster five file sources are declared and this host sets
`restic_offsite_restore_drill_min_sources: 3` (`host_vars/pve-nas-01.yml`), so a
drill that quietly narrows to one source fails. Three is the practical ceiling:
`ssd/databases` is empty and can never yield a candidate, leaving four
drillable sources.

It writes three gauges to `/var/lib/node_exporter/backup_restore_drill.prom`:

| Metric | Meaning |
|---|---|
| `backup_restore_drill_last_success_seconds` | last drill in which every compared file matched byte-for-byte (preserved across a failure) |
| `backup_restore_drill_last_run_seconds` | last drill ATTEMPT (advances even on failure) |
| `backup_restore_drill_files_compared` | files byte-compared in the last run (0 also fails the unit) |

Two alerts split the two failure modes, because they run on very different
clocks:

| Alert | Severity | Fires on |
|---|---|---|
| `BackupRestoreDrillStale` | critical, `for: 1h` | `_last_success_seconds` **or** `_last_run_seconds` older than 100 days (one quarter + a month of slack) |
| `BackupRestoreDrillNeverRan` | warning, `for: 26h` | `_last_success_seconds` absent entirely — no drill has ever passed |

A drill failure leaves the success timestamp untouched, so time-since-success is
what surfaces a persistently broken drill; the `_last_run_seconds` arm covers
the case where the drill has never passed at all (no success series exists to be
stale) and the timer has also stopped attempting.

**`BackupRestoreDrillStale` deliberately has no `absent()` arm.** The metric
does not exist until a drill has actually run, and a `Persistent=true` quarterly
timer does *not* fire when it is first enabled — systemd bases the next elapse
on the activation time when there is no `/var/lib/systemd/timers` stamp. An
`absent()` arm at critical therefore paged continuously from deploy day until
the first quarterly elapse, up to eight weeks later. The role now runs one drill
when the units are newly installed, so the metrics exist within a textfile
collector cycle of the deploy; `BackupRestoreDrillNeverRan` at warning with a
26h `for:` is what catches a seed that never ran or never passed, without the
false critical. (`GitLabBackupSecretsMissing` avoids the same trap the same way.)

#### The deeper drill (manual, annual)

The automated drill does not prove that a restored `pg_dump` replays or that an
encrypted HAOS tar decrypts. Those stay a manual exercise, one artefact per app
class:

1. **Fetch** — `restic-offsitectl restore backups` pulls the logical-dump tree.
2. **Postgres class** — the newest `authentik-*.sql.gz` replays into a throwaway
   database (`gunzip -c … | psql -d drill_tmp`); schema and a row count are
   confirmed, then the database is dropped.
3. **GitLab class** — the newest `*_gitlab_backup.tar` unpacks and its
   `backup_information.yml` names the expected GitLab version, and
   `gitlab-secrets.json` is present and non-empty in the same directory (a
   tarball without it cannot decrypt CI variables, 2FA or runner tokens).
4. **Home Assistant class** — the newest `Automatic_backup_*.tar` decrypts with
   `backup_encryption_key` from the **Home Assistant API Token** 1Password item.
   **If that field is empty the HA tars are unrecoverable.**
5. **File class** — one known file is restored out of `appdata` and diffed
   against the live copy (the automated drill's step, done by hand).


## Cost

Planning estimates that size the immich library from its ~2 TB sparse **zvol
ceiling** overshoot badly — the ceiling is not the footprint. Measured:

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

GFS is `keep-daily 7 / keep-weekly 2 / keep-monthly 3 / keep-yearly 1`, plus a
`keep-last 5` floor (`restic_offsite_keep_last`). The floor exists because the
bucket has **no Object Lock** (`scripts/b2-bucket-drift.py` sets
`defaultRetention: {mode: None}`) and the lifecycle expires hidden versions at 30
days: with the calendar buckets alone, corruption that persists longer than the
daily window walks every daily restore point out of it — `keep-daily 7` buys a
full week of detection time. The documented monthly/yearly depth
only materialises as the repo ages — a freshly-seeded repo has days of history,
not months, whatever the policy says. `restic-offsitectl snapshots` shows the
truth. Object Lock (governance mode) on the restic prefix remains the stronger
answer and is the recommended next step if the threat model tightens.

## Nightly-chain right-sizing (vzdump)

- **Exclude the 9 k3s VMs** from vzdump (`exclude: [202-207,222,223,227]`): they
  are IaC-rebuildable cattle (all persistent data on `backup=0` zvols, covered by
  archive + pg-dumps + B2); keeping OS-disk images also risks a **stale-member
  etcd restore** that can corrupt quorum. Saves ~200 G/night of dump I/O.
- **bwlimit 61440** (60 MiB/s), raised from 30: measurement showed 30 was the
  binding constraint rather than pool capability, and the drivers of the earlier
  I/O-saturation incident (CI DinD on the NAS agent, memory/swap thrash) are
  fixed.

**Measured window (2026-08):** the pve-nas-01 slice runs **03:30 → ~05:36**, not
the ~04:45 originally projected. Windows 11 (VM 155) is the long pole at ~1h12m,
with GitLab (153) ~29m and the two photo/cloud VMs ~11m each. That still lands
before media-mover (06:00) and archive (06:30) and
`archive_backup_dataset_deferred_runs{dataset="tank/proxmox"}` stays 0, so
nothing is deferred — but the real headroom is about **24 minutes, not ~75**.

Read the current numbers rather than trusting this paragraph:
`journalctl -u pvescheduler --since yesterday | grep -E '(Starting|Finished) Backup'`.
The gate for raising the bwlimit further (a comfortable margin to 06:00) is
therefore **not met**; a raise would have to be paired with shrinking or
rescheduling the Windows dump.

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

## Setup reference (rebuild / new bucket)

Everything below is already in place. It is recorded so the tier can be
reconstructed, not as a checklist to work through.

### 1Password

The **B2 Archive Backup** item carries two key pairs plus the repo password —
field-by-field detail in [docs/15](15-credential-rotation.md) § B2 Archive Backup.
Keep an **offline** copy of `restic_repo_password`: without it the entire offsite
repo is undecryptable.

### The capability split

Two B2 keys, deliberately:

- **restic / rclone** (the `restic_offsite` role and `restic-offsitectl`) use the
  restricted key on `restic_key_id` / `restic_application_key`. It has **no**
  `deleteFiles` — restic deletes by hiding, and the bucket's 30-day lifecycle
  expires hidden versions. That makes the nightly path tamper-resistant.
- **`scripts/b2-bucket-drift.py`** (the `b2-drift-plan` CI job and the supervised
  `task b2:apply`) uses the full bucket-settings key on `b2_key_id` /
  `b2_application_key`, because reconciling lifecycle / SSE / retention needs
  bucket-management capabilities the restricted key deliberately lacks.

Minting a replacement restricted key:

```bash
b2 key create --bucket weisssrv-backup weisssrv-restic-offsite \
  listBuckets,listFiles,readFiles,writeFiles,readBucketEncryption   # no deleteFiles
```

Update the `restic_*` fields and re-run `deploy-ansible-storage`. Restic keeps
working throughout — it never issues a real delete.

> The Backblaze Terraform provider's read path returns empty attributes against
> B2's current API, producing a permanent phantom diff, which is why bucket
> settings are reconciled by `scripts/b2-bucket-drift.py` rather than a
> `terraform/b2` module. Review `task b2:drift`, then `task b2:apply`.

### Object Lock is deliberately OFF (decision record)

`scripts/b2-bucket.json` declares `defaultRetention: {mode: null, period: null}`
— the bucket has **no Object Lock**, and that is a decision, not an oversight.
Ransomware resistance rests on two compensating controls instead:

- The nightly key has **no `deleteFiles`** capability, so a compromised NAS
  cannot issue a real delete: restic deletes by *hiding*, and the bucket's
  30-day `daysFromHidingToDeleting` lifecycle is what finally reclaims the
  bytes. A destructive actor with that key can hide versions; it cannot remove
  them, and the previous 30 days remain restorable.
- Bucket settings themselves are drift-checked (`b2-drift-plan` in CI,
  `task b2:apply` supervised), so silently clearing that lifecycle or flipping
  the bucket type shows up as a diff.

Why not enable it: Object Lock in governance mode is **irreversible per object**
for its retention period, which means `forget --prune` cannot reclaim space
inside the window — restic's GFS retention and the lock would fight, and the
bucket's cost becomes retention-period-times-churn rather than the ~50 GB steady
state costed above. Revisit if the key ever gains delete capability or the
threat model changes; the change would be `defaultRetention` in
`scripts/b2-bucket.json` plus a retention period at least matching
`restic_offsite_keep_last`, applied through `task b2:apply` so the drift gate
enforces it thereafter.

### Home Assistant native backups

HA's own scheduled backups land on the NFS target via a `nas_backup` network
storage entry — configuration steps and the encryption-key requirement are in
[docs/24](24-home-assistant-deployment.md) § Configure Automatic Backups. They
ride archsync into B2 and are watched by
`BackupArtifactStale{app="home-assistant"}`.

### Bringing up a fresh chain

After the storage deploy and the app-VM plays, seed each dump once rather than
waiting for the first scheduled night — the staleness alerts' `absent()` arms fire
until a first artefact exists:

```bash
kubectl create job --from=cronjob/authentik-pg-dump -n authentik authentik-pg-dump-seed
kubectl create job --from=cronjob/mealie-pg-dump   -n recipes  mealie-pg-dump-seed
# on the app VMs:
systemctl start gitlab-backup.service        # gitlab
systemctl start immich-backup.service        # immich
systemctl start nextcloud-backup.service     # nextcloud
# on pve-nas-01:
systemctl start pve-cluster-backup.service
systemctl start backup-artifact-collector.service
```

Then `restic-offsitectl run` (watch it: repo init, freshness guard, zvol clones
mounting and tearing down, snapshot, metrics) and `restic-offsitectl verify`.

**Check artefact NAMES and SIZES, not just mtimes** — `ls -lt
/mnt/tank/backups/apps/*/` should show a real, non-zero dump per app. A
directory holding only `gitlab.rb` and `gitlab-secrets.json` is exactly the
failure the per-app glob and `BackupArtifactStale` exist to catch; a dump that
is present but 0 bytes is what `BackupArtifactZeroBytes` catches; and a
directory holding a healthy tarball but no `gitlab-secrets.json` is what
`GitLabBackupSecretsMissing` catches.


## Related documentation

- [docs/17-disaster-recovery.md](17-disaster-recovery.md) — restic/B2 restore procedures
- [docs/06-zfs.md](06-zfs.md), [docs/32-zfs-encryption.md](32-zfs-encryption.md) — the local layers
- [docs/15-credential-rotation.md](15-credential-rotation.md) — the `B2 Archive Backup` 1Password item
- [docs/12-runbooks.md](12-runbooks.md) — backup-and-recovery runbook entries
- [docs/24-home-assistant-deployment.md](24-home-assistant-deployment.md) — HA's own backup target
