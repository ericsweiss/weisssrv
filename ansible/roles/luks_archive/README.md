# luks_archive

Boot-time unlock of LUKS containers backing the `archive` ZFS pool on
`pve-nas-01`. Sibling role to `zfs_encryption` — same Connect plumbing,
same token, same vault — different unlock primitive (`cryptsetup` vs
`zfs load-key`) and different ordering (must complete BEFORE
`zfs-import-cache.service`, not between import and mount).

## Why LUKS for archive when the other pools use ZFS-native?

The four ST6000NM0024 drives in the archive pool predate the encryption
hardening work and have a thermal/wear history (~3 years power-on, ~3066
historical over-temp events each). The decision recorded in
`docs/32-zfs-encryption.md`:

- **tank / ssd / nvme** → ZFS-native encryption via the existing
  `zfs_encryption` role. These pools are new (or new enough) that they
  can be created with encryption from day one.
- **archive** → LUKS, layered under ZFS, via this role. The rolling
  conversion procedure (one drive at a time, replace + resilver) lets us
  encrypt the existing pool without destroying it.
- **local-ssd** (compute nodes) → not encrypted. Avoids the cold-boot
  chicken-and-egg where compute nodes can't fetch from Connect because
  Connect's PV happens to live on an encrypted pool.

## Configuration

Per-host `host_vars/<host>.yml`:

```yaml
luks_archive_devices:
  - name: archive-1
    device: /dev/disk/by-id/ata-SEAGATE_ST6000NM0024_Z4D2BDD2
  - name: archive-2
    device: /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1JCL6
  - name: archive-3
    device: /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1RQSM
  - name: archive-4
    device: /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1JQBA

# Until the rolling conversion finishes, leave bootstrap_only true so the
# units aren't enabled against plaintext devices.
luks_archive_bootstrap_only: true
```

Set `luks_archive_bootstrap_only: false` once all four devices have been
LUKS-formatted and replaced in the pool (the role probes each with
`cryptsetup isLuks` before enabling its unit; mismatch fails the play).

## Rolling conversion runbook

See `docs/32-zfs-encryption.md` §LUKS rolling conversion for the full
procedure. Summary:

1. `task luks-archive:bootstrap` — deploys role in bootstrap mode (no
   units enabled, but Connect token + script in place).
2. For each drive (start with the survivors; the freshly-resilvered new
   drive goes last):
   - `sudo zpool offline archive <by-id>`
   - `sudo wipefs -a <by-id>` (clear ZFS labels before LUKS layer)
   - `sudo cryptsetup luksFormat --type luks2 --cipher aes-xts-plain64 --key-size 256 --hash sha256 --pbkdf argon2id <by-id>`
     (paste passphrase from 1P "LUKS Archive Passphrase" item — same flags as docs/32 §LUKS)
   - `sudo cryptsetup open <by-id> archive-N`
   - `sudo zpool replace archive <by-id> /dev/mapper/archive-N`
   - Wait for resilver (~6-9h per drive)
3. After all four are LUKS-backed: `task luks-archive:apply` (flips
   `bootstrap_only: false`, enables the units, probes for the LUKS header
   on each, fails loudly if any device is still plaintext).
4. Reboot to verify cold-boot path works end-to-end.

## Cold-cluster recovery

If Connect is unreachable at boot, the `archive-luks-open@<name>.service`
units fail and the archive pool stays unimported. Manual unlock:

```
ssh pve-nas-01
sudo cryptsetup open /dev/disk/by-id/<by-id> archive-N   # paste passphrase from 1P phone app
# repeat for all 4 mappers
sudo zpool import archive
sudo zfs mount -a
```

## 1Password items required

- **LUKS Archive Passphrase** — passphrase shared across all four LUKS
  containers (single passphrase because the threat model is "drives
  stolen as a group"; per-device passphrases don't materially raise the
  bar but multiply operational risk).
- **ZFS Encryption Connect Token** — reused; same token as
  `zfs_encryption`.

## Files deployed

| Path                                                       | Purpose                                  |
|------------------------------------------------------------|------------------------------------------|
| `/etc/onepassword-connect/token`                           | Connect bearer (shared with zfs_encryption) |
| `/etc/luks/archive/<name>.conf`                            | Per-device env file (device path, item, field) |
| `/usr/local/sbin/luks-archive-open.sh`                     | Fetch + cryptsetup-open script           |
| `/etc/systemd/system/archive-luks-open@.service`           | Template unit                            |
| `/etc/systemd/system/zfs-import-cache.service.requires/archive-luks-open@<name>.service` | Per-device enablement symlink (only when `bootstrap_only: false`) |
