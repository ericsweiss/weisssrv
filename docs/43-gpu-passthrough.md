# 43 — GPU passthrough (pve-prec-01 GTX 1660 Ti → Hindsight)

Puts the NVIDIA GTX 1660 Ti (TU116) in **pve-prec-01** into service for the
Hindsight ML stack: the host passes the whole card through to the k3s agent VM
(207), the agent runs the NVIDIA driver + container toolkit, and the Hindsight
`llama.cpp` sidecar offloads inference to the GPU. The same maintenance window
also right-sizes VM 207's RAM (18→30G) after the host went 31→62G physical.

This is the repo's **first** VM-level VFIO passthrough and first NVIDIA/CUDA
plumbing — all of it is codified, but the physical apply (reboot, `qm set`) is a
supervised operator window, never unattended CI.

## Host facts (pve-prec-01)

- **GPU**: TU116 [GTX 1660 Ti], four functions at `01:00.0-.3` in a clean IOMMU
  group. PCI IDs: `10de:2182` (VGA), `10de:1aeb` (HD audio), `10de:1aec`
  (USB-C xHCI), `10de:1aed` (UART/UCSI). Currently host-bound (`nouveau` et al.).
- **IOMMU**: functional (Intel VT-d on by default). The `vfio_passthrough` role
  still adds `intel_iommu=on iommu=pt` explicitly for correct DMA/reset behavior.
- **Host**: 62G RAM, GRUB boot (LVM root), headless (Intel iGPU `i915` keeps the
  console after `nouveau` is blacklisted).
- **VM 207**: i440fx + SeaBIOS, `cpu=host`, 6 cores. **Machine type stays
  i440fx** — a 6G-BAR Turing maps fine on i440fx, and q35+OVMF would risk a guest
  NIC rename vs the k3s role's `kube_vip_interface: eth0` (see *Machine type*
  below). No `pcie=1`, no `x-vga`.

## What is codified (ships in the MR, applies via CI on merge — no reboot)

| Piece | Where | Effect on merge |
|---|---|---|
| ARC cap 8G on prec-01 | `zfs_arc_cap` role + `hosts.yml` (`zfs_arc_cap_max_bytes`) | `deploy-ansible-proxmox` writes `/etc/modprobe.d/zfs.conf`, rebuilds initramfs, sets live `/sys` ARC cap. **No reboot.** |
| VFIO host prep | `vfio_passthrough` role + `hosts.yml` (`vfio_passthrough_enabled` + `vfio_passthrough_pci_ids`) | `deploy-ansible-proxmox` writes the GRUB drop-in (`intel_iommu=on iommu=pt` + a `vfio-pci.ids=` earliest-bind cmdline) + `/etc/modprobe.d/vfio.conf` (nouveau blacklist + audio/USB softdeps + redundant `vfio-pci ids=`) + `/etc/modules-load.d/vfio-pci.conf` (force-load vfio-pci at boot), rebuilds grub/initramfs, **prints reboot-required. Never reboots.** |
| Driver + toolkit | `k3s` role `tasks/gpu.yml` (gated `k3s_gpu_node: true`) | Runs only via `task k3s:deploy` (node ops are never CI). Installs `nvidia-open` (NVIDIA CUDA repo) + `nvidia-container-toolkit`. |
| VM RAM + hostpci | `hosts.yml` VM 207 (`vm_memory: 30720`, `vm_hostpci: ["0000:01:00"]`) | **Documentation only** — `proxmox_vm` applies memory + hostpci at qm-CREATE; the existing VM is changed by hand (below). |
| Device plugin + RuntimeClass | `infrastructure/controllers/nvidia-device-plugin` | Flux reconciles; DaemonSet has no node until 207 is labelled + toolkit installed (sits waiting, harmless). |
| GPU offload | `apps/hindsight/deployment.yaml` (CUDA image, `-ngl 99`, `nvidia.com/gpu: 1`, `runtimeClassName: nvidia`) | Flux reconciles; the llama pod goes **Pending** until the GPU is advertised. |
| Observability | `observability/exporters/dcgm-exporter.yaml` + ServiceMonitor + dashboard + `homelab.gpu` alerts | Flux reconciles; DCGM has no node until 207 is a GPU node. |

