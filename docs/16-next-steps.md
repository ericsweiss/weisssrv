# Next Steps and TODO

This document tracks **remaining** work for the weisssrv homelab: decisions that
need an owner, supervised steps that have not run yet, accepted risks, and the
deferred-refactor backlog. Completed work is summarised at the end under
[Shipped](#shipped-historical) — git history is the real record.

Per-area detail lives in the numbered docs; this page carries only what is not
done.

---

## Decisions needed

Each item below is a real, documented gap that needs an explicit call — usually
a hardware spend or a posture change — rather than an implementation task.

### Network segmentation / admin-IPSet tightening

- **Current state**: the whole estate is one flat L2 `192.168.0.0/24` (single
  `vmbr0`, `vlan: null`) shared with Home-Assistant-managed IoT and the
  Windows 11 VM (.155, now IaC-provisioned — [docs/39](39-windows-vm.md) — but
  still on the flat LAN). The `admin_lan` firewall IPSet is
  the entire /24, so SSH (22), the Proxmox API (8006), the kube-apiserver
  (6443), RDP (3389), and the AdGuard admin plane (:3000/:443/:853) accept from
  every device on the LAN. The services stay authenticated, so this is a
  defense-in-depth / L3-reachability gap, not direct compromise. See
  [docs/11-firewall.md](11-firewall.md) and [docs/06-zfs.md](06-zfs.md)
  ("Encryption Posture", LAN-trust model). VLANs are listed as "planned" below
  (Security Hardening) but never implemented.
- **Proposed change**: implement the planned management/IoT/guest VLANs and
  re-scope `admin_lan` to a management VLAN or an explicit small admin-host set.
  Interim (no VLAN hardware change): tighten the highest-value ports
  (8006/6443/22/3389) to a dedicated admin IPSet of specific workstation IPs,
  leaning on Tailscale (`admin_ts`) for remote admin.
- **Decision needed**: buy/configure a VLAN-capable switch and re-IP into
  segments, or take the interim IPSet tightening now.

### Network-fabric SPOF: second switch + corosync ring

- **Current state**: a single unmanaged switch carries both legs of every
  active-backup bond; a single router/gateway (Asus GT-AX11000 at 192.168.0.1)
  is also DHCP and the Cloudflare-origin port-forwarder; everything rides one
  flat `vmbr0` /24 with only one corosync ring (5405/ring0; 5406/ring1 is
  reserved but unused). A switch or router failure collapses Proxmox corosync
  quorum and k3s etcd L2 simultaneously. See
  [docs/17-disaster-recovery.md](17-disaster-recovery.md) (Network Fabric SPOF)
  and [docs/34-bond-mac-flapping.md](34-bond-mac-flapping.md).
- **Proposed change**: budget-permitting, add a second cheap switch and a second
  corosync ring on a separate NIC/VLAN for the Proxmox cluster so a single
  switch failure cannot lose cluster quorum.
- **Decision needed**: hardware spend, and whether the compute nodes have a
  spare NIC for ring1.

---

### UPS for the NAS (planned, with the network-hardware wave)

- **Current state**: no monitored UPS; a power loss hard-stops every host. The
  cold-boot path is unattended-safe (docs/32), so recovery is clean but
  unmanaged.
- **Planned**: a UPS on at least pve-nas-01 with NUT-driven shutdown, alongside
  the Ubiquiti router/switch purchase (which also unlocks the VLAN and
  second-corosync-ring items above).

### Split the `Homelab` vault (ESO-consumed vs host/CI-only)

Every ESO-synced item and every host/CI `op://` reference share one vault, so the
Connect credential can read all of them. Splitting out a `Homelab-Ops` vault
re-mints the Connect credential and rewrites every `op://` reference in the
Taskfile and CI — a botched cutover breaks ESO and every deploy job at once.
**Operator decision:** schedule the two-vault re-org, or accept whole-vault ESO
read as a documented risk. The interim narrowing (no admin-scoped item reachable
from an MR-branch job) is already done, so either answer is safe today.

### `tank/backups` legacy data and `archive` headroom

Two linked data-lifecycle calls only the operator can make:

- ~767 GB of immutable 2021-22 machine backups sit in `tank/backups` and are
  re-walked by the nightly restic run every night, for no recovery value anyone
  has claimed. **Decide: delete, cold-archive, or keep paying the nightly walk
  and the B2 footprint.**
- `archive` pool headroom is dominated by `archive/proxmox`. **Decide: tighten
  the vzdump retention, or buy disks.** Nothing else frees meaningful space.

### Opt-agent CPU saturation (hardware/placement call)

The three legacy 3-vCPU opt agents run at 7d p95 CPU 64–68% with 94–99% peaks.
**Decide: rebalance the `*arr` workloads onto the modern agents, or accept the
peaks until the hardware is replaced.** Not a code change either way.

### Authentik user lifecycle as code (small follow-up)

- **Current state**: groups/apps/providers are Terraform
  (`terraform/authentik`, docs/40), but adding a household member is still
  manual UI work (create user, set group memberships). Everything a
  family/friend touches authenticates via Authentik; the one standing
  exception is a WireGuard peer profile in wg-easy for VPN access.
- **Proposed**: a `users` map in the lib `authentik-sso` module (invite email +
  group memberships) so onboarding a person is a one-line MR.

## Pending supervised steps

Live steps that need a human at the console — a botched one severs access, so
none of them rides a pipeline. Some are codified but unapplied; others (like the
Tailscale host tagging below) are the remaining half of something already applied.

### Tailscale ACL least-privilege lockdown (applied; host tagging pending)

- **Codified**: `terraform/tailscale/policy.hujson` defines `group:admins`
  (`= [ericsweiss1@gmail.com]`, the `src` of every rule instead of
  `autogroup:member`) and `tag:subnet-router` (tagOwners), scopes access to two
  port-restricted `src group:admins -> dst` rules (rule 1 → the hosts' own
  services on their tailnet IP; rule 2 → the LAN via subnet routing, on the honest
  union of user-facing service ports) plus an SSH network gate (rule 3 →
  `autogroup:self:22`), auto-approves `192.168.0.0/24` for both the tag and
  (during migration) the owner, and **drops the `root` Tailscale SSH user**
  (nonroot + break-glass rule kept commented). The tailscale role advertises the
  tag via `tailscale_advertise_tags` (`group_vars/proxmox.yml`), adopted through
  its best-effort `tailscale set --advertise-tags` reconcile (strict under
  `tailscale_tags_require_adoption=true` for the supervised step). No host
  carries the tag yet — see [docs/05](05-tailscale.md).
- **Applied**: the supervised `terraform apply` has landed — the live tailnet ACL
  matches `policy.hujson` and `tailscale-drift-plan` is clean (empty plan). From
  here the job is a **drift signal**: any non-empty plan means someone hot-fixed
  the policy in the Admin console, and it should be reviewed with a supervised
  `task terraform:tailscale-plan` before applying.
- **Remaining step (supervised)**: tagging the six hosts. No host carries
  `tag:subnet-router` yet, so routes are still auto-approved via the owner entry.
  Follow the staged runbook in
  [terraform/tailscale/README.md](../terraform/tailscale/README.md): pre-apply
  nonroot-SSH checklist on all six hosts, run the tailscale role to adopt the
  tag, verify route approval + SSH + kube-API, break-glass ready.
- **Follow-up tightening (post-migration)**: remove the owner entry from
  `autoApprovers.routes` once all six hosts are tagged; revisit the host firewall
  `admin_ts` set now that tag-scoped tailnet ACLs exist.

### AQC113 firmware update (pve-nas-01)

Update the AQC113 NIC firmware from `1.5.38` to `1.5.45` on pve-nas-01. Needs
the vendor flashing tool on a Windows USB stick and a downtime window. The GRO
disable that stabilises the NIC in the meantime is codified in the `nic_tuning`
role, so this is a planned maintenance task rather than an emergency.

### pve-nas-01 stale manual config cleanup

The `/etc/network/interfaces` manual GRO-off stanza on pve-nas-01 is still in
place and must **not** be removed yet. Remove it only after the `nic_tuning`
drop-in has been redeployed and verified authoritative across a reboot
(`ifquery nic1` clean, `ethtool -k nic1` showing
`generic-receive-offload: off`).

### Move the wg-easy VIP out of the router's DHCP range

`cluster_wg_easy_vip` is `192.168.0.99`, which sits **inside** the router's DHCP
pool and depends on an uncoded router-side exclusion — documented three times
(`infrastructure/configs/metallb-ip-pools.yaml`, and docs/38 in both the router
setup checklist and the gotchas), but a dropped exclusion lets a workstation
lease collide with an internet-facing endpoint and shows up only as
`EndpointDown`. Moving it into the static block beside `.100`/`.101` removes the
dependency entirely. It is a one-line `cluster_wg_easy_vip` change **plus a
manual router port-forward edit** (51820/udp follows the VIP), which is why it is
here rather than done: the two must land together or the VPN endpoint is dark in
between.

---

## Accepted risks

Deliberate, documented, and **not** planned for change. Listed here so a future
reviewer does not re-raise them as gaps.

### Bulk media has no backup tier

`tank/media` (~15 TiB) and `nvme/media` (~440 GiB) are covered by same-pool ZFS
snapshots only — no archive replication, no offsite. The content is replaceable,
and adding it to `archive` would need a larger archive pool while adding it to
restic/B2 would dominate the bill. Offsite for media is explicitly declined.
See [docs/17](17-disaster-recovery.md) § Accepted Risk: NAS-Concentrated State.

### Observability plane is a single-NAS SPOF

Prometheus and Loki run single-replica, pinned to `k3s-agt-nas-01`, because their
storage is NAS-local by design. A NAS outage takes metrics and logs with it. The
mitigation is the external dead-man's-switch (`Healthchecks Watchdog`), not HA.
No node split or replica increase is planned. See
[docs/17](17-disaster-recovery.md) § Observability plane is a single-NAS SPOF.

### No offsite copy of guest images

`vzdump` writes to `tank/proxmox` and archsync replicates to `archive` — both on
site. The IaC-managed guests are reprovision-then-restore-data, so images are a
convenience rather than a dependency. The Windows VM (155) is the one guest whose
state is not IaC-reproducible; see [docs/17](17-disaster-recovery.md) § What
vzdump does and does not cover.

### Windows VM has no offsite export

Nothing on the Windows desktop (155) is exported to `tank/backups/apps/`, so it
has no offsite copy and no `BackupArtifact*` alert can cover it. The recommendation
in [docs/17](17-disaster-recovery.md) is advisory and deliberately not automated —
**decision: nothing on that desktop needs offsite durability.** Revisit by adding a
`windows` entry to `nas_storage_backup_artifact_apps` if that ever changes.

### Home Assistant automatic backup is PARTIAL

The HA-native scheduled backup is `type: partial` — core config, add-ons and the
`ssl` folder. `/media`, `/share` and `addons/local` are therefore absent from both
the HA-native and the offsite (B2) tiers. They are **not** unprotected: HAOS is
vmid 154 and is not in the vzdump exclusion list, so the whole guest image is
captured nightly to `tank/proxmox` and replicated to `archive` — image-level, local
+ archive only. **Decision: keep the partial scope** (those folders are empty on
this deployment); switch the HA scheduled backup to full only if `/media` or
`/share` ever holds something worth an offsite copy. Recorded in
[docs/24](24-home-assistant-deployment.md) § Configure Automatic Backups.

### Residual plaintext LAN hops

GitLab, HAOS, Plex and AdGuard all terminate TLS themselves and Traefik connects
via `scheme: https` + the `vm-tls-wildcard` ServersTransport, so no
Traefik → backend hop is plaintext any more. What remains:

- **Immich VM (.157) → Immich ML LXC (.158) :3003** — every photo byte, plain
  HTTP, scoped to the one source by `sg-immich-ml` (docs/36).
- **Router** — hardware/firmware-dependent. Configure manually if the router
  exposes an HTTPS UI; otherwise leave plain HTTP behind Traefik (lan-only).

The adguard-exporter hop is closed: it scrapes `https://dns-0X.esweiss.com` via
`hostAliases`, and `k3s_nodes` no longer appears on AdGuard's :3000 rule, which
now serves `admin_ts`/`admin_lan` break-glass only.

Both remaining hops are acceptable residual LAN-trust hops; the user-facing edge
is HTTPS throughout. The posture table is docs/06 § In Transit.

### Real client IP end-to-end (one coordinated change, not a Traefik edit)

Every downstream consumer — Authentik's event log, the Traefik access log, the
Nextcloud/GitLab/Immich/HAOS guest `nginx` real-IP chains — currently resolves a
**Cloudflare edge address** for every WAN visitor, because Traefik has no
`forwardedHeaders.trustedIPs` and therefore overwrites `X-Forwarded-*` for
everyone. That is the safe default and deliberately still in place: adding the CF
ranges to Traefik *alone* buys nothing (the guests' own trust lists would still
stop at Traefik) while newly letting an internet client's forged
`X-Forwarded-Host`/`-Proto` through the edge.

Do all four parts together, or none:

1. **Cloudflare edge Transform Rule** setting `X-Forwarded-For` (or a dedicated
   header) to `ip.src`, so the value Traefik is asked to trust is one the edge
   actually authored — `terraform/cloudflare`.
2. **Traefik** `ports.websecure.forwardedHeaders.trustedIPs` = Cloudflare's
   published v4+v6 ranges (`https://www.cloudflare.com/ips-v4` / `ips-v6`), an
   upstream-owned constant like the reserved-CIDR except-lists in
   `kubernetes/components/netpol-egress-public`, with a refresh note.
3. **A header-pinning middleware on every public route** that overwrites
   `X-Forwarded-Host` and `X-Forwarded-Proto` after the trust decision, so
   trusting the edge for XFF does not also trust a client for the other two.
   Internal-only routes keep today's overwrite-everything behaviour.
4. **The guest trust lists**: Cloudflare's ranges in the `set_real_ip_from` /
   `real_ip_header` blocks of the four VM guests' nginx (docs/35, docs/36,
   docs/27, docs/24) and in Authentik's trusted-proxy CIDR list, or those tiers
   still log Traefik's pod IP.

