# Security Posture (at rest and in transit)

The homelab's threat model treats the LAN as a trust boundary. TLS terminates at
the perimeter (Cloudflare to Traefik wildcard certs) and on outbound paths (SMTP
STARTTLS, DoT to upstream resolvers, WireGuard for Tailscale). Inside the LAN,
some application traffic and on-disk data are cleartext by choice, to keep
operations simple. This doc records what is and is not encrypted so the posture
is explicit rather than assumed.

Storage encryption itself, including encryption roots, boot unlock and key
handling, is [docs/32](32-zfs-encryption.md).

## At Rest

`tank` and `ssd` use ZFS-native dataset-level encryption with boot-time
passphrase unlock via 1Password Connect (`zfs_encryption` role). Pool roots stay
plaintext; the encryption roots are the sensitive child datasets. `nvme` is
plaintext by design: it is the media domain. `archive` is a plaintext raidz1
pool whose sensitive datasets arrive as raw `zfs send -w` streams, encrypted
under their source keys. Compute-node `local-ssd` pools and pve-nas-01's
`local-lvm` thin pool stay plaintext so that boot never waits on the Connect
VIP.

| Subsystem | Encrypted? | Notes |
|---|---|---|
| ZFS pools `tank`, `ssd` | Yes (dataset-level) | Encryption roots are `tank/share`, `tank/backups`, `tank/proxmox`, `tank/nextcloud-data`, `tank/immich-data`, `ssd/appdata` and children, `ssd/databases`, `ssd/pve`, `ssd/k3s-etcd`. `tank/media` and `tank/pve` stay plaintext by design. `ssd/databases` is empty and outside the archive and restic chains. |
| ZFS pool `nvme` | No | Media domain: hot-tier media, transcode scratch, ephemeral images. |
| ZFS pool `archive` | Dataset-level (raw) | Plaintext pool; the replicated backup datasets arrive as raw `zfs send -w` streams (`archive-backupctl`). |
| Compute-node `local-ssd` pools (5 hosts) | No | Plaintext on purpose; see the cold-boot note below. |
| pve-nas-01 `local-lvm` (LVM-thin in VG `pve`) | No | Not ZFS and not dm-crypt; see the note below. |
| App PVCs, zvol-backed | Yes | Authentik PG, Mealie PG, GitLab repos, Prometheus, Loki are zvols under `ssd/appdata/*` and inherit the encrypted parent. |
| App PVCs, NFS-backed | Yes, except the media share | Every app NFS PV resolves under `/appdata` or `/backups-apps`, both encryption roots. The `/media` PV used by the download stack is the plaintext media domain. |
| Proxmox VM disks | Mixed | See the note below. |
| K3s Secrets in etcd | Yes | `secrets-encryption: true`, `reencrypt_finished` confirmed. |
| 1Password Connect on-disk cache | Yes | Connect's encrypted SQLite, AES via bootstrap credentials. |
| 1Password vault (cloud + sync source) | Yes | Vendor end-to-end, SRP plus secret key. |
| Backups (archive pool, ZFS snapshots, GitLab backups) | Yes (dataset-level) | `tank/proxmox` is encrypted; `archive` datasets are raw-encrypted under their source keys. |
| Proxmox host root filesystems | No | Standard install, no LUKS. |
| Proxmox host swap (all 6 hosts) | Yes | `encrypted_swap` role: dm-crypt plain-mode AES-256-XTS over `/dev/pve/swap` with a random key per boot, so paged-out memory is unrecoverable after a reboot ([docs/32](32-zfs-encryption.md) § Host swap (dm-crypt)). A host that falls back to plaintext swap keeps a green unit, so the gauges behind `EncryptedSwapPlaintextFallback` and `EncryptedSwapMetricsMissing` are what report the gap. |

Notes on the mixed rows:

- **Cold boot.** Encrypting a compute node's `local-ssd` deadlocks boot: the k3s
  VMs live there, Connect runs in k3s, and pve-nas-01 needs Connect to fetch its
  own passphrase. Coverage moves to the drive-wipe SOP in
  [docs/15](15-credential-rotation.md) and to k3s `secrets-encryption`.
- **pve-nas-01 `local-lvm`.** A thin pool on partition 3 of the Proxmox boot
  NVMe. It carries VM 202 (k3s-agt-nas-01) and VM 222 (k3s-srv-nas-01, an etcd
  quorum member): VM 222 must never wait on unlock, or quorum would depend on
  the NAS. VM 202 appears in `zfs_encryption_guest_vmids` only for start
  ordering, because its passthrough data zvols live on the encrypted
  `ssd/appdata`. The plex and immich-ml LXC rootfs sit on the encrypted `ssd`
  storage id and start after the unlock ([docs/32](32-zfs-encryption.md)).
- **Proxmox VM disks.** App-data zvols under `ssd/appdata/*` are encrypted, and
  the NAS app-VM root disks land on `ssd/pve`, which is an encryption root.
  Guest disks on `local-ssd`, on `local-lvm` and on `tank/pve` are plaintext by
  design. `proxmox_backup_storage` pins the `ssd` storage id to `pool: ssd/pve`;
  `pool` is create-fixed and asserted, so an id recreated against the pool root
  fails the deploy instead of silently landing new disks in plaintext.
  `postflight.yml` additionally asserts `ssd/pve` is still `aes-256-gcm`.

## In Transit

