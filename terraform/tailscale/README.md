# terraform/tailscale — tailnet policy as code

Manages the Tailscale **ACL policy** (access rules, Tailscale SSH rules, tag
owners, and subnet-route **auto-approvers**) plus the `esweiss.com` **Split-DNS**
entry (`split_dns.tf`) for the homelab tailnet, mirroring the
`terraform/cloudflare` pattern (GitLab HTTP state backend + 1Password-injected
credentials).

`policy.hujson` is a **least-privilege lockdown**: tag/port-scoped rules, no
`autogroup:member -> *:*` full mesh and no root Tailscale SSH, while preserving
every path the operator uses today.

## ⚠️ Apply is a supervised step

`terraform apply` here **overwrites the live tailnet ACL**. A wrong policy can
sever tailnet connectivity and Tailscale SSH (the path this repo's remote admin
relies on). `apply` stays **out of CI and supervised**; only a read-only drift
`plan` runs in CI — the `tailscale-drift-plan` job (`.gitlab-ci.yml`) runs
`terraform plan` (never `apply`, `allow_failure: true`) on the schedule and
post-merge on `main`, so an Admin-console hot-fix surfaces as drift instead of
being silently reverted at the next apply. There is deliberately **no**
`merge_request_event` rule — the job materializes vault secrets and must not run
an unmerged branch's code — so the pre-merge control is a local
`task terraform:tailscale-plan`.

> **The ACL apply has landed** — the live tailnet policy is `policy.hujson`.
> So the rule is unconditional: `tailscale-drift-plan` is **expected empty**, and
> **any** non-empty plan is real drift — an Admin-console hot-fix that must be
> reconciled back into this file. Because the job is `allow_failure: true`, its
> colour is the only signal; a yellow one means review the diff with a supervised
> `task terraform:tailscale-plan` before applying anything, never wave it through.
>
> **Migration complete (2026-08-20):** all six Proxmox hosts carry
> `tag:subnet-router`, route auto-approval is tag-only (the owner entry is
> removed), and the post-migration tightening has been applied. The runbook
> below remains the procedure for any later supervised policy change or a
> rebuild-from-scratch.

## What the policy grants (rule by rule)

This section is the authoritative rationale; `policy.hujson` carries only a
one-line note per rule, and `docs/05-tailscale.md` points here.

- **`groups`** — `group:admins = [ericsweiss1@gmail.com]`. Every `acls`/`ssh` rule
  sources from this group, **not** `autogroup:member`. On this single-owner
  tailnet the two are equivalent today, but scoping to an explicit group is the
  least-privilege posture: a non-owner device that ever joins the tailnet inherits
  **zero** access to any host or LAN service until it is deliberately added to the
  group, instead of silently inheriting the full grant that `autogroup:member`
  would give every member.
- **`tagOwners`** — three tags. `tag:subnet-router` is owned by
  `ericsweiss1@gmail.com`, so the owner can apply the tag to the six Proxmox
  hosts without a separate tagged auth key. `tag:k8s-operator` is `[]` (tailnet
  admins) so the owner can mint the operator OAuth client, and `tag:k8s` must be
  ownable by `tag:k8s-operator` or the operator cannot register its proxies —
  the `traefik-tailnet` / `ts-dns` devices ACL rule 4 grants access to.
- **ACL rule 1 — admin devices → subnet-router hosts.**
  `src group:admins → dst tag:subnet-router:22,80,443,6443,8006`. Reaches a
  Proxmox host's own services on its tailnet (100.x) IP directly. The functional
  host services are SSH (22) and the Proxmox UI (8006); 80/443/6443 are defensive
  (the host firewall gates them) and inert unless a host later serves them.
