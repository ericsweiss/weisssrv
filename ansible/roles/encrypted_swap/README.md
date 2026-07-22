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
- **`/etc/fstab`** — the encrypted mapper line
  `/dev/mapper/cryptswap none swap sw,pri=100,nofail 0 0`. `nofail` lets
  `swapon -a` silently **skip** the mapper while it is still absent (the
  pre-reboot deferred-activation window) instead of erroring; `pri=` makes the
  kernel prefer the encrypted mapper for new swap-outs the moment it comes up.

### Never a zero-swap window

The role **never** produces a state where `swapon -a` yields zero swap:

- **Deferred path** — the plaintext backing line (`/dev/pve/swap none swap …`)
  is **kept** in fstab *alongside* the higher-priority mapper line. Until the
  activation reboot the mapper is absent (`nofail` ⇒ skipped) and the plaintext
  device carries swap. A one-shot **boot finalize unit**
  (`encrypted-swap-finalize.service`, `ConditionPathExists=/dev/mapper/cryptswap`,
  `After=swap.target`) then `swapoff`s the plaintext device and comments its
  fstab line **exactly once** after the mapper has come up — so after the
  activation reboot **only encrypted swap remains active and fstab is clean**.
  Idempotent: re-runs and later boots are no-ops.
- **Live path** — the memory-safe switch moves swap onto the mapper immediately,
  then the role flips fstab to encrypted-only (removes the plaintext line) right
  away. The finalize unit is a no-op there (plaintext line already gone).

## Activation (live vs. reboot)

A running host cannot cryptsetup-open an **active** swap device, so the switch
needs a `swapoff` first. The **memory-safety decision** lives in a sourceable
helper (`/usr/local/sbin/encrypted-swap-switch.sh`, `encrypted_swap_switch`) so
it is unit-testable. It performs the live switchover
(`swapoff -a → systemd-cryptsetup@cryptswap → swapon /dev/mapper/cryptswap`)
**only when `MemAvailable` covers the in-use swap + margin** — the common case on
the compute hosts (near-idle swap). The **memory-tight NAS** (GBs of swap in
use) takes the **defer-to-reboot** path: the config is written and encrypted swap
activates on the next host reboot, with the plaintext line kept until the
finalize unit drops it. **Reboot pve-nas-01 to activate** (documented in
`docs/42`). `swapoff` is **never** called on the already-active or defer arms.

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

The live switch is `molecule-notest` (needs real devices), so converge exercises
the **deferred shape**: it asserts `cryptsetup` installed, the crypttab entry
renders (mapper, source, `/dev/urandom`, plain-mode cipher/size), the fstab
**keeps** the plaintext line alongside the higher-priority `nofail` mapper line,
and the boot finalize unit + script are deployed and enabled (mapper-exists
guard, `After=swap.target`). `files/switch-decision-behavior.sh` sources the
extracted decision and drives all three arms against synthetic `/proc` inputs
(already-active / defer — asserting `swapoff` is **not** called / live-switch).