Verification is per-tier and needs an off-LAN, off-tailnet client: the visitor's
real address must appear in the Traefik access log, in Authentik's event log for
the same login, and in the guest's own access log — while a LAN/tailnet request
on the same entrypoint keeps its real remote address and no forged header is
honoured. `ipAllowList` middlewares are unaffected either way (they key on the
remote address, not the header).

---

## Planned work

**No application is queued.** Uptime Kuma — the last one — shipped
([docs/45](45-uptime-kuma.md)); everything below is platform and operations
work. A new app starts as an entry here.

### NAS nfsd file_lock slab leak — kernel watch (standing, ~weekly reboots)

The running 7.0.14-line kernel leaks unreclaimable `file_lock` slab at
~4 GiB/day on pve-nas-01 (root cause, fingerprint, mitigations and the
reboot procedure: [docs/06 § Kernel file_lock slab leak](06-zfs.md)). Until
it closes, `HostSlabLeakSuspected` pages roughly weekly and each page means
a NAS reboot window. To close it:

- **Watch Proxmox kernel releases** for a 7.1-line kernel (upstream's NFSD
  fixes landed in 7.1.3) or an nfsd backport in the 7.0.14 changelog
  (`apt-get changelog proxmox-kernel-7.0`); after installing one, keep the
  slab watch for a week and only then retire the cadence and the
  `nas_storage_nfs_disable_delegations` toggle per the docs/06 criteria.
