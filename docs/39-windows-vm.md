# 39 — Windows 11 VM

A NAS-pinned Windows 11 Pro VM (`windows`, VMID 155, `192.168.0.155`) on
`pve-nas-01`, provisioned as Infrastructure-as-Code down to a bootable
installer, then **installed and activated interactively** by the operator.
Reachable over RDP at `windows.esweiss.com:3389` from the LAN and over
Tailscale — no public exposure.

The IaC builds the VM *shell*: q35 machine, OVMF/UEFI with pre-enrolled Secure
Boot keys, a TPM 2.0 device, VirtIO SCSI disk + VirtIO NIC, and the Windows +
VirtIO driver ISOs attached. Provisioning ends at "the installer boots with all
devices visible." Windows Setup, driver loading, RDP enablement, and activation
are manual, one-time, human steps documented below.

## Specs

| Property | Value |
|----------|-------|
| VMID / IP | 155 / 192.168.0.155 (`windows.esweiss.com`) |
| Host | pve-nas-01 (NAS-pinned, `vm_cpu_type: host`, no live migration) |
| vCPU / RAM | 8 cores / 16 GiB |
| Disk | 250 GiB empty zvol on the **encrypted** `ssd` pool |
| Firmware | OVMF/UEFI, q35, pre-enrolled Secure Boot keys, TPM 2.0 (v2) |
| Storage bus / NIC | VirtIO SCSI (`virtio-scsi-pci`, discard + ssd) / VirtIO (`net0`) |
| Resource pool | `apps-private` |
| Autostart | `onboot=0` — started by `pve-start-encrypted-guests` after the ssd pool unlocks (docs/32) |
| Backups | vzdump (cluster-wide `all: true` job) → encrypted `tank/proxmox` |
| Firewall | `sg-windows`: RDP 3389 from `admin_lan` (LAN /24) + `admin_ts` (tailnet) |

## Where everything lives

| Concern | File |
|---------|------|
| Inventory host + specs | `ansible/inventories/prod/hosts.yml` (`windows_vms` group) |
| VM build logic | `ansible/roles/proxmox_vm` (`vm_guest_type: windows`) |
| VirtIO ISO version + sha256 | `ansible/inventories/prod/group_vars/all.yml` (`virtio_win_version` / `virtio_win_checksum`) |
| Encrypted-guest autostart | `ansible/inventories/prod/host_vars/pve-nas-01.yml` (`zfs_encryption_guest_vmids`) |
| Provisioning playbook | `ansible/playbooks/windows.yml` |
| Firewall group | `ansible/roles/proxmox_firewall/templates/cluster.fw.j2` (`[group sg-windows]`) |
| Internal DNS + PTR | `ansible/inventories/prod/group_vars/dns.yml` |
| Tasks | `task windows:{provision,provision-check,status,verify}` |
| RDP monitoring | `kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml` (commented target) + `WindowsRdpDown` alert |

There is **no** Kubernetes/Traefik object for this VM: RDP is not HTTP, so
access is a direct L3 connection to `192.168.0.155:3389`, gated purely at the
Proxmox guest firewall. There are **no** external-dns / Cloudflare records —
`windows.esweiss.com` (internal AdGuard rewrite) only.

## Encryption & backups

- The root disk + EFI vars + TPM state all live on the **encrypted `ssd`
  pool**. VMID 155 is in `zfs_encryption_guest_vmids`, so after a NAS reboot the
  VM cold-boots automatically once `zfs-mount-encrypted` unlocks `ssd` and
  `pve-start-encrypted-guests` runs (docs/32). This only matters after Windows
  is installed — before that the VM is intentionally stopped.
- vzdump backs the whole VM image up nightly (the cluster-wide `all: true` job)
  into `tank/proxmox`, which is an encrypted dataset — so backups are encrypted
  at rest. A 250 GiB desktop image is a non-trivial nightly footprint, with two
  consequences to keep in mind:
  - **Backup-window encroachment.** VM 155 is a *new pve-nas-01 local guest* in
    the `all` job, so its image lengthens **this host's** slice of the shared
    backup window. That window (03:30–~05:45, throttled to 30 MiB/s/node by the
    `bwlimit` on `pve_vzdump_jobs`) already ends only ~15 min ahead of the 06:00
    media-mover and 06:30 archive-backup timer. Once Windows is installed and
    the image fills out (~15–25 min at the bwlimit for a typical footprint, more
    as the guest and pagefile grow), **re-verify** that pve-nas-01's backup
    still finishes before 06:00 and that the `ProxmoxHost` I/O-pressure alerts
    stay quiet through a full window (this is the post-install check in the
    provisioning runbook). If it slips, exclude VMID 155 (below) or move it to a
    separate, lower-frequency job. History for this window lives in the
    `pve_vzdump_jobs` comment in `host_vars/pve-nas-01.yml`.
  - **Opting out.** If you'd rather not back the desktop up at all, exclude VMID
    155 from the vzdump job (`pve_vzdump_jobs` in `host_vars/pve-nas-01.yml` —
    set `exclude: [155]`); the default is **included**.

