# Proxmox Firewall

The Proxmox cluster uses the built-in firewall with centralized rules in `/etc/pve/firewall/`.

## Architecture

```
/etc/pve/firewall/
  cluster.fw          # Cluster-wide config (IPSets, Groups, Aliases)
  <vmid>.fw           # Per-VM/CT firewall rules

/etc/pve/nodes/<node>/
  host.fw             # Per-node host firewall
```

## Cluster Configuration

### IPSets

IPSets define groups of IPs for use in rules. IPSets are **dynamically generated from inventory** - hosts are automatically added to IPSets based on their `firewall_ipsets` metadata.

```ini
[IPSET dc/core-cluster]
# Proxmox hosts
192.168.0.102  # pve-nas-01
192.168.0.103  # pve-laptop-01
192.168.0.104  # pve-opt-01
192.168.0.105  # pve-opt-02
192.168.0.106  # pve-opt-03

[IPSET dc/k3s-nodes]
# K3s cluster VMs
192.168.0.202  # k3s-srv-nas-01
192.168.0.203  # k3s-srv-laptop-01
192.168.0.204  # k3s-srv-opt-01
192.168.0.205  # k3s-agt-opt-02
192.168.0.206  # k3s-agt-opt-03
192.168.0.207  # k3s-agt-nas-01

[IPSET dc/nfs_clients]
# Hosts allowed NFS access
192.168.0.102
192.168.0.103
192.168.0.104
192.168.0.105
192.168.0.106
192.168.0.154
192.168.0.200/29

[IPSET dc/smb_clients]
# Hosts allowed SMB access
192.168.0.0/24
```

### Security Groups

Security groups are reusable rule sets.

```ini
[group sg-ssh]
IN ACCEPT -source 192.168.0.150 -p tcp -dport 22 -log nolog

[group sg-nfs]
IN ACCEPT -source +dc/nfs_clients -p tcp -dport 2049 -log nolog
IN ACCEPT -source +dc/nfs_clients -p tcp -dport 111 -log nolog

[group sg-smb]
IN ACCEPT -source +dc/smb_clients -p tcp -dport 445 -log nolog

[group sg-smtp-relay]
IN ACCEPT -source +dc/core-cluster -p tcp -dport 587 -log nolog
IN ACCEPT -source 192.168.0.150 -p tcp -dport 22 -log nolog

[group sg-dns]
IN ACCEPT -p tcp -dport 53 -log nolog
IN ACCEPT -p udp -dport 53 -log nolog
IN ACCEPT -p tcp -dport 853 -log nolog
IN ACCEPT -p udp -dport 853 -log nolog
IN ACCEPT -p tcp -dport 443 -log nolog
IN ACCEPT -source 192.168.0.150 -p tcp -dport 22 -log nolog
```

### Options

```ini
[OPTIONS]
enable: 1
policy_in: DROP
policy_out: ACCEPT
log_level_in: nolog
log_level_out: nolog
```

## Host Firewall

Each node has a host.fw that can reference security groups:

**pve-nas-01 host.fw:**
```ini
[OPTIONS]
enable: 1

[RULES]
GROUP sg-nfs
GROUP sg-smb
```

## LXC Container Firewall

Containers can have individual firewall rules:

**smtp-relay:**
```ini
[RULES]
GROUP sg-smtp-relay
```

**dns-01/dns-02:**
```ini
[RULES]
GROUP sg-dns
```

## Ansible Role

Deploy with: `ansible/roles/proxmox_firewall`

The role manages:
- `/etc/pve/firewall/cluster.fw`
- `/etc/pve/nodes/<node>/host.fw`

### Managing IPSets via Inventory

IPSets are dynamically generated from inventory metadata. To add a host to an IPSet, add the `firewall_ipsets` list to the host definition:

**Example - Adding a new k3s node:**

```yaml
# inventories/prod/hosts.yml
k3s_servers:
  hosts:
    k3s-new-node:
      ansible_host: 192.168.0.208
      ansible_connection: local  # If not yet managed by Ansible
      firewall_ipsets:
        - k3s_nodes
        - core-cluster
        - nfs_clients
```

**Available IPSets:**
- `pve_hosts` - Proxmox VE hypervisor hosts
- `core-cluster` - All core infrastructure (Proxmox, DNS, SMTP, services, k3s)
- `k3s_nodes` - Kubernetes nodes
- `nfs_clients` - Hosts allowed NFS access

**Special Entries (VIPs, non-host IPs):**

For IPs that aren't inventory hosts (VIPs, floating IPs), add them to `group_vars/all.yml`:

```yaml
firewall_ipset_special_entries:
  k3s_nodes:
    - ip: 192.168.0.161
      comment: k3s API VIP (kube-vip)
```

**Benefits:**
- ✅ Single source of truth in inventory
- ✅ Automatic IPSet updates when hosts added/removed
- ✅ No duplication between firewall rules and NFS exports
- ✅ Git history tracks which hosts are in security groups

## Troubleshooting

### Check Firewall Status

```bash
# Cluster level
pve-firewall status

# Compile and show rules
pve-firewall compile

# Show active iptables rules
iptables -L -n -v
```

### Logs

```bash
# Firewall logs (if logging enabled)
journalctl -u pve-firewall

# Dropped packets
dmesg | grep -i drop
```

### Common Issues

1. **Locked out of SSH**: Access via Proxmox console, disable firewall temporarily
2. **NFS mounts failing**: Verify NFS IPSet includes client
3. **VM cannot reach network**: Check VM-level firewall is disabled or has proper rules

### Emergency Disable

```bash
# Disable cluster firewall
pvesh set /cluster/firewall/options --enable 0

# Or edit directly
nano /etc/pve/firewall/cluster.fw
# Set enable: 0
```