- **ACL rule 2 — admin devices → LAN via subnet routing.**
  `src group:admins → dst 10.0.10.0/24:<ports>`. This is the day-to-day
  browsing/admin surface. Ports are **proto-agnostic** (no `proto` field) so each
  matches TCP **and** UDP — which is what DNS (53/853) needs — and **ICMP is
  auto-allowed** for any matched src/dst pair, so ping/traceroute work with no
  port. The port set is the honest union of what a subnet-routed tailnet device
  can reach today (Tailscale SNATs routed traffic to the router host's LAN IP, so
  it lands in the host firewall as an `admin_lan` / `pve_hosts` / `nfs_clients` /
  `core-cluster` source). Each port maps to a real service; which host-firewall
  security group it lands in is owned by `docs/11-firewall.md`: 22 SSH, 53 DNS, 80
  HTTP, 111 rpcbind, 443 HTTPS, 445 SMB, 853 DoT, 2049 NFS, 2222 GitLab SSH, 3000
  AdGuard, 3389 RDP, 6443 kube-API (VIP .161 + servers .222/.223/.227), 8006
  Proxmox, 8123 HAOS, 22222 HAOS SSH, 32400 Plex, 32469 Plex companion. k3s-internal
  ports (etcd/kubelet/flannel/memberlist), the metrics ports, GitLab registry
  5050 / Pages 8443, corosync/migration, the Loki NodePort, and LAN-local
  multicast discovery are **deliberately excluded** (not reachable by, or not
  initiated from, a subnet-routed tailnet device).
- **ACL rule 3 — admin devices → the owner's own devices on SSH.**
  `src group:admins → dst autogroup:self:22`. This is the **network-access
  gate that backs the Tailscale SSH rule** — Tailscale requires *both* a network
  `acls` rule and an `ssh` rule for a connection to be permitted, so
  `autogroup:self` in the ssh rule alone grants nothing without this. It covers
  inter-device SSH between the owner's own client devices (laptop ↔ phone) and
  SSH to the Proxmox hosts while they are still untagged during migration (once
  tagged they are covered by rule 1's `tag:subnet-router`, since `autogroup:self`
  excludes tagged devices — a seamless handoff). Port 22 only; not a broad
  member→member mesh.
- **ACL rule 4 — admin devices → the operator-exposed proxy devices.**
  `src group:admins → dst tag:k8s:53,443`. `:443` is the `traefik-tailnet`
  device (L3 TCP passthrough; TLS terminated in-cluster on the `*.esweiss.com`
  wildcard) — the mesh path a remote phone uses to browse the internal web apps.
  `:53` is the `ts-dns` device, the CoreDNS split-horizon resolver that Tailscale
  Split-DNS forwards `esweiss.com` to (tcp+udp, no `proto` field). Access is
  governed by the proxy device tag, so no `autoApprovers.services` block is
  needed (that is HA-ProxyGroup only).
- **`autoApprovers.routes`** — `10.0.10.0/24` auto-approves for BOTH
  `tag:subnet-router` **and** `ericsweiss1@gmail.com`. The tag approves routes
  once a host is tagged; the owner keeps still-untagged hosts approved — no
  approval gap while the six hosts are tagged one by one. **Post-migration
  tightening:** once all six are tagged and approved, remove the owner entry
  (see the in-file comment) so only tag-owned devices auto-approve the route.
- **SSH** — `action check`, `src group:admins`,
  `dst [autogroup:self, tag:subnet-router]`, `users [autogroup:nonroot]`.
  `autogroup:self` covers SSH between the owner's own untagged client devices (and
  the hosts while still untagged mid-migration) — its network-access gate is acls
  rule 3 above; `tag:subnet-router` covers SSH **into** the tagged Proxmox hosts (a
  tagged device is not matched by `autogroup:self`). **`root` is dropped** — the operator
  connects as `eric` (passwordless sudo on every host). A commented break-glass
  rule (with `root`) is kept in the file for emergency re-add.

## Shape from the library, policy from here

`main.tf` is a thin caller of the weisssrv-lib **`tailscale-acl`** module at a
pinned `?ref=`: the module owns the `tailscale_acl` resource and its guardrails
(`reset_acl_on_destroy = false`, `prevent_destroy = true`) plus the Split-DNS
resources; `policy.hujson` and `local.split_dns` (`split_dns.tf`) are this site's
data. `moved.tf` carries the state-address migration from the pre-module layout.

