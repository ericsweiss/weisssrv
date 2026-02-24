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
- Version checking and upgrading
- Server installation (with embedded etcd)
- Agent installation (connects to API VIP)
- Kube-vip manifest deployment (first server only)
- Node label application
- Node taint application

## Configuration

```yaml
# Version (from group_vars/all.yml)
k3s_version: "v1.35.0+k3s1"
kube_vip_version: "v1.0.4"

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

- `tasks/main.yml` - Main orchestration
- `tasks/server.yml` - Server installation
- `tasks/agent.yml` - Agent installation
- `templates/k3s-server-config.yaml.j2` - Server configuration
- `templates/k3s-agent-config.yaml.j2` - Agent configuration
- `templates/kube-vip-manifest.yaml.j2` - Kube-vip DaemonSet
- `defaults/main.yml` - Default values
- `handlers/main.yml` - Service restart handlers

## Dependencies

- `base` role (networking, DNS)
- `qol` role (optional)
- `postfix_null_client` role (for system mail)

## 1Password Secrets

```yaml
secrets:
  k3s_token: "op://Homelab/K3s Cluster Token/credential"
```

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
# Update version in group_vars/all.yml
k3s_version: "v1.36.0+k3s1"

# Rolling update
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
