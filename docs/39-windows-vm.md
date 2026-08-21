# Windows 11 VM

A NAS-pinned Windows 11 Pro VM (`windows`, VMID 155, `10.0.10.155`) on
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
| VMID / IP | 155 / 10.0.10.155 (`windows.esweiss.com`) |
| Host | pve-nas-01 (NAS-pinned, `proxmox_vm_cpu_type: host`, no live migration) |
| vCPU / RAM | 8 cores / 16 GiB |
| Disk | 250 GiB empty zvol on the **encrypted** `ssd` pool |
| Firmware | OVMF/UEFI, q35, pre-enrolled Secure Boot keys, TPM 2.0 (v2) |
| Storage bus / NIC | VirtIO SCSI (`virtio-scsi-pci`, discard + ssd) / VirtIO (`net0`) |
| Resource pool | `apps-private` |
| Autostart | **Auto-starts at boot**, last in the cohort (startup order 60). `onboot=0` is correct and required — its disks are on the encrypted `ssd` pool, so it is started by `pve-start-encrypted-guests` *after* the unlock, via `zfs_encryption_guest_vmids` (docs/32) |
| Backups | vzdump (cluster-wide `all: true` job) → encrypted `tank/proxmox` |
| Firewall | `sg-windows`: RDP 3389 from `admin_lan` (the homelab `/24` + the `10.0.20.8/29` Home-VLAN admin block) + `admin_ts` (tailnet) |

## Where everything lives

| Concern | File |
|---------|------|
| Inventory host + specs | `ansible/inventories/prod/hosts.yml` (`windows_vms` group) |
| VM build logic | `weisssrv.infra.proxmox_vm` (`proxmox_vm_guest_type: windows`) |
| VirtIO ISO version + sha256 | `ansible/inventories/prod/group_vars/all.yml` (`proxmox_vm_virtio_win_version` / `proxmox_vm_virtio_win_checksum`) |
| Autostart policy | `ansible/inventories/prod/host_vars/pve-nas-01.yml` (`zfs_encryption_guest_vmids`, includes 155) |
| Provisioning playbook | `ansible/playbooks/windows.yml` |
| Firewall group | weisssrv-lib `ansible_collections/weisssrv/infra/roles/proxmox_firewall/templates/cluster.fw.j2` (`[group sg-windows]`) |
| Internal DNS + PTR | `ansible/inventories/prod/group_vars/dns.yml` |
| Tasks | `task windows:{provision,provision-check,status,verify}` |
| RDP monitoring | `kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml` (`windows-rdp` target) + `WindowsRdpDown` alert (fires only while the VM is powered on, so a deliberate shutdown stays quiet) |

There is **no** Kubernetes/Traefik object for this VM: RDP is not HTTP, so
access is a direct L3 connection to `10.0.10.155:3389`, gated purely at the
Proxmox guest firewall. There are **no** external-dns / Cloudflare records —
`windows.esweiss.com` (internal AdGuard rewrite) only.

## Encryption & backups

- The root disk + EFI vars + TPM state all live under the **encrypted `ssd`
  pool**. Encryption is ZFS-native and per-dataset: `zfs-load-key@ssd` fetches the
  passphrase from 1Password Connect once at NAS boot and
  `zfs-mount-encrypted` mounts the pool's encryption roots (docs/32). The pool
  *root* itself stays plaintext, so a new dataset added to `ssd` does **not**
  inherit encryption automatically — it has to sit under an encryption root.
  Operationally nothing else changes: Windows's disks are decrypted by the time
  the host finishes booting, starting the VM needs **no** manual unlock, and
  Windows does no in-guest crypto.
