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
  field on each item in `nfs_exports`. The production k3s exports use `tls` —
  **require**: a plaintext mount from those client lines is rejected. Example
  in `host_vars/pve-nas-01.yml`:

  ```yaml
  nfs_exports:
    - path: /tank/media
      xprtsec: "tls"        # require TLS — reject plaintext
      clients:
        - spec: 192.168.0.0/24
          options: rw,sync,no_subtree_check
  ```

  Use `none:tls` instead of `tls` for a permissive export (advertise TLS, still
  accept plaintext). Omit the `xprtsec` key (or set it falsy) to leave the
  export at the server default (`none:tls:mtls`, which accepts plaintext). The
  template also supports a **per-client** `xprtsec` override (on each
  `clients[]` entry), which takes precedence over the export-level value —
  this is how `/export/media` requires TLS from its k3s clients while keeping
  HAOS (.154) with no `xprtsec` (plaintext) on the same export. See the
  `nas_storage` README for the field semantics.
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

> The k3s exports require TLS (`xprtsec=tls`), so ordering matters: every
> TLS client must have `tlshd` running and the cert in place, and must mount
> by a hostname the `*.esweiss.com` cert covers (never by IP), before the
> export starts requiring TLS — otherwise its mount is rejected. Stage with a
> permissive value (`none:tls`) first if a client can't be guaranteed ready.

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
4. Add `xprtsec=tls` to the relevant `nfs_exports` entries on
   `pve-nas-01` (export-level for k3s-only exports; **per-client** for
   mixed exports like `/export/media` so HAOS keeps a non-xprtsec line).
   Re-run `nas_storage`; the handler reloads `exportfs`. Require-TLS rejects
   plaintext, so apply this only after the TLS clients are confirmed mounting
   over TLS by hostname (stage with `none:tls` first if unsure).
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

## One transport security per client, per server (cutover gotcha)

The NFSv4 client keys its transport and client state **per server IP**, and
multiplexes every mount to that server over it. A node therefore cannot hold a
plaintext *and* a `xprtsec=tls` mount to the same server at once: once a
plaintext session to `pve-nas-01` exists, a new TLS mount to it is refused with
`mount.nfs: Operation not permitted` (EPERM), and vice-versa. This is the most
likely cause of a post-cutover EPERM even though `tlshd` is up and handshakes
succeed — the handshake completing in `journalctl -u tlshd` is a red herring;
the rejection is at the NFS layer, not TLS.

This bites during the flip because long-running pods (e.g. `bar-assistant`,
`mealie`) keep their **original plaintext** mount alive after the PV spec
changes to TLS, and a force-deleted pod can leave an **orphaned** mount the
kubelet never unmounts. Either one pins the node's session to plaintext and
blocks every new TLS mount on that node — so a freshly scheduled pod hangs in
`ContainerCreating`/`Init`.

Cut a node over atomically — recycle **all** of its NAS-mounting pods together,
not just the one PV being changed:

1. Scale every Deployment on the node that mounts `pve-nas-01` to 0
   (downloads + recipes apps are NAS-pinned). `Recreate` strategy avoids a
   new pod racing the old one for an RWO mount.
2. Confirm no plaintext sessions linger, force-unmounting any orphans the
   kubelet left behind (safe — those pods are gone):
   ```sh
   mount -t nfs4 | grep -E 'pve-nas|192.168.0.102' | grep -v xprtsec=tls \
     | awk '{print $3}' | xargs -rn1 sudo umount -f -l
   ```
3. Scale the Deployments back up. With the node clean, the first mount
   establishes a TLS session and the rest reuse it; verify with
   `mount -t nfs4 | grep -c xprtsec=tls` (should equal the pod's mount count,
   with zero plaintext to the NAS).

Sweep the whole fleet after a cutover — any agent with a surviving plaintext
mount to the NAS is a latent failure that surfaces on the next reschedule:

```sh
for ip in 202 203 204 205 206 207; do
  echo ".$ip plaintext=$(ssh eric@192.168.0.$ip \
    "mount -t nfs4 | grep -E 'pve-nas|192.168.0.102' | grep -vc xprtsec=tls")"
done   # every node must report plaintext=0
```

HAOS (.154) is exempt: it mounts `/export/media` read-only over **plaintext**
from its own client line, and never opens a TLS session to the NAS, so it never
mixes transports on one client. The rule is per-client — each client must be
internally consistent, not identical to the others.
