# nfs_tls

Installs and configures tlshd (kernel TLS handshake daemon) for use by
NFSv4 transport-layer security (`xprtsec=tls` or `xprtsec=mtls`).

## What it deploys

When `nfs_tls_enabled: true`:

- Installs `ktls-utils` package (provides `/usr/sbin/tlshd` + the
  systemd unit).
- Drops `/etc/tlshd.conf` pointing at the host's TLS cert/key + the
  system CA bundle.
- Enables and starts `tlshd.service`.

The role itself only handles the daemon. The actual `xprtsec` handshake
is triggered by:

- **Server**: `xprtsec=tls` (or `xprtsec=mtls`) flag added to entries in
  `/etc/exports`. The `nas_storage` role's `exports.j2` template reads
  the per-export `xprtsec` field on each item in `nfs_exports`. Example
  in `host_vars/pve-nas-01.yml`:

  ```yaml
  nfs_exports:
    - path: /tank/media
      xprtsec: tls   # add this to opt the export into TLS-only access
      clients:
        - spec: 192.168.0.0/24
          options: rw,sync,no_subtree_check
  ```

  Omit the `xprtsec` key (or set it falsy) to leave the export in
  plaintext mode during a coordinated rollout.
- **Client**: `-o xprtsec=tls` (or `xprtsec=mtls`) flag added to mount
  options. For Proxmox `pve_storage` entries this is set in `host_vars`;
  for k3s VMs and the plex LXC, adjust the relevant role.

## Prerequisites

- Kernel ≥ 6.5 (Proxmox VE 9.x ships kernel 6.17 ✓).
- `nfs-utils` ≥ 2.6.3 (Debian 13 / trixie ships 2.8.x ✓).
- A TLS cert + key at `nfs_tls_cert_path` / `nfs_tls_key_path`, owned
  by root and mode 0600 on the key. `acme_certs`'s
  `homelab-cert-reload.sh` already distributes `fullchain.pem` +
  `privkey.pem` to `/etc/ssl/private/` on `pve-nas-01` and
  `k3s-agt-nas-01` (per `host_vars/dns-01.yml`); the role's defaults
  point at those paths so no extra config is needed for the standard
  NFS topology. Adding a new NFS host = add a new
  `cert_distribution_targets` entry.

## Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `nfs_tls_enabled` | `false` | Opt-in toggle. Set per-host or per-group. |
| `nfs_tls_cert_path` | `/etc/ssl/private/fullchain.pem` | Server + client cert (matches what acme_certs distributes) |
| `nfs_tls_key_path` | `/etc/ssl/private/privkey.pem` | Matching private key (mode 0600 enforced by acme_certs) |
| `nfs_tls_truststore` | `/etc/ssl/certs/ca-certificates.crt` | CA bundle for cert validation |

## Rollout procedure (coordinated across server + clients)

> Sequence matters — the server with `xprtsec=tls` rejects non-TLS
> clients, and clients without tlshd active can't speak `xprtsec=tls`.
> Flip clients first, then enable on the server.

1. Confirm `cert_distribution_targets` in `host_vars/dns-01.yml`
   covers every NFS server + client. Currently: `pve-nas-01` (server),
   `k3s-agt-nas-01` (client). Adding more (Proxmox hosts that mount
   `tank-proxmox` via `pve_storage`, plex LXC, etc.) is a per-host
   entry with `cert_dir: /etc/ssl/private`.
2. Run cert distribution (`task dns:deploy`); verify each host has
   `/etc/ssl/private/{fullchain,privkey}.pem`.
3. Set `nfs_tls_enabled: true` on every NFS server and client; re-run
   the playbook that includes `nfs_tls`. The role's pre-check asserts
   the cert files exist, so a missing distribution fails the play
   instead of silently bringing up a misconfigured tlshd.
4. Update NFS mount options on every client (`xprtsec=tls`):
   - Proxmox host `pve_storage` entries that point at the NFS server.
   - k3s NFS PVs (`spec.mountOptions` includes `xprtsec=tls`).
   - Plex bind-mount equivalents.
   Remount on each client; verify TLS handshake via `journalctl -u
   tlshd` on both sides.
5. Add `xprtsec: tls` to the relevant entries in `nfs_exports` on
   `pve-nas-01`. Re-run `nas_storage` and restart `nfs-server`.
6. Verify: clients show `xprtsec=tls` in `cat /proc/mounts`, server
   logs show TLS handshakes (`journalctl -u tlshd -n 50`), data still
   flows.

The role itself is non-breaking when activated — no exports or mounts
will demand TLS until step 4/5. But once those happen, partial
rollouts produce hard failures (EACCES / EPROTONOSUPPORT). Plan for a
maintenance window or use parallel exports (one with xprtsec, one
without) for the cutover.
