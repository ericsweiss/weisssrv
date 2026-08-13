# Add a Proxmox VM / LXC app

A VM/LXC app is provisioned by **Ansible** (not Flux): a collection role builds
the guest, a playbook here runs it, and the app is fronted by an in-cluster
Traefik IngressRoute (the "vm-ingress" pattern) so k3s routing still applies.
GitLab is the fullest worked example — read it alongside this list:
`docs/27-gitlab-deployment.md`. Storage: `docs/06-zfs.md` and
`docs/44-storage-bootstrap.md`. Firewall: `docs/11-firewall.md`. DR/backups:
`docs/17-disaster-recovery.md` and `docs/42-offsite-backup.md`. New-guest
bootstrap: `docs/18-bootstrap-new-systems.md`.

**The role is a weisssrv-lib change.** Roles live in the `weisssrv.infra`
collection, so steps 5 and 9 below straddle two repos: write and test the role
there, get a tag cut, then land the pin bump plus everything else in one MR here
(`CLAUDE.md` § Repo family).

## Checklist (mirror GitLab / Plex / Home Assistant)

1. **Inventory** — add the guest to `ansible/inventories/prod/hosts.yml` (VM or
   LXC), choosing the host and `proxmox_role`-derived storage (NAS → `ssd`,
   compute → `local-ssd`; override with `proxmox_storage`/`proxmox_lxc_storage`).
2. **Persistent disks** — `vm_additional_disks` block in `hosts.yml`: one zvol
   per data volume with a **unique `scsi_slot`** and `vzdump_backup: false`
   (zvols are backed up by the archive pipeline, not vzdump). Created + mounted
   by the `proxmox_vm` / `zvol_mount` roles.
3. **Encryption** — add the guest `vmid` to `zfs_encryption_guest_vmids` in
   `host_vars/pve-nas-01.yml` so the guest's zvols are unlocked at boot (NAS
   only; `docs/32-zfs-encryption.md`).
4. **Resource pool** — place the guest in the right `proxmox_resource_pools`
   entry in `group_vars/all.yml` (infra-core / apps-public / apps-private /
   platform).
5. **Role** — a new role in the collection (`weisssrv-lib`,
   `ansible_collections/weisssrv/infra/roles/<app>/`) following the service
   pattern (packages → system user `system: true` nologin → unit template
   `notify: [Reload systemd, Restart <svc>]` → enable+start), FQCN + snake_case +
   role-prefixed vars + `no_log: true` on secret tasks, with a molecule scenario.
   Mirror a neighbour role; site-specific values are inputs, not defaults.
6. **Secrets** — `op://Homelab/<Item>/<field>` refs in the Taskfile task's `env:`
   block and the matching CI job; add new 1P items to
   `docs/15-credential-rotation.md`.
7. **Playbook** — a `ansible/playbooks/<app>.yml` (or extend an existing one)
   referencing `weisssrv.infra.<app>`, wired into `ansible/playbooks/site.yml`.
8. **Taskfile** — add `<app>:deploy` / `:status` / `:verify` wrappers mirroring
   `gitlab:*` (globals like the VM IP/ID go at the top of `Taskfile.yml`).
9. **CI coverage** — the new deploy target needs a `deploy-*` job in
   `.gitlab-ci.yml` (`scripts/check-deploy-coverage.sh`, run by `task lint`);
   the role's molecule scenario is covered by the library's own matrix, and the
   pin bump is what brings it here.
10. **Firewall** — a new `[group sg-<app>]` in `proxmox_firewall_security_groups`
    (`group_vars/all.yml`) plus `guest_security_groups` on the guest in
    `hosts.yml` (add `sg-vm-admin` + `sg-metrics`; open Traefik-fronted ports
    from `+dc/k3s_nodes`, admin ports from `+dc/admin_lan|admin_ts` only —
    least privilege). A scrape port that must open on EVERY node instead goes in
    `proxmox_firewall_metrics_scrape_ports` (`{port, sources[], comment}`, next
    to `proxmox_firewall_dns_admin_ports` in the same file) — the role builds in
    only its own exporters' ports.
11. **Backups** — a NEW top-level ZFS dataset is added to
    `nas_storage_archive_backup_sources` in `host_vars/pve-nas-01.yml`;
    `ssd/appdata/*` children are already auto-enrolled. Anything that must go
    offsite also needs a `restic_offsite_*` source entry (`docs/42-offsite-backup.md`).
12. **Cert distribution** — add a `acme_certs_distribution_targets` entry in
    `host_vars/dns-01.yml` so acme.sh pushes the guest its TLS cert. The SSH
    `host_key` cannot be known until the guest exists — leave a clearly-commented
    placeholder and a **post-provision step in the MR deploy plan** to capture the
    real key (`ssh <host> cat /etc/ssh/ssh_host_*_key.pub`). Do NOT invent a key.
13. **In-cluster ingress** — an IngressRoute (+ ServiceMonitor for the VM) under
    the observability/service-monitors and the app's routing, following GitLab's
    vm-ingress + whitelist pattern.
14. **Certificate** — per-host cert (`letsencrypt-prod`, `renewBefore: 720h`) as
    for a k8s app when Traefik terminates TLS.
15. **DNS** — internal `adguard_home_rewrites` in `group_vars/dns.yml` (answer
    `192.168.0.101` for Traefik-fronted; direct guest IP only for non-HTTP);
    external via the external-dns annotation, or `terraform/cloudflare/dns.tf`
    for a nested/DNS-only record.
16. **Observability** — logs via the `alloy_host` role on the guest (ships
    journald to Loki over the internal Traefik IngressRoute); metrics via a node
    exporter / app `/metrics` + ServiceMonitor; a down/stale alert rule as a
    `PrometheusRule` under
    `kubernetes/infrastructure/observability/rules/` with a `runbook_url` and a
    promtool unit test (`references/add-k8s-app.md` § Alert rules is canonical);
    a blackbox probe for the user-facing endpoint.
17. **Docs** — a `docs/NN-*.md` deployment page (next free number), its row in the
    `README.md` docs index, the application table in `CLAUDE.md`, and
    `docs/16-next-steps.md` (mark done / remove from planned). The role's own
    README lives with the role, in the collection.

## Notes

- HA-managed guests (dns, smtp, Home Assistant) are placed under Proxmox HA — see
  `docs/25-multi-node-expansion.md`.
- HAOS (.154) is the one documented plaintext-NFS exception; every other guest
  that mounts NFS uses `xprtsec=tls` by hostname (`docs/24-home-assistant-deployment.md`, `CLAUDE.md`
  § Ansible roles).
- A guest with disks on an **encrypted** pool must be `onboot=0` and listed in
  `zfs_encryption_guest_vmids` — `pve-start-encrypted-guests` starts it after the
  keys load. `onboot=1` there fails the start against a locked pool (`docs/32-zfs-encryption.md`).
