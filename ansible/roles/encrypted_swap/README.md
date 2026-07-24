# encrypted_swap

**dm-crypt plain-mode swap with a random, ephemeral key** on the six bare-metal
Proxmox hosts. A fresh key is drawn from `/dev/urandom` at every boot and
discarded at shutdown, so on-disk swap is **unrecoverable after a reboot** — no
passphrase, no key material to manage, no unlock step. Closes the "secrets
paged to plaintext swap" gap alongside the estate's ZFS at-rest encryption.

## Mechanism

- **`/etc/crypttab`** — `cryptswap <source> /dev/urandom
  swap,cipher=aes-xts-plain64,size=512,sector-size=4096`. `systemd-cryptsetup`
  opens the backing device with a random key and `mkswap`s the mapper (the
  `swap` option) at boot. `size=512` = **AES-256-XTS** (two 256-bit keys),
  matching the estate's aes-256 posture. No `luks` option ⇒ plain mode.
  The role installs **both** `cryptsetup` (userspace tools) and
  `systemd-cryptsetup` — Debian trixie / PVE 9 ship the crypttab generator +
  `systemd-cryptsetup@.service` template in the separate `systemd-cryptsetup`
  package (carved out of `systemd`; `cryptsetup` does not pull it in), and
  without it the boot generator never materializes the unit and swap never
  activates.
- **`/etc/fstab`** — the encrypted mapper line
  `/dev/mapper/cryptswap none swap sw,pri=100,nofail 0 0`. `nofail` lets
  `swapon -a` silently **skip** the mapper while it is still absent (the
  pre-reboot deferred-activation window) instead of erroring; `pri=` makes the
  kernel prefer the encrypted mapper for new swap-outs the moment it comes up.

### Never a zero-swap window

The role **never** produces a state where `swapon -a` yields zero swap. The
plaintext backing line (`/dev/pve/swap none swap …`) is **kept** in fstab
*alongside* the higher-priority mapper line. Until the activation reboot the
mapper is absent (`nofail` ⇒ skipped) and the plaintext device carries swap. A
one-shot **boot finalize unit** (`encrypted-swap-finalize.service`,
`ConditionPathExists=/etc/crypttab`,
`After=swap.target systemd-cryptsetup@cryptswap.service`) then `swapoff`s the
plaintext device and comments its fstab line **exactly once** after the mapper
has come up — so after the activation reboot **only encrypted swap remains active
and fstab is clean**. Idempotent: re-runs and later boots are no-ops.

## Activation is deferred to reboot (no live switch)

Encrypted swap activates on the **next host reboot** — systemd-cryptsetup opens
and `mkswap`s the mapper from the crypttab entry, and the `nofail` fstab mapper
line swaps it on. The config is written idempotently; **reboot to activate**
(the compute hosts activate on their next `kured` reboot; reboot `pve-nas-01`
when convenient, per `docs/42`). Existing plaintext swap keeps running until
then.

There is deliberately **no live (running-host) switchover**. Converting an
*active* swap device to encrypted needs a `swapoff` first, and
systemd-cryptsetup's `mkswap` on the mapper **overwrites the plaintext device's
swap header** — so a live switch that fails its post-`swapon` verification cannot
restore plaintext swap and leaves the host **swapless**. That is exactly what a
first live run did to five hosts (pipeline 887), so the live path was removed in
favour of the clean, standard reboot path. Swap is ephemeral, so deferring costs
nothing.

### Boot-race recovery

At boot both contenders want the same backing LV: the retained plaintext fstab
line and `systemd-cryptsetup@cryptswap`. systemd orders `cryptsetup.target`
before `swap.target`, so the mapper normally wins; the finalize unit
(`After=swap.target`) handles the rare loss — if the mapper is **not** active it
`swapoff`s the plaintext device, opens the mapper, and `swapon`s it (memory is
empty at boot, so the `swapoff` cannot OOM), always keeping a working swap on any
failure. It detects an active mapper by resolving `/dev/mapper/cryptswap` to its
`/dev/dm-N` kernel name before matching `/proc/swaps` (dm devices never appear
there by their `/dev/mapper/` path).

Two things to expect around the **activation reboot** (both benign, both
self-healing):

- **One-shot `swap.target` degraded state.** The retained plaintext line
  (`/dev/pve/swap none swap sw 0 0`, no `nofail`) fails to `swapon` once the
  mapper has claimed the backing LV, so `dev-pve-swap.swap` enters `failed` and
  `systemctl is-system-running` reports `degraded` **for that one boot** — until
  the finalize unit comments the plaintext line out. Encrypted swap is already
  active (the `nofail` mapper line comes up independently), so boot is not
  harmed; subsequent boots are clean.
- **Rare recovery-arm swapless-until-reboot.** If the plaintext line wins the
  boot race, the finalize recovery arm completes the switch; on the narrow path
  where the mapper opens (its `mkswap` has by then overwritten the plaintext
  header) but the subsequent `swapon` fails, `swapon -a` can restore nothing and
  the host stays swapless until the next reboot. This is inherent to plain-mode
  random-key swap (the plaintext header is unrecoverable once the mapper is
  mkswap'd), low-probability, and surfaced by the `NASSwapGone` alert.

### Interaction with `nas_storage`'s swap-clean (NAS only)

`swap-clean.sh` is **device-agnostic** — it reads swap usage from
`/proc/meminfo` and cycles swap with `swapoff -a` / `swapon -a`, so post-reboot
it works transparently against `/dev/mapper/cryptswap`. The former pre-reboot
caveat is now **guarded on both sides**: swap-clean's **pre-flight** skips the
whole cycle as a deliberate no-op when any fstab swap device is absent (exactly
the deferred window, where fstab names the not-yet-present mapper), and even if
it did cycle, the kept plaintext fstab line means `swapon -a` restores real swap.
So a deferred activation can no longer strand the NAS swapless — reboot when
convenient rather than urgently.

## Scope

Runs on the `proxmox` group (`pve-nas-01` + the 5 compute hosts). Backing device
defaults to `/dev/pve/swap` (standard Proxmox LVM layout on all six) — override
`encrypted_swap_source_device` per host if a box differs.

## Molecule

Activation is deferred to reboot (real devices), so converge asserts the rendered
config: `cryptsetup` **and `systemd-cryptsetup`** installed (the split-out package
ships the crypttab generator + `@.service` template — without it swap never
activates at boot); the crypttab entry renders (mapper, source, `/dev/urandom`,
plain-mode cipher/size); the fstab **keeps** the plaintext line alongside the
higher-priority `nofail` mapper line; and the boot finalize unit + script are
deployed and enabled (`ConditionPathExists=/etc/crypttab`,
`After=swap.target systemd-cryptsetup@cryptswap.service`). The finalize-script
assertions cover the active-swap gate, the boot-race recovery arm, and — key to
the fix — that the mapper is resolved to its `/dev/dm-N` device (`readlink -f`)
before matching `/proc/swaps`.
