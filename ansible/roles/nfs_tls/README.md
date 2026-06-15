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

- **Server**: an `xprtsec` flag added to entries in `/etc/exports`. The
  `nas_storage` role's `exports.j2` template reads the per-export `xprtsec`
  field on each item in `nfs_exports`. Production uses `none:tls` —
  **permissive**: it advertises TLS but still accepts plaintext, which
  removes the deploy-ordering lockout. Example in `host_vars/pve-nas-01.yml`:

  ```yaml
  nfs_exports:
    - path: /tank/media
      xprtsec: "none:tls"   # advertise TLS, still accept plaintext (permissive)
      clients:
        - spec: 192.168.0.0/24
          options: rw,sync,no_subtree_check
  ```

  Use `tls` instead of `none:tls` to reject plaintext (a future hardening
  step — see docs/16). Omit the `xprtsec` key (or set it falsy) to leave the
  export at the server default (`none:tls:mtls`, also permissive). The
  template also supports a **per-client** `xprtsec` override (on each
  `clients[]` entry), which takes precedence over the export-level value —
  this is how `/export/media` advertises TLS to its k3s clients while keeping
  HAOS (.154) with no `xprtsec` on the same export. See the `nas_storage`
  README for the field semantics.
- **Client**: `-o xprtsec=tls` (or `xprtsec=mtls`) flag added to mount
  options. For k3s NFS PVs this is `spec.mountOptions` in the PV manifest;
  for Proxmox `pve_storage` entries it would be set in `host_vars`. **A TLS
  client MUST mount the server by a hostname the cert covers** — the
  distributed cert is the wildcard `*.esweiss.com`, so an IP mount
  (`192.168.0.102`) fails the handshake (`tlshd`: "Certificate owner
  unexpected"). The k3s PVs set `server: pve-nas-01.esweiss.com` (resolves to
  .102 via AdGuard, matches the wildcard). Plex (LXC) is **not** an NFS
  client — it bind-mounts the media directories on the NAS host directly, so
  it never appears in this rollout.

## Prerequisites

- Kernel ≥ 6.5 (Proxmox VE 9.x ships kernel 6.17 ✓).
- `nfs-utils` ≥ 2.6.3 (Debian 13 / trixie ships 2.8.x ✓).
- A TLS cert + key at `nfs_tls_cert_path` / `nfs_tls_key_path`, owned
  by root and mode 0600 on the key. `acme_certs`'s
  `homelab-cert-reload.sh` distributes `fullchain.pem` + `privkey.pem`
  to `/etc/ssl/private/` on `pve-nas-01` (NFS server) and on **all six
  k3s agents** (.202–.207) per `host_vars/dns-01.yml`; the role's
  defaults point at those paths so no extra config is needed for the
  standard NFS topology. All agents are covered because the NFS-backed
  PVs float across the cluster, so any agent can become the tlshd
  client. k3s **server** nodes don't mount NFS and are not targets.
  Adding a new NFS host = add a new `cert_distribution_targets` entry
  (its `host_key` must be captured via `task certs:show-host-keys`).

## Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `nfs_tls_enabled` | `false` | Opt-in toggle. Set per-host or per-group. |
| `nfs_tls_cert_path` | `/etc/ssl/private/fullchain.pem` | Server + client cert (matches what acme_certs distributes) |
| `nfs_tls_key_path` | `/etc/ssl/private/privkey.pem` | Matching private key (mode 0600 enforced by acme_certs) |
| `nfs_tls_truststore` | `/etc/ssl/certs/ca-certificates.crt` | CA bundle for cert validation |

## Rollout procedure (coordinated across server + clients)

> Permissive exports (`none:tls`) make ordering forgiving: the server
> accepts both plaintext and TLS, and a client without tlshd just mounts
> plaintext. The only hard requirement is that TLS clients mount by a
> hostname the `*.esweiss.com` cert covers (never by IP).

1. Confirm `cert_distribution_targets` in `host_vars/dns-01.yml`
   covers every NFS server + TLS client: `pve-nas-01` (server) and all
   six k3s agents (.202–.207). Each new entry needs a real `host_key`
   captured via `task certs:show-host-keys` — a placeholder key is
   fail-safe (StrictHostKeyChecking rejects the cert push) but blocks
   the rollout until replaced.
2. Run cert distribution (`task dns:deploy`); verify each host has
   `/etc/ssl/private/{fullchain,privkey}.pem`.
3. Set `nfs_tls_enabled: true` on the server and every TLS client. For
   k3s this is one line in `group_vars/k3s.yml` (the role runs on
   `k3s_agents`; it's a no-op on servers). Re-run the playbook that
   includes `nfs_tls`. The role's pre-check asserts the cert files
   exist, so a missing distribution fails the play loud instead of
   silently bringing up a misconfigured tlshd.
4. Add `xprtsec=none:tls` to the relevant `nfs_exports` entries on
   `pve-nas-01` (export-level for k3s-only exports; **per-client** for
   mixed exports like `/export/media` so HAOS keeps a non-xprtsec line).
   Re-run `nas_storage`; the handler reloads `exportfs`. Permissive, so
   this is safe to apply before or after the clients flip.
5. Point the k3s NFS PVs at the **hostname** and add `xprtsec=tls` to
   their mount options. `server:` is an immutable PV field, so this is a
   delete+recreate, not an in-place edit:
   - Set `server: pve-nas-01.esweiss.com` and add `xprtsec=tls` to
     `spec.mountOptions` (commit to `kubernetes/`, let Flux apply).
   - Live cutover for an already-bound PV: `kubectl delete pv <name>`
     (Retain policy → the NFS data is untouched), let Flux recreate it,
     then `kubectl rollout restart` the consuming workload so it remounts
     against the new server name with TLS.
   - Proxmox `pve_storage` entries (deferred for `tank-proxmox`).
6. Verify: clients show `xprtsec=tls` in `cat /proc/mounts`, server
   logs show TLS handshakes (`journalctl -u tlshd -n 50` — "Handshake
   with pve-nas-01.esweiss.com was successful"), data still flows;
   `exportfs -v` reflects the per-client xprtsec.

Why the cutover is delete+recreate: a PV's `nfs.server` is immutable, so
flipping IP→hostname can't be done in place — Flux can't patch it, and a
bound PV won't accept the change on reconcile. Deleting the PV with a
Retain reclaim policy detaches the object without touching the NFS data;
Flux recreates it from the manifest and the pod remounts on restart.
This is a deliberate post-merge operational step, not automatic on Flux
reconcile. Until the pod restarts it keeps its old (plaintext/IP) mount.
