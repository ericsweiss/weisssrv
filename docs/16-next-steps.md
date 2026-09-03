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

### Network segmentation / admin-IPSet tightening — DECIDED, shipping with the UniFi migration

Decided 2026-08-21: buy the VLAN-capable tier and segment. The UniFi
UCG-Fiber + USW-Pro-XG-8-PoE + U7 Pro XGS replace the Asus/unmanaged-switch
tier, each VLAN is its own firewall zone with inter-zone default-deny, and
`admin_lan` shrinks from the flat `/24` to the homelab LAN plus the
`10.0.20.8/29` admin-device block on the Home VLAN. Client devices reach
service ports through the new `lan_clients` set and the resolvers through
`dns_clients`, never the management plane. Design, port map, cutover and
validation: [docs/46-unifi-network.md](46-unifi-network.md); the sets
themselves: [docs/11-firewall.md](11-firewall.md) § Client scopes.

**The cutover ran on 2026-08-22** — the estate is on the UniFi gear, the VLANs,
zones and policies are live, and the deltas from the planned runbook are
recorded in [docs/46](46-unifi-network.md) § Cutover as executed.

Behind it, now largely closed (detail and current status in § UniFi network
follow-ups below):

- The switch/AP blackbox probes are green — the `.2`/`.3` reservations were
  applied at the v0.13.2 unfreeze apply and both devices answered there once
  they renewed onto them (resolved 2026-08-30, docs/46 § Post-cutover checklist).
- Connection A is finished — switch port 7 is native Home with VLAN 10 (and 30)
  tagged, DONE 2026-08-23/30.
- Phase 2 — renumbering the homelab from `192.168.0.0/24` to `10.0.10.0/24` —
  DONE 2026-08-25/26 (!255).
- Still open: the Windows VM (.155, [docs/39](39-windows-vm.md)) sits on the
  homelab VLAN; moving it to Home is a follow-up, not a blocker.

### Network-fabric SPOF: second switch + corosync ring — deferred indefinitely

May eventually happen, but not planned for any near-term window. The 2026-08
Ubiquiti wave (Cloud Fiber Gateway + Pro XG 8 PoE + U7 Pro XGS) has now landed
and carried the segmentation work ([docs/46](46-unifi-network.md)); it also
makes a second ring cheaper to add later, since the managed switch has a spare
SFP+ and 5406 stays reserved for ring1. Still deferred: one switch remains a
single point of failure for the whole estate.


### UPS for the NAS — deferred indefinitely

May eventually happen; not planned for the time being.


### ~~Split the `Homelab` vault~~ — DONE

