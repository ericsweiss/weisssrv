# Operational Runbooks

This document provides step-by-step procedures for common operational tasks.

## Table of Contents

1. [Adding a New Proxmox Host](#adding-a-new-proxmox-host)
2. [Deploying a New LXC Container](#deploying-a-new-lxc-container)
3. [Handling Disk Failure (ZFS)](#handling-disk-failure-zfs)
4. [Updating DNS Records](#updating-dns-records)
5. [Certificate Renewal Issues](#certificate-renewal-issues)
6. [Network Connectivity Issues](#network-connectivity-issues)
7. [Ansible Deployment Failures](#ansible-deployment-failures)
8. [Backup and Recovery](#backup-and-recovery)
9. [Performance Investigation](#performance-investigation)
10. [System Maintenance](#system-maintenance)
11. [Understanding Skipped Tasks](#understanding-skipped-tasks)
12. [Proxmox HA Post-Failover Reconciliation](#proxmox-ha-post-failover-reconciliation)

---

## Adding a New Proxmox Host

### Prerequisites

- Physical server installed with Proxmox VE
- Connected to network with static IP assigned
- SSH access via `eric` user with passwordless sudo

### Procedure

1. **Join Proxmox Cluster**:
   ```bash
   # On existing cluster node (pve-nas-01)
   sudo pvecm status  # Get cluster info

   # On new node
   sudo pvecm add 192.168.0.102  # Join cluster
   ```

2. **Add to Ansible Inventory**:
   ```yaml
   # ansible/inventories/prod/hosts.yml
   proxmox:
     hosts:
       pve-new-01:
         ansible_host: 192.168.0.XXX
         ansible_user: eric
         ansible_become: true
   ```

3. **Create Host Variables**:
   ```bash
   # ansible/inventories/prod/host_vars/pve-new-01.yml
   ---
   # Host-specific overrides
   ```

4. **Deploy Base Configuration**:
   ```bash
   ansible-playbook ansible/playbooks/base.yml --limit pve-new-01
   ```

5. **Configure Firewall**:
   - Add IP to `pve_hosts` IP Set
   - Attach `sg-host-admin` and `sg-pve-cluster` security groups

6. **Deploy Firewall**:
   ```bash
   ansible-playbook ansible/playbooks/site.yml --tags firewall
   ```

7. **Verify**:
   ```bash
   # Check cluster status
   sudo pvecm status

   # Verify firewall
   sudo pve-firewall status

   # Test Ansible
   ansible pve-new-01 -m ping
   ```

---

## Deploying a New LXC Container

### Prerequisites

- LXC template available in Proxmox
- IP address allocated
- Firewall rules planned

### Procedure

1. **Create Container**:
   ```bash
   # Via Proxmox Web UI or CLI
   # Note: Use Debian 13 (Trixie) and local-ssd storage
   sudo pct create 200 \
     local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
     --hostname new-service \
     --net0 name=eth0,bridge=vmbr0,ip=192.168.0.XXX/24,gw=192.168.0.1 \
     --storage local-ssd \
     --cores 2 \
     --memory 2048 \
     --unprivileged 1
   ```

2. **Start Container**:
   ```bash
   sudo pct start 200
   ```

3. **Configure SSH Access**:
   ```bash
   # Enter container
   sudo pct enter 200

   # Install SSH
   apt update && apt install openssh-server

   # Copy SSH key
   mkdir -p ~/.ssh
   echo "ssh-ed25519 AAAA..." > ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```

4. **Add to Inventory**:
   ```yaml
   # ansible/inventories/prod/hosts.yml
   new_service:
     hosts:
       new-service:
         ansible_host: 192.168.0.XXX
         ansible_user: eric
   ```

5. **Configure Firewall**:
   - Create VM-specific firewall file: `/etc/pve/firewall/200.fw`
   - Attach appropriate security groups

6. **Deploy Configuration**:
   ```bash
   ansible-playbook ansible/playbooks/site.yml --limit new-service
   ```

---

## Handling Disk Failure (ZFS)

### Symptoms

- ZFS pool shows DEGRADED status
- Disk errors in `dmesg` or pool status

### Procedure

1. **Identify Failed Disk**:
   ```bash
   sudo zpool status tank
   # Look for UNAVAIL or FAULTED disk
   ```

2. **Locate Physical Disk**:
   ```bash
   # Get disk serial
   sudo smartctl -i /dev/sdX | grep Serial

   # Match with disk label (if available)
   ls -l /dev/disk/by-id/ | grep sdX
   ```

3. **Order Replacement Disk**:
   - Match or exceed capacity
   - Same or better performance tier

4. **Replace Disk** (hot-swap if supported):
   ```bash
   # Power off if necessary
   sudo pct stop <vmid>  # Stop any VMs using the disk

   # Physically replace disk

   # Online new disk
   sudo zpool replace tank old-disk-id /dev/disk/by-id/new-disk-id
   ```

5. **Monitor Resilver**:
   ```bash
   # Watch progress
   sudo zpool status tank

   # Estimated time
   sudo zpool status -v tank | grep "resilver"
   ```

6. **Verify**:
   ```bash
   # Pool should show ONLINE
   sudo zpool status tank

   # Run scrub after resilver
   sudo zpool scrub tank
   ```

---

## Updating DNS Records

### Internal Records (*.esweiss.com)

Managed via AdGuard Home rewrites.

**Via Ansible**:

1. **Edit DNS Variables**:
   ```yaml
   # ansible/inventories/prod/group_vars/dns.yml
   adguard_rewrites:
     - domain: "new-service.{{ internal_domain }}"
       answer: "192.168.0.XXX"
   ```

2. **Deploy**:
   ```bash
   ansible-playbook ansible/playbooks/dns.yml --tags adguard
   ```

**Via AdGuard UI**:

1. Access https://192.168.0.150:3000
2. Navigate to Filters → DNS rewrites
3. Add new entry
4. Changes sync automatically to dns-02 via adguardhome-sync

### External Records (*.ericsweiss.com)

Managed via Terraform + Cloudflare.

1. **Edit Terraform**:
   ```hcl
   # terraform/cloudflare/main.tf
   resource "cloudflare_record" "new_service" {
     zone_id = var.zone_id
     name    = "new-service"
     value   = "192.168.0.XXX"  # Or public IP
     type    = "A"
     ttl     = 3600
   }
   ```

2. **Plan and Apply**:
   ```bash
   cd terraform/cloudflare
   terraform plan
   terraform apply
   ```

---

## Certificate Renewal Issues

### Symptom

Certificate expired or not renewing automatically.

### Procedure

1. **Check Certificate Status**:
   ```bash
   # On dns-01
   sudo /root/.acme.sh/acme.sh --list

   # Check expiry
   sudo openssl x509 -in /opt/AdGuardHome/certs/fullchain.pem -noout -dates
   ```

2. **Check Renewal Logs**:
   ```bash
   sudo tail -100 /root/.acme.sh/acme.sh.log
   ```

3. **Force Renewal**:
   ```bash
   sudo /root/.acme.sh/acme.sh --renew -d esweiss.com --force
   ```

4. **Verify Cloudflare Access**:
   ```bash
   # Test API token
   curl -X GET "https://api.cloudflare.com/client/v4/user/tokens/verify" \
     -H "Authorization: Bearer $(op read 'op://Homelab/Cloudflare DNS Token/credential')"
   ```

5. **Manual Distribution**:
   ```bash
   sudo /usr/local/bin/homelab-cert-reload.sh
   ```

6. **Restart Services**:
   ```bash
   # On dns-01, dns-02
   sudo systemctl restart adguardhome

   # On smtp-relay
   sudo systemctl restart postfix
   ```

---

## Network Connectivity Issues

### Cannot Reach Service

1. **Verify Service Running**:
   ```bash
   sudo systemctl status <service>
   sudo netstat -tlnp | grep <port>
   ```

2. **Check Firewall Rules**:
   ```bash
   # On host
   sudo iptables -L PVEFW-HOST-IN -v -n | grep <port>

   # Check security groups
   cat /etc/pve/nodes/$(hostname)/host.fw
   ```

3. **Test from Different Source**:
   ```bash
   # From LAN
   curl -v http://192.168.0.XXX:port

   # From Tailscale
   curl -v http://192.168.0.XXX:port --interface tailscale0
   ```

4. **Check DNS Resolution**:
   ```bash
   dig @192.168.0.150 service.esweiss.com
   ```

5. **Review Cluster Firewall**:
   ```bash
   sudo cat /etc/pve/firewall/cluster.fw
   ```

---

## Ansible Deployment Failures

### Failed Task

1. **Review Error Output**:
   - Note the failed task name
   - Check error message

2. **Run in Verbose Mode**:
   ```bash
   ansible-playbook ansible/playbooks/site.yml -vvv --limit failed-host
   ```

3. **Test Connectivity**:
   ```bash
   ansible failed-host -m ping
   ansible failed-host -m setup
   ```

4. **Check Logs on Target**:
   ```bash
   ssh eric@failed-host
   sudo journalctl -xe
   ```

5. **Run Specific Tags**:
   ```bash
   ansible-playbook ansible/playbooks/site.yml --tags failed-role --limit failed-host
   ```

---

## Backup and Recovery

### Full System Backup

1. **ZFS Snapshots**:
   ```bash
   for pool in tank ssd nvme archive; do
     sudo zfs snapshot -r $pool@backup-$(date +%Y%m%d)
   done
   ```

2. **Proxmox Backup**:
   ```bash
   # Via UI: Datacenter → Backup
   # Or via CLI
   sudo vzdump --all --storage local --mode snapshot
   ```

3. **Configuration Backup**:
   ```bash
   # This repository serves as config backup
   git status
   git add .
   git commit -m "Backup: $(date)"
   git push
   ```

### Restore from Backup

**ZFS Rollback**:
```bash
sudo zfs rollback tank/media@backup-20260101
```

**Proxmox Restore**:
```bash
sudo qmrestore /path/to/backup.vma.zst 100 --storage local-lvm
```

---

## Performance Investigation

### High Load

1. **Check System Resources**:
   ```bash
   htop
   iostat -x 5
   free -h
   ```

2. **Identify Top Processes**:
   ```bash
   top -o %CPU
   ps aux --sort=-%cpu | head
   ```

3. **Check Disk I/O**:
   ```bash
   sudo zpool iostat -v 5
   sudo iotop
   ```

4. **Network Usage**:
   ```bash
   sudo iftop
   sudo nethogs
   ```

### Slow Service Response

1. **Check Service Logs**:
   ```bash
   sudo journalctl -u <service> -f
   ```

2. **Measure Response Time**:
   ```bash
   curl -w "@curl-format.txt" -o /dev/null -s http://service/
   ```

3. **Database Performance** (if applicable):
   ```bash
   # Check slow queries, connections, etc.
   ```

4. **Review Recent Changes**:
   ```bash
   git log --since="1 day ago"
   ```

---

## System Maintenance

### Update Strategy

The infrastructure has two independent update scopes, each with rolling deployment (one host/node at a time) to maintain service availability:

1. **Base infrastructure** - Proxmox hosts, DNS servers, SMTP relay, Plex LXC (managed by Ansible)
2. **K3s cluster** - k3s nodes, Helm charts, application workloads (managed by Helm/kubectl)

### Quick Reference

| What to update | Command |
|---|---|
| Check for available updates | `task maintenance:check-versions` |
| Update versions in all.yml | `task maintenance:update-all-versions` |
| OS packages only | `task maintenance:update-packages` |
| Base apps (AdGuard, Tailscale, Plex) | `task maintenance:update-applications` |
| Full base update (packages + apps) | `task maintenance:update-full` |
| Full base update (auto-reboot) | `task maintenance:update-full-auto` |
| Plex only | `task maintenance:update-plex` |
| K3s nodes (rolling) | `task maintenance:update-k3s-nodes` |
| Helm charts | `task maintenance:update-helm-charts` |
| K3s workloads (Authentik, downloads, recipes) | `task maintenance:update-k3s-workloads` |
| Full cluster update (all of the above k3s tasks) | `task maintenance:update-cluster` |

### Automated Version Discovery

The version checker (`scripts/check-versions.py`) automatically queries official sources (GitHub releases, Docker Hub, Helm repos) to find available updates for all tracked services.

**Check for available updates**:
```bash
# Check all 24 managed services
task maintenance:check-versions

# Check a specific service
task maintenance:check-versions -- --service gluetun

# Check a category (github, lsio, dockerhub, helm, plex)
task maintenance:check-versions -- --category helm

# JSON output (for scripting)
task maintenance:check-versions-json

# Force fresh lookups (skip 1-hour cache)
task maintenance:check-versions -- --no-cache

# List all tracked services
task maintenance:check-versions -- --list
```

**Update versions in all.yml**:
```bash
# Update a single service
task maintenance:update-version SERVICE=prowlarr

# Update all outdated services at once
task maintenance:update-all-versions
```

After updating versions in `all.yml`, deploy with the appropriate task:
- **Ansible-managed** (AdGuard, Tailscale, Plex): `task maintenance:update-applications`
- **k3s node binary**: `task maintenance:update-k3s-nodes`
- **Helm charts** (MetalLB, Traefik, etc.): `task maintenance:update-helm-charts`
- **K3s workloads** (Authentik, downloads, recipes): `task maintenance:update-k3s-workloads`

**GitHub API rate limits**: Unauthenticated requests are limited to 60/hour. Set `GITHUB_TOKEN` for 5000/hour:
```bash
export GITHUB_TOKEN=$(op read "op://Homelab/GitHub Token/credential")
task maintenance:check-versions
```

Results are cached for 1 hour in `.version-cache/`. Clear with:
```bash
task maintenance:check-versions -- --clear-cache
```

### Recommended Update Workflow

1. **Check for available updates**:
   ```bash
   task maintenance:check-versions
   ```

2. **Update versions in all.yml** (does NOT deploy):
   ```bash
   task maintenance:update-all-versions
   # Review the changes
   git diff ansible/inventories/prod/group_vars/all.yml
   ```

3. **Deploy updates** (choose as appropriate):
   ```bash
   # Base infrastructure (AdGuard, Tailscale, Plex, OS packages)
   task maintenance:update-full

   # K3s cluster (nodes + charts + workloads)
   task maintenance:update-cluster
   ```

4. **Verify everything works**:
   ```bash
   task deploy:verify
   task k3s:status
   ```

5. **Commit**:
   ```bash
   git add -A && git commit -m "Update service versions"
   ```

### Base Infrastructure Update Details

#### What `update-full` does

Full base updates run in this order:

1. **OS Packages** (rolling, one host at a time)
   - Update apt cache
   - Display available updates
   - Upgrade packages (safe upgrade)
   - Reboot if needed (interactive or auto-reboot)
   - Verify SSH service

2. **AdGuard Home** (rolling, both DNS servers)
   - Check current vs target version
   - Temporarily switch dns-01 to use dns-02 for resolution
   - Stop service, backup config, download and install new binary
   - Start service, restore DNS, verify version

3. **adguardhome-sync** (dns-01 only)
   - Check current vs target version
   - Stop timer, install new binary, start timer

4. **Tailscale** (rolling, Proxmox hosts only)
   - Check current vs target version
   - Upgrade apt package to pinned version
   - Restart tailscaled service

5. **Plex Media Server** (plex LXC only)
   - Upgrade to latest via apt (when `plex_version: "latest"`)

6. **Ansible Collections**
   - Update Galaxy collections

#### Version Management

Application versions are centralized in `ansible/inventories/prod/group_vars/all.yml`.

To upgrade an application:
1. Check for updates: `task maintenance:check-versions`
2. Update version number: `task maintenance:update-version SERVICE=<name>` (or edit `all.yml` manually)
3. Deploy: `task maintenance:update-applications` or the appropriate deploy task

#### Update Schedule

**Monthly Updates**:
```bash
# Full update with auto-reboot (minimal interaction)
task maintenance:update-full-auto

# After update: Always verify
task deploy:verify
```

**Security Updates**:
```bash
# For urgent security patches: OS packages only
task maintenance:update-packages
# Add -e auto_reboot=true for auto-reboot:
task maintenance:update-packages -- -e auto_reboot=true
task deploy:verify
```

#### Troubleshooting Base Updates

**Update fails on one host**:
```bash
# Retry specific host
task maintenance:update-full -- --limit=pve-nas-01
```

**AdGuard configuration corrupted**:
```bash
# List backups
ansible dns -i inventories/prod -m shell -a "ls -la /opt/adguardhome/AdGuardHome.yaml.backup-*"

# Restore on specific host
ansible dns-01 -i inventories/prod -m copy -a "src=/opt/adguardhome/AdGuardHome.yaml.backup-TIMESTAMP dest=/opt/adguardhome/AdGuardHome.yaml remote_src=yes"

# Restart service
ansible dns-01 -i inventories/prod -m service -a "name=AdGuardHome state=restarted"
```

**Service not starting after update**:
```bash
# Check service status
ansible <host> -i inventories/prod -m service -a "name=AdGuardHome state=status"

# Check logs
ansible <host> -i inventories/prod -m shell -a "journalctl -u AdGuardHome -n 50"
```

---

## K3s Cluster Maintenance

### Updating K3s Cluster

The k3s cluster has three update layers, each with its own task.

#### 1. Node Updates (k3s binary)

```bash
# Update k3s version in group_vars/all.yml first
task maintenance:update-version SERVICE=k3s

# Rolling update with pod evacuation
task maintenance:update-k3s-nodes

# Verify cluster health
task k3s:status
```

**Process (per node, serial: 1):**
1. Cordons node (prevents new pods)
2. Drains node (evicts existing pods, 30s grace period)
3. Upgrades k3s binary via install script
4. Restarts k3s service
5. Uncordons node (allows scheduling)
6. Waits for node Ready status

**Special considerations:**
- Servers are updated first, then agents
- Agents with persistent storage (Authentik/Mealie PostgreSQL) are drained carefully
- DaemonSets are ignored during drain (expected behavior)
- If drain fails, the node is uncordoned and the upgrade aborts (investigate PDBs or stuck pods)

#### 2. Helm Chart Updates

```bash
# Update chart versions in group_vars/all.yml
task maintenance:update-version SERVICE=traefik

# Update all charts at once
task maintenance:update-helm-charts

# Or update individually
helm upgrade metallb metallb/metallb -n metallb-system --version X.Y.Z --reuse-values
helm upgrade traefik traefik/traefik -n traefik --version X.Y.Z --reuse-values
helm upgrade cert-manager jetstack/cert-manager -n cert-manager --version vX.Y.Z --reuse-values
helm upgrade external-dns external-dns/external-dns -n external-dns --version X.Y.Z --reuse-values
```

#### 3. Workload Image Updates

```bash
# Update container versions in group_vars/all.yml
task maintenance:update-version SERVICE=sonarr

# Update all k3s workloads (Authentik + downloads + recipes)
task maintenance:update-k3s-workloads

# Or update individual namespaces
task k3s:deploy-authentik   # Authentik SSO
task downloads:deploy       # Download clients + media stack
task recipes:deploy         # Recipe management stack
```

#### 4. Complete Cluster Update

```bash
# Update all versions in group_vars/all.yml first
task maintenance:update-all-versions

# Run complete update workflow (all 3 layers in order)
task maintenance:update-cluster
```

This runs:
1. `task maintenance:update-k3s-nodes` (rolling node upgrades)
2. `task maintenance:update-helm-charts` (platform components)
3. `task maintenance:update-k3s-workloads` (Authentik + downloads + recipes)

### Maintenance Windows

**Recommended schedule:**
- Monthly: OS package updates (`task maintenance:update-packages`)
- Quarterly: Full base infrastructure update (`task maintenance:update-full`)
- As needed: k3s cluster updates (`task maintenance:update-cluster`)
- As needed: Individual workload updates

**Downtime expectations:**
- Base infrastructure: 5-10 minutes per host (rolling, minimal DNS impact)
- K3s nodes: 2-5 minutes per node (rolling, workloads migrate)
- Helm charts: 1-2 minutes per chart (rolling updates)
- Workloads: 1-2 minutes per namespace (rolling restart)

### Rollback Procedures

#### Rolling back k3s version

```bash
# Update group_vars to previous version
task maintenance:update-version SERVICE=k3s
# (or manually edit all.yml to set previous version)

# Re-run node update
task maintenance:update-k3s-nodes
```

#### Rolling back Helm chart

```bash
# Rollback using Helm
helm rollback <release-name> -n <namespace>

# Examples
helm rollback metallb -n metallb-system
helm rollback traefik -n traefik
helm rollback cert-manager -n cert-manager
helm rollback external-dns -n external-dns

# Or redeploy specific version
helm upgrade traefik traefik/traefik -n traefik --version <old-version> --reuse-values
```

#### Rolling back workload images

```bash
# Update group_vars to previous versions (edit all.yml manually)

# Redeploy
task k3s:deploy-authentik
task downloads:deploy
task recipes:deploy
```

### Troubleshooting Cluster Updates

#### Node stuck in NotReady

```bash
# Check node status
kubectl get node <node-name> -o yaml
kubectl describe node <node-name>

# Check k3s service
ssh <node-name>
sudo systemctl status k3s  # or k3s-agent
sudo journalctl -u k3s -f  # or k3s-agent

# Restart k3s
sudo systemctl restart k3s  # or k3s-agent
```

#### Pods stuck in Pending/CrashLoopBackOff

```bash
# Check pod status
kubectl get pods -n <namespace>
kubectl describe pod <pod-name> -n <namespace>
kubectl logs <pod-name> -n <namespace>

# Check node resources
kubectl top nodes
kubectl describe node <node-name>

# Restart pod
kubectl delete pod <pod-name> -n <namespace>
```

#### Helm upgrade fails

```bash
# Check release status
helm list -n <namespace>
helm history <release-name> -n <namespace>

# View pending upgrade
kubectl get all -n <namespace>
kubectl get events -n <namespace> --sort-by='.lastTimestamp'

# Rollback
helm rollback <release-name> -n <namespace>
```

#### Node drain hangs

```bash
# Check what pods are blocking
kubectl get pods --all-namespaces --field-selector spec.nodeName=<node-name>

# Force drain if necessary (use carefully)
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --force --grace-period=0

# Or uncordon and try again
kubectl uncordon <node-name>
```

---

## Understanding Skipped Tasks

When running Ansible playbooks, tasks may show as "skipped" for intentional reasons.

### Common Skip Patterns

#### Idempotent Tasks
Tasks skip when already in the desired state:
- Oh My Zsh installation (when already installed)
- ZFS dataset properties (when already configured)
- Service configuration (when no changes needed)

#### Conditional Tasks
Tasks skip based on host role or conditions:
- Postfix virtual aliases (only on SMTP relay)
- Tailscale authentication (when already authenticated)
- Primary vs replica tasks (DNS servers)

#### Check Mode
Some tasks skip in check mode but run in actual deployment:
- ZFS property changes (shell commands don't execute in check mode)
- Service restarts (handlers don't run in check mode)
- User creation (can't verify before creation)

### Skip Counts by Host

| Host | Skipped | Primary Reasons |
|------|---------|-----------------|
| dns-01 | 8 | Replica-specific tasks, already-configured items |
| dns-02 | 18 | Primary-only tasks (acme_certs, adguard_sync) |
| pve-nas-01 | 13 | Check mode, idempotent tasks, no changes needed |
| pve-opt-03 | 9 | NAS-specific tasks, idempotent items |
| smtp-relay | 3 | Minimal role set, most infrastructure tasks excluded |

### Expected Skips

**DNS Servers**:
- dns-01 skips replica configuration tasks
- dns-02 skips all primary tasks (certificate management, sync source)

**NAS Server**:
- ZFS property tasks skip when properties match desired state
- MergerFS remount service skips when already enabled

**All Hosts**:
- Oh My Zsh installation skips when already present
- Postfix virtual aliases skip on null clients
- Tailscale auth skips when already running

### Verification

After deployment, verify expected state rather than focusing on skip counts:

```bash
# Run comprehensive verification
task deploy:verify
```

This checks:
- SSH connectivity
- DNS resolution
- NFS exports
- Service status
- Certificate SSH
- AdGuard API health
- ZFS pool health (tank, ssd, nvme, archive)
- SMART disk health (17 disks: 6 HDD tank + 3 SSD + 4 NVMe + 4 HDD archive)
- Disk space

---

## Proxmox HA Post-Failover Reconciliation

When Proxmox HA migrates a VM/container to a different node (due to node failure or manual migration), ZFS replication must be reconfigured. Replication only works FROM the source node, so after failover the service is running on what was previously a target node.

### Symptoms

- Replication jobs show errors in `pvesr status`
- `task proxmox:ha-status` shows service running on a different node than configured `source_node`
- ZFS recv errors in Proxmox task log

### Detect Failover

1. **Check current service locations:**
   ```bash
   task proxmox:ha-status
   ```

2. **Compare ha-manager status against configured source_node:**
   ```bash
   # Look at the "Node" column in ha-manager status
   # Compare against source_node values in ansible/inventories/prod/group_vars/all.yml

   # Example output showing failover (dns-01 expected on pve-laptop-01, running on pve-opt-01):
   # VMID   Type  State    Node
   # 150    ct    started  pve-opt-01   <-- MISMATCH: source_node is pve-laptop-01
   ```

3. **Check replication status for errors:**
   ```bash
   # Use the HA status task (checks all source nodes)
   task proxmox:ha-status

   # Or SSH to each replication SOURCE node (pvesr only shows local jobs)
   # Source nodes: pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01
   ssh pve-laptop-01 sudo pvesr status
   ssh pve-opt-01 sudo pvesr status
   # ... etc

   # Look for "error" state or failed last_sync timestamps
   ```

### Reconciliation Procedure

After a failover, you have two options:

#### Option A: Update Configuration (Permanent Migration)

Use this when the original node is offline for extended maintenance or has failed permanently.

1. **Edit `/Users/eric/src/weisssrv/ansible/inventories/prod/group_vars/all.yml`:**
   ```yaml
   # Find the storage_replication_jobs section
   # Update source_node for all jobs of the affected VMID

   # Example: dns-01 (VMID 150) failed over from pve-laptop-01 to pve-opt-01
   # BEFORE:
   - id: "150-0"
     source_node: pve-laptop-01  # <-- old source
     target_node: pve-opt-01

   # AFTER:
   - id: "150-0"
     source_node: pve-opt-01     # <-- new source (where service is now running)
     target_node: pve-laptop-01  # <-- swap: old source becomes a target
   ```

2. **Update all 4 jobs for the VMID:**
   - Change `source_node` to the current running node
   - Swap the old source to be a target
   - Ensure no job has source == target

3. **Apply the configuration:**
   ```bash
   task proxmox:ha
   ```

4. **Verify replication is working:**
   ```bash
   task proxmox:ha-status

   # Wait for next scheduled replication (check staggered schedule)
   # dns-01: minutes 0,15,30,45
   # smtp-relay: minutes 3,18,33,48
   # dns-02: minutes 6,21,36,51
   # home-assistant: minutes 9,24,39,54

   # Then verify
   sudo pvesr status
   ```

#### Option B: Migrate Back (Original Node Recovered)

Use this when the original node is back online and you want to restore the original topology.

1. **Verify original node is healthy:**
   ```bash
   sudo pvecm status
   # Ensure the node shows as online
   ```

2. **Manually migrate the service back:**
   ```bash
   # For containers
   sudo pct migrate <vmid> <original_node> --online

   # For VMs
   sudo qm migrate <vmid> <original_node> --online

   # Example: migrate dns-01 back to pve-laptop-01
   sudo pct migrate 150 pve-laptop-01 --online
   ```

3. **Verify replication resumes:**
   ```bash
   task proxmox:ha-status
   sudo pvesr status
   ```

   Since the configuration still points to the original source_node, replication should resume automatically.

### Service-Specific Notes

| Service | VMID | Primary Node | Schedule | Notes |
|---------|------|--------------|----------|-------|
| dns-01 | 150 | pve-laptop-01 | `*/15` (0,15,30,45) | Primary DNS; dns-02 provides redundancy |
| smtp-relay | 151 | pve-opt-01 | `3-59/15` (3,18,33,48) | Single instance; brief outage during failover |
| dns-02 | 160 | pve-opt-03 | `6-59/15` (6,21,36,51) | Secondary DNS; dns-01 provides redundancy |
| home-assistant | 154 | pve-prec-01 | `9-59/15` (9,24,39,54) | HAOS VM; check integrations after failover |

### Replication Job ID Format

Job IDs follow the format `<VMID>-<sequence>`:
- `150-0`, `150-1`, `150-2`, `150-3` - dns-01 to 4 targets
- `151-0`, `151-1`, `151-2`, `151-3` - smtp-relay to 4 targets
- `160-0`, `160-1`, `160-2`, `160-3` - dns-02 to 4 targets
- `154-0`, `154-1`, `154-2`, `154-3` - home-assistant to 4 targets

### Troubleshooting

**Replication job stuck in error state:**
```bash
# Check job details
sudo pvesr status --verbose

# View task log for specific job
sudo pvesr read <vmid>-<seq>

# Force immediate sync attempt (useful for testing)
sudo pvesr run <vmid>-<seq>
```

**Cannot create replication job (source not on this node):**
```bash
# Replication jobs can only be created from the node where the VM/CT disk resides
# SSH to the correct node first, or use the Proxmox web UI
```

**ZFS dataset doesn't exist on target:**
```bash
# The first replication creates a full copy; subsequent are incremental
# If target dataset is corrupted, remove and let replication recreate:
sudo zfs destroy local-ssd/data/images/<vmid>  # ON TARGET NODE ONLY
# Next replication job will create a fresh full copy
```

---

## Emergency Contact / Escalation

For critical issues:

1. Review logs: `sudo journalctl -xe`
2. Check cluster status: `sudo pvecm status`
3. Review firewall: `sudo pve-firewall status`
4. Collect state: `task collect-state`

---

## References

- [Proxmox VE Documentation](https://pve.proxmox.com/pve-docs/)
- [ZFS Administration Guide](https://openzfs.github.io/openzfs-docs/)
- [Ansible Troubleshooting](https://docs.ansible.com/ansible/latest/user_guide/playbooks_startnstep.html)
