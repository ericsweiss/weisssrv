# zfs_encryption

Boot-time unlock of ZFS-native encrypted pools by fetching the
passphrase from 1Password Connect.

## What it deploys

Per host:

- `/etc/onepassword-connect/token` (mode 0400, root) — Connect access
  token used by the unlock script.
- `/etc/zfs/encryption/pools/<pool>.conf` (mode 0400, root) — env file
  loaded by the systemd unit, containing `ZFS_ENCRYPTION_ITEM=<title>`
  and `ZFS_ENCRYPTION_FIELD=passphrase` for each pool.
- `/usr/local/sbin/zfs-load-key.sh` (mode 0750) — fetches passphrase
  from Connect and runs `zfs load-key`. Mounting is handled by
  `zfs-mount.service` (this unit is `Before=` it); calling `zfs mount
  -a` from inside the unit would race across parallel-running pool
  instances and couldn't propagate mounts outside its
  ProtectSystem=strict namespace anyway.
- `/etc/systemd/system/zfs-load-key@.service` — template unit ordered
  `After=zfs-import.target`, `RequiredBy=zfs-mount.service` (mount fails
  loudly if key load doesn't succeed, rather than silently mounting
  encrypted-but-unkeyed). `Restart=on-failure` with
  `StartLimitIntervalSec=3600s` / `StartLimitBurst=60`. Each ExecStart
  attempt does two sequential Connect calls (vault UUID resolve + item
  fetch), each capped at `fetch_timeout` (default 120s), so a single
  attempt can run up to ~240s before RestartSec=30s. Within the 1h
  window only ~13 attempts fit — well below the 60 burst — so in
  practice the unit retries continuously rather than tripping into
  permanent failed. Operator recovery via manual `zfs load-key`
  transitions the next ExecStart to a quick keystatus-short-circuit
  exit 0, marking the unit active.
- `zfs-load-key@<pool>.service` enabled per pool listed in
  `zfs_encryption_pools`.

## Threat model

Encryption protects against **disk-leaves-building** scenarios: RMA,
disposal, theft of an offline drive. It does NOT protect against
running-system theft — anyone with root on the running host can
extract the Connect token and use it (over LAN) to fetch the same
passphrase. Storing the literal key on disk would have the same
exposure with one fewer indirection.

The Connect token is strictly more limited than a 1Password Service
Account token: it can only read items from the configured Connect
server's vault (`Homelab`), and only over the LAN since
`connect.esweiss.com` is gated by `lan-tailscale-only` in Traefik.

## Cold-cluster boot

If everything power-cycles together and Connect isn't yet running,
each host's `zfs-load-key@<pool>.service` will retry continuously
under `Restart=on-failure` + `RestartSec=30s`. The 1h StartLimit
window almost certainly doesn't trip given the per-attempt budget
(~270s with C1 sequential vault+item resolves), so the unit retries
forever rather than transitioning to a permanent failed state —
which is fine, the operator-recovery path closes the loop:

```bash
ssh <host>
sudo zfs load-key <pool>     # paste passphrase from 1P mobile app
sudo zfs mount -a
```

`RequiredBy=zfs-mount.service` means `zfs-mount.service` is also
permanently failed at this point. After the manual unlock above, also
clear the failed states so subsequent `systemctl daemon-reload` or
service-restarts don't trip on the residual:

```bash
sudo systemctl reset-failed "zfs-load-key@<pool>.service" zfs-mount.service
sudo systemctl start zfs-mount.service        # idempotent if already mounted
```

This recovery path is documented in detail in `docs/32-zfs-encryption.md`.

## Variables

See `defaults/main.yml`. Key ones:

| Variable | Required | Notes |
|----------|----------|-------|
| `zfs_encryption_connect_token` | yes (active mode) | Provide via `op read` at runtime |
| `zfs_encryption_pools` | yes | List of `{name, item, field}` per pool |
| `zfs_encryption_bootstrap_only` | no (default `false`) | If `true`, install but don't enable units |
| `zfs_encryption_connect_url` | no | Defaults to `https://connect.{{ internal_domain }}` |
| `zfs_encryption_connect_vault` | no | Defaults to `Homelab` |

## Bootstrap workflow

1. Apply role with `zfs_encryption_bootstrap_only: true` to seed the
   token, script, and unit template before any pool is encrypted. Safe
   to leave deployed; the per-pool services are only enabled later.
2. Encrypt each pool (one at a time) per the procedure in
   `docs/32-zfs-encryption.md`.
3. After encryption, add the pool to `zfs_encryption_pools` for the
   host and re-apply with `zfs_encryption_bootstrap_only: false`.

## Required 1Password items

Per pool, create a `Password` item in `Homelab` vault with field
`passphrase`. Title should match `zfs_encryption_pools[*].item`.
Naming convention: `ZFS Pool <pool> Passphrase` (e.g. `ZFS Pool tank
Passphrase`).
