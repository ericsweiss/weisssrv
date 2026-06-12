# Tailscale VPN Setup

Tailscale provides secure remote access to the homelab via a WireGuard-based mesh VPN.

## Overview

Tailscale is installed on all Proxmox hosts to enable secure remote administration from anywhere.

- **Network**: `100.64.0.0/10` (CGNAT range used by Tailscale)
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
# Connect to Tailscale with specific flags
sudo tailscale up \
  --ssh \
  --operator=eric \
  --accept-dns=false \
  --advertise-exit-node=false
```

**Flags explained:**
- `--ssh`: Enable Tailscale SSH for secure access
- `--operator=eric`: Allow user `eric` to manage Tailscale
- `--accept-dns=false`: Use internal AdGuard DNS instead of Tailscale DNS
- `--advertise-exit-node=false`: Do not advertise as exit node

**Note**: `--accept-routes` is intentionally omitted to prevent routing loops. Hosts should not accept routes from other Tailscale nodes.

### Ansible Variables

Tailscale configuration is managed in `group_vars/all.yml`:

```yaml
tailscale_enabled: true
tailscale_accept_routes: false  # Prevents routing loops; hosts should not accept routes
tailscale_accept_dns: false  # We use our own DNS

secrets:
  tailscale_auth_key: "op://Homelab/Tailscale Auth Key/credential"
```

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
- **SSH Access**: Tailscale SSH provides additional security layer
- **Operator User**: Only `eric` user can manage Tailscale on each host
- **No Exit Node**: Hosts do not route internet traffic through the homelab

## References

- [Tailscale Documentation](https://tailscale.com/kb/)
- [Tailscale on Debian](https://tailscale.com/kb/1039/install-debian-trixie/)
