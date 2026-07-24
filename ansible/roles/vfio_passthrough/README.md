# vfio_passthrough

Host-side codification of GPU **VFIO passthrough** on a Proxmox host. Currently
enabled only on `pve-prec-01` (GTX 1660 Ti → the k3s GPU agent). See
`docs/43-gpu-passthrough.md` for the full runbook.

**OFF by default** — every task self-gates on `vfio_passthrough_enabled`, so the
role is a clean no-op on every host except where it is explicitly turned on
(in `hosts.yml`). It **stages** config and **prints a reboot-required warning**;
it **never reboots** — the operator applies it in a maintenance window.

## What it does (only when `vfio_passthrough_enabled`)

1. Writes `/etc/default/grub.d/vfio-iommu.cfg` — a GRUB drop-in that appends the
   IOMMU kernel params (`intel_iommu=on iommu=pt`) **and** `vfio-pci.ids=<functions>`
   without editing the main grub file → notifies **Update GRUB for VFIO**. The
   cmdline `ids=` is the *primary, earliest* bind — vfio-pci grabs the functions
   the instant it loads in the initramfs, ahead of any host driver.
2. Writes `/etc/modprobe.d/vfio.conf` — `options vfio-pci ids=<functions>` (the
   redundant modprobe.d twin of the cmdline bind), plus `blacklist <mod>` for each
   hard-blacklisted VGA driver (default `nouveau`) and `softdep <mod> pre: vfio-pci`
   for each host driver that *does* load at boot but must yield its function
   (default `snd_hda_intel`, `xhci_hcd`) → notifies **Rebuild initramfs for VFIO**.
3. Writes `/etc/modules-load.d/vfio-pci.conf` — force-loads `vfio-pci` at real-root
   boot as a belt-and-suspenders fallback to the cmdline bind, in case nothing else
   pulls it in (a blacklisted `nouveau` never loads, so its softdep can't) →
   notifies **Rebuild initramfs for VFIO**.
4. Prints **reboot-required** (a `debug` handler, fired only on a real change).
   Never reboots.

**Binding mechanism** — the `vfio-pci.ids=` kernel cmdline (step 1) is the primary
bind: vfio-pci claims the four functions as soon as it loads in the initramfs. The
modprobe.d `ids=`/`blacklist`/`softdep` (step 2, baked into the initramfs) and the
modules-load.d force-load (step 3) are belt-and-suspenders — they keep the host
drivers off the card and guarantee vfio-pci loads even if the coldplug order
changes. Together they make vfio-pci claim all four functions (VGA + HD-audio +
USB-C xHCI + UART) after a host reboot, which is what the docs/43 step-5 gate
verifies. (PVE would also bind vfio-pci at `qm start`, but boot-time binding keeps
the host drivers off the card cleanly.)

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `vfio_passthrough_enabled` | `false` | Master gate. Set true on the GPU host in `hosts.yml`. |
| `vfio_passthrough_pci_ids` | `[]` | `vendor:device` IDs bound to vfio-pci (REQUIRED when enabled). List every function of a multifunction GPU. |
| `vfio_passthrough_blacklist_modules` | `[nouveau]` | VGA drivers HARD-blacklisted (never load; no softdep — it would be dead). |
| `vfio_passthrough_softdep_modules` | `[snd_hda_intel, xhci_hcd]` | Host drivers that load at boot but must yield their function → `softdep … pre: vfio-pci`. |
| `vfio_passthrough_force_load` | `true` | Write `/etc/modules-load.d/vfio-pci.conf` to force-load vfio-pci at boot (belt-and-suspenders fallback to the cmdline `vfio-pci.ids=`). |
| `vfio_passthrough_cmdline_params` | `[intel_iommu=on, iommu=pt]` | IOMMU cmdline params appended via the GRUB drop-in. The role *also* appends `vfio-pci.ids=<vfio_passthrough_pci_ids>` (from the template) for the earliest bind — don't add an `ids=` here. |
| `vfio_passthrough_skip_boot_update` | `false` | Molecule/check-mode: render the files, skip `update-grub`/`update-initramfs`. |

## Assumptions & scope

- **GRUB-managed host** — the cmdline is appended via `/etc/default/grub.d/`.
  `pve-prec-01` boots via GRUB (LVM root). A systemd-boot / proxmox-boot-tool
  host keeps its cmdline in `/etc/kernel/cmdline` and would need different
  handling (out of scope; see `docs/43`).
- The role **never** reboots and **never** does a live driver unbind — capturing
  the GPU is done cleanly on the next boot. Applies in a maintenance window.

## Where it runs

Included by the `proxmox` play in `playbooks/site.yml` (tasks self-gate on the
var, so only the enabled host acts). Deployed via `deploy-ansible-proxmox` (CI)
or `task infra:deploy`. The apply (reboot) is always operator-driven — docs/43.