---

## Provisioning runbook

### 1. Stage the Windows 11 ISO (manual, one-time)

The Windows 11 ISO cannot be redistributed, so no role downloads it. Download
the official ISO from Microsoft and place it on the NAS ISO store:

```bash
# On pve-nas-01 (or copy it there): the tank-proxmox ISO dir is
#   /mnt/pve/tank-proxmox/template/iso/
# Name the file to match vm_install_iso in hosts.yml (default:
#   Win11_24H2_English_x64.iso). Either rename the download to match, or
# update vm_install_iso to the actual filename.
ls -lh /mnt/pve/tank-proxmox/template/iso/Win11_24H2_English_x64.iso
```

The VirtIO driver ISO is fetched + checksum-verified automatically by the role
(pinned via `virtio_win_version` in `all.yml`) — no manual step.

### 2. Provision the VM shell

```bash
task windows:provision-check      # dry-run
task windows:provision            # create the VM (STOPPED) + guest firewall
```

This creates VMID 155 with OVMF + TPM + q35 + VirtIO + both ISOs and the boot
order set to the install CD first. It does **not** start the VM. Re-running is
idempotent — an existing VM is never re-clobbered (create-time-only semantics).

### 3. Boot + install Windows (interactive, via the Proxmox console)

1. Start the VM and open its console (Proxmox UI → VM 155 → Console), or
   `ssh eric@192.168.0.102 "sudo qm start 155"`.
2. Press a key to boot from the install CD. Proceed to "Where do you want to
   install Windows?" — **no disk is listed** because Windows has no in-box
   VirtIO SCSI driver.
