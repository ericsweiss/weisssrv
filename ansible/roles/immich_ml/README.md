# immich_ml

Deploys the Immich machine-learning service (`immich-machine-learning`,
**OpenVINO** variant) as a single-service Docker Compose stack in a GPU LXC
(`immich-ml`, 192.168.0.158, vmid 158 on pve-nas-01). The Immich VM (.157)
consumes it as its **primary** ML endpoint; the VM's own CPU ML container stays
in place as the failover.

Full architecture, design rationale, and runbooks: **docs/36-immich.md**
(GPU machine learning).

## Design

- **GPU share, not VFIO**: the Intel Arc B580's `/dev/dri` is passed into this
  LXC by `proxmox_lxc` (`lxc_gpu_passthrough` in `host_vars/immich-ml.yml`) the
  same non-exclusive way it is shared into the Plex LXC — the kernel `xe`
  driver arbitrates between consumers. A VM could not do this: VFIO passthrough
  is exclusive and the card already serves Plex. The `-openvino` image bundles
  its own Intel compute-runtime; the host supplies only the kernel driver.
- **Version lockstep**: `immich_ml_image` derives from the same
  `immich_version` pin (`group_vars/all.yml`) as the Immich VM's containers, so
  any version-bump MR redeploys both sides with matching tags — the documented
  server/ML lockstep is automatic.
- **Failover**: `immich-server` tries `machineLearning.urls` in order (the
  `immich` role renders `[.158:3003, in-VM CPU container]` into
  `immich-config.json`), so a GPU-LXC outage degrades to CPU ML, not to broken
  ML.
- **Authless endpoint / firewall boundary**: the ML API has no authentication
  by upstream design. The guest firewall group `sg-immich-ml` admits **only**
  the Immich VM (.157) on 3003 — that rule IS the security boundary. SSH/ICMP
  admin comes from `sg-vm-admin`.
- **No state**: the multi-GB model cache is a named docker volume in the LXC
  rootfs (`local-lvm`) — re-downloadable cache, not data. No zvols, no bind
  mounts, no backup enrollment, and (unlike plex) no encrypted-pool coupling,
  so the container is `onboot=1` and boots unattended.

## What it does

1. **GPU guard** — asserts `/dev/dri/renderD128` + `card0` exist in the guest
   and discovers their GIDs for the container's `group_add` (in an unprivileged
   LXC the group match is the only way to open the 0660 device nodes).
2. **Docker Engine** (shared `docker_engine` role, via the shared `compose_app`
   role) — the same fingerprint-verified apt repo (`apt_signed_repo`) and the
   same shared `docker_ce_version` etc. pins + holds the immich role uses.
   `journald` log driver so container logs ride `alloy_host` → Loki.
3. **Compose stack** (`templates/docker-compose.yml.j2`) — mirrors upstream's
   `hwaccel.ml.yml` openvino stanza (`/dev/dri` device, `c 189:*` cgroup rule,
   `model-cache` volume, port 3003). Lifecycle owned by
   `immich-ml-compose.service` (the shared `compose_app` compose systemd unit;
   no `RequiresMountsFor` since this LXC has no zvols).
4. **Health wait** — polls `http://127.0.0.1:3003/ping` (answers before the
   first-boot model download finishes; models load lazily).

## Deployment

```bash
task immich-ml:deploy          # provisions the LXC + installs the stack
task immich-ml:deploy-check    # dry-run
task immich-ml:status          # compose ps + /dev/dri
```

The `deploy-immich-ml` CI job re-runs the playbook on merges touching the
role / playbook / host_vars / pins.

## Molecule

`molecule/default` is a render/contract scenario: `skip_immich_ml_deploy: true`
skips the GPU guard, Docker install, and service management, so the compose
file (image tag `-openvino` on `immich_version`, `/dev/dri` device, port 3003,
group_add GIDs) and the systemd unit are rendered and asserted without a
container runtime or GPU.

## Related

- `proxmox_lxc` — creates the container with the `/dev/dri` passthrough and
  the video/render GID idmap (applied for GPU guests with or without bind
  mounts).
- `compose_app` — the shared compose scaffolding (Docker install + compose
  systemd unit) this role delegates to.
- `docker_engine` — the shared pinned Docker Engine install (repo + held engine + daemon.json).
- `apt_signed_repo` — the fingerprint-verified Docker apt repo (via `docker_engine`).
- `immich` — renders `machineLearning.urls` (this endpoint first, CPU
  failover second) into the Immich system config.
- `alloy_host` — journald → Loki log shipping.
- `proxmox_firewall` — the `sg-immich-ml` guest security group.
