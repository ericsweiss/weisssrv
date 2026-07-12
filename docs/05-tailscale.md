# Tailscale VPN Setup

Tailscale provides secure remote access to the homelab via a WireGuard-based mesh VPN.

## Overview

Tailscale is installed on all Proxmox hosts to enable secure remote administration from anywhere.

- **Network**: `100.64.0.0/10` (CGNAT range used by Tailscale)
- **Subnet routing**: all six Proxmox hosts advertise `192.168.0.0/24`, so any
  one of them can carry LAN access for remote clients (real failover, not a
  single-host SPOF)
- **ACL policy as code**: the tailnet ACL (access rules, Tailscale SSH rules,
  route auto-approvers) is managed in `terraform/tailscale/` — see below
- **Firewall**: `admin_ts` IP Set allows Tailscale network access
- **DNS**: Tailscale DNS is disabled; internal AdGuard DNS is used instead

## Installation

Tailscale is deployed via the `tailscale` Ansible role on all Proxmox hosts.

### Automated Deployment

```bash
# Deploy Tailscale to all Proxmox hosts
ansible-playbook ansible/playbooks/site.yml --tags tailscale
```

### Manual Installation (if needed)

On Debian "trixie" (or newer), the Tailscale repo must be added first:

```bash
# Add Tailscale's package repository
sudo curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
sudo curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.list | sudo tee /etc/apt/sources.list.d/tailscale.list

# Install Tailscale
sudo apt update
sudo apt install tailscale
```

## Configuration

### Initial Setup

After installation, authenticate each host:

```bash
# Connect to Tailscale with specific flags (what the role runs on Proxmox hosts)
sudo tailscale up \
  --ssh \
  --operator=eric \
  --accept-dns=false \
  --accept-routes=false \
  --advertise-routes=192.168.0.0/24
```

**Flags explained:**
- `--ssh`: Enable Tailscale SSH for secure access
- `--operator=eric`: Allow user `eric` to manage Tailscale
- `--accept-dns=false`: Use internal AdGuard DNS instead of Tailscale DNS
- `--accept-routes=false`: Subnet routers must NOT accept routes from other
  peers — prevents routing loops where a host routes its own LAN traffic
  through Tailscale
- `--advertise-routes=192.168.0.0/24`: Advertise the internal LAN so remote
  clients can reach it via any Proxmox host

The role only runs `tailscale up` for the initial authentication; on
already-running nodes it reconciles preferences every run with
`tailscale set` (idempotent, no re-auth).

### Ansible Variables

Defaults live in `group_vars/all.yml`; the Proxmox hosts opt into subnet
routing in `group_vars/proxmox.yml`:

```yaml
# group_vars/all.yml
tailscale_enabled: true
tailscale_accept_routes: false  # Prevents routing loops; hosts should not accept routes
tailscale_accept_dns: false  # We use our own DNS
tailscale_advertise_routes: []  # Only subnet routers (Proxmox hosts) advertise routes

secrets:
  tailscale_auth_key: "op://Homelab/Tailscale Auth Key/credential"

# group_vars/proxmox.yml
tailscale_advertise_routes:
  - "192.168.0.0/24"
tailscale_additional_flags:
  - "--operator=eric"
  - "--ssh"
```

## Subnet Routing

All six Proxmox hosts advertise `192.168.0.0/24`. The role enables the
required IP forwarding via a role-owned sysctl drop-in
(`/etc/sysctl.d/99-tailscale-ip-forward.conf`) plus a tailscaled systemd
drop-in (`ExecStartPost` re-applies `net.ipv4.ip_forward=1`, since Proxmox
bridge/network init can reset it after systemd-sysctl at boot). When
`tailscale_advertise_routes` is empty the role removes both drop-ins.

An advertised route is only usable once **approved** in the tailnet. The
`autoApprovers` block in `terraform/tailscale/policy.hujson` auto-approves any
owner-advertised `192.168.0.0/24` route, which is what makes subnet-router
failover across the six hosts real instead of a per-host manual approval.

## Tailnet ACL Policy as Code (terraform/tailscale)

`terraform/tailscale/` manages the tailnet **ACL policy** — access rules,
Tailscale SSH rules, and subnet-route auto-approvers — as `policy.hujson`,
mirroring the `terraform/cloudflare` pattern (GitLab HTTP state backend,
1Password-injected credentials).

- **Credentials**: the `Tailscale OAuth` 1Password item (fields `client id`
  and `credential`) holds an OAuth client scoped to `acl` (write). See
  `docs/15-credential-rotation.md` for the item inventory.
- **Apply is supervised**: a wrong policy can sever tailnet connectivity and
  Tailscale SSH, so `terraform apply` here is a deliberate operator step (a
  read-only drift `plan` in CI is fine). Follow the runbook in
  `terraform/tailscale/README.md`.

## Usage

### Check Status

```bash
# View Tailscale status
sudo tailscale status

# View current IP
sudo tailscale ip -4

# View connection details
sudo tailscale netcheck
```

### SSH via Tailscale

Once configured, you can SSH directly via Tailscale hostnames:

```bash
ssh eric@pve-nas-01.<tailnet>.ts.net  # replace <tailnet> with your tailnet name
```

Or via Tailscale IP:

```bash
tailscale status  # Find the Tailscale IP
ssh eric@100.x.x.x
```

## Firewall Integration

The Proxmox firewall includes an `admin_ts` IP Set (`100.64.0.0/10`) that allows administrative access from Tailscale:

- **SSH** (port 22)
- **Proxmox Web UI** (port 8006)
- **DNS containers (sg-dns)**: 53 udp/tcp, 853 udp/tcp (DoT), 3000 (admin UI; HTTPS UI goes via Traefik ingress on 443)
- **k3s Ingress** (ports 80, 443)

See [11-firewall.md](11-firewall.md) for details.

## Troubleshooting

### Tailscale Not Starting

If Tailscale fails to start:

```bash
# Check service status
sudo systemctl status tailscaled

# View logs
sudo journalctl -u tailscaled -f

# Restart service
sudo systemctl restart tailscaled
```

### Cannot Access Homelab via Tailscale

1. Verify Tailscale is connected:
   ```bash
   sudo tailscale status
   ```

2. Check firewall allows Tailscale network:
   ```bash
   sudo iptables -L PVEFW-HOST-IN -v -n | grep "100.64"
   ```

3. Verify routes are accepted:
   ```bash
   sudo tailscale status --json | jq '.Self.AllowedIPs'
   ```

### DNS Issues

If DNS resolution doesn't work over Tailscale:

1. Ensure `--accept-dns=false` was used during `tailscale up`
2. Verify `/etc/resolv.conf` points to internal DNS (192.168.0.150/160)
3. Check that DNS ports (53, 853) are allowed in firewall for Tailscale network

## Security Considerations

- **Auth Keys**: Use ephemeral or reusable auth keys from Tailscale admin console
- **SSH Access**: Tailscale SSH provides additional security layer; SSH
  authorization is governed by the tailnet ACL, now versioned in
  `terraform/tailscale/policy.hujson`
- **Operator User**: Only `eric` user can manage Tailscale on each host
- **No Exit Node**: Hosts do not route internet traffic through the homelab
- **ACL changes**: review `policy.hujson` against the live Admin-console ACL
  before any supervised apply (see `terraform/tailscale/README.md`)

## References

- [Tailscale Documentation](https://tailscale.com/kb/)
- [Tailscale on Debian](https://tailscale.com/kb/1039/install-debian-trixie/)