Completed: `Homelab-Admin` carries the admin/CI-only items (service-account
token, Connect credentials, bot tokens, …), `Homelab-Boot` the ZFS pool
passphrases, and the ESO `ClusterSecretStore` reads only `Homelab`. The
remaining, separate idea — per-namespace scoping *within* the ESO-consumed
vault — stays parked (re-score wf-b#8 residual).


### ~~`tank/backups` legacy data and `archive` headroom~~ — CLOSED

Decided and shipped 2026-08-19: the 2021-22 machine backups stay in all
three tiers (tank + archive replication + B2); the nightly restic job
excludes the immutable subdirectories and their existing B2 data is pinned
by a forever-kept `legacy`-tagged snapshot (docs/42 § Legacy machine
backups). `archive` headroom: deliberately untracked — revisit
holistically only if the pool ever runs hot.


### ~~Opt-agent CPU saturation~~ — resolved by the August placement work

Re-measured 2026-08-19: 7d average 23–25%, p95 31–33% across all three opt
agents (was p95 64–68%). The VPA retunes and CI-placement changes absorbed
it; nothing to decide. Watch only — the numbers live in Grafana.

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

### ~~Tailscale ACL least-privilege lockdown~~ — COMPLETE

Finished 2026-08-20: all six Proxmox hosts adopted `tag:subnet-router`
(admin-console tag assignment + the strict `tailscale_tags_require_adoption`
play, green on all six), route approval verified riding the tag
(single-primary failover intact), Tailscale SSH + kube-API verified, and the
owner entry removed from `autoApprovers.routes` — an untagged device can no
longer self-approve the LAN route. Drift watch: `tailscale-drift-plan` stays
the signal. The `admin_ts` firewall-set revisit was taken up in the Ubiquiti
session and **concluded with no change**: the ACL policy already enforces
device/user granularity, so narrowing the ipset would duplicate that layer
while adding DR-lockout risk ([docs/11](11-firewall.md) § IPSets).


### ~~AQC113 firmware update (pve-nas-01)~~ — CLOSED 2026-08-20 (stays at 1.5.38)

Attempted and deliberately closed. The 1.5.48 attempt proved vendor firmware
packages carry per-board MAC/PHY provisioning: flashing the only published
1.5.48 image (a raw Lenovo-Vulcan clx) left the PHY unable to train — no link
even after a cold boot. Recovery used the 1.5.38 package's proper toolkit
(`customclx2` + `updatedata.xml`, whose bdp id=8 exactly matches this card's
generic `1d6a:0001` subsystem) to build a board-customized image; the card is
now at **1.5.38 factory-equivalent, freshly provisioned, 10G verified**. No
further firmware work is planned: the GRO disable in `nic_tuning` keeps the
NIC stable (0 recurrences since codified) and no published upgrade offers a
fix worth the risk. The working flash procedure (VM PCI-passthrough — Hiren's
PE cannot start the miniport) is recorded in the ops memory if it is ever
needed again.

### ~~pve-nas-01 stale manual config cleanup~~ — DONE

Removed 2026-08-19 after verifying the `nic_tuning` drop-in authoritative
across two reboots (`ifquery nic1` clean via the drop-in alone,
`generic-receive-offload: off` live). `/etc/network/interfaces` no longer
carries the manual stanza; backup at `/root/interfaces.bak` on the host.


### ~~Move the wg-easy VIP out of the router's DHCP range~~ — already excluded

Corrected 2026-08-20: the router's DHCP pool was shrunk to end at .98 when
wg-easy was set up, so `.99` is NOT inside the pool and no collision exists.
Closed 2026-08-21: the residual "it lives in router config, not code" half is
gone — the Homelab VLAN's pool is declared as `10.0.10.2`-`10.0.10.98` in
`terraform/unifi/`, so the exclusion of `.99` (and of the `.100`/`.101`/`.161`
VIPs) is now codified and drift-checked ([docs/46](46-unifi-network.md)
§ Networks).


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
is HTTPS throughout. The posture table is docs/06 § In Transit.

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

---

## Planned work

**No application is queued.** Uptime Kuma — the last one — shipped
([docs/45](45-uptime-kuma.md)); everything below is platform and operations
work. A new app starts as an entry here.

### NAS 192B-slab kernel leak — isolate the tenant, then the fix (standing, ~weekly reboots)

The running 7.0.14-line kernel leaks an unreclaimable **merged 192 B slab**
at ~4 GiB/day on pve-nas-01 — displayed as `file_lock_cache` in slabinfo,
but tracing proved that alias innocent (mechanism, fingerprint, bpftrace
recipe and the reboot procedure: [docs/06 § Kernel 192-byte slab
leak](06-zfs.md)). Until it closes, `HostSlabLeakSuspected` pages roughly
weekly and each page means a NAS reboot window. To close it:

1. ~~Identify the tenant~~ **DONE 2026-08-19**: `skbuff_ext_cache`, leaked
   by br_netfilter per bridged frame (`skb_ext_add` via
   `br_nf_pre_routing`/`br_nf_forward`/`br_flood` — bpftrace-captured).
   Fleet-wide but NAS-dominant (the NFS data plane rides its bridge).
2. **Watch Proxmox kernel changelogs** for a br_netfilter / skb_ext fix
   (`apt-get changelog proxmox-kernel-7.0`), and consider reporting
   upstream with the captured stacks (docs/06 has the fingerprint); after
   a fixed kernel installs, a week of flat `skbuff_ext_cache` retires the
   reboot cadence and (optionally) `slub_nomerge`. (The delegations toggle
   is already retired — 2026-08-20, after it was exonerated for the leak
   and proven to cause the *arr SQLite-on-NFS stall regression; docs/06.)

### UniFi network follow-ups (opened by the 2026-08-21 migration)

Design, runbook and the codified-vs-manual contract:
[docs/46-unifi-network.md](46-unifi-network.md).

- [x] ~~**Finish Connection A.**~~ DONE 2026-08-23: switch port 7 is native
  Home with VLAN 10 tagged (VLAN 30 added later for the bedroom Pi's
  self-tagged wired-IoT leg), and pve-nas-01 rides its `nic1.10`
  sub-interface ([docs/46](46-unifi-network.md) § Physical port map).
- [ ] **USW Flex Mini for the Connection A drops (optional).** Two standing
  limits of the dumb TP-Link chain behind port 7: (1) tag-unaware wired
  devices cannot be steered to IoT — a per-MAC override forces *tagged*
  delivery, which black-holes them (the 2026-08-29/30 bedroom-Hyperion
  finding; that Pi now self-tags `eth0.30`, but the wired living-room TV
  stays on Home for the same reason), and (2) the chain is invisible — on
  2026-08-30 it dropped the NAS uplink for four hours (switch restarts during
  hands-on work) with nothing observable but the carrier flap on the NAS. A
  managed USW Flex Mini at the TV/bedroom drops would do the tagging per
  port and show up in the controller. Optional: everything currently works
  without it.
- [ ] **Dock MAC-passthrough experiment (optional).** The HP dock on the
  Connection A run presents its own MAC (`9c:7b:ef:9e:e6:46`) for whichever
  laptop is docked, so per-laptop wired steering is impossible — the work
  laptop docks onto Home today and uses `DunderMiffLAN` over Wi-Fi for its
  own VLAN. If the work laptop's firmware supports MAC address pass-through
  (common on business HP/Lenovo/Dell), the dock would present the laptop's
  built-in MAC and a Work steering reservation becomes possible. Firmware
  toggle + one dock-in to read the resulting MAC.
- [ ] **Confirm and reserve the two `ESP_*` devices — almost certainly the two
  Tuya blinds drivers.** `ESP_70688C` (`48:3F:DA:70:68:8C`) and `ESP_719BF2`
  (`48:3F:DA:71:9B:F2`) are on Home with pool leases. The 2026-08-23 household
  inventory leaves the two Tuya smart-blinds drivers (Eric's bedroom, left and
  right) as the only unaccounted Espressif devices — every Levoit and WLED unit
  announces a real hostname, and no other Wi-Fi Espressif device exists in the
  apartment. Confirm by power-cycling one blind and watching which ESP drops
  (or reading the MAC from the Smart Life app), then add both as IoT
  reservations — a reservation is what moves a wireless device's VLAN
  ([docs/46](46-unifi-network.md) § DHCP reservations). They stay out of
  `terraform/unifi/networks.tf` until confirmed: reserving a misidentified
  device onto a VLAN that denies it everything breaks something nobody can
  name.
- [x] ~~**Home Assistant post-migration sweep (deliberately LAST).**~~ DONE
  2026-08-30: Hyperion entries repointed to `10.0.30.210`/`.211`, the WLED
  and cloud integrations settled with the device migration, the transient
  Sonarr errors cleared with the network, and the deprecated `http:` block
  is deleted — HTTP settings are UI-managed now ([docs/24](24-home-assistant-deployment.md)
  § Configure HTTP Settings, including the resolved `.storage/http` trap).
  HAOS network-storage/backup target repointed to `10.0.10.102` the same
  night. The two Tuya `ESP_*` devices remain under their own entry above.
- [ ] **Re-onboard the steered IoT devices onto `Panopticon`** (the IoT SSID
  since the 2026-08-29 rename), at whatever pace suits — one device at a time
  is fine. Largely done 2026-08-30: soundbar, Fire TV, live WLED bars,
  Levoits, Echoes, scale and the bedroom TV all joined. Per-MAC steering
  places the rest on IoT today, but placement is not authorization: a device
  still holding the `TheRevengers` PSK falls back to Home if its MAC ever
  stops matching the reservation (randomization, spoofing after a compromise,
  replaced hardware). Re-joining `Panopticon` removes the Home credential; the
  reservation keeps steering identically afterward, so nothing else changes
  ([docs/46](46-unifi-network.md) § DHCP reservations). Remaining holders of
  the Home PSK: the Kasa plugs and anything not yet re-joined by hand.
- [ ] **Give the TVs and Echoes friendly names.** Seven IoT reservations carry
  the controller's reported hostname because nobody has mapped them to rooms
  yet: `amazon-01f20c070`, `amazon-5b51cd6d9`, `amazon-a70f51c2d`,
  `amazon-a9c5657f8`, plus `amazon-f57e91` and `amazon-c7d8bc` (Amazon OUI, no
  hostname reported — presumed Echoes), and `vizio-wifi`. Rename them as they
  are identified. Two specifics worth resolving at the same time:
  `vizio-wifi` (`A0:6A:44:50:EE:95`) is most likely the **living-room Vizio
  soundbar** (the 2026-08-23 inventory: three Vizio TVs of which only one is
  wired-connected, plus one Wi-Fi soundbar), and `vizio-cast-display`
  (`3C:9B:D6:7A:36:A3`, wired on the Connection A/MoCA run) is believed to be
  the **living-room TV** — both to confirm before renaming, e.g.
  `vizio-livingroom-tv` / `vizio-soundbar`. **Keep both reservations**
  regardless (steering is per-MAC); the bedroom and Vasim's Vizio TVs and the
  two Fire TV sticks' hosts will need entries as they appear on the network. A rename is an in-place `unifi_client` change, so each
  one needs `-replace` (upstream #428,
  [docs/46](46-unifi-network.md) § DHCP reservations).
- [ ] **Report the provider bugs upstream** (`ubiquiti-community/unifi`
  0.55.0). All of these cost real time during the cutover or the unfreeze and
  none is filed (drafts first, operator reviews before anything is posted):
  - `GetClientByMAC` compares MACs case-sensitively against the controller's
    lowercase spellings (a plain string match over `/rest/user`), so a
    config-cased MAC makes the `api.err.MacUsed` -> adopt path die with
    `not found: type=` — and `allow_existing` can therefore never adopt.
    Same-cause corollary: `mac` is ForceNew, so an imported client whose config
    spells the MAC in a different case is permanently replace-planned.
  - The client read-back after create can report `not found: type=` while the
    object exists, and the site `ips` / WLAN `ap_group_ids` writes round-trip
    controller-owned values into "inconsistent result after apply" errors
    (absorbed module-side in v0.13.2).
  - `unifi_network` writes with the default `setting_preference = "auto"` make
    the controller reset the manual DHCP fields (`dns_enabled`, `domain_name`)
    that the same request sets. Repro: apply a network with those fields, read
    it back, observe them cleared; setting `setting_preference = "manual"`
    fixes it, which is what module v0.13.1 does.
  - `unifi_client` with `allow_existing = true` fails `not found: type=` for a
    MAC the controller has never seen, *and* leaves the object created
    server-side, so the retry succeeds. Repro: apply a reservation for an
    unseen MAC twice.
  - Several resources report read-back inconsistencies right after create
    (`inconsistent result after apply`), tainting them; the object is correct
    on the controller. Repro is timing-dependent — capture `TF_LOG=DEBUG` for
    the create and the immediately following read.
- [x] ~~**Phase 2 — renumber the homelab**~~ DONE 2026-08-25/26 (!255 merged
  after the supervised window; `10.0.10.0/24` live everywhere, old subnet off
  the wire, first fully-green post-renumber main pipeline 2026-08-26).
- [ ] **Decide IPS: detect vs inline block.** Ships as `ips_mode = "ids"`. The
  flip to inline `"ips"` is a **console** action (Settings → CyberSecure), not a
  Terraform edit — the module ignores the `ips` block, so editing `ips_mode` is a
  no-op (docs/46 § Site settings). But when you do flip it in the console,
  **also change the codified `ips_mode` to `"ips"` in the same MR** — the
  `ignore_changes` suppresses the plan diff, so it is harmless now, and it keeps
  the create-time intent aligned with reality: otherwise the day the ignore
  comes off (below) or the resource is recreated, the plan silently restores
  `"ids"` and quietly disables inline blocking. Do not read the burn-in as a passed "clean
  week": it produced **zero** detections, on a set of 34 of ~53 categories with
  no current-events category and `memory_optimized` on, and the Suricata engine
  is two majors behind (6, with an upgrade to 8 pending). Gate the flip on the
  engine upgrade landing, then re-baseline a week on Suricata 8 with the category
  set reviewed. Close the notification gap first (below) — an inline false
  positive with site alerting off drops packets across six VLANs silently.
  Upstream #381 means alert suppressions stay a UI concern. The console is now on
  UniFi OS 10.6.101 (upgraded from 10.5.67 on 2026-08-30); the module's
  `ignore_changes = [ips]` workaround was written against 10.5, so the next
  supervised apply is the moment to re-check whether the `ips` write still flaps
  on 10.6 — if it no longer does, the ignore can eventually come off and the
  console-vs-Terraform split resolves itself. (No scheduled WAN speedtest, by
  choice — it saturates the WAN and blackbox probes already cover reachability.)
- [ ] **Provider bump when the blockers clear.** Two things stay UI-only at
  `ubiquiti-community/unifi` 0.55.0 and are worth re-testing on each release:
  switch port/native-VLAN management (#438/#430/#431 make
  `unifi_device.port_override` unsafe) and firewall-policy ordering (#407).
  `unifi_client` in-place updates (#428) are the day-to-day annoyance. 6 GHz is
  no longer on this list — it is codified per-SSID via `bands` (lib v0.14.0);
  #406 now bites only a from-scratch WLAN CREATE (drop `6g`, apply, re-add).
- [ ] **A real identity boundary for admin devices.** The `10.0.20.8/29`
  admin block is a DHCP-reservation convention on a shared VLAN — a Home
  device that statically claims an address in it inherits the block's L3
  trust (docs/46 § Accepted trust decisions). A dedicated admin SSID/VLAN
  (own PSK at minimum, 802.1X if ever worth the ceremony) would turn it into
  an authenticated boundary; cheap to add once the UniFi terraform root is
  routine.
- [ ] **Passphrase validation in the lib module** — `unifi-network` bounds
  WLAN passphrases by character count; the WPA rule is 8–63 *printable ASCII
  octets*. The weisssrv and cluster-template roots enforce the ASCII form on
  their own variables; fold the same regex into the module's `wlans`
  validation in the next lib release so every consumer gets it.
- [ ] **UniFi metrics into Prometheus** (unpoller or equivalent). Today the
  gear is observed only by ICMP blackbox probes feeding
  `NetworkGearProbeFailed`; per-port throughput, PoE draw, AP client counts
  and WAN health are not collected.
- [x] ~~**Re-scope the role-owned rules.**~~ DONE 2026-08-21: `weisssrv.infra`
  v0.13.0 added `proxmox_firewall_dns_client_sources` and
  `proxmox_firewall_k3s_ingress_int_sources` (both defaulting to the old
  `[admin_ts, admin_lan]`), and this repo points `sg-dns`'s `:53` at
  `dns_clients` and `sg-k3s-ingress-int` at `lan_clients`
  ([docs/11](11-firewall.md) § Client scopes).
- [ ] **Move the Windows VM (.155) to the Home VLAN** — it is a client
  machine sitting on the homelab segment ([docs/39](39-windows-vm.md)).
- [ ] **Narrow the drift jobs' `allow_failure` to `exit_codes: [2]`** — a
  library-wide change, not a unifi one. All three drift-plan jobs
  (`tailscale`, `authentik`, `unifi`) run `terraform plan -detailed-exitcode`
  under a blanket `allow_failure: true`, so exit 2 (drift, the case the
  allowance exists for) and exit 1 (auth failure, unreachable endpoint, state
  lock, missing vault item) render as the same yellow badge on a job nothing
  gates on — a permanently broken detector is indistinguishable from real
  drift. The fix belongs in the shared CI template so all three move together;
  doing it for `unifi` alone would diverge the house pattern for no gain.
  Until then, docs/46 § Expected breakage carries the "must go green after the
  first apply" assertion that makes a lingering yellow noticeable.
- [ ] **Restore external observation of the `git` A record.** The cross-domain
  rewrites that stopped the hairpin outages ([docs/08](08-dns.md)
  § Cross-domain rewrites) also stopped four blackbox probes from leaving the
  LAN. Most of that coverage survives elsewhere — `registry.git`, `pages.git`
  and `ide.git` are CNAMEs to `direct`, which `gitlab-webide-external` still
  exercises — but `git` is its own DDNS-managed A record and now has no
  external probe at all, so DDNS drift on it would surface only when someone
  outside the house tried to clone. The fix is a probe that genuinely resolves
  publicly: a blackbox module pinned to a public resolver, or a check that
  compares the record's Cloudflare content against the current WAN IP. The
  wrong fix is dropping a rewrite.

Opened by the 2026-08-30/31 UniFi configuration audit (decisions, not
implementation tasks — the terraform remediation MR !270 already merged and the
drift plan is clean):

- [ ] **Offsite console backup + a real cadence.** The `.unf` lives only on the
  console's own storage and dies with it, and it carries exactly the
  console-owned set Terraform does not (the switch port map incl. port-7
  tagging, mDNS scope, IPS state, 6 GHz radios, adoption, the three admin
  accounts). The two API values even disagree on whether auto-backup is on
  (`sysinfo.autobackup: false` vs `super_mgmt.autobackup_enabled: true`, a
  monthly cron) — read Control Plane → Backups to settle it. Decide: set it
  daily, then pull the newest `.unf` onto pve-nas-01 so the nightly restic →
  Backblaze B2 job (docs/42) carries it under the same GFS retention and
  client-side encryption. This matters more than a normal config backup — the
  UniFi layer is the one whose loss blocks reaching everything else during a
  recovery (docs/17 assumes the network is up).
- [x] **Owner-account MFA verified.** The console's entire MFA posture reduces
  to whatever 2FA the single ui.com Owner (`ericsweiss1@gmail.com`) carries at
  account.ui.com — the `terraform` and `homeassistant` accounts are local-only
  and cannot have 2FA. With Remote Access on, losing that second factor is a
  lockout with no second admin to recover through. **Confirmed 2026-09-02
  (operator): 2FA is on with a non-SMS factor.** Console-only — no API read can
  confirm it.
- [ ] **Vault the Owner-account recovery codes** in the Homelab vault (operator
  sub-step to the above, so a lost factor is recoverable).
- [ ] **Wire console events into the homelab Alertmanager.** Site alerting
  (`mgmt.alert_enabled`) is off, so device-down / WAN-failover / IDS events reach
  only ui.com cloud email/push; the repo's blackbox probes only detect a dead
  box. Ship gateway syslog to Loki (making console events Grafana-queryable and
  Alertmanager-alertable) and/or enable site alerts, keeping cloud email as the
  out-of-band fallback. This is a **prerequisite** for flipping IPS inline
  (above) and for noticing a WAN2 failover onto the pre-bound-but-empty SFP+ 2.
  Turning on `report_wan_event` for both WANs is the cheap first half.
- [ ] **Name the ALLOW_ALL default-security-posture decision.** The site knob
  `global_network.default_security_posture` is `ALLOW_ALL`; the deny-by-default
  this design relies on comes entirely from Terraform putting each VLAN in a
  custom zone, so a network *created in the UI* lands in the built-in `Internal`
  zone wide open. Either flip the posture to Block All and add the three
  `Internal →` ALLOW policies the mgmt VLAN needs (its internet + adoption path
  inverts otherwise), or document ALLOW_ALL as deliberate with the "adding a
  VLAN is not a UI-safe op" trap it implies (docs/46 § Codified vs manual).
  Check whether `unifi_setting` at 0.55.0 can even express it.
- [ ] **DHCP guarding — enable per-network or accept snooping-only.** Per-network
  guarding (`dhcpguard_enabled`) is off on all six networks; switch-side DHCP
  snooping is the only rogue-DHCP protection in place. Decide whether to enable
  per-network guarding with the gateway as the sole allowed server (a UI change —
  the provider drops `dhcp_guarding.servers` on write, #419), or record
  snooping-only as sufficient (docs/46 § Codified vs manual).
- [ ] **Gateway Local DNS records for the GitLab family (optional second layer).**
  `static-dns` is empty. Adding git / registry.git / pages.git .ericsweiss.com →
  `10.0.10.101` on the gateway makes a fallback to the gateway resolver safe,
  backing up the AdGuard split-horizon rewrites that fixed the hairpin outages.
  Console change (no provider resource).
- [ ] **Re-verify the bond invariant against the new switch, and disambiguate the
  standby-member flapping.** The docs/34 `all_slaves_active 0` invariant was last
  verified against the *old* switch; the USW Pro XG 8 is a new link partner and
  the three standby bond members (ports 1/3/5, the opt nodes' `nic0`) show 9
  link-down events each over ~8 days while their partners show zero. Run
  `cat /sys/class/net/<bond>/bonding/all_slaves_active` (expect 0) on the three
  bonded hosts plus `ethtool -S nic0` / `journalctl -k` for e1000e
  carrier/hang events over the same window — matching counts mean the e1000e
  story is continuing on the standby leg; clean hosts mean it is bonding-driver
  noise and the row can close (docs/46 § Post-cutover checklist).
- [ ] **Tighten the pure access ports to native-VLAN-only (segmentation
  hardening).** UCG 1-3 and USW 1-6 carry the controller's default **All** port
  profile (native VLAN + forward every tagged VLAN), so a compromised device on
  one could VLAN-hop by emitting tagged frames past the zone policies (audit
  PORT-01/ZBF-06). On the host ports (UCG 2-3, USW 1-6) it is bounded — Proxmox
  hosts and the NAS, already fully trusted on VLAN 10 — but **UCG 1 is the Hue
  bridge, an untrusted IoT appliance**, which is the real motivation; include it.
  Defence-in-depth wants a native-only profile on every genuine access port.
  Console change, not codified: `unifi_device.port_override`
  is unsafe at provider 0.55.0 (#438/#430/#431). Leave the real trunks alone —
  USW 7 (ConnA, must keep native Home + tagged 10/30 for pve-nas-01's `nic1.10`
  and the bedroom Pi's `eth0.30`), USW 8 (AP) and USW 10 (SFP+ uplink to the
  UCG, whose port 6 is the DAC to the switch); USW 5-6 are pve-opt-03 access
  ports, not trunks. Do it after the offsite console backup above exists, since
  a bad port override is exactly what that backup recovers from (docs/46
  § Physical port map).
- [ ] **Disable IPv6 on pve-nas-01's `nic1` carrier (closes a link-local Home
  bypass).** `nic1` is address-less on port 7 (native Home VLAN 20), but with
  IPv6 up it holds an `fe80::` link-local adjacency to the Home VLAN that no
  zone sees. It is bidirectional: the NAS→Home direction is bounded by nothing
  (the audit source-scoped `homelab → home` to HA/Plex, but link-local skips the
  gateway), so it is a narrow live bypass of that tightening, not just a future
  risk (docs/46 § Cutover as executed, step 7 residual). Set
  `net.ipv6.conf.nic1.disable_ipv6=1` through the `nic_tuning` sysctl mechanism
  (a host_var on pve-nas-01, or a lib knob if it recurs), deploy, and verify
  `ip -6 addr show nic1` is empty. Consistent with the IPv4-only posture; touch
  only the physical `nic1`, not `nic1.10`/`vmbr0`. Low urgency (link-local
  reaches only L2-adjacent nodes and nothing listens on it), tracked so it is
  not lost.
- [ ] **Clear the pre-renumber `config_network` on the switch and AP (optional).**
  Both still record their old `192.168.0.x` in the Configure-IP field; inert
  while DHCP, but the value either would take if flipped to static — on a subnet
  the gateway no longer routes. Clear it in the console.
- [x] ~~**Proxmox guest network config left on the old subnet by the renumber.**~~
  DONE 2026-09-01: the Phase 2 renumber moved every guest's network in-guest but
  left the Proxmox-level cloud-init/LXC config (`ipconfig0`, `nameserver`,
  `searchdomain`) on `192.168.0.x` — inert for running VMs but a rebuild
  landmine, and for LXCs an active bug (Proxmox rewrites the container's
  `/etc/resolv.conf` from it on every restart, which broke dns-02's own name
  resolution after a kured reboot). `proxmox_vm`/`proxmox_lxc` set net config
  create-path only, so nothing reconciled it. Fixed properly: weisssrv.infra
  **v0.15.0** reconciles guest net config from inventory on every run (the
  resolver LXCs override `proxmox_lxc_nameserver: "127.0.0.1"` in
  `group_vars/dns.yml`); the guest deploys apply it.

### Nextcloud follow-ups (not blockers)

- [x] ~~**Move Nextcloud's outgoing mail onto submission.**~~ DONE 2026-08-20:
  the role gained SASL support in lib v0.11.1 (auth + credentials converge in
  both directions, stdin-only secret transport, half-set pair and plaintext
  channel both fail the play) and `nextcloud_servers.yml` now rides 587 +
  STARTTLS with the shared null-client credential.


- [x] ~~Grafana dashboard for the Nextcloud exporter~~ DONE 2026-08-20:
  upstream contrib dashboard imported as
  `observability/dashboards/nextcloud.json` — datasource fixed to the
  sidecar's `prometheus` uid, import-inputs dropped, panel variables
  Flux-escaped (`$${var}`), and the flux-lint keys-check taught to honor
  that escape (lib v0.12.1 + the Taskfile twin).
- [ ] Optional Collabora/OnlyOffice office suite (not deployed).

### CI/CD

- [x] ~~**Distributed cache backend for the runners.**~~ DONE 2026-08-20:
  in-cluster Garage S3 (`kubernetes/apps/ci-cache` — Garage over MinIO, whose
  upstream was archived 2026-04) with both runners' `[runners.cache]` pointed
  at it, `Shared = true`. The formerly-inert `cache:` blocks are live;
  `CiCacheDown` covers the degrade-not-break failure mode.

- [x] ~~**Alert on the runner reaper's partial sweeps.**~~ DONE 2026-08-20:
  `GitlabRunnerReaperPartialSweep` (loki/runner-reaper.yaml) fires on the
  BUDGET STOP line over 25h; the `LokiRulerRulesMissing` count-gate and its
  guard test cover the new file.

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
  `ansible/requirements.yml` + `WEISSSRV_LIB_REF` + the Terraform `?ref=`
  module pins (one per root under `terraform/`), run
  `scripts/check-lib-pins.py --fix` and
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

- [x] Network segmentation with VLANs (IoT, guest, management) — decided and
  codified with the UniFi migration; see
  [Network segmentation](#network-segmentation--admin-ipset-tightening--decided-shipping-with-the-unifi-migration)
  above and [docs/46-unifi-network.md](46-unifi-network.md). The remaining work
  is the supervised cutover and Phase 2's renumber.
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
