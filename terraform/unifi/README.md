# terraform/unifi — UniFi network state as code

Codifies the UniFi site that fronts the whole homelab (UCG-Fiber gateway,
USW-Pro-XG-8-PoE switch, U7 Pro XGS AP — docs/46-unifi-network.md): the VLANs
and their DHCP scopes, one firewall zone per VLAN, the zone-based policies that
are the only inter-VLAN allowances, the four WLANs, the fixed-IP reservations,
the WAN port forwards and the hardened site settings. It mirrors the
`terraform/cloudflare` / `terraform/tailscale` / `terraform/authentik` pattern:
GitLab HTTP state backend, 1Password-injected credentials, supervised apply,
read-only drift plan in CI.

The resource **shape** — every resource, the `prevent_destroy` guards, the
derived firewall `matching_target`, the WPA3/PMF pairing and the hardened
setting defaults — comes from the weisssrv-lib `unifi-network` module at the
`?ref=` pinned in `main.tf`. What lives here is site data: `networks.tf` (the
networks, zones, policies, client reservations and port forwards), the `wlans`
map in `main.tf` (which has to name the passphrase variables), and the
credential variables.

## ⚠️ Apply is a supervised step

`terraform apply` here rewrites the gateway's own segmentation. A wrong zone
membership or a dropped policy is not a failed pipeline — it is a LAN you
cannot reach the controller from, recovered with a console cable. Apply
therefore runs only from an operator's terminal, with the plan reviewed line by
line:

```bash
task terraform:unifi-plan     # review
task terraform:unifi-apply    # confirm at the prompt
```

`terraform:unifi-apply` **refuses `-auto-approve`** (it exits non-zero before
invoking terraform if the flag is present), the same hard guard
`terraform:authentik-apply` and `terraform:tailscale-apply` carry, so plan
review cannot be bypassed by an errant flag. CI never applies: it runs only the
read-only `unifi-drift-plan` job (`terraform plan -detailed-exitcode`,
`allow_failure: true`, on the schedule and post-merge on `main`), so a
controller-UI hot-fix surfaces as drift instead of being silently reverted
later. There is deliberately **no** `merge_request_event` rule — the job reads
five vault items and must not run an unmerged branch's code — so the pre-merge
control is a local `task terraform:unifi-plan`.

**Take a controller backup (`.unf` export) before any apply that touches
networks or zones.** The controller's own settings are not in this state, and
a rollback of a zone change is faster from a backup than from a plan.

## Guardrails

`unifi_network` and `unifi_firewall_zone` carry
`lifecycle { prevent_destroy = true }` — **module-side**, so it is not something
an edit here can drop (`lifecycle` blocks take no variables).

- Destroying a **network** drops every client on that VLAN.
- Destroying a **zone** silently returns its networks to the default zone: the
  segmentation is gone, but everything still routes — the failure mode with no
  symptom.

Both are also what a renamed map key plans, which is the point. Renaming a key
in `local.networks` / `local.zones` is a `moved {}` block, which
`prevent_destroy` does not block. Removing one deliberately is two steps:

```bash
task terraform:unifi-init
task terraform:unifi-state -- rm 'module.network.unifi_network.this["<key>"]'
# then delete the map entry here, and delete the network in the controller
```

Go through the task, not a bare `op run -- terraform state rm`: the GitLab HTTP
state backend's credentials live only in the task's `env:` anchor and are not
persisted by `terraform init`, so a bare invocation answers 401.

Nothing else is protected, deliberately: a removed **policy** fails closed (it
is an allowance against a default deny), and `unifi_setting` has no delete at
all — destroy drops the state entry and changes nothing on the controller.

## What is managed

| Kind | Count | Terraform address | Site data |
|---|---|---|---|
| Networks (VLANs + DHCP scopes) | 6 | `module.network.unifi_network.this[<key>]` | `local.networks` |
| Custom firewall zones | 5 | `module.network.unifi_firewall_zone.this[<name>]` | `local.zones` |
| Zone policies | 17 | `module.network.unifi_firewall_policy.this[<name>]` | `local.policies` |
| WLANs | 4 | `module.network.unifi_wlan.this[<key>]` | `wlans` in `main.tf` |
| Client reservations | 15 | `module.network.unifi_client.this[<key>]` | `local.clients` |
| WAN port forwards | 5 | `module.network.unifi_port_forward.this[<key>]` | `local.port_forwards` |
| Site settings | 1 | `module.network.unifi_setting.site` | `site_settings` in `main.tf` |

