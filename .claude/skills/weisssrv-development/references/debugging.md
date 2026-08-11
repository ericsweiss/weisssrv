# Debugging & incident response — symptom → entry point

Read-only inspection is always safe (`kubectl get/describe/logs`, `flux get`,
`ssh <host>` for read-only checks). Mutating cluster state is not part of normal
ops — Flux owns `kubernetes/`. Full day-2 procedures: `docs/29-flux-operations.md`
(Troubleshooting) and the runbooks in `docs/12-runbooks.md`.

## Flux not reconciling / drift

- `task flux:status` (concise) then `task flux:verify` (`flux check` + all
  managed resources). Chase a failing Kustomization/HelmRelease with
  `flux get kustomizations -A` / `flux get helmreleases -A` and
  `flux logs --level error`.
- Unsubstituted `${var}` in a rendered manifest → the pin is missing from
  `versions-configmap.yaml`; fix `all.yml` + `task flux:sync-versions`, commit
  both. See `docs/29-flux-operations.md` (Version pinning / Substitution Not Applied).
- Force a cycle with `task flux:reconcile` (push-trigger is the normal path;
  ~1-min git poll is the fallback).

## Pod issues

- `task <ns>:status` / `task <ns>:logs` wrappers exist for the app namespaces —
  `downloads`, `recipes`, `home-assistant`, `gitlab`, `authentik`,
  `observability`, `hermes`, `hindsight`, `homarr`, `immich`, `immich-ml`,
  `nextcloud`, `wg-easy` (`task --list` is the source of truth; there are also
  `downloads:vpn-status`, `observability:silence`, `b2:drift`, and the
  terraform plan tasks). Otherwise `kubectl -n <ns> describe pod/<p>` +
  `kubectl -n <ns> logs`.
- **NFS stale file handle** on an established mount (after a NAS reboot): delete
  the pod so it remounts (`kubectl -n <ns> delete pod <p>`). The PV is
  hostname+TLS-mounted; a fresh pod re-does the handshake.
- **Clearing a pod restart counter** for a Flux-managed workload: `kubectl
  delete pod` — NOT `kubectl rollout restart` (the kustomize-controller reverts
  the restart annotation on the next reconcile).

## Secrets not syncing

- `kubectl -n <ns> get externalsecret` → look for `SecretSynced/Ready`. Rotate a
  value in 1P then `task flux:rotate-secret -- <app>` (force-sync + roll pods) or
  `task flux:refresh-secret -- <ns>/<name>` (sync, no restart). `remoteRef.key` =
  item title, `remoteRef.property` = field. Bootstrap secrets self-rotate via
  `task flux:bootstrap-onepassword`.

## DNS

- Split-horizon: internal `*.esweiss.com` from AdGuard (`.150`/`.160`), external
  `*.ericsweiss.com` from Cloudflare. Test with `dig @192.168.0.150 <name>` /
  `dig @192.168.0.160 <name>`. Details: `docs/08-dns.md`.

## Certs

- acme.sh + Cloudflare DNS-01, distributed to guests via `acme_certs_distribution_targets`;
  in-cluster certs are cert-manager `Certificate` CRs against `letsencrypt-prod`.
  `docs/09-certs.md`.

## Nodes / hosts

- IP + host table and the ssh-name map: `docs/01-overview.md`. Names resolve
  directly (`ssh pve-nas-01`, `ssh k3s-agt-nas-01`, `ssh dns-02`). Full
  access/kubeconfig detail: `references/cluster-access.md`.
- k3s health: `task k3s:status`. Base infra: `task infra:verify`.

## Backups

- Health is surfaced as `archive_backup_*` / `restic_offsite_*` Prometheus
  metrics with alert rules; runbooks in `docs/12-runbooks.md`. What is backed up
  is inventory (`nas_storage_archive_backup_sources`, `restic_offsite_sources`);
  the offsite chain to Backblaze B2, its `restic-offsitectl` CLI, and the restore
  drill are `docs/42-offsite-backup.md`. Restore paths: `docs/17-disaster-recovery.md`
  (pool/dataset rebuild: `docs/44-storage-bootstrap.md`).

## Reboot safety

- Coordinated reboots go through kured; the maintenance path gates on a
  reboot-safety check. NAS boot-time ZFS-key unlock: `docs/32-zfs-encryption.md`.
  A known active-backup bond MAC-flap black-hole and its `nic_tuning` guard:
  `docs/34-bond-mac-flapping.md`.

## Ansible testing caveats

- **Per-role molecule scenarios live in weisssrv-lib**, next to the roles, and
  run in that repo's matrix. What remains here are the multi-role integration
  scenarios (`ansible/integration-tests/`), run with `task ansible:test-integration`.
  Details and coverage: `ansible/TESTING.md`.
- Needs Docker; on Apple Silicon the amd64 emulation works after a binfmt
  install. Some scenarios only pass in CI's privileged DinD (real loop devices,
  Postfix, passwordless sudo), so **CI is the arbiter** of a genuine pass.
- Do NOT run full suites unless asked — `task lint`, a collection install, and
  `ansible-playbook --syntax-check` are the normal gate.
- A play that goes green having done nothing is the failure mode to watch for:
  `--tags <x>` that matches no declared tag exits 0, and a role variable left at
  its old (now unread) name silently takes the collection's default.