- **Optional experiment** while waiting: mount one busy NFS PV with
  `vers=4.1` and compare that client's contribution to the GETATTR-correlated
  growth — cheap signal on whether the leaking path is v4.2-specific.

### Nextcloud follow-ups (not blockers)

- [ ] **Move Nextcloud's outgoing mail onto submission.** `nextcloud_smtp_*` in
  `group_vars/nextcloud_servers.yml` still points at the relay's port 25 with no
  SASL, from the era when the relay trusted the LAN in `mynetworks`. It does not
  any more (docs/10), so use the null-client SASL credentials on 587, the way
  `gitlab_servers.yml` does.

- [ ] Grafana dashboard: the upstream xperimental exporter dashboard uses a
  `${DS_LOCAL}` import-input datasource that needs adapting for the sidecar.
  Metrics are queryable in Explore and covered by alerts in the meantime.
- [ ] Optional Collabora/OnlyOffice office suite (not deployed).

### CI/CD

- [ ] **Distributed cache backend for the runners.** Every pip/galaxy/toolchain
  `cache:` block in `.gitlab-ci.yml` is inert because neither runner declares a
  cache backend — measured, not theoretical: no job restores a cache today.
  Standing up an in-cluster S3/MinIO target (the `registry-cache` app is the
  precedent) is the largest safe wall-clock win in the pipeline, and it closes
  the inert-`cache:` blocks in the same change — see
  [docs/13](13-ci-cd.md) § Lint Stage. Deferred: needs an S3-compatible store
  stood up first.
- [ ] **Alert on the runner reaper's partial sweeps.** `gitlab-runner-reaper`
  exits 0 on a clean budget stop and says so only in its log
  (`BUDGET STOP after <n>s; not fully reaped this run: <namespaces>`), so a
  permanently over-budget reaper looks like a clean no-op. The prefix is
  deliberately distinguishable; what is missing is the rule. The mechanism is
  already there — a LogQL rule in a `loki_rule` ConfigMap next to
  `observability/loki/host-log-staleness.yaml` — so this is a small,
  self-contained follow-up rather than new plumbing. See docs/13 § Runner
  garbage collection.
- [ ] **GitLab runner ResourceQuota is overcommitted** — ~46 cores requested
  against 31 allocatable, so a full concurrency burst cannot schedule. Resolving
  it is a capacity decision (lower `concurrent`, lower per-job requests, or more
  hardware), not an edit.
