# K3s Role

Installs and configures Kubernetes k3s cluster with embedded etcd, kube-vip VIP, persistent storage (ZFS zvols), and node labels/taints for workload placement.

## What This Role Manages

### Prerequisites
- Package installation (curl, open-iscsi, nfs-common)
- iscsid service enablement
- Config directory creation

### Persistent Storage
- Additional disk formatting (ZFS zvols as /dev/sdb, /dev/sdc, etc.)
- Filesystem creation (ext4)
- UUID-based mounting in /etc/fstab
- Mount point creation and management

### K3s Installation
- Version checking and upgrading (pinned installer script, optional sha256 pin
  via `k3s_install_script_checksum`)
- Server installation (with embedded etcd, `secrets-encryption: true`,
  WireGuard flannel backend — see docs/19)
- Agent installation (connects to API VIP with the lower-privilege agent
  token; existing agents are migrated off the server token — see docs/19)
- Kube-vip manifest deployment (first server only)
- /etc/hosts pins: container-registry hostname → internal Traefik VIP
  (`k3s_registry_host_pins`) and NAS storage hostname for NFS-over-TLS PVs
  (`k3s_storage_host_pins`)
- Node label application
- Node taint application
- Off-node etcd snapshot copy (opt-in, servers only): a systemd timer that
  copies the newest local etcd snapshot to an NFS export on pve-nas-01 (by
  hostname over TLS) and emits an `etcd_snapshot_last_copy_timestamp_seconds`
  textfile metric for the `EtcdSnapshotStale` alert — off by default via
  `k3s_etcd_snapshot_offnode_enabled` (see docs/17 and defaults for the
  companion NFS export + `node_exporter_host` + `nfs_tls`/tlshd on the servers
  it needs — the `xprtsec=tls` mount hangs without the TLS handshake daemon)

## Configuration

```yaml
# Versions are pinned in group_vars/all.yml (k3s_version, kube_vip_version)
# Use `task maintenance:check-versions` to see available updates.

# Cluster configuration
k3s_api_vip: "192.168.0.161"
k3s_token: "{{ lookup('ansible.builtin.env', 'K3S_TOKEN') }}"

# Server-specific
k3s_role: server
k3s_is_first_server: true  # Only for one server

# Agent-specific
k3s_role: agent

# Node customization
k3s_labels:
  esweiss.com/nas: "true"
  esweiss.com/general: "true"
k3s_taints:
  - key: esweiss.com/nas
    value: "true"
    effect: PreferNoSchedule

# Persistent disks (for PostgreSQL, etc.)
vm_additional_disks:
  - name: postgres-data
    size: 10G
    zvol: ssd/appdata/authentik/postgres
    mount_point: /mnt/postgres-data
    fstype: ext4
```

## Deployment

```bash
# Full k3s cluster deployment
task k3s:deploy

# Provision VMs first (if needed)
task k3s:provision-vms

# Deploy k3s on existing VMs
ansible-playbook ansible/playbooks/k3s.yml

# Get kubeconfig
task k3s:kubeconfig
```

## Architecture

```
k3s Cluster (9 nodes: 3 servers + 6 agents)
├─ Servers (etcd quorum)
│  ├─ k3s-srv-nas-01 (.222) - pve-nas-01
│  ├─ k3s-srv-laptop-01 (.223) - pve-laptop-01
│  └─ k3s-srv-prec-01 (.227) - pve-prec-01
│
├─ Kube-vip VIP: 192.168.0.161
│
└─ Agents (workloads)
   ├─ k3s-agt-nas-01 (.202) - NAS workloads, persistent disks
   ├─ k3s-agt-laptop-01 (.203) - ingress + general
   ├─ k3s-agt-opt-01 (.204) - ingress + general
   ├─ k3s-agt-opt-02 (.205) - ingress + general
   ├─ k3s-agt-opt-03 (.206) - ingress + general
   └─ k3s-agt-prec-01 (.207) - compute + general
```

## Task Flow

```
1. Install prerequisites
2. Enable iscsid service
3. Create k3s config directory
4. Mount additional persistent disks (if defined)
   ├─ Check if formatted
   ├─ Format if needed (ext4)
   ├─ Get filesystem UUID
   ├─ Create mount points
   └─ Add to /etc/fstab and mount
5. Include server or agent tasks based on k3s_role
6. Apply node labels
7. Apply node taints
```

## Files

- `tasks/main.yml` - Main orchestration (prerequisites, /etc/hosts pins, disks)
- `tasks/server.yml` - Server installation
- `tasks/agent.yml` - Agent installation (incl. agent-token migration)
- `tasks/install-script.yml` - Shared version detection + installer staging
- `tasks/etcd-snapshot-offnode.yml` - Off-node etcd snapshot copy (opt-in)
- `templates/k3s-server-config.yaml.j2` - Server configuration
- `templates/k3s-agent-config.yaml.j2` - Agent configuration
- `templates/kube-vip-manifest.yaml.j2` - Kube-vip DaemonSet
- `templates/k3s-etcd-snapshot-copy.{sh,service,timer}.j2` - Off-node snapshot copy
- `defaults/main.yml` - Default values
- `handlers/main.yml` - Service restart + Ready-gate handlers
- `molecule/default/` - Server scenario (bootstrap + join branches)
- `molecule/agent/` - Agent scenario (config, token migration)

## Dependencies

Applied by `ansible/playbooks/k3s.yml` (not meta dependencies):

- `base` role (networking, DNS)
- `qol` role
- `postfix_null_client` role (for system mail)
- `alloy_host` role (host journald -> Loki shipping)
- `nfs_tls` / `proxmox_firewall` roles (separate plays in the same playbook)

## 1Password Secrets

```yaml
secrets:
  k3s_token: "op://Homelab/K3s Cluster Token/credential"        # K3S_TOKEN
  k3s_agent_token: "op://Homelab/K3s Agent Token/credential"    # K3S_AGENT_TOKEN
```

`K3S_AGENT_TOKEN` is the dedicated lower-privilege agent join token (falls
back to the server token when unset). See docs/19 for the agent-token,
secrets-encryption, flannel-backend, and registry-pin details.

## Persistent Storage

ZFS zvols provide persistent database storage that survives VM recreation:

```yaml
# Zvol created on Proxmox host
zfs create -V 10G ssd/appdata/authentik/postgres

# Attached to VM as scsi1 (/dev/sdb)
# Formatted as ext4
# Mounted at /mnt/postgres-data
# Used by PostgreSQL pod
```

Data persists across VM recreation since zvols live on the Proxmox host's ZFS pool. This also gives you ZFS snapshots and compression for free.

## Cluster Access

```bash
# Get kubeconfig
task k3s:kubeconfig

# Use kubectl
export KUBECONFIG=~/.kube/config-k3s
kubectl get nodes
kubectl get pods -A
```

## Operational Notes

### Scaling the Cluster

Add more nodes by:
1. Defining in inventory (hosts.yml)
2. Provisioning VMs: `task k3s:provision-vms`
3. Deploying k3s: `ansible-playbook ansible/playbooks/k3s.yml --limit new-node`

### Version Updates

```bash
# Bump k3s_version in ansible/inventories/prod/group_vars/all.yml, then run:
task maintenance:update-k3s-nodes
```

### Troubleshooting

```bash
# Check k3s service
systemctl status k3s  # On servers
systemctl status k3s-agent  # On agents

# View logs
journalctl -u k3s -f
journalctl -u k3s-agent -f

# Check cluster
kubectl get nodes
kubectl get pods -A --field-selector spec.nodeName=k3s-agt-nas-01
```
