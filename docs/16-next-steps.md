# Next Steps and TODO

This document tracks **remaining** work for the weisssrv homelab: accepted
risks, open work, and the deferred backlog. Completed work is summarised at the
end under [Shipped](#shipped-historical) — git history is the real record.

Per-area detail lives in the numbered docs; this page carries only what is not
done.

---

## Accepted risks

Deliberate, documented, and not planned for any near-term window. Listed here so
a future reviewer does not re-raise them as gaps.

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

### Hindsight llama models have no backup

The GGUF weights under `ssd/appdata/hindsight/models/` are restic-excluded and
re-downloaded on first start, so a restore brings the model bank back empty
until the download finishes. Hindsight's agent memory itself is backed up; see
[docs/42](42-offsite-backup.md) for the coverage rows and
`kubernetes/apps/hindsight/README.md` for the dump and restore procedure.

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
- **Gateway UI (`router.esweiss.com`)** — closed as a plaintext hop 2026-08-21:
  the UniFi UCG serves its UI over HTTPS only, so Traefik reaches it on `:443`.
  The residual is that the certificate is the gateway's own self-signed one, so
  that single backend uses a dedicated `unifi-self-signed` ServersTransport
  with `insecureSkipVerify` ([docs/46](46-unifi-network.md)) — one LAN hop to
  the default gateway itself, behind `lan-tailscale-strict`.

The adguard-exporter hop is closed: it scrapes `https://dns-0X.esweiss.com` via
`hostAliases`, and `k3s_nodes` no longer appears on AdGuard's :3000 rule, which
now serves `admin_ts`/`admin_lan` break-glass only.

Both remaining hops are acceptable residual LAN-trust hops; the user-facing edge
is HTTPS throughout. The posture table is
[docs/47](47-security-posture.md) § In Transit.

### UniFi `homeassistant` admin is a full-privilege, unvaulted credential

The Home Assistant UniFi Network integration authenticates as a local
**Super Admin** (`homeassistant`) that is deliberately **not** in the Homelab
vault — the credential lives only in HA's own config store. This is a genuine
exposure, recorded here rather than minimized: it is full controller admin with
no 2FA, so a compromise of Home Assistant is a compromise of the whole UniFi
network — the blast radius is **not** bounded. The operator accepted it
knowingly (2026-08-30), judging the rotation burden not worth it for this
account. Standing mitigations: HA sits behind Authentik SSO with no external
ingress, and the account can be disabled on the console in seconds if HA is ever
suspected. The obvious hardening if the risk appetite changes — reduce it to a
scoped role (the integration needs write only for client-block / PoE /
WLAN-toggle) and vault it for recovery — is available if wanted, but **the
operator's settled decision (re-confirmed 2026-09-02) is to KEEP it as-is**: a
full-privilege super admin, accepted risk. This is a closed decision, not an
open item — the recurring audit re-raise resolves here
([docs/46](46-unifi-network.md) § Codified vs manual).

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

### The container registry has no offsite copy

The registry blobs live on the `gitlab-repos` zvol, and neither of the two
copies a GitLab restore usually leans on holds them: the nightly tarball skips
them (`gitlab_backup_skip: "registry,artifacts"`) and `hosts.yml` keeps the zvol
out of vzdump (`vzdump_backup: false`). Their one copy is the on-site
raw-encrypted `ssd/appdata` to `archive` ZFS replication. Nothing reaches B2
either, because restic walks `/mnt/ssd/appdata` on the NAS, where that zvol
child is an empty mountpoint. Images are re-pushable from the Dockerfiles in
this repo, from the weisssrv-lib CI images and from upstream, so losing the
archive copy as well costs a rebuild, not data. Accepted.

### Network fabric is a single point of failure

One switch carries the whole estate and corosync has a single ring. A second
ring is cheap to add later (the managed switch has a spare SFP+ and 5406 stays
reserved for ring1), but it is not planned for any near-term window.

### The NAS has no UPS

Nothing rides out a mains cut. May eventually happen; not planned for the time
being.

---

## Open work

An MR could start on any of these today, grouped by owning area. No application
is queued — everything here is platform and operations work, and a new app would
start as an entry in this section.

### Platform and applications

- [ ] **NAS 192B-slab kernel leak — wait for a fixed kernel.** The running
  7.0.14-line kernel leaks an unreclaimable merged 192 B slab at ~4 GiB/day on
  pve-nas-01. The tenant is `skbuff_ext_cache`, leaked by br_netfilter per
  bridged frame, fleet-wide but NAS-dominant because the NFS data plane rides
  its bridge. `HostSlabLeakSuspected` pages roughly weekly and each page means a
  NAS reboot window. Watch Proxmox kernel changelogs for a br_netfilter /
  skb_ext fix (`apt-get changelog proxmox-kernel-7.0`); after a fixed kernel
  installs, a flat week of
  `node_memory_SUnreclaim_bytes - node_zfs_arc_size` in Grafana retires the
  reboot cadence and, optionally, `slub_nomerge`. The cache was formerly
  displayed under the `file_lock_cache` alias; with `slub_nomerge` armed it
  reports as `skbuff_ext_cache`. Arming `slub_nomerge` on the other five hosts,
  to settle the fleet-wide 230-380 MB/host/day claim, is an owner decision and
  its own MR. Mitigation under trial, staged: pve-opt-02 carries
  `proxmox_firewall_nftables: true` in its `host_vars`, so verify the guest
  firewall still filters there (including `sg-syslog-vip` on the ingress
  agents), then watch that node's slab trend for a week — the per-host rate
  flattening is the success metric. The NAS follows in its own supervised MR
  once opt-02 holds. Keep the
  `HostSlabLeakSuspected` weekly reboot pager until a week of flat
  `node_memory_SUnreclaim_bytes - node_zfs_arc_size`. Mechanism, fingerprint,
  bpftrace recipe and the reboot procedure:
  [docs/06 § Kernel 192-byte slab leak](06-zfs.md).
- [ ] **CoreDNS pod topology spread.** The HPA pin (`configs/coredns/hpa.yaml`,
  min == max == 2) guarantees two replicas but not that they land on different
  nodes, so a single node loss can take out both. k3s owns the CoreDNS Deployment
  (a bundled AddOn) and resets it, so a durable `topologySpreadConstraints` needs
  `coredns` in `k3s_disable` (`group_vars/k3s.yml`) plus a self-managed CoreDNS
  manifest in the k3s server manifests dir. Self-managing CoreDNS is a live
  cluster-DNS migration and should be its own closely-watched change.
- [ ] **MetalLB stays held at 0.15.x.** 0.16.x floods the apiserver on this
  topology. Confirm a chart release ships the upstream fix before unholding;
  the pin and its reason are in `group_vars/all.yml`.
- [ ] **external-dns annotation-prefix migration.** Retag every IngressRoute in
  weisssrv, `weisssrv-app-template` and `weisssrv-cluster-template` to
  `external-dns.kubernetes.io/`, then drop `--annotation-prefix` from
  `controllers/external-dns/release.yaml`
  ([docs/08](08-dns.md) § Annotation prefix pin).
- [ ] **Admission policy for tenant namespaces.** Tenant Flux Kustomizations are
  RBAC-scoped per namespace, so a tenant cannot apply into another namespace.
  What RBAC does not constrain is the *content* of resources a tenant
  legitimately owns: which `ClusterSecretStore` its ExternalSecrets reference,
  and — because Traefik runs
  `--providers.kubernetescrd.allowCrossNamespace=true` — an IngressRoute in the
  tenant namespace whose `Host()` rule and priority shadow a platform hostname.
  A `ValidatingAdmissionPolicy` (built into the API server, so no Kyverno or
  Gatekeeper install) can constrain both: restrict IngressRoute match rules to a
  per-namespace hostname suffix, and restrict `secretStoreRef.name` to the
  namespace's own store. Scope: one VAP plus one binding per tenant namespace,
  added to `kubernetes/clusters/weisssrv/tenants/`. The cluster has no
  ValidatingAdmissionPolicy objects today.
- [ ] Optional Collabora/OnlyOffice office suite (not deployed).

### Pending supervised steps

One-off operator actions, each removable from this list once applied.

- [ ] **Grow `k3s-agt-prec-01`'s root disk 64G → 128G.** `qm resize 207 scsi0
  128G` on pve-prec-01, then `growpart` + `resize2fs` in the guest. `hosts.yml`
  already declares 128G; `proxmox_vm` applies disk size at qm-create only, so
  this needs a drain in the next scheduled host window.
  `KubeletImageGCIneffective` is a true positive until then.
- [ ] **Re-register the privileged runner as a project runner.** The
  `gitlab-runner-privileged` token is registered instance-wide, so any project
  on this GitLab can claim root+DinD execution by declaring
  `tags: [infrastructure]` — the registration scope, not the tag, is the
  isolation boundary. Create a project runner locked to weisssrv (tags
  `infrastructure`, untagged no), store its `glrt-*` token in the 1Password
  `GitLab Runner Privileged` item so ESO re-renders the Secret, restart the
  runner Deployment, then delete the old instance runner. Steps:
  [docs/27](27-gitlab-deployment.md) § Step 8.

### UniFi network follow-ups

Design, runbook and the codified-vs-manual contract:
[docs/46-unifi-network.md](46-unifi-network.md).

- [ ] **Offsite console backup + a real cadence.** The `.unf` lives only on the
  console's own storage and dies with it, and it carries exactly the
  console-owned set Terraform does not: the switch port map, mDNS scope, IPS
  state, 6 GHz radios, adoption, and the three admin accounts. Set it daily,
  then pull the newest `.unf` onto pve-nas-01 so the nightly restic → Backblaze
  B2 job ([docs/42](42-offsite-backup.md)) carries it under the same retention
  and client-side encryption. The two API values disagree on whether
  auto-backup is on, so read Control Plane → Backups to settle it. Losing the
  UniFi layer blocks reaching everything else during a recovery, and
  [docs/17](17-disaster-recovery.md) assumes the network is up.
- [ ] **Vault the Owner-account recovery codes.** The console's MFA posture
  reduces to the 2FA on the single ui.com Owner account — the `terraform` and
  `homeassistant` accounts are local-only and cannot carry 2FA. With Remote
  Access on, a lost factor is a lockout with no second admin to recover
  through, so the recovery codes belong in the Homelab vault.
- [ ] **Alert on UniFi console events.** Gateway syslog reaches Loki
  (`alloy-syslog` on the syslog VIP) and native site alerts are on, but nothing
  turns those lines into Alertmanager alerts: no Loki ruler rule matches
  device-down / WAN-failover / IDS events, and `report_wan_event` is off on both
  WANs, so a WAN2 failover onto the pre-bound-but-empty SFP+ 2 goes unnoticed.
  Add the rules next to
  `kubernetes/infrastructure/observability/loki/host-log-staleness.yaml`; ui.com
  cloud email stays the out-of-band fallback. **Survey first**: read 24 hours of
  the gateway's own lines in Loki and write the rules against the formats they
  actually use. A guessed regex ships a rule that matches nothing and looks
  green, which is worse than no rule. The IPS-block-spike and WAN-failover arms
  are blocked on that survey; the rest of the UniFi warning set is not.
- [ ] **CyberSecure CEF/IPS export — a second receiver path.** UniFi emits its
  CEF/IPS events without a syslog PRI, and `loki.source.syslog` cannot frame
  them, so those events are deliberately **not** collected today. Framing them
  needs a receiver that accepts unframed datagrams, and that is useless until
  the console-side export is retargeted at it — so both halves are one change.
  No framing-error alert: with the export as it is, it would fire forever.
- [ ] **Gateway SYN-flood protection (`usg.syn_cookies`) — enable or record as
  accepted.** Off on the site's only internet-facing device. The provider
  exposes the attribute but the lib `unifi-network` module leaves it unset, so
  the provider round-trips it and it stays console-owned. Decide: flip it in the
  console, or codify it by adding `syn_cookies` to `site_settings` in the module
  (a lib MR + tag + four-repo pin bump).
- [ ] **Re-baseline the IPS category set on Suricata 8.** Inline blocking is
  live (`ips_mode = "ips"`), but 34 of ~53 categories are enabled, with no
  current-events category and `memory_optimized` trimming the ruleset, against a
  Suricata 6 engine. The engine upgrade is blocked **device-side**, not here:
  the gateway reports `EVT_GW_UpgradeSuricata status:INSUFFICIENT_MEMORY
  target_version:8` hourly at Info level, so there is no repo-side lever.
  Re-baseline the category set, a current-events category included, once the
  engine lands. Upstream #381 keeps alert suppressions a UI concern.
- [ ] **Re-check `ignore_changes = [ips]` on UniFi OS 10.6.** The module
  workaround was written against 10.5. If the `ips` write no longer flaps the
  ignore can come off and the console-vs-Terraform split on IPS resolves
  ([docs/46](46-unifi-network.md) § Site settings).
- [ ] **Name the ALLOW_ALL default-security-posture decision.** The site knob
  `global_network.default_security_posture` is `ALLOW_ALL`; the deny-by-default
  this design relies on comes entirely from Terraform putting each VLAN in a
  custom zone, so a network created in the UI lands in the built-in `Internal`
  zone wide open. Either flip the posture to Block All and add the three
  `Internal →` ALLOW policies the mgmt VLAN needs, or record ALLOW_ALL as
  deliberate with the "adding a VLAN is not a UI-safe op" trap it implies
  ([docs/46](46-unifi-network.md) § Codified vs manual).
- [ ] **DHCP guarding — enable per-network or accept snooping-only.**
  Per-network guarding (`dhcpguard_enabled`) is off on all six networks and
  switch-side DHCP snooping is the only rogue-DHCP protection in place. Decide
  between enabling per-network guarding with the gateway as the sole allowed
  server (a UI change — the provider drops `dhcp_guarding.servers` on write,
  #419) and recording snooping-only as sufficient.
- [ ] **Gateway Local DNS records for the GitLab family (optional).**
  `static-dns` is empty. Adding git / registry.git / pages.git .ericsweiss.com →
  `10.0.10.101` on the gateway makes a fallback to the gateway resolver safe,
  backing up the AdGuard split-horizon rewrites. Console change, no provider
  resource.
- [ ] **Tighten the pure access ports to native-VLAN-only.** UCG 1-3 and USW 1-6
  carry the controller's default **All** port profile (native VLAN plus every
  tagged VLAN forwarded), so a compromised device on one could VLAN-hop past the
  zone policies. UCG 1 is the Hue bridge, an untrusted IoT appliance, which is
  the real motivation. Leave the real trunks alone — USW 7 (native Home plus
  tagged 10/30), USW 8 (AP) and USW 10 (SFP+ uplink). Console change:
  `unifi_device.port_override` is unsafe at provider 0.55.0 (#438/#430/#431).
  Do it after the offsite console backup exists, since a bad port override is
  exactly what that backup recovers from.
- [ ] **A real identity boundary for admin devices.** The `10.0.20.8/29` admin
  block is a DHCP-reservation convention on a shared VLAN, so a Home device that
  statically claims an address in it inherits the block's L3 trust. A dedicated
  admin SSID/VLAN (own PSK at minimum) would turn it into an authenticated
  boundary.
- [ ] **Move the Windows VM (.155) to the Home VLAN** — it is a client machine
  sitting on the homelab segment ([docs/39](39-windows-vm.md)).
- [ ] **Re-verify the bond invariant against the new switch.** The docs/34
  `all_slaves_active 0` invariant was last verified against the old switch, and
  the three standby bond members (the opt nodes' `nic0`) show 9 link-down events
  each over ~8 days while their partners show zero. Run
  `cat /sys/class/net/<bond>/bonding/all_slaves_active` (expect 0) on the three
  bonded hosts plus `ethtool -S nic0` / `journalctl -k` for e1000e
  carrier/hang events: matching counts mean the e1000e story continues on the
  standby leg, clean hosts mean it is bonding-driver noise
  ([docs/34](34-bond-mac-flapping.md)). While there, decide whether to move the
  three bonded opt hosts to LACP/802.3ad now that they land on
  USW-Pro-XG-8-PoE access ports (USW 1-6); active-backup is not broken, so this
  is optimisation, not remediation.
- [ ] **Clear the stale older-generation `subvol-150-disk-0` replicas** on
  pve-opt-01, pve-opt-03 and pve-laptop-01. They are inert, and misleading
  during a recovery.
- [ ] **A repeat host-dark event on an opt node with a clean kernel log is a
  DIFFERENT fault**, not an e1000e regression: no `Detected Hardware Unit Hang`
  line means check MCE/EDAC counters, the BIOS event log and the UCG
  switch-port log for that minute. pve-opt-02 froze 2026-09-09 10:44 to
  2026-09-12 13:54 with no hang line and the tso/gso/gro-off mitigation
  verifiably applied; that event is unattributed. An MCE/EDAC sweep across the
  three opt nodes would settle it.
  [docs/34](34-bond-mac-flapping.md) § Host-dark events without the hang
  signature carries the check.
- [ ] **UniFi metrics into Prometheus** (unpoller or equivalent). The gear is
  observed only by ICMP blackbox probes feeding `NetworkGearProbeFailed`;
  per-port throughput, PoE draw, AP client counts and WAN health are not
  collected.
- [ ] **Provider bump when the blockers clear.** Two things stay UI-only at
  `ubiquiti-community/unifi` 0.55.0 and are worth re-testing on each release:
  switch port/native-VLAN management (#438/#430/#431 make
  `unifi_device.port_override` unsafe) and firewall-policy ordering (#407).
  `unifi_client` in-place updates (#428) are the day-to-day annoyance.
- [ ] **Passphrase validation in the lib module.** `unifi-network` bounds WLAN
  passphrases by character count; the WPA rule is 8–63 printable ASCII octets.
  The weisssrv and cluster-template roots enforce the ASCII form on their own
  variables; fold the same regex into the module's `wlans` validation in the
  next lib release so every consumer gets it.
- [ ] **Restore external observation of the `git` A record.** The cross-domain
  rewrites that stopped the hairpin outages ([docs/08](08-dns.md) § Cross-domain
  rewrites) also stopped four blackbox probes from leaving the LAN. Most of that
  coverage survives elsewhere, but `git` is its own DDNS-managed A record with
  no external probe, so DDNS drift on it would surface only when someone outside
  the house tried to clone. The fix is a probe that genuinely resolves publicly:
  a blackbox module pinned to a public resolver, or a check comparing the
  record's Cloudflare content against the current WAN IP. The wrong fix is
  dropping a rewrite.
- [ ] **UniFi client housekeeping** — unnamed IoT reservations, the two
  unconfirmed `ESP_*` devices, `Panopticon` re-onboarding, the pre-renumber
  `config_network` on the switch and AP, and the optional Flex Mini / dock
  experiments. Detail: [docs/46](46-unifi-network.md) § Client housekeeping.

### Storage

- [ ] **Codify the per-host `local-ssd` storage ids.** `proxmox_backup_storage`
  now declares pve-nas-01's `ssd`, `tank` and `nvme` zfspool ids (and
  `tank-proxmox`), so the at-rest posture of the GitLab / Nextcloud / Immich root
  disks is asserted rather than assumed. The five compute hosts' `local-ssd` ids
  are still hand-created in `storage.cfg`; they carry only k3s VM and HA-guest
  disks, which are plaintext by design ([docs/47](47-security-posture.md)
  § At Rest), so this is a
  reproducibility gap rather than a security one.
- [ ] **Move the *arr SQLite databases off NFS onto block storage.** The config
  volumes behind the `_nfs-pv-arr` component (sonarr, radarr, lidarr, prowlarr)
  hold WAL-mode SQLite on NFS; `local_lock=all` on those PVs keeps the locks
  client-local and is the current fix. Escalate to a zvol-backed PV per app if
  liveness failures return:
  `increase(prober_probe_total{namespace="downloads", probe_type="Liveness",
  result="failed"}[1d])` above zero for a week is the gate
  ([docs/21](21-download-clients-deployment.md) § Troubleshooting).
- [ ] ZFS scrub-completion ZED email (per-scrub success/error notification;
  scrub *staleness* already ships via the `ZFSPoolScrubStale` alert).
- [ ] Consider ZFS special devices for metadata acceleration.

### Observability and autoscaling

- [ ] **Detect a wholly-absent
  `proxmox_corosync_health_collector_last_success_seconds`.**
  `CorosyncHealthCollectorStale` only catches "metric exists but stuck", not
  "metric never appeared". Bridging it needs a host-derived label joining
  `up{job="observability/node-exporter-host"}` to the textfile metric (their
  `instance` labels match by construction — both come from the same node_exporter
  scrape). Add a recording rule or extend the existing alert once the join is
  confirmed in prod.

- [ ] **Throttle the WAN scanner noise on git-over-SSH 2222.** The rule in
  `group_vars/all.yml` opens 2222 to the WAN on purpose and carries `nolog`, so
  the firewall logs nothing — but sshd logs every pre-auth attempt before the
  gitlab-ssh jail bans the source, and `alloy_host` ships the gitlab guest's
  whole journal, so the volume lands in Loki with no diagnostic value. Neither
  throttle is weisssrv-local: a drop or sample stage needs a journal-stage input
  on the library's `alloy_host` role (its `config.alloy.j2` has none), and a
  fail2ban `recidive` jail needs the library's `gitlab` role. Keep successful
  and post-auth lines intact either way.

- [ ] **Push the `*.esweiss.com` wildcard to pveproxy on the scraped Proxmox
  nodes** (`acme_certs_distribution_targets`, `cert_dir /etc/pve/local`,
  `pveproxy-ssl.pem`/`.key`) and switch the proxmox-exporter ServiceMonitor
  targets to node names, so `PVE_VERIFY_SSL` can go true. The comment in
  `kubernetes/infrastructure/observability/exporters/proxmox-exporter.yaml`
  points here.
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
- [ ] **Re-derive the VPA caps the gate cannot see.** The scoped cap rule
  (docs/33 § Limit oscillation) is now enforced by the vendored
  `scripts/check-hpa-vpa-invariant.py` under `task flux:lint`, and every policy
  it can judge conforms with an empty `vpa_cap_allowlist` — the *arrs,
  mealie/mealie-postgres/bar-assistant/meilisearch/salt-rim, the small exporters
  (adguard/exportarr/redis/proxmox), registry-cache, tailnet-dns and
  wg-easy were re-derived from their declared limits, and the download clients'
  one-shot init containers moved to `mode: "Off"`. Live sizing changes for those
  workloads on the next admission, so watch for `VPARecommendationCapped` after
  the deploy. `kubernetes/infrastructure/configs/vpa/flux-system.yaml` is
  re-derived: 819Mi for the three limit-controlling controllers, 256Mi for
  notification-controller. Still outstanding: both gitlab-runners, whose limits
  are chart-set and so never enter the kustomize corpus the gate reads.
  Re-derive those by hand against the rendered chart output; teaching the gate
  to read HelmRelease `.spec.values` would fold them in, and that is a
  **weisssrv-lib** MR + tag + re-vendor, not an edit in this repo.
  The cert-manager controller cap was re-derived (limit 192Mi, cap 154Mi)
  against its ~114Mi 30-day peak. The `wg-easy` and `tailnet-dns`
  `VPARecommendationCapped` watches stay open pending the RequestsOnly decision.
  Grafana is settled: its VPA is RequestsOnly with `maxAllowed.memory` equal to
  the declared 1Gi limit.

### CI/CD

- [ ] **GitLab runner quota headroom is thin.** The two runner quotas are sized
  to the `pods` dimension, not to concurrency
  (`kubernetes/apps/gitlab-runner-privileged/resourcequota.yaml` says so). Their
  hard `requests.cpu` sums to 1.48x allocatable, just under upstream
  `KubeCPUQuotaOvercommit`'s 1.5 threshold. That sum is an **admission ceiling,
  not demand**: actual full-concurrency demand is ~23 cores (12 x 1.5 +
  7 x ~0.6), which does schedule, and a CI burst past allocatable queuing as
  Pending is the accepted design — `ci-jobs` is a negative PriorityClass with
  `preemptionPolicy: Never`, so it waits rather than evicting anything. Only the
  three servers' 6 cores are out of reach
  (`node-role.kubernetes.io/control-plane:NoSchedule`, and the runner pods carry
  no toleration). Resolving the headroom is a capacity decision (lower
  `concurrent`, lower per-job requests, or more hardware), not an edit, and no
  new overcommit rule is warranted. Both quota headers point here.
- [ ] **Move the k8s-touching CI jobs off the 1Password kubeconfig** onto the
  GitLab agent's scoped, short-lived credential. The agent's `ci_access` grant
  was removed — it was unused and its ServiceAccount is cluster-admin — so that
  MR re-adds it, narrowed with the RBAC in
  `kubernetes/apps/gitlab-agent/release.yaml`. The agent config comment points
  here.
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

### Security and access

- [ ] **ESO vault scoping inside `Homelab`.** `Homelab-Admin` (admin/CI-only
  items) and `Homelab-Boot` (the ZFS pool passphrases) are split out, but the
  `Homelab` vault ESO reads is still wholesale. `spec.conditions` on
  `cluster-secret-store.yaml` scopes **which** namespaces may reference the
  store, never **what** they may mint: `provider.onepassword.vaults` is
  `{Homelab: 1}`, so all 16 listed namespaces can read every `Homelab` item —
  the Proxmox API token, the k3s cluster and agent tokens, the k3s kubeconfig,
  the SSH key, the Flux GitLab PAT, the UniFi credential and three Terraform
  tokens among them. Closing it means either per-namespace vaults or a
  per-namespace `SecretStore`. The manifest cites this section.
- [ ] **A second download-client VPN provider.** Privado is the only wired one;
  the half-wired VPN Unlimited branch was removed. Adding one is a single MR:
  the provider credential fields on a 1Password item, matching `secretKey`
  entries in `kubernetes/apps/download-clients/externalsecret.yaml`, a `case`
  arm in `_vpn-sidecar/vpn-sidecar.yaml`, and the matching arms in
  `scripts/vpn-credcheck.sh` and `scripts/downloads-vpn-provider.sh`
  (`scripts/check-vpn-provider-parity.py` holds the three in step).
- [ ] **Write the last four ESO rotation procedures.**
  `observability/alertmanager-config`, `observability/loki-push-auth`,
  `observability/observability-secrets` and
  `tailscale/tailscale-operator-oauth` are reached by no rotation path and sit
  in `scripts/check-secret-rotation-coverage.py`'s `DECLARED_MANUAL` map. Each
  needs either a `task flux:rotate-secret` case in
  `scripts/flux-rotate-secret.sh` or a documented
  `task flux:refresh-secret -- <ns>/<name>` plus restart procedure in
  [docs/15](15-credential-rotation.md); the entry then leaves the map and the
  gate holds it.
- [ ] **Mint a metrics-only Uptime Kuma API key.** The `/metrics` scrape
  authenticates as Kuma's single admin account today, so the scrape credential
  and the only login are the same pair ([docs/45](45-uptime-kuma.md)). Enabling
  Kuma's API Keys feature flips `/metrics` to key-only auth the moment it lands,
  so the UI change, the 1Password field swap and the ServiceMonitor edit are one
  coordinated window, not three commits.

### Terraform and gates

- [ ] **Cloudflare provider v4 → v5 migration.** `terraform/cloudflare/versions.tf`
  pins `cloudflare/cloudflare` at `~> 4.52.0`; v5 is a breaking rewrite that
  removed or renamed every resource this config uses — `cloudflare_record` →
  `cloudflare_dns_record` (different argument schema; CAA `data {}` blocks become
  a typed `data` object) and `cloudflare_zone_settings_override` → per-setting
  `cloudflare_zone_setting` resources. Migrating means rewriting every resource
  plus a `terraform state mv` for each, so it is its own change — do not bump to
  v5 incidentally. Re-check each quarter, and immediately if
  `cloudflare/terraform-provider-cloudflare` announces a v4 end-of-support date
  or a v4 CVE: `gh release list -R cloudflare/terraform-provider-cloudflare |
  head`. `terraform/cloudflare/versions.tf` points here.
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
- [ ] `authentik-auth` middleware consumers ↔ `terraform/authentik` proxy
  providers: a route can gain the middleware without its provider (404 at the
  outpost). Derive the provider list from the `.tf` and diff against the
  IngressRoutes.
- [ ] `k3s_disable` ↔ the self-managed twins: nothing asserts that everything
  in `group_vars/k3s.yml`'s disable list has its Flux-managed replacement (and
  vice versa — metrics-server is the precedent).
- [ ] `test_vendored_byte_identity.py`: add the third hint branch ("registered
  in the library working tree but absent at the pin — bump the pin") mirroring
  the lib's `check-vendored-copies.py` wording.

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

---

## Deferred — needs its own change

Real work, but each item is blocked on measurement, a maintenance window, or a
dependency, so none of it belongs in a general MR.

### Kubernetes and platform

- **De-duplicate the wildcard Certificates.** The
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
- **Express the `*arr` overlay rename patches via a kustomize labels
  transformer** instead of per-overlay name/label patches.
- **Split the `downloads` namespace** into a privileged tier
  (qbittorrent/nzbget) and a restricted tier (`*arr`) so PSS can enforce
  `restricted` on the managers. This is also the only route to PSA `restricted`
  on that namespace — deferred with the split, not separately.
- **Per-namespace egress NetworkPolicies for the 10 namespaces without one.**
  The ingress default-deny is universal; egress is per-app and ten namespaces
  (gitlab, kube-system, observability among them) have no allowlist. Authoring
  them needs measured traffic per namespace and carries high breakage risk —
  weeks of iteration, deferred as its own project.
- **Rate-limiting / in-flight-request middleware on the public perimeter.**
  There is none today. It is blocked on the real-client-IP work above: without
  `trustedIPs` every request buckets under the proxy's address, so a limit would
  either be useless or throttle our own runners. Thresholds also need traffic
  baselining first, or the first incident it causes is self-inflicted. That
  includes the Cloudflare-bypassing hostnames — the same dependency applies.
- **Two hand-maintained mirrors of kube-prometheus-stack rule content.** They are
  correct today; keeping them correct automatically needs a chart-render step in
  the test pipeline, which is the actual deferred work.

### Terraform

- **Delete `terraform/cloudflare/moved.tf`.** Module-adoption scaffolding. A
  `moved` block whose source address is no longer in state is a no-op, so it is
  not causing drift; delete it once the cloudflare supervised apply is confirmed
  landed. Tailscale's is already gone.
- **The supervised authentik MR.** One plan+apply carries: removing
  `terraform/authentik/moved.tf`, an `outputs.tf` re-exporting the module ids,
  the provider bump to a 2026.8.x with a regenerated `.terraform.lock.hcl`, and
  the provider/server lockstep gate (`lint:authentik-provider-pin`, which
  cannot land before the bump because the provider pin is 2026.5.0 against an
  `authentik_version` of 2026.8.2).

### CI/CD

- **Split `.gitlab-ci.yml` into `include:` files.** The single-file
  pipeline is anchor-free (extends/!reference only), so a split is safe in
  principle, but `local:` includes can only be validated by pushing and
  iterating on the live pipeline, and the template/job sections are interleaved.
  Purely a maintainability change.
- **Image pinning, remaining scope.** The ~72 workload images still on mutable
  tags need a digest-refresh workflow first, which is why this is one item and
  not 72. The default runner
  executor images are now digest-pinned (`debian:trixie` in
  `gitlab-runner/release.yaml`, `python:3.11` in
  `gitlab-runner-privileged/release.yaml`), and the molecule-test/molecule-ci
  base images are pinned by manifest-list digest. Still open: the mutable-tag
  CI *job* images in `.gitlab-ci.yml` (`python:3.11-slim`, `alpine:3.23`,
  `koalaman/shellcheck-alpine` — `docker:24.0-dind` and the terraform job
  image are already digest-pinned) and
  the unpinned apt packages in the molecule-test image.
  Resolved manifest-list digest for the secret-bearing subset, ready to use:
  `python:3.11-slim` =
  `sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534`.
  Pinning them needs a matching `scripts/version-registry.py` entry so the
  digests are refreshed rather than left to rot.
- **CI optimizations** — `ci-gitlab-broad-trigger` (move `gitlab_version` to a
  dedicated group_vars file so only it triggers a GitLab reconfigure) and
  `ci-no-build-cache` (add a pip/apt cache to lint jobs). Low-value pipeline
  tuning, best validated against the live pipeline.
- **CI render-loop dedup (partial).** The kustomize
  version+sha256 is single-sourced via the `KUSTOMIZE_VERSION` /
  `KUSTOMIZE_SHA256` CI variables, and `scripts/flux-render.sh` now
  consolidates the 4-site versions-extraction + kubeconform-version derivation
  (Taskfile `flux:lint`/`dev-apply` + CI `flux-lint`/`deploy-verify`). The
  per-Kustomization kustomize-build/kubeconform **loop body** remains
  implemented separately in `flux-lint` and `deploy-verify` — sharing it is
  deferred; the two jobs differ enough (offline kubeconform vs live
  server-side dry-run) that a `!reference` split is low-value churn.

### weisssrv-lib follow-ups

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
- Optional: fold multi-ConfigMap support into `flux-render.sh` and retire the
  `flux-env.sh` wrapper. The wrapper is vendored from weisssrv-lib and
  byte-identity-gated today.
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
- **`check-deploy-playbooks` cannot catch a job that forgot an `op://` variable.**
  Stated in the job header; closing it needs a different check.

### Live ops

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

### Test debt

- **Molecule test build-outs** — the SSH-hardening path, the zvol data-safety
  cases and health-verify resilience all need a runnable molecule environment to
  author and validate.
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

### Refactors

- update-k3s-nodes.yml: the server and agent paths still repeat the cordon task
  and the staged-installer/restart scaffolding (×2); the uncordon and
  kured-serialisation halves are already shared as `_uncordon-and-wait-ready.yml`
  / `_wait-no-kured-server-reboot.yml`
- base: e1000e/atlantic NIC workaround near-twins; k3s role server/agent.yml
  ~60-line overlap
- check-versions.py: three apt-Packages fetch/parse implementations → one helper
- archive-backupctl: derive MAP/RMAP/lock lists from SRC_LIST; add `-s` to the
  restore-path receives (replication receives already resumable)

### Smaller correctness and hardening follow-ups

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
| UniFi network | UCG-Fiber + USW-Pro-XG-8-PoE + U7 Pro XGS; every VLAN its own firewall zone with inter-zone default-deny; the homelab renumbered to `10.0.10.0/24` | [46](46-unifi-network.md) |
| Tailscale ACL | Least-privilege tailnet policy as code; all six Proxmox hosts carry `tag:subnet-router`, and no untagged device can self-approve the LAN route | [05](05-tailscale.md), [11](11-firewall.md) |
| Vault split | `Homelab-Admin` holds the admin/CI items and `Homelab-Boot` the ZFS pool passphrases; the ESO `ClusterSecretStore` reads only `Homelab` | [15](15-credential-rotation.md) |
| Authentik users | Usernames as code in `terraform/authentik/users.tf`, identities in 1Password, scaffolded by `task authentik:add-user` | [40](40-authentik-terraform.md) |
| NIC firmware (AQC113) | pve-nas-01 stays at 1.5.38 factory-equivalent; the `nic_tuning` GRO disable is what keeps the link stable, and no published upgrade is worth the risk | [34](34-bond-mac-flapping.md) |
| CI drift detection | The three Terraform drift-plan jobs allow only `exit_codes: [2]`, so a broken detector no longer renders as drift | [13](13-ci-cd.md) |
| Link-local bypass closed | IPv6 off on pve-nas-01's `nic1`, so the Home VLAN has no unfiltered `fe80::` path to the NAS | [11](11-firewall.md) |
| Drive decommission / RMA | A written SOP for wiping and returning a failed disk | [15](15-credential-rotation.md) |
| Flannel wireguard-native | Pod-to-pod traffic encrypted on the wire (`k3s_flannel_backend` in `group_vars/k3s.yml`) | [19](19-k3s-deployment.md) |
| AdGuard sync over HTTPS | `adguardhome-sync` targets the Traefik-fronted hostnames, so the dns-01 → dns-02 hop is end-to-end TLS | [08](08-dns.md) |
| CI kubectl setup | One `.kubectl-setup` fragment replaces the duplicated kubectl and kubeconfig install blocks in the deploy jobs | [13](13-ci-cd.md) |
| Flux substitution exports | `scripts/flux-render.sh` behind `scripts/flux-env.sh` is the single entry point for the substitution variables the Taskfile, deploy-verify and CI flux-lint all read | [29](29-flux-operations.md) |
| deploy-preflight extraction | The gate is the library's `scripts/check-deploy-preflight.py`, vendored here with `scripts/ci_playbook_invocations.py` and `scripts/ci_yaml.py`, covered by `scripts/test_deploy_preflight.py` and `scripts/test_ci_playbook_invocations.py` | [13](13-ci-cd.md) |

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