Built-in zones (`Internal`, `External`, `Gateway`) are **read** through
`data.unifi_firewall_zone`, never managed — the provider at this pin cannot
import one by name, and a managed built-in would fight the controller over its
membership list, which the provider replaces wholesale on every apply.

Map keys are state addresses. The `local.zones` key is additionally the zone's
DISPLAY NAME on the controller, and a policy's `name` is the rule name in the
UI.

### The segmentation in one paragraph

The baseline is UniFi's own: **every zone reaches External and Gateway; no zone
reaches another internal zone.** Every VLAN is its own zone, so the eleven
`ALLOW` entries in `local.policies` are the complete list of what crosses a VLAN
boundary — Home reaches Homelab and IoT in full, Homelab reaches Home and IoT in
full, IoT gets the two DNS resolvers plus Plex `:32400` and Home Assistant
`:8123`, Work and Guest get the resolvers only, and the homelab↔management ICMP
pair exists so the blackbox probes for the switch and the AP can reach them.
Anything else between internal zones is blocked, including iot→home,
work→anything else, guest→anything else, and home→work.

The six `BLOCK` entries narrow the two default-allow paths an `ALLOW` list
cannot touch: `{guest,iot,work}-to-gateway-mgmt` keeps the console login off
those VLANs' own gateway addresses, and `{guest,iot,work}-to-external-dns` stops
a device with a hardcoded resolver from bypassing AdGuard on `:53`/`:853`.
Zone-per-VLAN is what makes the provider's inability to order rules irrelevant:
the allowances are against a deny rather than a first-match list, and each
`BLOCK` targets a zone-pair that no `ALLOW` here touches.

## What is deliberately UNMANAGED (and why)

Everything in this section is a **UI or runbook step in docs/46**, not drift
this root will report.