## Driver / CUDA compatibility — why nvidia-open 590

The Hindsight sidecar image `ghcr.io/ggml-org/llama.cpp:server-cuda-b10068` is
built against **CUDA 12.8** (verified: `NVIDIA_REQUIRE_CUDA=cuda>=12.8`). CUDA
12.8 needs a **native driver ≥ 570** to initialize on a consumer **GeForce**
card, and NVIDIA CUDA **forward-compat** (`cuda-compat`) is officially
**unsupported on GeForce** — so a too-old driver fails to init CUDA.

The repo therefore ships **`nvidia-open` (590.48.01)** from **NVIDIA's own CUDA
apt repo** (`developer.download.nvidia.com/.../debian13`), pinned as
`nvidia_driver_version` and installed by the k3s role's `tasks/gpu.yml`. This is
**not** the Debian non-free `nvidia-driver`, which tops out at 550 (CUDA 12.4)
and would fail CUDA init. The open GPU kernel modules require a **GSP-equipped**
GPU — the 1660 Ti (TU116, Turing) has a GSP, so it is supported — and the
CUDA-repo `nvidia-open` is CUDA-12.8-capable.

The DKMS module build + `nvidia-smi` + CUDA init are still **validated in the
window** (step 8) — a first driver install always needs one VM reboot to load
the freshly-built module. **If the open kernel module ever fails to initialize
on this specific card**, the proprietary `cuda-drivers` metapackage from the
*same* CUDA repo is the drop-in alternative (`apt-get install cuda-drivers`,
then reboot).

## Operational window runbook

Preconditions: the in-cluster manifests are merged + reconciled (device plugin,
RuntimeClass, DCGM, the CUDA Hindsight deployment — all waiting), and
`deploy-ansible-proxmox` has run (ARC cap + VFIO config staged on prec-01). Never
run this while a `deploy-*` pipeline is in flight (serialize).

1. **Baseline (optional).** Run one Hindsight retain/query and record CPU tok/s
   for a before/after (the CPU baseline is ~13 tok/s).
2. **Remove any legacy manual ARC cap** on prec-01 so the role's
   `/etc/modprobe.d/zfs.conf` is the single source (the host historically carried
   a hand-set `zfs_arc_max`). Confirm `cat /etc/modprobe.d/zfs.conf` shows 8G.