- VMID 155 is **included** in `zfs_encryption_guest_vmids` ([autostart](#autostart),
  below), so it cold-boots after a NAS reboot along with the other encrypted-storage
  guests — last in that cohort, after the services have claimed their memory.
- vzdump backs the whole VM image up nightly (the cluster-wide `all: true` job)
  into `tank/proxmox`, which is an encrypted dataset — so backups are encrypted
  at rest. A 250 GiB desktop image is a non-trivial nightly footprint, with two
  consequences to keep in mind:
  - **Backup-window encroachment.** VM 155 is a *new pve-nas-01 local guest* in
    the `all` job, so its image lengthens **this host's** slice of the shared
    backup window. The measured window and the real headroom ahead of the 06:00
    media-mover and 06:30 archive-backup timer are in
    [docs/42](42-offsite-backup.md) § Nightly-chain right-sizing — read them from
    `journalctl -u pvescheduler`, not from prose. Once Windows is installed and
    the image fills out, **re-verify** that pve-nas-01's backup still finishes
    before 06:00 and that the `ProxmoxHost` I/O-pressure alerts stay quiet
    through a full window (this is the post-install check in the provisioning
    runbook). If it slips, exclude VMID 155 (below) or move it to a
    separate, lower-frequency job. History for this window lives in the
    `proxmox_backup_vzdump_jobs` comment in `host_vars/pve-nas-01.yml`.
  - **Opting out.** If you'd rather not back the desktop up at all, exclude VMID
    155 from the vzdump job (`proxmox_backup_vzdump_jobs` in `host_vars/pve-nas-01.yml` —
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
#   Win11_25H2_English_x64_v2.iso). Either rename the download to match, or
# update vm_install_iso to the actual filename.
ls -lh /mnt/pve/tank-proxmox/template/iso/Win11_25H2_English_x64_v2.iso
```

The VirtIO driver ISO is fetched + checksum-verified automatically by the role
(pinned via `proxmox_vm_virtio_win_version` in `all.yml`) — no manual step.

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
   `ssh eric@10.0.10.102 "sudo qm start 155"`.
2. Press a key to boot from the install CD. Proceed to "Where do you want to
   install Windows?" — **no disk is listed** because Windows has no in-box
   VirtIO SCSI driver.
3. Click **Load driver** → **Browse** → the VirtIO CD
   (`virtio-win-…`) → `amd64\w11\` → load **viostor** (Red Hat VirtIO SCSI
   controller). The 250 GiB disk now appears; install onto it.
4. After the desktop is up, install the rest of the VirtIO drivers and the
   guest agent: open the VirtIO CD and run **`virtio-win-guest-tools.exe`**
   (installs `netkvm` NIC driver, balloon, QEMU guest agent, etc.). The NIC
   comes up and the VM gets `10.0.10.155` (set a **static IP** of
   `10.0.10.155/24`, gw `10.0.10.1`, DNS `10.0.10.150` / `10.0.10.160`,
   or reserve `.155` for the VM's MAC in your router's DHCP).

> **DNS note — disable encrypted DNS (DoH/DoT).** The VM must resolve through
> the homelab AdGuard servers (`10.0.10.150` / `.160`) in **plaintext**. In
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
ssh eric@10.0.10.102 "sudo qm set 155 --boot order=scsi0"
```

(Optionally detach the CDROMs entirely later: `qm set 155 --ide2 none --ide0 none`.)

### 6. Activate Windows

Activation is the operator's responsibility (Settings → System → Activation).

### 7. Confirm RDP monitoring

The `windows-rdp` blackbox target is live in
`kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml`, so
once Windows is installed and RDP answers, the `WindowsRdpDown` alert (30-minute
`for`, deliberately tolerant of the desktop being powered off) covers
reachability with no further change. `probe_success` also feeds the Blackbox
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
ssh eric@10.0.10.102 "journalctl -u 'vzdump*' --since yesterday | tail"
```

If pve-nas-01's backup now finishes after ~05:45, exclude VMID 155 or move it to
a separate job (see [Encryption & backups](#encryption--backups)).

---

## Autostart

Windows **starts automatically** at boot and after a NAS reboot, last in the
encrypted-guest cohort (startup order 60) — it idles holding ~13 GiB the balloon
driver won't reliably reclaim, so it claims its memory after the services have
theirs.

**`onboot=0` and auto-started is not a contradiction.** Its disks live on the
encrypted `ssd` pool, so `pve-guests` — which is what honours `onboot` — runs too
early, before `zfs-mount-encrypted` has unlocked it. Every encrypted-storage guest
on this host is `onboot=0` and started afterwards by `pve-start-encrypted-guests`,
driven by `zfs_encryption_guest_vmids` in `host_vars/pve-nas-01.yml`. Setting
`onboot=1` to "make it start at boot" would do the opposite: the start would fail
against a locked pool.

To stop it auto-starting, remove `155` from that list — do not touch `onboot`.

You can start or stop it by hand at any time:

- **Proxmox UI**: VM 155 → **Start**.
- **CLI**: `ssh eric@10.0.10.102 "sudo qm start 155"`.

**No manual decryption is needed.** The disks live on the encrypted `ssd` pool,
which is unlocked once at NAS boot (key from 1Password Connect, docs/32) for all
its guests — so by the time you start Windows the storage is already decrypted,
and Windows itself does no in-guest crypto. Shutting it down from inside Windows
(or `qm shutdown 155`) leaves it off until you start it again — or until the next
NAS boot, which auto-starts it as described above.

---

## Access paths

- **LAN**: RDP client → `windows.esweiss.com` (AdGuard rewrite → `10.0.10.155`)
  → `:3389`. Allowed from `admin_lan` — the whole homelab `/24` plus the
  `10.0.20.8/29` admin block on the Home VLAN ([docs/11](11-firewall.md)
  § Client scopes).
- **Remote**: over Tailscale via a Proxmox subnet router — the tailnet CGNAT
  range (`admin_ts`, `100.64.0.0/10`) is allowed to 3389. No public/WAN
  exposure.

> **Hardening note.** `admin_lan` is the homelab `10.0.10.0/24` plus the
> `10.0.20.8/29` admin block, so RDP still accepts from every device on the
> homelab segment (defense-in-depth gap, not direct compromise — NLA still
> authenticates). No client-VLAN device outside that /29 reaches it at all:
> IoT, Guest and Work are stopped by the gateway's inter-zone deny, and a Home
> device outside the admin block is refused by `admin_lan` — that is what the
> VLAN split bought. Getting the VM itself off the homelab segment is tracked in
> [docs/16-next-steps.md](16-next-steps.md) § UniFi network follow-ups ("Move
> the Windows VM (.155) to the Home VLAN").

## Observability

- **VM resource metrics** (CPU/RAM/disk/status) are covered by the Proxmox PVE
  exporter as soon as the VM exists — no in-guest agent required.
- **Logs / in-guest metrics** are **not** collected: `alloy_host` and
  `node_exporter_host` are Linux-only and are deliberately absent from the
  `windows_vms` plays (the guest isn't Ansible-managed).
- **RDP reachability**: the `windows-rdp` blackbox target + `WindowsRdpDown`
  alert.

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
3. Add a static scrape target / `Probe`/`ScrapeConfig` for `10.0.10.155:9182`
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
| get_url checksum mismatch on the VirtIO ISO | `proxmox_vm_virtio_win_checksum` in `all.yml` is stale for the pinned version — recompute per the comment there. |
| Windows did not come up after a NAS reboot | 155 is in `zfs_encryption_guest_vmids` and starts last in that cohort, so this is a fault, not the design. Check `systemctl status pve-start-encrypted-guests` and `journalctl -t zfs-start-encrypted-guests`; if the `ssd` pool failed to unlock, every other `ssd` guest is down too, so it is not Windows-specific (docs/32). `qm start 155` still works by hand. |
| `qm start 155` fails with a storage/volume error | The `ssd` pool didn't unlock at boot — check `systemctl status zfs-mount-encrypted` and 1Password Connect reachability (docs/32). Every other `ssd` guest would be down too, so this is not Windows-specific. |
| RDP refused | RDP not enabled / NLA blocking / account has no password (step 4). |
| Microsoft-account login / activation / Windows Update fails, or `*.esweiss.com` won't resolve | Encrypted DNS (DoH/DoT) is enabled, bypassing AdGuard — set **DNS over HTTPS = Off** and use plaintext DNS to `10.0.10.150` / `.160` (step 4 DNS note). |

## Related documentation

- [docs/06-zfs.md](06-zfs.md) - the `ssd` pool this guest lives on
- [docs/17-disaster-recovery.md](17-disaster-recovery.md) - what is and is not recoverable for this guest
- [docs/32-zfs-encryption.md](32-zfs-encryption.md) - the unlock-then-start ordering
- [docs/42-offsite-backup.md](42-offsite-backup.md) - the measured nightly backup window