| Path | Encrypted? | Notes |
|---|---|---|
| Internet to Cloudflare edge | Yes | TLS 1.2+/1.3, `always_use_https=on`. |
| Cloudflare to origin (Traefik) | Yes | `ssl=strict`. Traefik enforces TLS 1.3 minimum via the cluster-default `TLSOption`. |
| LAN/Tailscale client to Traefik | Yes | Wildcard certs, HSTS middleware, same default `TLSOption`. |
| Traefik to in-cluster pods | Yes (cross-node) | Plain HTTP at L7; flannel-wireguard encrypts every cross-node packet. Same-node hops stay on the local bridge. |
| Traefik to VM backends (GitLab web, HAOS, Plex) | Yes | See the note below. |
| Traefik to AdGuard admin (dns-01/dns-02) :443 | Yes | Both the resolver-hostname routes and the SSO routes target the backend Services with `scheme: https` and the `vm-tls-wildcard` ServersTransport. |
| adguard-exporter to AdGuard admin API :443 | Yes | `ADGUARD_SERVERS: https://dns-0X.esweiss.com` with pod-level `hostAliases` pinning each name to the host IP. The name is required because the wildcard cert has no IP SAN. |
| Traefik to GitLab Container Registry :5050 | Yes | `registry_nginx['listen_https'] = true` with the distributed wildcard cert. |
| Traefik to GitLab Pages :8443 | Yes | `pages_nginx` terminates TLS on :8443; the pages daemon binds localhost only. |
| Traefik to router | Yes (self-signed, unverified) | See the note below. |
| Pod to pod (CNI) | Yes (cross-node) | flannel wireguard-native, UDP/51820. Same-node pod-to-pod stays on the local bridge. |
| ESO to 1Password Connect | Yes (cross-node) | Plain HTTP at L7; cross-node hops ride flannel-wireguard. |
| Alloy to Loki ingestion | Yes (cross-node) | Plain HTTP at L7; cross-node hops ride flannel-wireguard. |
| Host Alloy to Loki | Yes | `alloy_host_loki_url` is the Traefik IngressRoute `https://loki.esweiss.com/loki/api/v1/push`. The plaintext `:31100` NodePort is not in git; it is applied by hand for the duration of an ingress outage only ([docs/12](12-runbooks.md) § Loki break-glass NodePort). |
| AdGuard sync (dns-01 to dns-02) | Yes | adguardhome-sync targets the Traefik-fronted `dns-{01,02}.esweiss.com` hostnames. |
| Local clients to smtp-relay | Yes in practice | See the note below. |
| Immich VM (.157) to Immich ML LXC (.158) :3003 | No | Every photo byte crosses the LAN as plain HTTP, scoped to the one source by the `sg-immich-ml` security group ([docs/36](36-immich.md)). |
| smtp-relay to Gmail | Yes | `smtp_tls_security_level: secure`, so the relay verifies Gmail's certificate. |
| Unbound to upstream resolvers | Yes | DoT with `tls-cert-bundle`. |
| LAN clients to AdGuard :53 | Partial | Plain UDP/TCP 53; DoT is exposed but stub resolvers rarely use it. |
| NFS exports | TLS, with one plaintext client | The k3s client lines and `/export/tank-proxmox` require TLS; HAOS (.154) is the documented plaintext exception. [docs/07](07-fileservices.md) § Transport Security owns the rule. |
| Samba | Yes | `smb encrypt = required` and `server min protocol = SMB3_00`. |
| Tailscale (admin remote access) | Yes | WireGuard. |
| K3s API server | Yes | TLS, kube-vip plus the standard k3s API certs. |
| GitLab SSH | Yes | Port 22 internal, 2222 external. |

Notes on the mixed rows:

- **Traefik to VM backends.** Each backend terminates TLS with the
  `*.esweiss.com` wildcard distributed by `acme_certs`: gitlab nginx :443, HAOS
  `http.ssl_certificate` :8123, Plex custom-cert PFX :32400. Traefik connects
  with `scheme: https` and the shared `vm-tls-wildcard` ServersTransport, and
  renewal rides the acme.sh post-renewal hook.
- **Traefik to router.** The UCG-Fiber serves its UI over HTTPS with its own
  self-signed certificate, which no wildcard covers, so that one backend uses
  the dedicated `unifi-self-signed` ServersTransport with `insecureSkipVerify`
  instead of `vm-tls-wildcard`. One LAN hop to the default gateway, behind
  `lan-tailscale-strict`.
- **Local clients to smtp-relay.** The hosts' null-client sets
  `smtp_tls_security_level = secure`, which encrypts and verifies, and
  submission :587 requires TLS. The relay's :25 smtpd is opportunistic
  (`smtpd_tls_security_level = may`), so plaintext would be accepted there, but
  no client uses it and `mynetworks` / `smtpd_relay_restrictions` are
  loopback-only plus SASL, so an unauthenticated LAN sender on :25 is refused.

## Related documentation

- [docs/32 — ZFS encryption](32-zfs-encryption.md) (encryption roots, boot unlock, key handling, host swap)
- [docs/06 — ZFS](06-zfs.md) (pool and dataset layout)
- [docs/07 — File services](07-fileservices.md) (NFS/Samba transport rules)
- [docs/09 — Certificates](09-certs.md) (the wildcard certs these paths use)
- [docs/11 — Firewall](11-firewall.md) (what can reach each path)
- [docs/42 — Offsite backup](42-offsite-backup.md) (offsite ciphertext)
- [docs/35 — Nextcloud](35-nextcloud.md) (§ SSRF toggle: the one accepted risk that widens a server-side fetch surface, and the inventory line that opts into it)