3. **Preflight etcd, then evacuate Home Assistant.** Confirm etcd is 3/3
   (`task k3s:status`): the prec-01 server (227) is one of the three, and a
   3-node quorum tolerates exactly one server down, so losing it for the host
   reboot is safe (sequence the window so *only* prec-01's guests are affected).
   Then **migrate Home Assistant off prec-01 explicitly — a reboot does NOT
   relocate it.** Under PVE's default `conditional` HA policy the LRM *freezes*
   `vm:154` in place across a reboot (stopped, then restarted on the *same* node
   when it returns); only a full node **shutdown** relocates HA resources. So move
   it by hand first (per docs/26):
   `ssh pve-prec-01 "sudo ha-manager migrate vm:154 <target-node>"`, and confirm
   it lands and is healthy on the target before rebooting. (Skip this only if HA
   downtime for the whole reboot window is acceptable — then expect `vm:154` down
   until prec-01 is back.)
4. **Drain the agent.** `kubectl cordon k3s-agt-prec-01` then
   `kubectl drain k3s-agt-prec-01 --ignore-daemonsets --delete-emptydir-data`.
   Hindsight (Recreate/RWO) goes down — Hermes degrades to built-in memory
   (accepted).
5. **Reboot the host.** With Home Assistant already migrated off (step 3) and the
   agent drained (step 4), `qm stop 207`, then reboot pve-prec-01. On boot, verify
   VFIO claimed the card: `lspci -nnk -s 01:00 | grep -i 'Kernel driver'` →
   `vfio-pci` on **all four** functions (the `vfio_passthrough` role binds vfio-pci
   at the earliest point via the `vfio-pci.ids=` kernel cmdline, force-loads it via
   `/etc/modules-load.d/vfio-pci.conf`, and the modprobe.d softdeps/blacklist keep
   the host audio/USB/VGA drivers off it); `dmesg | grep -i vfio`. If a function
   still shows a host driver, confirm the cmdline `vfio-pci.ids=` landed
   (`cat /proc/cmdline`) and the modules-load.d + modprobe.d files made it into the
   initramfs (the role runs `update-initramfs -u`; re-run it + reboot if so).
6. **Attach GPU + grow RAM** (VM stopped):
   ```bash
   qm set 207 --memory 30720 --hostpci0 0000:01:00
   qm start 207
   ```
7. **Install the driver in the guest.** `task k3s:deploy` limited to the prec
   agent (installs `nvidia-open` + `nvidia-container-toolkit`), then **reboot
   the VM once** to load the DKMS kernel module. Verify in-guest:
   `nvidia-smi` reports the 1660 Ti; `getent hosts` / `ip link` confirm the NIC
   is still `eth0` (i440fx keeps the name).
8. **Validate GPU inference (THE gate).** Uncordon
   (`kubectl uncordon k3s-agt-prec-01`), `task flux:reconcile`, and confirm:
   - the device plugin advertises `nvidia.com/gpu: 1` on the node
     (`kubectl describe node k3s-agt-prec-01 | grep nvidia.com/gpu`);
   - the Hindsight pod is Running on prec-01 and the **llama container loaded the
     model on the GPU** (`kubectl logs … -c llama` shows CUDA/offload lines;
     `nvidia-smi` in the guest shows the llama process + VRAM used);
   - DCGM metrics flow (Grafana "GPU (NVIDIA DCGM)" dashboard) and no
     `HindsightGpuOffloadIdle`;
   - measure tok/s (expect ≥ ~10× the CPU baseline).
   **If the llama container crash-loops on a CUDA init error** despite
   `nvidia-smi` reporting the card, the open kernel module is not initializing on
   this GPU → swap to the proprietary `cuda-drivers` metapackage from the same
   CUDA repo (`apt-get install cuda-drivers`, reboot) and re-check.
9. **Reconcile against the baseline.** `task flux:status` all READY, nodes 9/9,
   endpoint sweep matches, etcd 3/3, HA guests settled.

### DCGM device visibility caveat

On a single-GPU node with the device plugin active, the container toolkit needs
`accept-nvidia-visible-devices-envvar-when-unprivileged = true` in
`/etc/nvidia-container-runtime/config.toml` for the DCGM exporter's
`NVIDIA_VISIBLE_DEVICES=all` to inject the GPU. The k3s role's `tasks/gpu.yml`
now sets this automatically (`nvidia-ctk config --set … --in-place`, idempotent)
right after installing the toolkit, so it should already be `true` when you reach
step 8. **Still verify in the window**: without it DCGM runs and its target reads
`up==1` but emits **zero** `DCGM_FI_DEV_*` series — a silent telemetry loss the
`GpuTelemetryMissing` alert (below) now backstops. If metrics are absent, confirm
the flag (`grep accept-nvidia-visible-devices /etc/nvidia-container-runtime/config.toml`)
and restart the DaemonSet (`kubectl -n observability rollout restart ds/dcgm-exporter`).
(This never affects the llama container, which allocates the GPU via
`nvidia.com/gpu`.)

## Sharing the GPU with other workloads

The GPU is a **general cluster resource**, not Hindsight-specific. Time-slicing
(the nvidia-device-plugin `config` block in
`infrastructure/controllers/nvidia-device-plugin/release.yaml`) advertises the
one physical card as `nvidia.com/gpu: 4`, so up to four pods can co-schedule on
it. To run another GPU workload, give the pod:

- `runtimeClassName: nvidia` — run under the nvidia container runtime;
- `resources.limits.nvidia.com/gpu: 1` — claim one time-slice;
- a toleration for the node's compute taint
  (`key: esweiss.com/compute`, `value: "true"`, `effect: PreferNoSchedule`);
- `nodeSelector: { esweiss.com/gpu: nvidia }` — land on the GPU node (VM 207).

Caveats:

- **Shared VRAM.** Time-slicing shares *compute*, not memory — the 6 GB is shared
  across every co-scheduled pod, which must all fit at once. Hindsight's
  Gemma-4B-Q4 uses ~2.5 GB; size other workloads against the remainder.
- **`replicas` is the concurrency ceiling.** Four is the current cap; raise it in
  `release.yaml` (`config.map.any` → `replicas`) if more slices are needed,
  subject to the VRAM budget.

## Rollback

The VM boots and runs fine without the GPU — rollback is non-destructive:

```bash
kubectl cordon k3s-agt-prec-01
kubectl drain  k3s-agt-prec-01 --ignore-daemonsets --delete-emptydir-data
qm stop 207
qm set 207 --delete hostpci0        # detach the GPU
qm set 207 --memory 18432           # (optional) revert RAM to the pre-window value
qm start 207
kubectl uncordon k3s-agt-prec-01
```

Then revert the Hindsight deployment to the CPU `server-` image (drop
`-ngl 99` / `nvidia.com/gpu` / `runtimeClassName`) in a follow-up MR, or leave
the pod Pending until the GPU is reattached. If GPU binding cannot be made to
work after two systematic-debugging passes, roll back the hostpci, keep the RAM
raise, uncordon, and report — do **not** leave the node cordoned.

## Machine type (future work)

Proper PCIe passthrough (`--machine q35 --hostpci0 …,pcie=1`) is intentionally
NOT done here: converting VM 207 to q35 can rename the guest NIC (`ens*`/`enp*`
vs `eth0`), and the k3s role pins `kube_vip_interface: eth0` — a wrong assumption
would break the node's networking + the MetalLB/Traefik VIP announcer on reboot.
If q35 is ever pursued, verify the post-q35 NIC name in a window first (and pin
it with a systemd-link rule if it changes). i440fx conventional PCI passthrough
is the safe, sufficient path for a 6GB Turing.

## Gotchas fixed on first live enablement (all codified — here for context)

Four things bit the first real bring-up and are now fixed in-repo, so a future
GPU node should not re-hit them:

- **CUDA repo key.** The debian13 CUDA repo's `InRelease` is signed by a key
  (`02182E60…8793F200`) NVIDIA ships **only** in the `cuda-keyring` package — not
  as a standalone `.pub`. Fetching debian12's `3bf863cc.pub` (a *different*, older
  key) left apt unable to verify the repo and the driver never installed.
  `k3s/tasks/gpu.yml` now installs `cuda-keyring`, SHA256-verified before install
  (the deb runs maintainer scripts as root — verify-before-trust).