- [ ] **Whole-pipeline deploy atomicity via a deploy child pipeline.** Today's
  `resource_group`s are per target, so pipeline A's fleet-wide
  `deploy-ansible-base` can run concurrently with pipeline B's
  `deploy-ansible-proxmox` or a manual maintenance op on the same Proxmox hosts.
  That is an **accepted trade-off**, stated in the `workflow:` comment in
  `.gitlab-ci.yml` and backstopped by the "serialize merges" operating rule — a
  single repo-wide group would close it at the cost of serialising the app-deploy
  fan-out. The design that closes it *without* losing parallelism: move the
  deploy stage into a child pipeline and put the lock on the trigger job —
  `deploy-fleet: {stage: deploy, resource_group: fleet-deploy,
  interruptible: false, trigger: {include: .gitlab/ci/deploy-jobs.yml,
  strategy: depend}}`. With `strategy: depend` the trigger job stays Running for
  the whole child pipeline, so `fleet-deploy` is held across the entire fan-out
  while the child keeps full internal parallelism. Put the manual maintenance
  jobs in the same group (a job may declare only one) so a maintenance op queues
  behind an in-flight deploy, and set that group's process mode to `oldest_first`
  like the rest (docs/17 § GitLab project state).

### Cross-file invariant gates (from the 2026-08 review's mutation pass)

Seams that hold today but are enforced by nothing; each is a small gate:

- [ ] `authentik-auth` middleware consumers ↔ `terraform/authentik` proxy
  providers: a route can gain the middleware without its provider (404 at the
  outpost). Derive the provider list from the `.tf` and diff against the
  IngressRoutes.
- [ ] `k3s_disable` ↔ the self-managed twins: nothing asserts that everything
  in `group_vars/k3s.yml`'s disable list has its Flux-managed replacement (and
  vice versa — metrics-server is the precedent).
- [ ] `deploy-preflight`'s ~130-line inline parser in `.gitlab-ci.yml` is
  invisible to `test_scripts_have_tests.py`; extract to `scripts/` with tests
  when it next changes.
- [ ] `test_vendored_byte_identity.py`: add the third hint branch ("registered
  in the library working tree but absent at the pin — bump the pin") mirroring
  the lib's `check-vendored-copies.py` wording.

### Ansible collection migration residue

The Ansible layer now consumes roles from the `weisssrv.infra` collection in
`eric/weisssrv-lib` rather than in-tree `ansible/roles/*`. Residue to watch:

The deploy-job `changes:` gating (`check-deploy-coverage.sh` +
`check-collection-pin-trigger.py` in `repo-policy-checks`), the molecule
scenarios and the `docs/` role-README links were all reconciled. Open:

- [ ] **Adopt weisssrv-lib v0.8.0 (per-consumer wave).** The library MR that
  ships with this branch changes role behaviour this repo already assumes, so
  the adoption is its own MR and its own deploy window, after the cluster has
  settled. Per consumer (weisssrv, then both templates): bump
  `ansible/requirements.yml` + `WEISSSRV_LIB_REF` + the three Terraform `?ref=`
  pins, run `scripts/check-lib-pins.py --fix` and
  `scripts/check-molecule-image-pin.py --fix`, **re-vendor** (this is the pass
  that flips `check-default-deny-coverage.py` from a local file to a vendored
  one and picks up the extended `check-hpa-vpa-invariant.py`), then
  `ansible-galaxy install -r ansible/requirements.yml --force`. Two site-facing
  consequences land with it: `unbound_legacy_dropins` stops being a library
  default and is honoured from `group_vars/dns.yml` (verify `weisssrv.conf` is
  actually gone on both resolvers), and `ArchiveBackupPruneBlocked`
  (`observability/rules/scripts.yaml`) stops being dormant once
  archive-backupctl emits `archive_backup_last_prune_success`. The re-vendored
  HPA/VPA gate also collapses the "the gate belongs in the library" half of the
  § Autoscaling entry below to just the remaining re-derivations.

### Storage

- [ ] **Codify the per-host `local-ssd` storage ids.** `proxmox_backup_storage`
  now declares pve-nas-01's `ssd`, `tank` and `nvme` zfspool ids (and
  `tank-proxmox`), so the at-rest posture of the GitLab / Nextcloud / Immich root
  disks is asserted rather than assumed. The five compute hosts' `local-ssd` ids
  are still hand-created in `storage.cfg`; they carry only k3s VM and HA-guest
  disks, which are plaintext by design (docs/06 § At Rest), so this is a
  reproducibility gap rather than a security one.

---

## Deferred refactors and durability work

Refactors, durability work and supervised live steps that were deliberately kept
out of the review MRs that fixed the bug/security/correctness classes. Each
deserves its own focused change.

- **DUP-5 — de-duplicate the wildcard Certificates.** Still deferred. The
  per-namespace `*.esweiss.com` wildcard `Certificate` resources
  (`infrastructure/observability/ingress/certificate.yaml`,
  `apps/download-clients/certificate.yaml`, `apps/authentik/certificate.yaml`,
  `apps/recipes/certificate.yaml`, plus
  `infrastructure/configs/wildcard-certificates.yaml` and
  `onepassword-connect-certificate.yaml`) should be issued once and propagated
  cross-namespace — but no secret-reflection controller (emberstack/reflector
  or trust-manager) is deployed, and consolidation requires adding one. Keep
  the staggered `renewBefore` (720h/600h/480h) workaround until a controller
  is intentionally introduced.
- **DUP-11 — express the `*arr` overlay rename patches via a kustomize labels
  transformer** instead of per-overlay name/label patches.
- **k8s-apps-08 — split the `downloads` namespace** into a privileged tier
  (qbittorrent/nzbget) and a restricted tier (`*arr`) so PSS can enforce
  `restricted` on the managers. This is also the only route to PSA `restricted`
  on that namespace — deferred with the split, not separately.
- **Per-namespace egress NetworkPolicies for the 10 namespaces without one.**
  The ingress default-deny is universal; egress is per-app and ten namespaces
  (gitlab, kube-system, observability among them) have no allowlist. Authoring
  them needs measured traffic per namespace and carries high breakage risk —
  weeks of iteration, deferred as its own project.
- **Rate-limiting / in-flight-request middleware on the public perimeter.**
  There is none today. Adding one needs traffic baselining before thresholds can
  be chosen, or the first incident it causes is self-inflicted.