Same two notes as `terraform/cloudflare`: the `?ref=` is bumped **by hand**
(`scripts/check-lib-pins.py` does not read Terraform module sources), and
`terraform init` clones `weisssrv-lib` over HTTPS — already covered in CI by the
global `GIT_CONFIG_*` job-token URL rewrite in `.gitlab-ci.yml`.

## Split-DNS (`split_dns.tf`)

`local.split_dns` points tailnet `esweiss.com` queries at the `ts-dns` device's
IPv4 tailnet address, resolved by hostname at plan time so a device rebuild
self-heals. Managing it is why the OAuth client needs the `dns` scope alongside
`acl`.

Three things to know:

- **The nameserver is selected by construction, not by ordering.** The module
  filters the device's address list to its IPv4 (`100.x`) entry and preconditions
  on there being exactly one, so an ordering change in the provider or the API
  cannot repoint tailnet `esweiss.com` resolution at a v6 address — a device that
  exposes no IPv4 fails the plan instead.
- **`prevent_destroy` covers the entry.** Like the ACL, the module's
  `tailscale_dns_split_nameservers` resources carry `prevent_destroy`, so
  removing the `esweiss.com` key is a hard plan error rather than a destroy. The
  deliberate path is
  `terraform state rm 'module.tailnet.tailscale_dns_split_nameservers.this["esweiss.com"]'`
  (the live mapping survives that), then dropping the key — and doing so breaks
  `*.esweiss.com` resolution for every tailnet client (the mesh path in
  `docs/05-tailscale.md`). Treat the map as break-glass and read the plan.
- **A renamed device fails the plan outright.** Tailscale appends a numeric
  suffix (`ts-dns-1`) when the bare hostname is still held by a device that has
  not aged out — the common outcome when the Service is recreated before the old
  node key expires. The lookup then errors after its 60s wait, which looks
  identical to ACL drift in the `allow_failure` job. Recovery: delete the stale
  `ts-dns` device in the Admin console (or `tailscale logout` it) so the rebuilt
  Service reclaims the hostname, then re-plan.

> This tailnet ACL is a **separate layer** from the Proxmox host firewall
> (`admin_ts` = the full `100.64.0.0/10` CGNAT range). The firewall governs which
> ports a source may hit; the tailnet ACL governs which tailnet peer may send
> traffic at all. Both gates apply after lockdown.

## One-time setup

1. **OAuth client** — Admin console → Settings → OAuth clients → generate a
   client with the **`acl` and `dns`** scopes (both write; `dns` is what lets
   `split_dns.tf` manage the esweiss.com Split-DNS entry every tailnet client
   depends on). Store id + secret in the 1Password item `Tailscale OAuth`
   (fields `client id`, `credential`). Rotation: `docs/15-credential-rotation.md`.
2. **Verify the owner identity** in `policy.hujson` (`tagOwners`,
   `autoApprovers.routes`) matches your tailnet owner (`ericsweiss1@gmail.com`).
3. **State backend** — a tailscale-specific GitLab state name (already wired into
   the `terraform:tailscale-*` Taskfile tasks and the `tailscale-drift-plan` CI
   job), so it does not collide with the cloudflare state:
   ```
   .../terraform/state/tailscale   (+ /lock)
   TF_HTTP_LOCK_METHOD=POST         # GitLab state backend locks via POST
   TF_HTTP_UNLOCK_METHOD=DELETE     # and unlocks via DELETE (else apply → 405)
   ```

## Taskfile wrappers

`op run`-wrapped tasks (inject the Tailscale OAuth creds + `TF_HTTP_*`):

```bash
task terraform:tailscale-init     # terraform init (GitLab state backend)
task terraform:tailscale-plan     # review the diff vs the live ACL
task terraform:tailscale-apply    # SUPERVISED — do NOT pass -auto-approve
```