3. Click **Load driver** → **Browse** → the VirtIO CD
   (`virtio-win-…`) → `amd64\w11\` → load **viostor** (Red Hat VirtIO SCSI
   controller). The 250 GiB disk now appears; install onto it.
4. After the desktop is up, install the rest of the VirtIO drivers and the
   guest agent: open the VirtIO CD and run **`virtio-win-guest-tools.exe`**
   (installs `netkvm` NIC driver, balloon, QEMU guest agent, etc.). The NIC
   comes up and the VM gets `192.168.0.155` (set a **static IP** of
   `192.168.0.155/24`, gw `192.168.0.1`, DNS `192.168.0.150` / `192.168.0.160`,
   or reserve `.155` for the VM's MAC in your router's DHCP).

> **DNS note — disable encrypted DNS (DoH/DoT).** The VM must resolve through
> the homelab AdGuard servers (`192.168.0.150` / `.160`) in **plaintext**. In
> Windows 11 (*Settings → Network & internet → Ethernet → DNS server
> assignment → Edit*) set **DNS over HTTPS = Off** for the adapter, and do not
> enable any third-party DoH/DoT resolver. Encrypted DNS bypasses AdGuard
> entirely — it breaks Microsoft-account sign-in, Windows Update, and internal
> `*.esweiss.com` name resolution (this was the root cause of the earlier
> Windows login/activation failures). Only plaintext DNS to the AdGuard IPs
> keeps split-horizon resolution and outbound filtering intact.

### 4. Enable RDP

- Settings → System → Remote Desktop → **On**.
- Keep **Network Level Authentication (NLA)** enabled.
- Confirm the account you'll RDP in as has a password (RDP rejects blank
  passwords by policy).

### 5. Flip the boot order off the installer

Once Windows boots from disk, drop the install CD from the boot order so the VM
doesn't try the installer on the next power-cycle:

```bash
ssh eric@192.168.0.102 "sudo qm set 155 --boot order=scsi0"
```

(Optionally detach the CDROMs entirely later: `qm set 155 --ide2 none --ide0 none`.)

### 6. Activate Windows

Activation is the operator's responsibility (Settings → System → Activation).

### 7. Arm RDP monitoring

Only after Windows is installed and RDP answers, uncomment the `windows-rdp`
blackbox target in
`kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml` (one
target block) and commit/push. Flux reconciles the probe; the `WindowsRdpDown`
alert (30-minute `for`, deliberately tolerant of an on-demand desktop being
powered off) then covers reachability. `probe_success` also feeds the Blackbox
Grafana dashboard.

```bash
task windows:verify               # nc RDP 3389 — PASS once installed + running
```

### 8. Re-check the nightly backup window

Once the VM is installed and its disk has filled out, confirm its image hasn't
pushed pve-nas-01's slice of the nightly vzdump past the 06:00 media-mover /
06:30 archive-backup boundaries (see [Encryption & backups](#encryption--backups)):

```bash
# On pve-nas-01, after a nightly run: how long did the last backup take, and
# did the I/O-pressure alert stay quiet through the window?
ssh eric@192.168.0.102 "journalctl -u 'vzdump*' --since yesterday | tail"
```

If pve-nas-01's backup now finishes after ~05:55, exclude VMID 155 or move it to
a separate job (see [Encryption & backups](#encryption--backups)).

---

## Access paths

- **LAN**: RDP client → `windows.esweiss.com` (AdGuard rewrite → `192.168.0.155`)
  → `:3389`. Allowed from the whole LAN /24 (`admin_lan`).
- **Remote**: over Tailscale via a Proxmox subnet router — the tailnet CGNAT
  range (`admin_ts`, `100.64.0.0/10`) is allowed to 3389. No public/WAN
  exposure.

> **Hardening note.** `admin_lan` is the entire `192.168.0.0/24`, so RDP accepts
> from every LAN device (defense-in-depth gap, not direct compromise — NLA still
> authenticates). Tightening 3389 to a dedicated admin IPSet is tracked in
> [docs/16-next-steps.md](16-next-steps.md) (Network segmentation).

## Observability

- **VM resource metrics** (CPU/RAM/disk/status) are covered by the Proxmox PVE
  exporter as soon as the VM exists — no in-guest agent required.
- **Logs / in-guest metrics** are **not** collected: `alloy_host` and
  `node_exporter_host` are Linux-only and are deliberately absent from the
  `windows_vms` plays (the guest isn't Ansible-managed).
- **RDP reachability**: the commented `windows-rdp` blackbox target +
  `WindowsRdpDown` alert (step 7).

### Optional follow-up — `windows_exporter` (not automated)

For in-guest Windows metrics (CPU, memory, disk, services), install
[`windows_exporter`](https://github.com/prometheus-community/windows_exporter)
inside the guest and scrape it. This is a manual, optional step — not wired into
this repo:

1. In the guest, install the latest `windows_exporter` MSI (defaults to
   `:9182/metrics`).
2. Open 3389's sibling scrape port on the guest firewall: add a rule to
   `[group sg-windows]` in `cluster.fw.j2` allowing `9182/tcp` from
   `+dc/k3s_nodes` (so Prometheus can reach it), then `task windows:provision`.
3. Add a static scrape target / `Probe`/`ScrapeConfig` for `192.168.0.155:9182`
   in the observability stack, plus a NetworkPolicy scrape-allow if needed.

## Idempotence & recreation

- `task windows:provision` is safe to re-run. CPU/RAM/disk/firmware are
  create-time only; re-running against an existing VM only reconciles the
  metadata-level flags (NIC `firewall=1`, agent, onboot/startup).
- To rebuild from scratch: `qm stop 155 && qm destroy 155` on pve-nas-01, then
  re-provision and re-install (all guest state is lost).

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Installer shows no disk | VirtIO SCSI driver not loaded — Load driver → `amd64\w11\viostor` (step 3). |
| No network after install | `netkvm` not installed — run `virtio-win-guest-tools.exe` (step 4). |
| `task windows:provision` fails "vm_install_iso is empty" | Set `vm_install_iso` in hosts.yml and stage the ISO (step 1). |
| get_url checksum mismatch on the VirtIO ISO | `virtio_win_checksum` in `all.yml` is stale for the pinned version — recompute per the comment there. |
| VM auto-started into the installer after a NAS reboot | Expected once the VM exists — `pve-start-encrypted-guests` starts the cohort after `ssd` unlocks (docs/32). Harmless before install (empty `scsi0` → UEFI shell); flip the boot order to `scsi0` after install so it boots Windows. If it did *not* start, `ssd` likely didn't unlock. |
| RDP refused | RDP not enabled / NLA blocking / account has no password (step 4). |
| Microsoft-account login / activation / Windows Update fails, or `*.esweiss.com` won't resolve | Encrypted DNS (DoH/DoT) is enabled, bypassing AdGuard — set **DNS over HTTPS = Off** and use plaintext DNS to `192.168.0.150` / `.160` (step 4 DNS note). |
