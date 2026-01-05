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
   sudo pct create 200 \
     local:vztmpl/debian-12-standard_12.0-1_amd64.tar.zst \
     --hostname new-service \
     --net0 name=eth0,bridge=vmbr0,ip=192.168.0.XXX/24,gw=192.168.0.1 \
     --storage local-lvm \
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

The infrastructure supports three types of updates with rolling deployment (one host at a time) to maintain service availability.

#### Update Commands

**Individual Updates**:
```bash
# Update only OS packages (interactive)
task maintenance:update-packages

# Update only OS packages (auto-reboot)
task maintenance:update-packages-auto

# Update only applications
task maintenance:update-applications
```

**Full Updates (Recommended)**:
```bash
# Full update - packages + applications (interactive)
task maintenance:update-full

# Full update - packages + applications (auto-reboot)
task maintenance:update-full-auto
```

#### Update Workflow

1. **Review current versions**:
   ```bash
   cat ansible/inventories/prod/group_vars/versions.yml
   ```

2. **Check what would change** (optional):
   ```bash
   task deploy:check
   ```

3. **Run full update** (interactive):
   ```bash
   task maintenance:update-full
   ```

4. **Verify everything works**:
   ```bash
   task deploy:verify
   ```

#### Update Phases

Full updates run in this order:

1. **OS Packages** (rolling, one host at a time)
   - Update apt cache
   - Display available updates
   - Upgrade packages
   - Reboot if needed
   - Verify SSH service

2. **AdGuard Home** (rolling, both DNS servers)
   - Check current version
   - Stop service if upgrade needed
   - Backup configuration
   - Download and install new version
   - Start service
   - Wait for service ready
   - Verify version

3. **adguardhome-sync** (dns-01 only)
   - Check current version
   - Download new version if needed
   - Stop timer
   - Install new binary
   - Start timer
   - Verify update

4. **Ansible Collections**
   - Update Galaxy collections
   - Display results

#### Version Management

Application versions are centralized in `ansible/inventories/prod/group_vars/versions.yml`.

To upgrade an application:
1. Update version number in `versions.yml`
2. Run `task maintenance:update-applications` or `task maintenance:update-full`

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
task maintenance:update-packages-auto
task deploy:verify
```

#### Troubleshooting Updates

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