- **Two hand-maintained mirrors of kube-prometheus-stack rule content.** They are
  correct today; keeping them correct automatically needs a chart-render step in
  the test pipeline, which is the actual deferred work.
- **Delete the three `moved.tf` files.** `terraform/{authentik,cloudflare,tailscale}/moved.tf`
  are module-adoption scaffolding. A `moved` block whose source address is no
  longer in state is a no-op, so they are not causing drift — this is
  housekeeping, to be done once the authentik and cloudflare supervised applies
  are confirmed landed (tailscale's is recorded above).
- **Molecule test build-outs** — ANS-A-08 (SSH-hardening path), ANS-C-10 (zvol
  data-safety cases), and ANS-INV-13 (health-verify resilience) need a runnable
  molecule environment to author and validate, so they are deferred from this MR.
- **Tailscale host tagging.** The tailnet policy-as-code landed
  (`terraform/tailscale/`), is a **least-privilege lockdown** (tag/port scoped,
  root SSH dropped) and is **applied** — see the section above. What remains is
  adopting `tag:subnet-router` on the six hosts, a supervised live step (a
  botched change can sever tailnet/SSH access) — follow
  `terraform/tailscale/README.md` in a maintenance window. Host egress
  filtering, staged alongside it, is
  **enabled on all six Proxmox hosts** (`proxmox_firewall_egress_filtering:
  true`, docs/11) and the smtp-relay guest enforces default-deny egress
  (`guest_firewall_policy_out: DROP`).
- **ARCH-4 — split `.gitlab-ci.yml` into `include:` files.** The single-file
  pipeline is anchor-free (extends/!reference only), so a split is safe in
  principle, but `local:` includes can only be validated by pushing and
  iterating on the live pipeline, and the template/job sections are interleaved.
  Deferred to its own focused MR to avoid risking this MR's pipeline; purely a
  maintainability change.
- **k8s-apps-10 — image pinning, remaining scope.** (Also in scope: the ~72
  workload images still on mutable tags. Doing that maintainably needs a
  digest-refresh workflow first, which is why it is one item, not 72.) The default runner
  executor images are now digest-pinned (`debian:trixie` in
  `gitlab-runner/release.yaml`, `python:3.11` in
  `gitlab-runner-privileged/release.yaml`), and the molecule-test/molecule-ci
  base images are pinned by manifest-list digest. Still open: the mutable-tag
  CI *job* images in `.gitlab-ci.yml` (`python:3.11-slim`, `alpine:3.23`,
  `hashicorp/terraform:1.15`, `koalaman/shellcheck-alpine` — `docker:24.0-dind`
  is already digest-pinned) and
  the unpinned apt packages in the molecule-test image.
- **CI optimizations** — `ci-gitlab-broad-trigger` (move `gitlab_version` to a
  dedicated group_vars file so only it triggers a GitLab reconfigure) and
  `ci-no-build-cache` (add a pip/apt cache to lint jobs). Low-value pipeline
  tuning, best validated against the live pipeline.
  > NetworkPolicy DNS/apiserver egress duplication (DUP-7 / k8s-infra-03 /
  > RV-SIMP-5) is **not** a deferred refactor — it is a deliberate design choice
  > (the per-pod egress policies are intentionally granular). The decision and
  > rationale are documented in `docs/11-firewall.md` ("Kubernetes NetworkPolicies").
  > DUP-9 (the inline `if schedule: when never` rule on ~16 jobs) is likewise
  > **not** deduped into `.skip-schedule-web`: that anchor also skips `web`, but
  > those jobs are schedule-only skips and some (e.g. `integration-tests`)
  > deliberately *run* on `web` — folding them into the anchor would break that.
- **CI render-loop dedup (ci-dup-kustomize-versions, partial)** — the kustomize
  version+sha256 is single-sourced via the `KUSTOMIZE_VERSION` /
  `KUSTOMIZE_SHA256` CI variables, and `scripts/flux-render.sh` now
  consolidates the 4-site versions-extraction + kubeconform-version derivation
  (Taskfile `flux:lint`/`dev-apply` + CI `flux-lint`/`deploy-verify`). The
  per-Kustomization kustomize-build/kubeconform **loop body** remains
  implemented separately in `flux-lint` and `deploy-verify` — sharing it is
  deferred; the two jobs differ enough (offline kubeconform vs live
  server-side dry-run) that a `!reference` split is low-value churn.

---

## Review backlog

Findings from full-repo reviews that were deliberately deferred: refactors, test
debt, and follow-ups that deserve their own changes. Grouped by the files that
own them.

The bracketed codes (`DUP-n`, `ARCH-n`, `k8s-apps-n`, `ANS-*`, `ci-*`) are the
review session's own item ids, kept only so a finding can be traced back to the
review that raised it. They are not tracked anywhere else — each item stands on
its own text.

**weisssrv-lib (from the !11 review tail — valid, deferred as follow-ups):**

- `nas_storage` mergerfs remount: wrap the unexport/remount sequence in a
  `block`/`always` that restores the MergerFS targets and bind mounts on a
  mid-sequence failure (today it fails loud and the runbook covers recovery).
- `nas_storage` mergerfs idle-check: include exports whose `bind_source` sits
  *below* a MergerFS target, not only exact matches (no such export exists in
  this cluster today — generic-consumer correctness).
- CLI `wire hpa`: preflight-parse `deployment.yaml` and `vpa.yaml` before
  enabling the kustomization entry, so an unparseable manifest cannot leave the
  paired edits half-applied.
- `adguard_home` download: add `until`/`retries`/`delay` to the AdGuard
  `get_url` (v0.6.1). Neither the in-tree role nor the collection retried it, so
  a transient GitHub TLS-handshake timeout fails an otherwise-clean deploy or
  integration run.

**Live ops**:
- Watch agent image-filesystem usage on the 64G roots; if `FreeDiskSpaceFailed`
  events persist outside churn windows, lower the kubelet image-gc thresholds in
  `group_vars/k3s.yml`
- Optional governance hardening for multi-author/AI velocity: CODEOWNERS on
  the guest/storage inventory (hosts.yml, host_vars/pve-*) + kubernetes/infrastructure/,
  and a policy check (conftest) for risky manifest classes
- Dedicated CI deploy SSH keypair, separate from the operator key: the
  shared key's `from=` now includes the k3s pod CIDR (runner-pod hairpin,
  !82). Splitting keys would let the operator key drop the pod range and
  scope the CI key to exactly the deploy paths (new 1P item, CI variables
  swap, authorized_keys gains a second entry)