- **Device-plugin nodeAffinity.** The NVIDIA device-plugin chart injects a
  *required* nodeAffinity keyed on NFD/GFD labels (`…/pci-10de.present`,
  `nvidia.com/gpu.present`, …). This cluster labels the GPU node manually and runs
  no NFD/GFD, so the DaemonSet sat at `DESIRED=0` and the node never advertised
  `nvidia.com/gpu`. The HelmRelease now sets `affinity.nodeAffinity: null` —
  **not** `{}`, which Helm's map deep-merge leaves the chart default in place —
  so the `nodeSelector` alone governs placement.
- **UCSI function (`01:00.3`) host driver.** `i2c_nvidia_gpu` auto-loads and
  claims `.3` before vfio-pci despite `vfio-pci.ids=`, forcing a Proxmox rebind at
  VM start. `vfio_passthrough` now hard-blacklists it, so all four functions bind
  vfio-pci at host boot.
- **DCGM exporter OOM.** Its 256Mi limit OOMKilled (exit 137) the instant DCGM +
  NVML initialized on the real GPU; the limit is now 1Gi.

## Related

- `docs/19-k3s-deployment.md` — k3s node layer.
- `docs/37-hermes.md` — Hindsight memory backend.
- `docs/31-observability.md` — exporters/dashboards/alerts pattern.
- `docs/33-autoscaling.md` — VPA (Hindsight stays `updateMode: Off`).
- `docs/06-zfs.md` — ARC / storage tiers.