`terraform:tailscale-apply` **refuses `-auto-approve`** (it exits non-zero before
running `terraform apply` if the flag is present), so the plan review cannot be
bypassed by an errant flag. Review the plan and type `yes` at the prompt.

## Staged apply runbook (supervised — maintenance window)

Do this in a **maintenance window** with a **non-tailnet fallback** available
(local LAN console / Proxmox IPMI), in case an SSH cutover goes wrong.

> **Steps 1 and 2 are DONE** — the policy is applied and the drift plan is clean.
> The live remainder is **step 3** (tag the six hosts) plus the post-migration
> tightening at the end. Steps 1 and 2 are kept because they are also the
> procedure for any *later* supervised apply of a policy change, and for a
> rebuild-from-scratch.

### 1. Pre-apply checklist — validate nonroot SSH on ALL SIX hosts FIRST

Dropping `root` from the Tailscale SSH rule is only safe once `eric` + sudo works
on every host over the tailnet. Losing both `root` and `eric` = lockout. From a
tailnet-connected client, for each host `pve-nas-01 pve-opt-01 pve-opt-02
pve-opt-03 pve-prec-01 pve-laptop-01`:

```bash
for h in pve-nas-01 pve-opt-01 pve-opt-02 pve-opt-03 pve-prec-01 pve-laptop-01; do
  echo "== $h =="
  ssh "eric@${h}" 'sudo -v && echo "sudo OK on $(hostname)"' || echo "FAIL: $h"
done
```

Every host must print `sudo OK`. If any fails, **stop** — fix nonroot+sudo, or
keep the break-glass `root` rule (see step 5) until it is fixed.

Also confirm the OAuth creds resolve and review the diff:

```bash
task terraform:tailscale-init
task terraform:tailscale-plan     # confirm it matches the intended lockdown
```

### 2. Apply the ACL (adds tagOwners + tag-based route auto-approver)

```bash
task terraform:tailscale-apply    # review the plan, type `yes`
```

Because `autoApprovers` still lists the owner, the six (still-untagged) hosts keep
their `10.0.10.0/24` route approved through this step, so subnet routing stays
up and Rule 2 preserves LAN reach. **SSH access to the hosts is continuous across
this window** — there is no lockout:

- **By tailnet name / tailnet IP (Tailscale SSH):** while a host is still untagged
  it *is* `autogroup:self`, so acls **rule 3** (`autogroup:self:22`) plus the ssh
  `autogroup:self` rule permit Tailscale SSH to it. Rule 1 and the ssh
  `tag:subnet-router` rule do not match yet (nothing is tagged); the moment a host
  adopts the tag in step 3 the coverage hands off from `autogroup:self` to
  `tag:subnet-router` with no gap.
- **By LAN IP over subnet routing (plain SSH):** `ssh eric@10.0.10.10x` is
  permitted by Rule 2 (`:22`) regardless of tag state. This is the path the step-3
  Ansible run uses — the inventory `ansible_host` values are the LAN IPs — so
  tagging never depends on Tailscale SSH being up.

> **CI ordering note.** The `deploy-ansible-proxmox` pipeline runs the tailscale
> role automatically (it triggers on the inventory and the
> `ansible/requirements.yml` collection pin). Until step 3 has been completed on
> a host, its **"Reconcile advertised Tailscale ACL tags"** task benignly reports
> **needs reauth** — first-time tag adoption on a user-owned device needs an
> interactive reauth. The task is best-effort by default
> (`tailscale_tags_require_adoption: false`) and the following debug task surfaces
> the `rc`/`stderr`, so the pipeline stays green. This is expected, not a failure;
> step 3 runs the role strictly (`-e tailscale_tags_require_adoption=true`) so an
> adoption that fails on a host is caught instead of passing green.

> Bootstrapping a tailnet from scratch, first apply only: if the resource reports
> the ACL already has content, either
> `terraform import 'module.tailnet.tailscale_acl.this' acl`, or set
> `overwrite_existing_content=true` on the module's resource for that first apply.