**Test debt**:
- proxmox_ha molecule exercises none of the drift logic (stub ha-manager/pvesr
  with invocation logging + JSON fixtures)
- AdGuard API-config: the per-role adguard_home molecule scenario now
  exercises api_base_config.yml; still open is extending the dns-stack
  integration scenario to cover rewrites reconciliation end-to-end
- check-versions parser fixtures: fetch_helm_version (multi-chart index +
  pre-release), apt-Packages variants, Docker Hub tag selection,
  update_version_in_file, debian_version_compare
- shellcheck CI pattern misses *.j2 shell templates (archive-backupctl,
  media-mover, cert-reload) — add a render-then-shellcheck step
- cert-distribution postflight asserts only 2 of 8 targets
- Samba password-rotation path (smbclient auth-probe → smbpasswd) has no
  molecule coverage; same for the qm/pct firewall=1 reconcile failure path
  (inject a failing qm set in the existing stub) and collect-state's
  tri-state classification (partial-readiness fixtures)

**Refactors (explicitness vs duplication tradeoffs documented in review)**:
- update-k3s-nodes.yml: ~90-line cordon/drain/runner-relocation block ×3 →
  `_k3s-drain-node.yml` include (pattern: `_reboot-if-needed.yml`)
- base: e1000e/atlantic NIC workaround near-twins; k3s role server/agent.yml
  ~60-line overlap
- check-versions.py: three apt-Packages fetch/parse implementations → one
  helper; .gitlab-ci.yml: kubectl+kubeconfig install block ×2, versions-render
  logic ×4 → scripts/flux-render.sh
- archive-backupctl: derive MAP/RMAP/lock lists from SRC_LIST; add `-s` to the
  restore-path receives (replication receives already resumable)

**Smaller correctness/hardening follow-ups**:
- zfs_exporter tarball sha256 pin (digest fetch was rate-limited during the
  review; add `zfs_exporter_sha256` to all.yml + get_url checksum)
