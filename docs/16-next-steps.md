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

---

## Planned work

### Applications

- [ ] **Uptime Kuma** — external endpoint monitoring and status page at
  `status.esweiss.com`. The only queued application — every other planned app has
  shipped.

### Nextcloud follow-ups (not blockers)

- [ ] Grafana dashboard: the upstream xperimental exporter dashboard uses a
  `${DS_LOCAL}` import-input datasource that needs adapting for the sidecar.
  Metrics are queryable in Explore and covered by alerts in the meantime.
- [ ] Optional Collabora/OnlyOffice office suite (not deployed).

### CI/CD

- [ ] **Distributed cache backend for the runners.** Every pip/galaxy/toolchain
  `cache:` block in `.gitlab-ci.yml` is inert because neither runner declares a
  cache backend. Standing up an in-cluster S3/MinIO target (the `registry-cache`
  app is the precedent) is the largest safe wall-clock win in the pipeline —
  see [docs/13](13-ci-cd.md) § Lint Stage.

### Ansible collection migration residue

The Ansible layer now consumes roles from the `weisssrv.infra` collection in
`eric/weisssrv-lib` rather than in-tree `ansible/roles/*`. Residue to watch:

- [ ] Confirm every deploy job's `changes:` list tracks the collection pin
  rather than the deleted role paths, and that `check-deploy-coverage` gates on
  collection-pin semantics.
- [ ] Re-home any molecule scenario still expecting an in-tree role path.
- [ ] Re-check role-README links from `docs/` after the move.

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
  `restricted` on the managers.
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
- **k8s-apps-10 — image pinning, remaining scope.** The default runner
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

- [ ] **Complete the metrics-server cutover: run `task k3s:deploy`** (or play the
  manual `maintenance-k3s-provision` job) so the k3s servers pick up
  `--disable=metrics-server` and the HelmRelease can finally install. There is
  **no automatic deploy job for `group_vars/k3s.yml`**, so nothing closes this
  window but an operator. Until it lands, all of the following are expected and
  none of them is a regression:
  - `infrastructure-metrics-server` is not-Ready by design;
    `scripts/deploy-verify.sh` detects the open cutover live (the AddOn's
    objectset stamp on the APIService), prints it as a NOTICE, and excludes
    only those two resources from its readiness gates — so `deploy-verify`
    stays green and a red job during the window is a real, unrelated failure;
  - `FluxResourceNotReady` fires for both the `infrastructure-metrics-server`
    Kustomization and the `kube-system/metrics-server` HelmRelease — warning,
    Discord, 12h repeat;
  - `task collect-state` cannot return a green verdict (it requires zero firing
    alerts), and `task flux:reconcile` reports that one stage as failed while
    still reconciling every other stage.

  Silence for a long window with
  `amtool silence add alertname=FluxResourceNotReady name=~"metrics-server|infrastructure-metrics-server" --duration=…`
  (the silence mutes those two names entirely, cutover-related or not — keep it
  no longer than the planned window; docs/33 § metrics-server has the caveat).
  Design detail: [docs/33](33-autoscaling.md) § metrics-server.
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
- **13 hand-maintained `molecule-test:<tag>` fallbacks** in the integration
  scenarios have no gate and must be re-bumped alongside `WEISSSRV_LIB_REF`. CI
  overrides them via `MOLECULE_TEST_IMAGE`, so only local molecule runs break.
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

### Observability

- [ ] **Detect a wholly-absent
  `proxmox_corosync_health_collector_last_success_seconds`.**
  `CorosyncHealthCollectorStale` only catches "metric exists but stuck", not
  "metric never appeared". Bridging it needs a host-derived label joining
  `up{job="observability/node-exporter-host"}` to the textfile metric (their
  `instance` labels match by construction — both come from the same node_exporter
  scrape). Add a recording rule or extend the existing alert once the join is
  confirmed in prod.

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
| Applications | Plex, download/media stack, recipes, Home Assistant, Hermes, Homarr, wg-easy, Immich, Nextcloud, Windows VM | per-app docs 20-24, 35-41 |
| SSO | Authentik as the identity provider; objects codified in `terraform/authentik` | [40](40-authentik-terraform.md) |
| Storage encryption | Per-dataset ZFS encryption roots, passphrase-from-Connect boot unlock | [32](32-zfs-encryption.md) |
| Offsite backups | Nightly restic → Backblaze B2, GFS retention, client-side encryption | [42](42-offsite-backup.md) |
| GPU | GTX 1660 Ti VFIO passthrough to the k3s GPU agent, time-sliced device plugin | [43](43-gpu-passthrough.md) |
| k3s secrets encryption | Enabled cluster-wide; rotation stage `reencrypt_finished` | [17](17-disaster-recovery.md) |
| NFS over TLS | Every k3s export line and `/export/tank-proxmox` require `xprtsec=tls`; PVs mount by hostname | [07](07-fileservices.md) |
| metrics-server HA | Moved off the k3s static AddOn to a Flux HelmRelease: 2 replicas, PDB, anti-affinity, pinned limits (manifests shipped; the live cutover stays the open checkbox above until `task k3s:deploy` lands) | [33](33-autoscaling.md) |
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