### 3. Tag the six hosts (adopt tag:subnet-router)

The `tailscale_advertise_tags: ["tag:subnet-router"]` var is set in
`group_vars/proxmox.yml`; run the tailscale role against the Proxmox hosts. Pass
`-e tailscale_tags_require_adoption=true` so this **intentional** adoption step
runs strictly — a host that fails to adopt the tag fails the play instead of
passing green (the default best-effort mode is for the pre-cutover pipeline run,
where "needs reauth" is expected):

```bash
op run -- ansible-playbook -i ansible/inventories/prod ansible/playbooks/site.yml \
  --limit proxmox --tags tailscale -e tailscale_tags_require_adoption=true
```

The role runs `tailscale set --advertise-tags=tag:subnet-router` on each running
host (the tag is adopted via this reconcile task, not the initial `tailscale up`).
**First-time tag adoption on a user-owned device requires an interactive
reauthentication** (a Tailscale platform behavior) — with strict mode the run
fails on those hosts (rc/stderr surfaced), and you complete the reauth per host
below, then re-run. For any host needing reauth, either:

- re-authenticate it with the tag (preferred, keeps it codified):
  ```bash
  ssh eric@<host> 'sudo tailscale up --reset \
    --accept-routes=false --accept-dns=false \
    --advertise-routes=10.0.10.0/24 --advertise-tags=tag:subnet-router \
    --operator=eric --ssh'
  ```
  (`--reset` + the full flag set because `up` resets unspecified prefs), **or**
- assign `tag:subnet-router` to the host in the Admin console (Machines → host →
  Edit ACL tags) — simplest for a one-time migration; key expiry is unaffected.

### 4. Post-apply verification

```bash
# All six hosts tagged and their LAN route approved:
tailscale status
for h in pve-nas-01 pve-opt-01 pve-opt-02 pve-opt-03 pve-prec-01 pve-laptop-01; do
  ssh "eric@${h}" 'tailscale status --json | jq -r ".Self.Tags, .Self.PrimaryRoutes"'
done
# Admin console → Machines: each Proxmox host shows tag:subnet-router and an
# approved 10.0.10.0/24 route (subnet-router failover intact).

# SSH over the tailnet still works as nonroot:
ssh eric@pve-nas-01   # then: sudo -v

# kube-API over the tailnet (subnet-routed to the VIP / server VMs):
kubectl --kubeconfig ~/.kube/config-k3s get nodes   # hits .161/.222/.223/.227:6443

# Spot-check the day-to-day surface over the tailnet: a web app (443), Plex
# (32400), Home Assistant (8123), AdGuard (3000), Proxmox UI (8006).

# CI drift should now be clean:
#   tailscale-drift-plan → empty plan (green) once live == policy.hujson.
```

### 5. Break-glass (if the cutover locks you out)

- **Emergency root SSH:** uncomment the break-glass `ssh` rule (with `root`) in
  `policy.hujson` and re-apply, **or** add it directly in the Admin console
  (Access controls). A console edit surfaces as drift in `tailscale-drift-plan`
  until reconciled back into the repo.
- **Full revert:** `git revert` the lockdown commit and `task
  terraform:tailscale-apply` to restore the previous policy. The library module's
  guardrails (`reset_acl_on_destroy = false` and `prevent_destroy = true` on
  `tailscale_acl.this`) mean an accidental `destroy` cannot silently revert the
  tailnet to allow-all or tear the ACL down.
- **Non-tailnet path:** the Proxmox host firewall still trusts `admin_lan`
  (`10.0.10.0/24`), so a device physically on the LAN reaches SSH/8006 directly
  regardless of the tailnet ACL.

## Post-migration tightening (follow-ups)

- Remove the `ericsweiss1@gmail.com` owner entry from `autoApprovers.routes`
  once all six hosts are tagged (leaves only `tag:subnet-router`).
- Consider narrowing the host firewall `admin_ts` set now that tag-scoped tailnet
  ACLs exist (tracked in `docs/16-next-steps.md`).