- nas_storage: mergerfs auto-remount chain is structurally dead (findmnt -t
  none / SOURCE matching) — rewrite or remove + always warn; zfs.yml property
  task compare-before-set idempotency; stop managing archive/* mountpoints in
  host_vars (fights backupctl lockdown); add x-systemd.requires=zfs-mount to
  mergerfs fstab options
- unbound: drop unbound-control-setup certs (unix socket needs none)
- proxmox_lxc: surface pveam download failures at download time; DNS-verify
  task can rewrite resolv.conf but is changed_when: false
- proxmox_vm: document create-only semantics (cores/memory don't reconcile);
  vm_additional_disks positional-slot lifecycle; nic_tuning per-NIC
  persistence via if-up.d + stale drop-in cleanup when list empties
- proxmox_ha: groups.yml legacy path; rule-comment removal never converges;
  cluster.fw: confirm 9345 (RKE2 supervisor, not k3s) can drop.
- base: requirements.yml >= floors vs pinning philosophy; alloy apt package
  unpinned (ssh hardening now lives in a validated `sshd_config.d/00-hardening.conf`
  drop-in with an `sshd -T` effectiveness assert — docs/03)
- home_assistant: no rollback when `ha core check` fails post-deploy
  (node_exporter_host now ships a smartmon textfile collector feeding the
  SMART* alerts — docs/12)
- smtp_relay: role-default smtpd cert paths point at a layout nothing
  populates; submission service should override smtpd_relay_restrictions;
  smtp_tls_mandatory_protocols unset
- update-k3s-nodes: assert k3s_token non-empty before agent upgrades
- HAOS cert-receiver hardening: HAOS keeps the legacy scp cert push
  (operator-managed authorized_keys, no sudo); pin its key to a
  `/config/cert-receive.sh` forced command via the SSH add-on — runbook in
  docs/09-certs.md
- CI: host_vars changes don't trigger consuming deploy jobs; version-check
  schedule hard-fails on routine "updates available" and its MR-comment path
  never gets GITLAB_API_TOKEN; prefer the GitLab agent context over the
  static kubeconfig in .k3s-deploy-base
  (`OP_SERVICE_ACCOUNT_TOKEN` protection: **done** — it is protected, and
  docs/13 § Validate Stage carries the accepted costs)
- k8s: add helm.sh/resource-policy=keep annotations for MetalLB/ESO CRDs;
  consider a staging ClusterIssuer for cert iteration; gotk-sync.yaml carries
  an obsolete migration comment block
- Alertmanager: AlertmanagerClusterFailedToSendAlerts fires critical at tiny
  failure ratios during storms (1 failed Discord post in a 5m window) —
  consider routing it warning-severity or raising the threshold

**Open follow-ups from the R4 mega-review hardening MR** (carried out of that
MR's deploy runbook, which is not tracked in git):

- **Promote `scripts/flux-env.sh` into weisssrv-lib.** It is a byte copy of the
  cluster-template's file but is *not* in the library's vendored registry, so
  nothing keeps the two in step. Alternative: fold multi-ConfigMap support into
  `flux-render.sh` and retire the wrapper.
- **`sg-smtp-relay` :25 has no inventory seam** — the rule is hardcoded in the
  library template. Postfix now refuses unauthenticated relay on it, so the
  exposure is closed at the application layer; the firewall half is a lib MR.
- **`backup_restore_drill_sources_covered` gauge is not built.** It is the
  prerequisite for any drill *coverage* alert.
- **No archive-restore-drill unit in `nas_storage`** — there is no restore-side
  metric, which is why the matching alerts were skipped rather than written.
- **No parity gate for the two secret environments.** The Taskfile task `env:`
  blocks and the matching CI job `variables:` were reconciled by hand; the
  pytest asserting set equality was never written, so nothing prevents re-drift.
- **`deploy-preflight` cannot catch a job that forgot an `op://` variable.**
  Stated in the job header; closing it needs a different check.
- **Nothing enforces a yamllint config for `ansible/`** since `ansible/.yamllint`
  was deleted (nothing read it). Wants either a repo-root `.yamllint` or a
  `yamllint -c` change in `Taskfile.yml`.

## Infrastructure Improvements

### Security hardening

- [ ] Network segmentation with VLANs (IoT, guest, management) — detail and the
  interim admin-IPSet option are under
  [Network segmentation](#network-segmentation--admin-ipset-tightening) above.
- [ ] **Agent guardrails: add the cluster-mutating verbs to `deny` in the tracked
  `.claude/settings.json`.** Its 17 deny rules already cover the irreversible
  verbs (delete ns/pvc/pv, `helm uninstall`, `terraform destroy`,
  `terraform apply -auto-approve`, `ssh * sudo rm|dd`, force-push, hard reset).
  What is still missing is the set the development skill declares
  non-negotiable: `kubectl apply -k`, `kubectl patch`, `kubectl annotate`,
  `kubectl label`, `kubectl rollout restart` (which the skill's `debugging.md`
  says never to use on a Flux-managed workload — kustomize-controller reverts
  it), plus `git push * main`. Deny wins over a local allow, which is the point:
  the gitignored `.claude/settings.local.json` currently allows all of them.
  Keep the rule scoped to `kubectl apply -k` — denying bare `kubectl apply`
  would break `task flux:dev-apply`, the one sanctioned in-cluster write path.
  Applying settings changes is an operator action; an agent cannot edit its own
  permission configuration. (The companion `pre-commit install` half of this
  item has landed — SKILL.md § Pre-MR gates and `references/cluster-access.md`.)

- [ ] **MetalLB stays held at 0.15.3.** The 0.16.x apiserver-flood regression
  (metallb#3063) has a merged upstream fix (#3079, merged 2026-08-05) but no
  release carries it yet — the latest tag is v0.16.1 (2026-05-27). Re-check the
  releases page before any unhold; the pin and its reason are in
  `group_vars/all.yml`.

### GitOps / Flux bootstrap robustness

- [ ] **CoreDNS pod topology spread.** The HPA pin (`configs/coredns/hpa.yaml`,
  min == max == 2) guarantees two replicas but not that they land on different
  nodes, so a single node loss can take out both. k3s owns the CoreDNS Deployment
  (a bundled AddOn) and resets it, so a durable `topologySpreadConstraints` needs
  `coredns` in `k3s_disable` (`group_vars/k3s.yml`) plus a self-managed CoreDNS
  manifest in the k3s server manifests dir. Self-managing CoreDNS is a live
  cluster-DNS migration and should be its own closely-watched change.

### Terraform / Cloudflare

- [ ] **Cloudflare provider v4 → v5 migration.** `terraform/cloudflare/versions.tf`
  pins `cloudflare/cloudflare` at `~> 4.52.0`; v5 is a breaking rewrite that
  removed or renamed every resource this config uses — `cloudflare_record` →
  `cloudflare_dns_record` (different argument schema; CAA `data {}` blocks become
  a typed `data` object) and `cloudflare_zone_settings_override` → per-setting
  `cloudflare_zone_setting` resources. Migrating means rewriting every resource
  plus a `terraform state mv` for each, so it is its own change — do not bump to
  v5 incidentally.

### Storage

- [ ] ZFS scrub-completion ZED email (per-scrub success/error notification;
  scrub *staleness* already ships via the `ZFSPoolScrubStale` alert).
- [ ] Consider ZFS special devices for metadata acceleration.

### Documentation

- [ ] Network topology diagrams (draw.io or Mermaid).
- [ ] Troubleshooting flowcharts.
- [ ] **A human-facing architecture page.** The cluster template ships
  `docs/ARCHITECTURE.md` (two lifecycles, the Flux stage graph, the substitution
  model, a backend-seam table); this repo's equivalent map lives only in
  `CLAUDE.md`, which is agent-facing. Adding the twin here also gives the
  template a live page to diff its claims against.
- [ ] **Rename the two odd cross-link headings** — `ansible/TESTING.md`
  § References and `kubernetes/README.md` § Documentation — to
  `## Related documentation`, so grepping the convention's name returns the whole
  doc set (README § Documentation conventions).

### Terraform and CI gates

- [ ] **Extend the `policy.hujson` gate beyond syntax.** It parses HuJSON and
  checks the five top-level keys; it does not assert that every `tag:` used in
  `acls`/`ssh`/`autoApprovers` has a `tagOwners` entry, nor that
  `autoApprovers.routes` covers `tailscale_advertise_routes` from the inventory.
  Both are cheap and match the house gate style (`check-cluster-literals.py`,
  `check-netpol-except-parity.py`).
- [ ] **Assert `keys(local.proxy_providers) ⊆ embedded_outpost.proxy_provider_keys`.**
  The module builds the outpost's provider list purely from that key list, so a
  forward-auth provider omitted from it plans clean and 404s at the outpost.
  Today the two sets are 10/10 by hand.
- [ ] **Reject a `custom_scope_mappings` expression referencing
  `request.user.attributes`.** The basic-auth injection credentials ride group
  attributes, which merge into member user attributes, so such a mapping would
  emit them into ID tokens. No present exposure — the one authored mapping
  returns `email`/`email_verified` — this is a guard against a future edit.
- [ ] **Teach `check-lib-pins.py --fix` about the Terraform `?ref=` pins.**
  `scripts/test_site_configs.py` already *fails* a mismatched ref pre-merge, so
  coverage exists; what is missing is the one-command rewrite, leaving a bump
  partly manual.
- [ ] **Protect release tags in the `weisssrv-lib` GitLab project** (a project
  setting, not an edit). Terraform's lock file covers providers only — module
  sources are re-resolved on every `init`, so a moved tag silently changes
  infrastructure code. Confirm the setting before treating this as open.

### Observability

- [ ] **Detect a wholly-absent
  `proxmox_corosync_health_collector_last_success_seconds`.**
  `CorosyncHealthCollectorStale` only catches "metric exists but stuck", not
  "metric never appeared". Bridging it needs a host-derived label joining
  `up{job="observability/node-exporter-host"}` to the textfile metric (their
  `instance` labels match by construction — both come from the same node_exporter
  scrape). Add a recording rule or extend the existing alert once the join is
  confirmed in prod.

- [ ] **Root-cause hindsight/llama's anonymous RSS growth.** The llama.cpp
  container's memory is dominated by anonymous (non-reclaimable) pages that keep
  climbing between restarts rather than settling at the model's resident size, so
  its 4Gi limit is sized off "what it has reached" instead of a measured steady
  state. Its VPA is `Off`, so nothing acts on the recommendation and nothing
  alerts until it OOMs — the growth is only visible in the container memory
  panels. Establish whether it is the KV cache growing with context, GGUF mmap
  accounting, or a genuine leak, before the next limit bump; a restart to test is
  expensive (~30 min GPU model reload, and the 900m CPU request has to be
  re-satisfied on a node near its ceiling).

### Autoscaling

- [ ] **Re-derive the VPA caps the gate cannot see.** The scoped cap rule
  (docs/33 § Limit oscillation) is now enforced by the vendored
  `scripts/check-hpa-vpa-invariant.py` under `task flux:lint`, and every policy
  it can judge conforms with an empty `vpa_cap_allowlist` — the *arrs,
  mealie/mealie-postgres/bar-assistant/meilisearch/salt-rim, the small exporters
  (adguard/exportarr/plex/redis/proxmox), registry-cache, tailnet-dns and
  wg-easy were re-derived from their declared limits, and the download clients'
  one-shot init containers moved to `mode: "Off"`. Live sizing changes for those
  workloads on the next admission, so watch for `VPARecommendationCapped` after
  the deploy. Still outstanding: the policies whose target limits never enter
  the kustomize corpus — the flux-system controllers (1Gi caps against the 1Gi
  limits in upstream `gotk-components.yaml`), both gitlab-runners and grafana
  (chart-set limits). Re-derive those by hand against the rendered chart output;
  teaching the gate to read HelmRelease `.spec.values` would fold them in, and
  that is a **weisssrv-lib** MR + tag + re-vendor, not an edit in this repo.

---

## Shipped (historical)

Everything below is done and covered by a current doc. Kept as a one-line index
only — the detail belongs to the owning document, and git history holds the
implementation story.

| Area | Outcome | Canonical doc |
|---|---|---|
| Base infrastructure | 6-node Proxmox cluster, ZFS pools, DNS pair + Unbound, SMTP relay, certs, firewall, Tailscale | [01](01-overview.md), [06](06-zfs.md), [08](08-dns.md), [11](11-firewall.md) |
| K3s platform | 9 nodes (3 servers + 6 agents), kube-vip API VIP, MetalLB, Traefik, external-dns, ESO | [19](19-k3s-deployment.md) |
| Proxmox HA | HA groups + storage replication for dns-01/dns-02/smtp-relay/HAOS | [12](12-runbooks.md), [25](25-multi-node-expansion.md) |
| GitLab | Self-hosted EE on a NAS-pinned VM; registry, Pages, runners, agent, SAML SSO | [27](27-gitlab-deployment.md) |
| GitOps | Flux CD reconciles all of `kubernetes/`; five chained infrastructure stages + apps, plus the off-chain metrics-server stage | [29](29-flux-operations.md) |
| Observability | Prometheus + Grafana + Loki + Alloy, exporters, dashboards, alert routing | [31](31-observability.md) |
| Autoscaling | VPA tiers, HPAs, CoreDNS pin, lint invariants | [33](33-autoscaling.md) |
| Applications | Plex, download/media stack, recipes, Home Assistant, Hermes, Homarr, wg-easy, Immich, Nextcloud, Windows VM, Uptime Kuma | per-app docs 20-24, 35-41, [45](45-uptime-kuma.md) |
| SSO | Authentik as the identity provider; objects codified in `terraform/authentik` | [40](40-authentik-terraform.md) |
| Storage encryption | Per-dataset ZFS encryption roots, passphrase-from-Connect boot unlock | [32](32-zfs-encryption.md) |
| Offsite backups | Nightly restic → Backblaze B2, GFS retention, client-side encryption | [42](42-offsite-backup.md) |
| GPU | GTX 1660 Ti VFIO passthrough to the k3s GPU agent, time-sliced device plugin | [43](43-gpu-passthrough.md) |
| k3s secrets encryption | Enabled cluster-wide; rotation stage `reencrypt_finished` | [17](17-disaster-recovery.md) |
| NFS over TLS | Every k3s export line and `/export/tank-proxmox` require `xprtsec=tls`; PVs mount by hostname | [07](07-fileservices.md) |
| metrics-server HA | Moved off the k3s static AddOn to a Flux HelmRelease: 2 replicas, PDB, anti-affinity, pinned limits; the live cutover landed 2026-08-13 | [33](33-autoscaling.md) |
| Off-node etcd snapshots | `k3s_etcd_snapshot_offnode_enabled` copies each server's snapshots to the NAS | [17](17-disaster-recovery.md) |
| Multi-repo tenants | Tenant onboarding via `weisssrv-app-template` + wiring under `kubernetes/clusters/weisssrv/tenants/` | [30](30-multi-repo-onboarding.md) |

**Related repositories.** The family is four repos: this one, the shared CI
library `eric/weisssrv-lib`, the cluster scaffold `eric/weisssrv-cluster-template`
that weisssrv was generalized into, and the tenant scaffold
`eric/weisssrv-app-template`. Generalizable changes belong in the library or a
template rather than here. [docs/13](13-ci-cd.md) § Shared CI library owns the
pin/bump flow; [docs/30](30-multi-repo-onboarding.md) owns the app template's
contents.

---

## Related documentation

- [docs/12-runbooks.md](12-runbooks.md) - operational procedures
- [docs/13-ci-cd.md](13-ci-cd.md) - pipeline structure and the shared CI library
- [docs/17-disaster-recovery.md](17-disaster-recovery.md) - disaster recovery
- [docs/19-k3s-deployment.md](19-k3s-deployment.md) - k3s cluster deployment
- [docs/25-multi-node-expansion.md](25-multi-node-expansion.md) - multi-node HA expansion