- **Policy ORDER.** `unifi_firewall_policy.index` is read-only at this pin and
  the controller appends every new policy to the end of its zone-pair
  (upstream #407). Zone-per-VLAN is what makes that safe.
- **mDNS reflection.** `unifi_network.multicast_dns` is ignored by UniFi OS
  gateways, which always store `false`; the module leaves it unset rather than
  planning a lie. Casting across Home↔IoT needs the reflector enabled per
  network in the UI, and **SSDP never crosses a VLAN** (UniFi has no SSDP
  reflector) — which is why Plex names the HDHomeRun by IP and several Home
  Assistant integrations are configured by IP.
- **Devices, ports, per-port native/tagged VLAN.** `unifi_device` cannot create
  anything (adoption only) and its `port_override` block is unsafe at this pin
  (upstream #438/#430/#431 — zero blocks wipes live overrides). The switch port
  map is a documented physical layout in docs/46, and adoption is a console
  step.
- **6 GHz.** Including `6g` in `wlan_bands` fails WLAN creation (upstream
  #406), so every SSID here is 2.4 + 5 GHz. Enabling the U7's 6 GHz radio for
  an SSID is a UI step, and it is drift this root cannot see.
- **WAN settings, remote access, the controller's own account.** WAN DNS
  (1.1.1.1 / 9.9.9.9 plain, deliberately not the internal resolvers — a
  gateway that resolves through the cluster cannot boot the cluster), ui.com
  remote access and the Limited Admin account that owns the API key are
  console-side.
- **Guest portal / Hotspot.** Nothing here uses the controller's guest portal:
  the Guest VLAN is `purpose = "corporate"` in a custom zone (see below), so
  its isolation is policy plus `l2_isolation`, not the Hotspot feature.

## Two behaviours to verify on the controller, not assume

- **Does a custom zone take its network out of `Internal`?** The provider
  issues exactly one API call, for the zone it manages; it neither moves nor
  detects anything else. After the first apply, read
  `data.unifi_firewall_zone` for `internal` (or the console's Zone Matrix) and
  confirm the five VLANs left it. If they did not, a network sits in two zones
  and policy evaluation is ambiguous.
- **The built-in zone display names.** `Internal` / `External` / `Gateway` are
  passed explicitly in `main.tf`; capitalisation matters and localised
  controllers differ. A wrong name fails the `data` read with a lookup error,
  not a policy error.

## Secret injection (no secrets in git — ever)

All credentials are `op run`-injected `TF_VAR_*`s (Taskfile locally, `op read`
in CI), from the Homelab vault (docs/15-credential-rotation.md):

| TF variable | 1Password reference |
|---|---|
| `unifi_api_url` | `op://Homelab/UniFi Controller/url` |
| `unifi_api_key` | `op://Homelab/UniFi Controller/api-key` |
| `wlan_passphrase_home` | `op://Homelab/WiFi TheRevengers/password` |
| `wlan_passphrase_iot` | `op://Homelab/WiFi 3601-IoT/password` |
| `wlan_passphrase_guest` | `op://Homelab/WiFi kugel-tikka-masala/password` |
| `wlan_passphrase_work` | `op://Homelab/WiFi 3601-Work/password` |
| (state backend) | `op://Homelab/GitLab Terraform State Token/credential` |

The `UniFi Controller` item's `username`/`password` fields are the Limited
Admin's console login — kept there for break-glass, not read by Terraform
(`api_key` wins whenever it is set, and the provider cannot log in to an
account with 2FA).

Every one of these variables has a length floor, because the failure they guard
is silent: a renamed 1Password field resolves to an empty string, a
`sensitive` value's diff is hidden, and an applied empty PSK drops every device
on that VLAN at once.

## State backend

Same GitLab HTTP backend as the siblings, its own state name (no collision):

```
.../terraform/state/unifi   (+ /lock)
TF_HTTP_LOCK_METHOD=POST         # GitLab state backend locks via POST
TF_HTTP_UNLOCK_METHOD=DELETE     # and unlocks via DELETE (else apply → 405)
```

> **This state is secret-bearing.** Terraform stores every WLAN passphrase in
> state **in the clear**, regardless of the `sensitive` flag — and
> GitLab-managed state is downloadable over the API by any project Maintainer
> with an `api`-scoped token. Treat read access to `terraform/state/unifi` as
> vault-equivalent, and note that rotating a PSK does **not** remove the old one
> from retained state versions (docs/15-credential-rotation.md). Saved plans
> have the same property, which is why `terraform/.gitignore` ignores `tfplan` /
> `tfplan.json` as well as `*.tfplan`.

## Taskfile wrappers

```bash
task terraform:unifi-init     # terraform init (GitLab state backend)
task terraform:unifi-plan     # review the diff vs the live controller
task terraform:unifi-apply    # SUPERVISED — refuses -auto-approve
```

## Adopting the live site (first apply, and DR)

The gateway is configured before Terraform ever runs: the built-in Default
network exists, the clients exist the moment the controller sees their MACs,
and after the cutover the VLANs exist too. A bare `terraform plan` over empty
state therefore plans **creates against objects that already exist**. For
networks, WLANs, zones and the settings singleton that apply fails part-way on
the unique-name constraints rather than duplicating them, so importing them is
mandatory. Clients are the exception — the module sets `allow_existing`, so a
create ADOPTS the client the controller already knows; import one only to have
it tracked from the first plan rather than adopted on the first apply. Import
first:

```bash
task terraform:unifi-init

# The built-in Default network — the only resource with name= import support.
task terraform:unifi-import -- 'module.network.unifi_network.this["default"]' name=Default

# Everything else with an id: <id>, or <site>:<id>.
task terraform:unifi-import -- 'module.network.unifi_network.this["iot"]' 5dc28e5e9106d105bdc87217
task terraform:unifi-import -- 'module.network.unifi_firewall_zone.this["iot"]' default:5f3e9b2c4ee8cb0f1f4a1234
task terraform:unifi-import -- 'module.network.unifi_wlan.this["home"]' 5dc28e5e9106d105bdc87218

# Clients import by MAC, and the MAC MUST contain colons (no site:id form).
task terraform:unifi-import -- 'module.network.unifi_client.this["hue"]' 00:17:88:7E:C7:A2

# The settings resource's id IS the site name.
task terraform:unifi-import -- module.network.unifi_setting.site default
```

Ids come from the controller API (`/proxy/network/v2/api/site/default/...`) or
the object's URL in the console. Built-in zones are never imported — they are
read through a data source.

Only after every pre-existing object is in state is "0 to add" the expected
plan result. The same sequence is the DR path when the state is lost and the
controller is intact.

## Changing a client reservation

Upstream #428: every in-place UPDATE of a `unifi_client` fails with
`inconsistent result after apply: .last_ip`. Renaming a reservation or moving
its address is therefore a replace:

```bash
task terraform:unifi-init
task terraform:unifi-apply -- -replace='module.network.unifi_client.this["hue"]'
```

The extra arguments ride through the supervised apply task, which is also where
the state-backend credentials come from — a bare
`op run -- terraform apply -replace=…` answers 401 (they live in the task's
`env:` anchor and `terraform init` does not persist them).

The client is re-adopted by MAC, so the device is untouched; only the
controller-side object is recreated.

## Provider quirks (ubiquiti-community/unifi)

- **Patch-floating pin, `~> 0.55.0`.** Pre-1.0 and a ground-up rewrite of the
  abandoned `paultyng/unifi` provider: 0.52 → 0.55 alone made firewall-policy
  `index` read-only, added `unifi_network.purpose` and made endpoint match
  lists Computed. Treat a minor bump as its own change and re-read the release
  notes for schema moves.
- **Names from the old provider do not exist.** `vlan_id`, `dhcp_start`,
  `dhcp_dns`, `internet_access_enabled`, `dst_port`, `fwd_ip`, `wpa3`,
  `unifi_user`, `unifi_user_group` — all renamed or restructured. The module is
  the only place that spells them, which is the point of the split.
- **Nested config is `= { ... }` object syntax, not blocks.** The provider's
  only real blocks — `unifi_device.port_override` and `unifi_wlan.schedule` —
  are not used here.
- **A network's zone cannot be set from the network side.** `unifi_network` has
  no `firewall_zone_id` (upstream #417); membership comes only from
  `local.zones`, and the provider sends the whole `network_ids` list on every
  apply.
- **A guest VLAN is `purpose = "corporate"`.** `guest` only sticks while the
  network is in the controller's own Hotspot zone; anywhere else the controller
  rewrites it and the apply fails with an inconsistent-result error.
- **`create_allow_respond` is rejected for ICMP.** Nothing here is ICMP; an
  ICMP allowance needs an explicit reverse policy instead.
- **An empty `dhcp_server.dns_servers` never converges** (upstream #429) — the
  module sends `null` instead, so "no DHCP DNS" is an empty list here.

## Adding a VLAN

1. One entry in `local.networks` (`networks.tf`): key, name, `vlan`, `subnet`
   in **gateway form**, `domain_name`, and the DHCP scope.
2. One entry in `local.zones` naming that key — a VLAN with no zone lands in
   `Internal` and inherits its neighbours' reachability.
3. The allowances it needs in `local.policies`, remembering it gets **nothing**
   by default, including DNS.
4. Its SSID in the `wlans` map (`main.tf`) plus a `wlan_passphrase_<key>`
   variable, the `WiFi <ssid>` 1Password item, and the `TF_VAR` wired into both
   the Taskfile anchor and the `unifi-drift-plan` job — if it is wireless.
5. Whether the Proxmox firewall needs it: `lan_clients` / `dns_clients` in
   `ansible/inventories/prod/group_vars/all.yml` are the LAN-facing scopes, and
   a new VLAN that must reach a guest service belongs in one of them.
6. `task terraform:unifi-plan` → review → supervised apply.

## Phase 2 — the homelab renumber

Phase 1 deliberately leaves the homelab on `192.168.0.0/24` (VLAN-tagged, but
not renumbered) so cutover night changes routing without touching a single
address in `ansible/`, `kubernetes/` or any guest. Phase 2 moves it to
`10.0.10.0/24` in its own MR, after Phase 1 has been live long enough to trust.

In this root that is a small, contained edit, and this list is the complete one:
`local.networks.homelab.subnet` and its DHCP scope, the three homelab locals
`dns_ips` / `plex_ip` / `ha_ip` (which every policy references rather than
repeating), and the five `local.port_forwards` targets. Nothing else here names
a homelab address.
Everything outside this root — the inventory, the k8s manifests, the DNS
rewrites — is the bulk of that MR; docs/46 § Phase 2 owns the sequence.
