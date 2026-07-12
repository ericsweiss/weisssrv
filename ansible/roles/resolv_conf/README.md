# Role: resolv_conf

Shared helper that owns `/etc/resolv.conf` for hosts that need a managed DNS
config. Used transitively by other roles rather than invoked directly.

Used by:

- `base` role: when `skip_dns_config: false` (default outside molecule tests)
- `adguard_home` role: ensures the local resolver host points at its own
  AdGuard instance via `127.0.0.1`

## Inputs

Required:

- `host_dns_servers` — list of nameserver IPs (e.g.
  `["192.168.0.150", "192.168.0.160"]`)
- `internal_domain` — used only as the default first entry of
  `resolv_conf_search_domains`; not required if you override the search
  list explicitly.

Optional:

- `resolv_conf_search_domains` — list of search-suffix domains. Defaults
  to `[internal_domain]`; explicitly set to `[]` to omit BOTH the
  `domain` and `search` lines. (`domain` is functionally a 1-element
  search list, so suppressing only `search` would still apply
  search-suffix behavior via `domain`.) K3s VMs override to `[]` in
  `group_vars/k3s.yml` so kubelet doesn't propagate any search domains
  into pods (which would inflate every cluster-internal lookup by
  `ndots:5`).
- `resolv_conf_unsafe_writes` — defaults to `false`. Set to `true` only
  in Molecule container environments where `/etc/resolv.conf` is
  bind-mounted from the host and atomic rename returns `EBUSY`.
  Production hosts never need this.
- `resolv_conf_immutable` — defaults to `false`. When `true`, the role
  removes the `chattr +i` immutable flag before writing, re-sets it
  afterwards, and verifies it stuck (protects the file from DHCP/systemd
  overwrites). On unprivileged containers (LXC guests such as dns-01/02
  and smtp-relay) `chattr +i` cannot succeed — the role emits a warning
  there instead of failing, and protection relies on the file being
  Ansible-managed. Container detection is `resolv_conf_is_container`
  (derived from `ansible_facts['virtualization_type']`, overridable).

## See also

- `docs/08-dns.md` — DNS architecture
