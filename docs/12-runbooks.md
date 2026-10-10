# Operational Runbooks

This is the file every alert's `runbook_url` points at, so the section headings
below are load-bearing: renaming one breaks the links baked into
`cluster_runbook_base_url`. Two gates hold that —
`scripts/check-runbook-anchors.py` and `scripts/test_runbook_coverage.py`.

`task --list` is the authoritative command reference. A command on this page
that does not exist is a bug, not a missing feature, and
`scripts/test_doc_inventories.py` asserts it.

## Where to look first

| Question | Command |
|---|---|
| Is the platform healthy? | `task flux:status` |
| Is Flux itself healthy? | `task flux:verify` |
| Are the hosts converged? | `task infra:verify` |
| Are the nodes healthy? | `task k3s:status` |
| Is the whole fleet reachable? | `task diagnose:network` |
| What is the current state snapshot? | `task collect-state` |
| What changed recently? | `git log --oneline -20` |
| Why is this service down? | its dashboard, then its logs, then its NetworkPolicy |

If `task` itself fails before running anything, the usual causes are a missing
tool (the precondition message names it), an unauthenticated 1Password CLI, or
the collection not being installed.

## Alerts that link here

An alert's `runbook_url` carries an `#anchor` when this document owns its
remediation, and each of those sections carries an alert index. Alerts that link
here without an anchor land at the top of the page; this table routes them.

| Alerts | Go to |
|---|---|
| `ProxmoxHostDown`, `NodeNotReadyProlonged`, `NodeOutageStormControl`, `NodeStuckCordoned`, `NodeMemoryPressure`, `ProxmoxHostIOPressure`, `ProxmoxHostMemoryPressure`, `CorosyncWedged`, `PmxcfsStale`, `CorosyncHealthCollectorStale` | § A Proxmox host or guest went dark, then `task diagnose:network` |
| `MaintenanceRebootDeferred`, `KuredRebootStuck` | § Maintenance Windows |
| `NodeSystemdServiceFailed`, `NodeSystemdServiceCrashlooping`, `HostFilesystemReadOnly`, `HostClockNotSynchronising` | the named host: `systemctl --failed`, `journalctl -u <unit>`, `timedatectl` |
| `HostExporterDown`, `HostTextfileCollectorScrapeError`, `NodeExporterSystemdCollectorFailed` | the host-side exporter and its textfile collectors — [docs/31](31-observability.md) |
| `DiskUsageWarning`, `DiskUsageCritical`, `InodeUsageWarning`, `InodeUsageCritical`, `PVCUsageWarning`, `PVCUsageCritical` | `df -h` / `df -i` on the named host; for the observability zvols, § Recovering Space on the Prometheus / Loki zvols |
| `SATADriveTempWarning`, `SATADriveTempCritical`, `NVMeDriveTempWarning`, `NVMeDriveTempHigh`, `NVMeDriveTempCritical`, `CPUTempWarning`, `CPUTempCritical`, `HostGpuTempWarning`, `HostGpuTempCritical`, `NICTempWarning` | the Thermals dashboard in Grafana. Sustained drive over-temperature is a bay-airflow problem, not a disk fault. `NVMeDriveTempHigh` is the 75-80C band: expected nightly on the NAS nvme2 chip while vzdump reads the VM disks, a regression on any other chip |
| `FluxReconciliationFailure`, `FluxResourceNotReady` | [docs/29](29-flux-operations.md) |
| `ExternalSecretSyncFailure`, `OnePasswordConnectDown` | [docs/15](15-credential-rotation.md) and [docs/29](29-flux-operations.md) |
| `VPARecommendationCapped` | [docs/33](33-autoscaling.md) |
| `PostgresDown`, `PostgresConnectionsHigh`, `LokiRequestFailures`, `TraefikHighErrorRate`, `TraefikPublicVipMissing`, `TraefikInternalVipMissing` | [docs/31](31-observability.md) § Alert rules |
| `VPNDown`, `VPNExporterDown` | [docs/21](21-download-clients-deployment.md) |

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
   sudo pvecm add 10.0.10.102  # Join cluster
   ```

2. **Add to Ansible Inventory**:
   ```yaml
   # ansible/inventories/prod/hosts.yml
   proxmox:
     hosts:
       pve-new-01:
         ansible_host: 10.0.10.XXX
         ansible_user: eric
         ansible_become: true
   ```

3. **Create Host Variables**:
   ```bash
   # ansible/inventories/prod/host_vars/pve-new-01.yml
   ---
   # Host-specific overrides
   ```

4. **Regenerate the files derived from `hosts.yml`** and commit them with the
   inventory change. `scripts/hosts.env` and the per-host `HostLogShippingStale`
   rules are generated, and `task lint` plus the CI lint stage fail until the
   committed copies match:
   ```bash
   task hosts:sync                     # scripts/hosts.env
   task flux:sync-host-log-staleness   # loki/host-log-staleness.yaml
   ```

5. **Deploy Base Configuration**:
   ```bash
   task infra:base -- --limit pve-new-01
   ```

6. **Declare the firewall metadata in inventory**: add `firewall_ipsets:
   [pve_hosts]` and the host's security groups to the host entry. `cluster.fw`
   and each `host.fw` are generated from inventory, so there is nothing to edit
   on the node ([docs/11](11-firewall.md)).

7. **Deploy Firewall**:
   ```bash
   task infra:deploy -- --tags proxmox_firewall
   ```

8. **Verify**:
   ```bash
   # Check cluster status
   sudo pvecm status

   # Verify firewall
   sudo pve-firewall status

   # Test Ansible
   ansible -i ansible/inventories/prod pve-new-01 -m ping
   ```

---

## Deploying a New LXC Container

Containers are provisioned from inventory by `weisssrv.infra.proxmox_lxc`, which
creates the container, applies the unprivileged idmap, bootstraps the admin user
and its SSH key, and hands off to `proxmox_firewall` for `<vmid>.fw`. Declare the
guest first and let the role build it.

### Prerequisites

- A free VMID and IP address
- The security groups the guest needs ([docs/11](11-firewall.md))
- The template named by `proxmox_lxc_template` in `group_vars/all.yml` (the role
  downloads it when the host's storage does not have it)

### Procedure

1. **Declare the guest in inventory**:
   ```yaml
   # ansible/inventories/prod/hosts.yml
   new_service:
     hosts:
       new-service:
         ansible_host: 10.0.10.XXX
         vmid: 200
         proxmox_host: pve-opt-01       # the node it lives on
         proxmox_resource_pool: infra-core
         proxmox_lxc_cores: 2           # sizing; role defaults otherwise
         proxmox_lxc_memory: 2048
         proxmox_lxc_disk_size: 8G
         firewall_ipsets:
           - core-cluster
         guest_security_groups:
           - sg-vm-admin
           - sg-metrics
   ```

   `proxmox_lxc_gateway`, `dns_servers` and `internal_domain` come from
   `group_vars/all.yml`, and storage is derived from the target host's
   `proxmox_role` unless `proxmox_lxc_storage` overrides it. The admin user's
   key comes from the `SSH_PUBLIC_KEY` env var that the `task` wrappers inject
   via `op run`, which is why the provisioning step below is a task and not a
   bare `ansible-playbook`.

2. **Add a provisioning play** for the new group, mirroring
   `ansible/playbooks/plex.yml` (or `dns.yml`): one play over the group running
   `weisssrv.infra.proxmox_lxc`, delegated to the Proxmox host. Add the
   service's own roles and any `group_vars`/`host_vars` it needs.

3. **Deploy** with the task that owns the playbook the play went into —
   `task infra:deploy -- --limit new-service` for `site.yml`, or the service's
   own task for a dedicated playbook (`task dns:deploy`, `task plex:deploy`):
   ```bash
   task infra:deploy -- --limit new-service
   ```

4. **Verify**:
   ```bash
   ansible -i ansible/inventories/prod new-service -m ping
   ssh pve-opt-01 "sudo pct config 200"
   task infra:verify
   ```

**Recovery / out-of-band.** `pct create`, `pct enter` and a hand-written
`/etc/pve/firewall/200.fw` still work when the Proxmox API path is broken, but
the next role run overwrites the firewall file and the container's
`authorized_keys`. Use them only to get back to a state Ansible can converge
from.

---

## Handling Disk Failure (ZFS)

### Symptoms

- ZFS pool shows DEGRADED status
- Disk errors in `dmesg` or pool status

**Related alerts** (SMART metrics come from the node_exporter_host textfile
collector; all point here):

- **SMARTDeviceUnhealthy** — a drive's overall SMART health assessment
  failed. Treat as a failing disk: identify it (below) and plan replacement.
- **SMARTReallocatedSectorsGrowing** — reallocated sector count is
  increasing. The drive is remapping bad sectors; replace before it degrades
  further.
- **SMARTPendingSectors** — sectors awaiting reallocation (unreadable on
  last access). Scrub the pool to force reads; if the count doesn't drop,
  replace the drive.
- **SMARTOfflineUncorrectable** — sectors the drive could not correct
  offline. Same handling as pending sectors, higher urgency.
- **SMARTMediaErrors** — NVMe media/data-integrity errors. Replace the
  device.
- **SMARTCollectorStale** — meta-alert: the SMART textfile collector is
  stale or missing, so the alerts above are running on old (or no) data.
  Check the collector timer/service on the affected host before trusting
  disk state.

**Alert index** — the pool-health alerts that anchor here (inputs from the
`zpool-status-collector` textfile collector):

| Alert | Means | First move |
|---|---|---|
| `ZFSPoolNotOnline` | the pool reports DEGRADED or worse | `zpool status -v <pool>`, then the replacement procedure below |
| `ZFSPoolDataErrors` | `zpool status -v` lists unrecoverable data; a single-vdev pool cannot self-heal it | restore the listed files from the archive tier ([docs/42](42-offsite-backup.md)) |
| `ZFSPoolDeviceErrors` | read/write/cksum errors while the pool may still read ONLINE — the silent-corruption signature | `zpool status -v <pool>`, fix the cause, then `zpool clear` |
| `ZFSPoolScrubStale` | no completed scrub in 35 days, so latent corruption surfaces only when something reads it | `zpool scrub <pool>` and check the scrub timer |
| `ZFSPoolSpaceWarning` / `ZFSPoolSpaceCritical` | the pool is above 80% / 90% capacity | `zfs list -o name,used,avail,usedbysnapshots`; prune snapshots |
| `ZFSPoolCollectorStale` | the collector is stale or missing, so every alert above is serving old or no data | `systemctl status zpool-status-collector.timer` on the named host |

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
   sudo qm stop <vmid>   # Stop VMs using the disk
   sudo pct stop <ctid>  # Stop containers using the disk

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

**Related alert**: **DDNSStale** — the `cloudflare-ddns` CronJob (every 5
minutes) has had no successful run in over an hour, so the public A records go
stale at the next WAN IP change. Check
`kubectl -n cloudflare-ddns get cronjob,job`.

Also anchored here, all three about external-dns running `policy: sync` against
its TXT registry, where a source that stops yielding endpoints becomes a mass
deletion:

- **ExternalDNSNoSourceEndpoints** — 0 endpoints across every source, or the
  metric absent.
- **ExternalDNSRecordsDropped** — the registry lost more than 20% of the records
  it held over the last 6h.
- **ExternalDNSSyncStale** — no successful sync in the freshness window.

Start with `kubectl -n external-dns logs deploy/external-dns` and the annotation
prefix pin ([docs/08](08-dns.md) § Annotation prefix pin), which is the way this
has actually broken.

### Internal Records (*.esweiss.com)

Managed via AdGuard Home rewrites.

**Via Ansible**:

1. **Edit DNS Variables**:
   ```yaml
   # ansible/inventories/prod/group_vars/dns.yml
   adguard_home_rewrites:
     - domain: "new-service.{{ internal_domain }}"
       answer: "10.0.10.XXX"
   ```

2. **Deploy**:
   ```bash
   task infra:deploy -- --tags adguard_home
   # Or the whole DNS stack: task dns:deploy
   ```

**Via AdGuard UI** (temporary changes only):

1. Access https://dns-01.esweiss.com
2. Navigate to Filters → DNS rewrites
3. Add new entry
4. Changes sync automatically to dns-02 via adguardhome-sync

**Warning**: UI-added rewrites are deleted on the next Ansible deploy — the
adguard_home role prunes any rewrite not in the codified `adguard_home_rewrites`
list. Codify in `group_vars/dns.yml` (Option 1) for anything permanent.

### External Records (*.ericsweiss.com)

Managed via Terraform + Cloudflare.

Most service records are owned by external-dns from the k3s IngressRoutes.
`dns.tf` holds only what external-dns cannot express (apex, DDNS-tracked A
records, CAA, SPF/DMARC, nested wildcards). This layer is a caller of the
library `cloudflare-zone` module, so a record is a map **entry**, not a resource:

1. **Edit Terraform**:
   ```hcl
   # terraform/cloudflare/dns.tf — inside local.dns_records
   new-service = {
     name      = "new-service"
     type      = "A"
     content   = "10.0.10.XXX" # or the public IP
     ttl       = 3600
     proxied   = false
     comment   = "Managed by Terraform"
     protected = true # every record here carries it — see the file header
   }
   ```
   No `zone_id`: the module supplies it. The map key is the record's state
   address, and `protected` selects the module lifecycle class, so it appears as
   `module.zone.cloudflare_record.protected["new-service"]` in the plan.

2. **Plan and Apply**:
   ```bash
   task terraform:cloudflare-plan
   task terraform:cloudflare-apply
   ```

---

## Certificate Renewal Issues

### Symptom

Certificate expired or not renewing automatically.

**Related alerts** (see docs/31-observability.md):
- **CertRenewalFailed** — the acme.sh renewal/distribution script exited non-zero.
- **CertExpiringSoon** — the host-distributed `*.esweiss.com` cert is within 14
  days of its real `notAfter` (so renewal/distribution has actually stopped
  working), or the metric is missing. It fires off
  `cert_local_expiry_timestamp_seconds`, which the cert-reload script emits from
  the live cert.
- **CertRenewalFailedProlonged** — renewal/distribution on the named host has
  failed for 3 days straight. Same procedure, higher urgency.
- **CertRenewalStale** — no successful renewal or distribution on the named
  host for 75 days, past the ~60-day acme.sh cycle. This is the arm for a
  renewal that stopped *running*: a dead acme.sh cron never fails, so
  **CertRenewalFailed** stays silent while the cert ages out. Check `crontab -l`
  on the host for the `acme.sh --cron` entry.
- **CertDistributionTargetFailed** — acme.sh renewed the cert but could not
  install it on one target host, named in the alert. The other targets are
  fine, so that host keeps serving its existing cert until it expires. Start
  with SSH reachability to the named host, then `journalctl -t
  homelab-cert-reload` on dns-01.
- **CertExpiringSoonCritical** — the acme.sh-distributed wildcard on the named
  host expires in under 3 days. NFS-over-TLS (the k3s PV mounts), AdGuard TLS
  and the Loki ingress all break at expiry.
- **CertExpiringWarning** / **CertExpiringCritical** — a cert-manager
  `Certificate` in the cluster is within 14 / 3 days of expiry. Work it with
  `kubectl get certificate -A` and the cert-manager logs, not acme.sh.
- **CertificateNotReady** — a cert-manager `Certificate` has been not-Ready for
  an hour. This is the only arm that covers a `Certificate` that never issued at
  all: the expiry alerts read a series that appears only after a first
  successful issuance. Work it with `kubectl describe certificate -n <ns> <name>`
  and the matching CertificateRequest/Order/Challenge.
- **BlackboxCertExpiringSoon** — the Cloudflare **edge** cert for the probed
  host is within 14 days. Neither acme.sh nor cert-manager manages it;
  Cloudflare normally renews it on its own.

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
   sudo /usr/local/sbin/homelab-cert-reload.sh
   ```

6. **Restart Services**:
   ```bash
   # On dns-01, dns-02 (systemd unit name is case-sensitive: AdGuardHome)
   sudo systemctl restart AdGuardHome

   # On smtp-relay
   sudo systemctl restart postfix
   ```

---

## Network Connectivity Issues

**Related alerts**: **EndpointDown** (warning, any blackbox probe failing
10m) and **EndpointDownCritical** (critical — the load-bearing three:
`auth.esweiss.com` (SSO front door), `git.esweiss.com` (GitOps source),
`home.esweiss.com`; pages email, and inhibits the matching EndpointDown).
Work the checklist below against the probed URL in the alert's `instance`
label.

Also anchored here:

- **DNSResolutionDown** — every probed AdGuard resolver (10.0.10.150 and
  10.0.10.160) has failed for 5 minutes, so resolution is down cluster-wide.
  Start with § A Proxmox host or guest went dark for the two containers.
- **DNSResolverProbeMissing** — one resolver has no `probe_success` series, so
  the aggregate above no longer covers it. Restore the target in
  `kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml`.
- **BlackboxExporterDown** — all endpoint monitoring is blind.
- **AdGuardSyncFailed** / **AdGuardSyncStale** — `adguardhome-sync` on dns-01
  is failing, or has not succeeded in 30 minutes, so dns-02's config drifts.
  Read `journalctl -u adguardhome-sync` on dns-01.
- **ExternalIngressDown** — the critical tier for three or more public hostnames
  unreachable at once, distinct from **EndpointDownCritical**, which covers
  three internal names. Three at once points at the ingress path or the WAN
  address, not at any one app.
- **KeyEndpointProbeMissing** — fewer than three `probe_success` series match
  EndpointDownCritical's regex, so one of auth/git/home stopped being probed.
  Restore the target in
  `kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml`.
- **InternetProbeMissing** — the 1.1.1.1 WAN witness has no `probe_success`
  series, so ExternalIngressDown can no longer tell an upstream outage from a
  public-ingress fault. Restore the target in the same file.

### After an addressing change

Old addresses survive in stored state that nothing reconciles. Sweep 1Password
item URLs, kubeconfigs, and any daemon still running with the old address in its
config, then walk the holders below — each one stays wrong silently.

- **Home Assistant trusted proxies** — `/config/.storage/http` on the HAOS
  guest, not YAML, and it regenerates on update, so a hand-edit can come back
  with the old address
  ([docs/24](24-home-assistant-deployment.md) § Step 3: Configure HTTP Settings).
- **Plex LAN Networks** — `Settings > Network > LAN Networks`, stored as
  `LanNetworksBandwidth` in the server's `Preferences.xml`. A stale CIDR
  classifies every current-VLAN client as *remote*
  ([docs/20](20-plex-deployment.md) § LAN Networks (segmented VLANs)):

  ```bash
  ssh eric@10.0.10.152 \
    "grep -o 'LanNetworksBandwidth=\"[^\"]*\"' \
     '/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml'"
  ```

- **UniFi device Configure-IP** — each device's `config_network` keeps whatever
  static address it was adopted with, inert while it is DHCP but stranding the
  device on the retired subnet at the next static flip. The switch and AP still
  carry theirs ([docs/46](46-unifi-network.md) § Client housekeeping).
- **`known_hosts`** — an entry keyed by the old address keeps answering for a
  host that has moved, and the new address prompts as unknown:

  ```bash
  ssh-keygen -F 10.0.10.153            # the new address: expect a hit
  ssh-keygen -R 192.168.0.153          # drop the retired one
  ```

### Cannot Reach Service

1. **Verify Service Running**:
   ```bash
   sudo systemctl status <service>
   sudo ss -tlnp | grep <port>
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
   curl -v http://10.0.10.XXX:port

   # From Tailscale
   curl -v http://10.0.10.XXX:port --interface tailscale0
   ```

4. **Check DNS Resolution**:
   ```bash
   dig @10.0.10.150 service.esweiss.com
   ```

5. **Review Cluster Firewall**:
   ```bash
   sudo cat /etc/pve/firewall/cluster.fw
   ```

### Sweep all hosts at once

```bash
task diagnose:network        # cluster/HA/ARP/bond/corosync/kube-vip/MetalLB, every host
```

One SSH pass over all Proxmox hosts and k3s servers, each call wall-clock
bounded so an unreachable host does not stall the sweep. Start here when the
fault is not obviously scoped to a single service — the output names which of
the two shapes below you have.

### A Proxmox host or guest went dark

Alerts: ProxmoxHostDown, NodeNotReadyProlonged, NodeOutageStormControl,
NodeStuckCordoned, NodeMemoryPressure, ProxmoxHostIOPressure,
ProxmoxHostMemoryPressure, CorosyncWedged, PmxcfsStale,
CorosyncHealthCollectorStale.

Two host-level network faults have their own runbook in
[34-bond-mac-flapping.md](34-bond-mac-flapping.md) — check which shape you have
before chasing the switch:

- **A single guest** on a bonded host (`.104` / `.105` / `.106`) black-holed
  while the host and its co-resident guests stay reachable → the
  `all_slaves_active` bond bug.
- **A whole `e1000e` host** (.104 / .105 / .106 / .107) dropped out of the
  cluster and needed a power-cycle, with `e1000e 0000:00:19.0 nic0: Detected
  Hardware Unit Hang` in the journal (`00:1f.6` on .107, or in Loki, which
  survives the power-cycle) → the e1000e TX hang; the fix is `tso/gso/gro off`
  on `nic0`, codified once in `group_vars/e1000e_hosts.yml` (a child group of
  `proxmox`: pve-opt-01/02/03 + pve-prec-01).
- **A whole host dark with a clean kernel log** — no `Detected Hardware Unit
  Hang` line, the `tso/gso/gro off` mitigation verifiably applied → a different
  fault. Check MCE/EDAC counters, the BIOS event log, and the UniFi switch-port
  log for that minute ([docs/34](34-bond-mac-flapping.md) § Host-dark events
  without the hang signature).

**Unattributed event**: pve-opt-02 hard froze on 2026-09-09 and stayed down
until 2026-09-12, with no `e1000e` hang evidence. It came back on a power-cycle
of its smart outlet and has not repeated.

**Power-cycling a frozen host**: where the host is fed from a Kasa smart outlet
on the IoT VLAN, a host that answers neither SSH nor the Proxmox web UI can be
power-cycled from the Kasa app or through Home Assistant, which holds the same
plugs. Confirm the host is genuinely dark first — power-cycling a live host is
an unclean shutdown of every guest on it. `ProxmoxHostDown` is the alert: a
critical tier that fires after 5 minutes of `pve_up == 0`.

### A MetalLB VIP receives nothing while the service looks healthy

A frame destined for a MetalLB VIP (not the node's own address) is FORWARDED to
the guest and filtered by the per-VM **guest** firewall, after the host bridge
(`fwpr`/`fwln`). It never reaches `PVEFW-HOST-IN`, so a `cluster.fw [RULES]`
entry compiles into the wrong chain and does nothing.

The receiver looks healthy throughout: the pod's listener is fine and in-pod
injection works, so the only symptom is that nothing arrives.

Fix: a per-guest security group in `group_vars/all.yml`, for example
`sg-syslog-vip` with `IN ACCEPT -source <sender> -dest <vip> -p udp -dport
<port>`, assigned in `hosts.yml` to **every** node the VIP can be announced
from. A pod scheduled to a class of nodes but not pinned lets MetalLB announce
from any of them. See [docs/11](11-firewall.md).

**Related alert**: **MetalLBSpeakerNotScheduled** (critical) — the
metallb-speaker DaemonSet wants zero pods, or has gone from kube-state-metrics.
Its `nodeSelector` is `esweiss.com/ingress`, so dropping or renaming that label
leaves `.99`, `.100`, `.101` and `.162` assigned and unannounced. Check
`kubectl -n metallb-system get ds metallb-speaker` and
`kubectl get nodes -l esweiss.com/ingress=true`, then re-run the k3s node-label
play. The label contract is [docs/25](25-multi-node-expansion.md) § Node Labels and Taints.

---

## NFS Server Recovery (pve-nas-01)

**Related alert**: **NFSServerDown** (critical) — `node_nfsd_server_threads`
on 10.0.10.102 is 0 or absent for 5 minutes. Zero kernel nfsd threads
means the server is not serving; metric absence means the NAS
node-exporter-host itself is dark. Either way, every NFS-backed consumer
(k3s PVs, Grafana storage, the HAOS media mount, the tank-proxmox backup
target) is stalled.

### Procedure

1. **Check the service**:
   ```bash
   ssh pve-nas-01
   sudo systemctl status nfs-server
   cat /proc/fs/nfsd/threads   # 0 = not serving
   ```

2. **Recover from a stop-hang** (a documented failure mode after host
   reboots — nfsd can hang in `deactivating` and leave the unit failed):
   ```bash
   sudo systemctl reset-failed nfs-server
   sudo systemctl start nfs-server
   sudo exportfs -v            # confirm exports are back
   ```

3. **Clean up stale client handles**: pods with established mounts may keep
   getting `Stale file handle` after the server recovers. Delete the
   affected pods so they remount (use `kubectl delete pod`, not `rollout
   restart` — Flux-managed workloads drift-revert a rollout-restart
   annotation):
   ```bash
   kubectl get pods -A -o wide | grep <affected-node-or-app>
   kubectl delete pod -n <namespace> <pod>
   ```

### A deploy that restarts nfsd

A `weisssrv.infra` bump that changes the rendered
`/etc/systemd/system/nfs-server.service.d/zfs-encrypted.conf` notifies the
ordering handler, so `task infra:deploy` restarts nfsd on pve-nas-01. That holds
even when only the drop-in's comment moved. Schedule such a bump as a NAS
window rather than folding it into a routine run: established k3s NFS mounts go
stale and their pods need step 3, and a stop-hang needs step 2.

---

## SMTP Relay (Postfix) Alerts

All alert email egress (Alertmanager email, host cron, backup notifications)
flows through the single relay on smtp-relay (10.0.10.151). Metrics come
from the postfix-queue textfile collector (smtp_relay role) scraped from
`10.0.10.151:9101`. Config reference: [10-mail.md](10-mail.md).

- **PostfixDown** (critical) — `postfix_up == 0` for 10m: the relay's
  postfix service is not active. All alert email egress is down until it
  recovers.
- **PostfixQueueBacklog** (warning) — more than 5 messages queued for 30m.
  The Gmail hop is likely wedged (expired app password, rate-limit); the
  LXC and postfix look healthy while mail silently queues.
- **PostfixQueueCollectorStale** (warning) — the queue collector is stale
  (>15m) or absent, so the two alerts above are running on stale/no inputs.

### Procedure

1. **Service and queue state**:
   ```bash
   ssh eric@10.0.10.151
   sudo systemctl status postfix
   sudo postqueue -p            # inspect the deferred queue
   sudo tail -50 /var/log/mail.log
   ```

2. **Wedged Gmail hop**: `SASL authentication failed` in mail.log usually
   means the Gmail app password expired — rotate per
   [15-credential-rotation.md](15-credential-rotation.md) (SMTP Passwords),
   then flush:
   ```bash
   sudo postqueue -f
   ```

3. **Collector stale**:
   ```bash
   sudo systemctl status postfix-queue-collector.service
   sudo journalctl -u postfix-queue-collector.service -n 50
   systemctl list-timers | grep postfix-queue
   ```

4. **While smtp-relay is down**, the email leg of critical alerts is blind —
   the Discord webhook is the only delivery path. Fix the relay promptly and
   watch Discord in the meantime.

---

## Ansible Deployment Failures

### Failed Task

1. **Review Error Output**:
   - Note the failed task name
   - Check error message

2. **Run in Verbose Mode**:
   ```bash
   task infra:deploy -- -vvv --limit failed-host
   ```

3. **Test Connectivity**:
   ```bash
   ansible -i ansible/inventories/prod failed-host -m ping
   ansible -i ansible/inventories/prod failed-host -m setup
   ```

4. **Check Logs on Target**:
   ```bash
   ssh eric@failed-host
   sudo journalctl -xe
   ```

5. **Run Specific Tags**:
   ```bash
   task infra:deploy -- --tags <role-tag> --limit <host>
   ```

### Deploy reachability gate

The multi-host plays in `base.yml` and `site.yml` carry `ignore_unreachable:
true`, so one down box cannot red the whole fleet run. ansible-core does not
record such a host as dark: with `ignore_unreachable` set it increments `ok` and
`ignored`, never adds the host to `_unreachable_hosts`, and the recap prints
`unreachable=0` with `ansible-playbook` exiting 0. A deploy that configured
**nothing** on a host would otherwise report success.

- `_reachability-probe.yml` records the misses in the in-memory `deploy_skipped`
  group. Deploy plays target `<group>:!deploy_skipped`, so a host known absent is
  not dialled again in every later play and cannot hard-fail on the first task
  templating a fact it never gathered.
- `_reachability-gate.yml` carries the probe's host pattern, not
  `hosts: localhost`. `--limit` applies to every play's host list, implicit
  localhost included, so a localhost gate matched zero hosts and never ran under
  exactly the three scoped CI invocations it exists to protect (`site.yml
  --limit proxmox` / `--limit mail` / `--limit dns`). `tags: always` cannot
  rescue a play with no hosts.
- The ledger is a t0 snapshot and this fleet loses hosts mid-run (opt-node
  e1000e TX hangs, [docs/34](34-bond-mac-flapping.md)), so the gate re-probes
  whatever is not already on it. `deploy_skipped` converged nothing;
  `deploy_lost` converged part of the run and is the more dangerous state to
  report green.
- A host knowingly out for hardware work is declared in
  `deploy_expected_absent_hosts` (`group_vars/all.yml`), which downgrades it to
  a warning. The concession is then recorded in git and expires with a commit.

The plays outside that ledger report their misses differently. Host metrics and
host log shipping also target the application and k3s guests, so a skip there
reaches neither the probe nor the gate; each play names what it missed in a
`post_tasks` step instead — `Host metrics NOT converged on: ...` and
`Journald shipping NOT converged on: ...`. Read those lines before treating a
green run as converged, then re-run with `task infra:deploy -- --limit <host>`
once the host is back.

---

## Backup and Recovery

**Related alerts** (fed by a vzdump hookscript deployed to every Proxmox host
by `node_exporter_host` — the cluster-wide job runs on each node for its local
guests — that writes `vzdump_backup_last_run_success` /
`_last_success_timestamp_seconds` to the node_exporter textfile dir; the nightly
job itself is managed by the `proxmox_backup` role):

- **VzdumpBackupFailed** — the nightly vzdump job (guest images →
  tank-proxmox) did not complete successfully. Check the Proxmox task log
  (`Datacenter → Tasks` or `journalctl -u pvescheduler` on pve-nas-01) and
  the target storage's health/space.
- **VzdumpBackupStale** — no successful vzdump in over 36 hours (one nightly
  cycle plus a night deferred by planned host maintenance) on the named node,
  or the metric is absent on every Proxmox
  host (hookscript not deployed anywhere). The metric is per-node, so a single
  node_exporter being unreachable is covered by the node-down alerts, not this
  one. Guest VM/LXC images are the DR path for the compute-node guests — treat
  staleness as a real gap, not noise.

**Alert index** — the other alerts that anchor here. The two families with a
runbook of their own (archive replication, restic offsite) follow below.

| Alert | Means | First move |
|---|---|---|
| `VzdumpBackupStaleCritical` | no successful vzdump on the named node in over 3 days | as `VzdumpBackupStale` above, but treat it as a real DR gap |
| `ArchiveBackupFailedProlonged` | archive replication has failed on every attempt for 24h | `journalctl -u archive-backup.service` on pve-nas-01 |
| `ArchiveBackupStaleCritical` | no successful archive replication in over 4 days — the offsite chain ages with it | same journal, plus `zpool status archive` |
| `ArchiveBackupPruneBlocked` | snapshot retention on `archive` has failed for 2 days while replication kept succeeding, so the pool only fills | find the failing `zfs destroy` in the same journal, then re-run `archive-backupctl` |
| `PveClusterBackupFailed` / `PveClusterBackupStale` | the nightly `/etc/pve` tar (cluster CA, corosync config, guest definitions) failed, or none in 36h | `systemctl status pve-cluster-backup.timer` on pve-nas-01 — [docs/17](17-disaster-recovery.md) restores from this first |
| `GitLabBackupSecretsMissing` | `gitlab-secrets.json` is missing or 0 bytes in the landing zone, so the tarball alone cannot restore | check the `cp` stage of `gitlab-backup-run.sh` ([docs/27](27-gitlab-deployment.md)) |
| `ImmichBackupFailed` / `ImmichBackupStale` | the nightly Immich `pg_dumpall` failed, or none in 48h | [docs/36](36-immich.md) § Backups |
| `AuthentikBackupStale` | the `authentik-pg-dump` CronJob has not succeeded in 26h | `kubectl -n authentik get cronjob,job` |
| `MealieBackupStale` | the `mealie-pg-dump` CronJob has not succeeded in 26h | `kubectl -n recipes get cronjob,job` |
| `BackupArtifactStaleCritical` | the newest dump under `tank/backups/apps/<app>` is over 4 days old | work the named app's own dump job |
| `BackupArtifactNeverLanded` | no dump has ever landed for an app: the job never ran, or it writes outside the collector's glob | that app's dump job, then its `nas_storage_backup_artifact_apps` glob |
| `BackupArtifactZeroBytes` | the newest artefact is 0 bytes with a recent mtime — the dump ran and wrote nothing | re-run that app's dump by hand and read stderr |
| `BackupArtifactCollectorStale` | the mtime collector is stale or absent, so the two alerts above run on no data | `systemctl status backup-artifact-mtime-collector.timer` on pve-nas-01 |
| `LocalPathPVExists` | a claim fell through to the `local-path` default class, which lands on the stateless k3s bootdisk and is in no backup | pin its `storageClassName` and rebind ([docs/29](29-flux-operations.md)) |
| `VzdumpBackupNoGuests` | a vzdump job finished successfully without starting a single guest backup, so its success metric stays green while nothing lands | check the guest selection on the Proxmox backup schedule, and that the named host still owns guests |

#### ArchiveBackupFailed / ArchiveBackupStale

Cold-tier replication to the `archive` pool via `archive-backupctl`
(`/usr/local/sbin/archive-backupctl run`, `nas_storage` role), fired by
`archive-backup.timer` (OnCalendar in
weisssrv-lib `ansible_collections/weisssrv/infra/roles/nas_storage/templates/archive-backup.timer.j2` is the source of
truth — currently 06:30, after the throttled cluster-wide vzdump finishes). It
emits `archive_backup_last_run_success` / freshness metrics; `ArchiveBackupStale`
additionally guards ~2-day staleness.

- **ArchiveBackupFailed** — the last `archive-backupctl run` exited non-zero
  from a genuine `zfs send`/`receive` error, so a dataset's cold copy may be at
  risk. (A vzdump-quiesce overrun is NOT this alert: it is a per-dataset
  DEFERRAL, exit 0, surfaced by the two alerts below.) Read
  `journalctl -u archive-backup.service` on pve-nas-01 for the failing
  dataset's send/receive error, fix the cause (pool health, space, resume
  token), then re-run with `sudo /usr/local/sbin/archive-backupctl run`.
  Restore / replication design: docs/06 and docs/17.
- **ArchiveBackupStale** — no fully successful run within the freshness
  window. Confirm the timer is enabled (`systemctl status archive-backup.timer`)
  and that the `archive` pool is imported and healthy (`zpool status archive`).
- **ArchiveBackupDatasetStale / ArchiveBackupChronicallyDeferred** — one
  dataset's copy is aging (>2 days) or was deferred 3+ consecutive runs while
  the rest of the run succeeds. Almost always `tank/proxmox`: the 03:30 vzdump
  is overrunning the 06:30 archive window, so the quiesce guard defers it every
  night. Check today's vzdump completion (`ls -lt /mnt/tank/proxmox/dump |
  head`) against the timer; either re-run `archive-backupctl run` once vzdump
  is idle (a success clears both alerts), or if the overrun is the new normal,
  move `archive-backup.timer.j2` later / revisit the vzdump `bwlimit` in
  host_vars.

#### ResticOffsite* / BackupArtifactStale

Nightly offsite backup to Backblaze B2 via `restic-offsitectl run`
(`restic_offsite` role), on its own 07:15 timer and additionally chained
`OnSuccess=` after `archive-backup.service` (opt-in, via pve-nas-01 host_vars).
Full architecture + restore paths: **docs/42**. Operator
commands (source the env first: `set -a; . /etc/restic-offsite/env; set +a`):
`restic-offsitectl status|snapshots|verify|restore <name>|prune`.

The run emits one metric per stage rather than a single pass/fail, so upload
health and retention health alert independently — a blocked prune no longer
reads as a failed backup:

| metric | alert(s) | means |
|---|---|---|
| `restic_offsite_last_backup_success` | ResticOffsiteFailed (1h, warning) → ResticOffsiteFailedProlonged (24h, critical) | the upload to B2 |
| `restic_offsite_last_prune_success` | ResticOffsitePruneFailed (24h, warning) | the forget/prune stage |
| `restic_offsite_retention_blocked` | ResticOffsitePruneBlocked (48h, warning) | the forget-ceiling guard refused |
| `restic_offsite_repo_size_bytes` | ResticOffsiteRepoShrank (critical) | over-broad forget: >20% shrink in 2 days |
| `restic_offsite_last_success_timestamp_seconds` | ResticOffsiteStale (50h, warning) → ResticOffsiteStaleCritical (4d, critical) | freshness of the last good run |
| `restic_offsite_last_verify_success` / `_timestamp_seconds` | ResticOffsiteVerifyFailed (critical) / ResticOffsiteVerifyStale (8d, warning) → …StaleCritical (14d, critical) | the weekly `restic check` |

- **ResticOffsiteFailed** — the upload stage failed. This ALSO fires on a
  **freshness-guard abort** (a source's newest `archsync-*` snapshot older than
  `restic_offsite_freshness_max_age_h`, 26h — the guard refuses to upload a
  stale tree; look for `stale-source` in the log). Inspect
  `journalctl -u restic-offsite.service` on pve-nas-01. If the archive run
  itself is deferring `tank/proxmox`, fix that first (the ArchiveBackup runbook
  above); B2 rides a good archive run. Re-run: `sudo restic-offsitectl run`.
  Uploads that keep failing for a full day escalate to
  **ResticOffsiteFailedProlonged** (critical, emails).
- **ResticOffsitePruneFailed / ResticOffsitePruneBlocked** — uploads are fine
  but retention is not. `PruneFailed` is usually a stale repository lock from an
  interrupted prune (`restic-offsitectl status` shows it). `PruneBlocked` is the
  forget-ceiling guard: the delete set exceeded
  `restic_offsite_forget_max_remove` and the run refused rather than expiring a
  large batch unattended. The guard is self-latching — the delete set only grows
  — so confirm the set is legitimate (`restic-offsitectl snapshots`) and then
  run `sudo restic-offsitectl prune --max-remove <n>` once.
- **ResticOffsiteRepoShrank** — treat as a possible over-broad forget. B2's
  hide lifecycle keeps deleted objects for 30 days; stop the next prune and
  recover inside that window (docs/42).
- **ResticOffsiteStale** — no successful offsite run within ~50h (tolerates one
  deferred-archive night). Confirm the timer + the OnSuccess chain, then run once.
- **ResticOffsiteVerifyFailed / ResticOffsiteVerifyStale** — `Failed` means the
  weekly `restic check` found repository damage: stop writing and work docs/42's
  recovery path. `Stale` only means the check has not run (commonly blocked by
  the same lock as `PruneFailed`); clear the blocker and
  `sudo restic-offsitectl verify`.
- **BackupArtifactStale** (`{app=...}`) — the NAS-side mtime collector sees no
  fresh file under `tank/backups/apps/<app>`, i.e. a relocated dump did not LAND
  (broken NFS mount / wrong path) even if the app's own wrapper metric is green.
  Check the app's backup mount + dump job. This is the independent
  "landed-offsite-eligible" signal; the per-app `*BackupStale` (VM/k8s wrapper)
  is the "the dump ran" signal.

First-run supervision, the restricted-B2-key swap, and the B2 spend check are the
post-merge checklist in docs/42.

#### GitLabBackupFailed / GitLabBackupStale / GitLabBackupStaleCritical

Nightly GitLab backup via `/usr/local/sbin/gitlab-backup-run.sh` (`gitlab` role,
`gitlab-backup.timer` at 02:00 — a systemd timer, not cron), which emits
`gitlab_backup_last_run_success` / `..._last_run_duration_seconds` /
`..._last_size_bytes` textfile metrics.

- **GitLabBackupFailed** — the last backup run exited non-zero. Inspect the run
  (`ssh gitlab "journalctl -t gitlab-backup-run"`) and re-run with
  `ssh gitlab "sudo /usr/local/sbin/gitlab-backup-run.sh"`.
- **GitLabBackupStale / …StaleCritical** — no successful backup within the
  warning / critical freshness windows. Full backup/restore procedure: docs/27.
- **BackupArtifactEmpty** (`metric=gitlab_backup_last_size_bytes`) — the run
  reported SUCCESS but the newest artefact is 0 bytes, i.e. the wrapper found no
  file to size. The alert matches `.+_backup_last_size_bytes` across every
  producer (gitlab, immich, nextcloud, pve-cluster), and all of them size the
  newest EXISTING artefact, so 0 always means "none in the landing zone" — a
  failed run with good artefacts still there reports their size and fires only
  `<app>BackupFailed`. Almost always a landing-path change that never reached
  the application. Compare the two paths, which is what actually bit here:
  `ssh gitlab "sudo grep backup_path /etc/gitlab/gitlab.rb"` versus
  `ssh gitlab "sudo grep -A1 '^  backup:' /var/opt/gitlab/gitlab-rails/etc/gitlab.yml"`.
  If they disagree, `gitlab.rb` was never applied — run `sudo gitlab-ctl
  reconfigure` (a re-run of the `gitlab` role now detects and repairs this by
  itself). Then confirm the next 02:00 run lands a `*_gitlab_backup.tar` in
  `/mnt/backups-offsite`, not just the `gitlab.rb`/`gitlab-secrets.json` copies.

#### EtcdSnapshotStale

The off-node etcd snapshot copy (k3s servers, opt-in) copies each server's local
etcd snapshots to `pve-nas-01:/export/k3s-etcd` over TLS via
`k3s-etcd-snapshot-copy.timer` (hourly) and emits
`etcd_snapshot_last_copy_timestamp_seconds`.

- **EtcdSnapshotStale** — no fresh off-node copy. Check
  `systemctl status k3s-etcd-snapshot-copy.timer` and the TLS NFS mount on the
  server node. etcd restore procedure: docs/17.

#### MediaMoverFailed / MediaMoverStale

`media-mover.service` (`nas_storage` role) tiers aged files from the nvme hot tier
to `tank/media` (see docs/07).

- **MediaMoverFailed** — a run exited non-zero. The common cause is
  `SRC missing: /mnt/nvme/media/library` (the nvme pool/dataset not mounted), which
  hard-fails by design so an unmounted source pool surfaces rather than silently
  no-oping. Verify the nvme mounts (`zfs mount | grep nvme`) before re-running.
- **MediaMoverStale** — no successful move within the freshness window; the
  `absent()` arm also covers the metric never being written.

#### AuthentikBackupStale / MealieBackupStale

Both are logical PostgreSQL dumps from a CronJob in the app's own namespace,
landing on the NFS backup PV. The window is staggered so they do not collide
with the block-level jobs: the Mealie dump runs at 02:45 local — after smartd's
02:00 short tests, before the 03:30-05:45 vzdump window, the 06:00 media-mover
and the 06:30 archive replication — and fifteen minutes behind the Authentik
dump.

Restore a Mealie dump from a pod in the `recipes` namespace:

```bash
gunzip -c <file> | psql -h mealie-postgres -U mealie mealie
```

### Incomplete offsite run

Alerts: ResticOffsiteIncomplete, ResticOffsiteIncompleteProlonged.

restic exited 3: the snapshot was saved, but some paths could not be read, so
the backup is not complete. The wrapper records that as
`restic_offsite_last_run_incomplete=1` rather than failing the run.

1. Find the skipped paths in the wrapper's journal entry:
   `journalctl -u restic-offsite -n 200` on pve-nas-01.
2. If they sit under `/mnt/restic-clone-*`, the ext4 clone could not be read.
   Confirm the clone is mounted plain `ro` (not `ro,noload`) so the journal
   replays — `noload` makes reads come back EBADMSG.
3. Re-run `systemctl start restic-offsite.service` and confirm the gauge is
   back to 0.

Two consecutive incomplete runs (over 36 hours) escalates to critical: whatever
cannot be read has not reached B2 for more than a day.

### Full System Backup

1. **ZFS Snapshots**:
   ```bash
   for pool in tank ssd nvme archive; do
     sudo zfs snapshot -r $pool@backup-$(date +%Y%m%d)
   done
   ```

2. **Proxmox Backup** (the nightly job targets `tank-proxmox`; use the same
   storage by hand so the archive replication and restic offsite chain see it):
   ```bash
   # Via UI: Datacenter → Backup
   # Or via CLI
   sudo vzdump --all --storage tank-proxmox --mode snapshot
   ```

3. **Configuration Backup** — this repository *is* the configuration backup, and
   the normal branch + merge-request flow already pushes it. Confirm nothing is
   uncommitted locally:
   ```bash
   git status --short
   ```

### Restore from Backup

**ZFS Rollback**:
```bash
sudo zfs rollback tank/media@backup-20260101
```

**Proxmox Restore** (use a ZFS pool actually in use — `ssd` on the NAS,
`local-ssd` on compute nodes; `local-lvm` exists from the default install and
holds only the two NAS k3s VM roots (202, 222), docs/32):
```bash
sudo qmrestore /path/to/backup.vma.zst 100 --storage local-ssd
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
   curl -o /dev/null -s -w \
     'dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}\n' \
     https://<service>.esweiss.com/
   ```

3. **Review Recent Changes**:
   ```bash
   git log --since="1 day ago"
   ```

---

## System Maintenance

### Update Strategy

The infrastructure has three independent update scopes, each with rolling deployment (one host/node at a time) to maintain service availability:

1. **Base infrastructure** - Proxmox hosts, DNS servers, SMTP relay, Plex LXC (managed by Ansible)
2. **K3s cluster nodes** - k3s binary on server/agent VMs (Ansible, rolling cordon/restart; kernel reboots via kured)
3. **K3s workloads** - Helm charts and application images (managed by Flux: update `all.yml`, `task flux:sync-versions`, commit, push)

**Note:** OS package updates (`task maintenance:update-packages`) span base
infrastructure hosts, k3s node VMs, and the `app_servers` group (plex, gitlab,
nextcloud, immich, immich-ml) in a single rolling run.

### Quick Reference

| What to update | Command |
|---|---|
| Check for available updates | `task maintenance:check-versions` |
| Update versions in all.yml | `task maintenance:update-all-versions` |
| OS packages (all hosts: base, k3s, app VMs) | `task maintenance:update-packages` |
| Base apps (AdGuard, Tailscale, Plex) | `task maintenance:update-applications` |
| Full base update (packages + apps) | `task maintenance:update-full` |
| Full base update (auto-reboot) | `task maintenance:update-full-auto` |
| Plex only | `task maintenance:update-plex` |
| K3s nodes (rolling cordon/upgrade; reboots via kured) | `task maintenance:update-k3s-nodes` |
| K3s Helm charts + workload images | Edit `all.yml` → `task flux:sync-versions` → `git commit` → `git push`. Flux reconciles. |
| Full cluster update (nodes + versions) | `task maintenance:update-cluster` (4-phase: k3s node upgrades, check-versions, update-all-versions, sync-versions; commit + push to deploy via Flux) |

### Automated Version Discovery

The version checker (`scripts/check-versions.py`) automatically queries official sources (GitHub releases, Docker Hub, Helm repos) to find available updates for all tracked services.

**Check for available updates**:
```bash
# Check all managed services
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

After updating versions in `all.yml`, deploy with the appropriate mechanism:
- **Ansible-managed** (AdGuard, Tailscale, Plex): `task maintenance:update-applications`
  — this is a plain Ansible run against the hosts, so it neither reconciles nor
  waits on Flux; anything in `kubernetes/` still needs the commit + push below.
- **k3s node binary**: `task maintenance:update-k3s-nodes`
- **K3s Helm charts and workloads** (Flux-managed — MetalLB, Traefik, cert-manager,
  external-dns, Authentik, downloads, recipes, gitlab-runner, gitlab-agent):
  `task flux:sync-versions` → commit `ansible/inventories/prod/group_vars/all.yml`
  and `kubernetes/infrastructure/sources/versions-configmap.yaml` → `git push`.
  Flux picks up the new ConfigMap within ~1 minute and rolls HelmReleases +
  Kustomizations. Watch with `task flux:status` and `flux get all -A`.

**GitHub API rate limits**: Unauthenticated requests are limited to 60/hour. Set `GITHUB_TOKEN` for 5000/hour:
```bash
# Assign, then export: `export VAR=$(...)` returns export's status, so a
# failed vault read would leave the variable empty and pass silently.
GITHUB_TOKEN=$(op read "op://Homelab/GitHub Token/credential")
export GITHUB_TOKEN
task maintenance:check-versions
```

Results are cached for 1 hour in `.version-cache/`. Clear with:
```bash
task maintenance:check-versions -- --clear-cache
```

### Checks the version tooling cannot make

Three things `task maintenance:check-versions` will never surface. Walk them on
the same cycle as a held pin.

- **`"category": "manual"` registry entries.** They report
  `update_available: false` forever, because there is no upstream tag to compare
  against. Walk them quarterly: read the source URL in the entry, re-resolve any
  hand-edited `image:` tag+digest pin, and record what you resolved in that
  entry's `notes`. No `:latest`-tagged digest pin is allowed back into the tree —
  the tag is what the registry reads as the current version, so a `latest` pin
  compares equal to itself and can never go stale.
- **prometheus-operator-crds.** Its `appVersion` must equal the pinned
  kube-prometheus-stack's operator `appVersion`. Check both charts before
  bumping either, or the CRDs and the operator drift apart.
- **The Cloudflare Terraform provider**, held at `~> 4.52.0`. Each quarter run
  `gh release list -R cloudflare/terraform-provider-cloudflare | head` and check
  whether v4 has an announced end-of-support date or an open CVE. If either is
  true, the v4 → v5 migration in [docs/16](16-next-steps.md) § Terraform and
  gates stops being deferrable.

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

3. **Regenerate Flux ConfigMap and deploy**:
   ```bash
   # Regenerate versions-configmap.yaml from all.yml
   task flux:sync-versions

   # Review and commit BOTH files
   git diff ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/sources/versions-configmap.yaml
   git add ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/sources/versions-configmap.yaml
   git commit -m "Update service versions"
   git push

   # Flux reconciles Helm charts + workloads automatically (~1 min)
   # For base infrastructure updates (AdGuard, Tailscale, Plex, k3s nodes, OS):
   task maintenance:update-full       # base + apps (OS packages)
   task maintenance:update-k3s-nodes  # k3s binary, rolling
   ```

4. **Verify everything works**:
   ```bash
   task flux:status         # all HelmReleases + Kustomizations Ready
   task infra:verify       # base infrastructure health
   task k3s:status          # cluster health
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

#### Version Pinning Philosophy

This is the canonical statement of the pinning policy (CLAUDE.md and
`.cursorrules` point here):

- **k3s, Authentik, Helm charts**: pinned to specific versions for stability
- **Download/recipe containers**: pinned to specific stable tags (no
  "latest") for reproducible deployments
- **Bar Assistant / Salt Rim**: pinned to specific versions (check for
  breaking changes on major bumps)
- **Tailscale**: pinned to a specific apt version
- **Plex**: pinned to a specific apt version (set to `"latest"` for
  auto-update behavior)
- **Home Assistant (HAOS)**: manual updates via its UI; documented version
  only, not pinned in `all.yml`

#### Silencing alerts around planned maintenance

Deploys that stop/restart the HA-managed LXCs (dns-01/dns-02/smtp-relay) or
the HAOS VM will fire **HAInfraGuestDown**. Silence it first:

```bash
task observability:silence ALERT=HAInfraGuestDown DURATION=1H
```

(`DURATION` units are S/M/H/d; the task works with either BSD or GNU `date`.)
Note that smtp-relay (lxc/151) downtime blinds the **email** leg of critical
alerts for its duration — the Discord webhook is then the only delivery
path.

#### Update Schedule

**Monthly Updates**:
```bash
# Full update with auto-reboot (minimal interaction)
task maintenance:update-full-auto

# After update: Always verify
task infra:verify
```

**Security Updates**:
```bash
# For urgent security patches: OS packages on all hosts (base infra, k3s nodes, app VMs)
task maintenance:update-packages
# Add -e auto_reboot=true for auto-reboot:
task maintenance:update-packages -- -e auto_reboot=true
task infra:verify

# After k3s node updates, verify cluster health
task k3s:status
kubectl get nodes
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded
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
ansible dns -i ansible/inventories/prod -m shell -a "ls -la /opt/AdGuardHome/AdGuardHome.yaml.backup-*"

# Restore on specific host
ansible dns-01 -i ansible/inventories/prod -m copy -a "src=/opt/AdGuardHome/AdGuardHome.yaml.backup-TIMESTAMP dest=/opt/AdGuardHome/AdGuardHome.yaml remote_src=yes"

# Restart service
ansible dns-01 -i ansible/inventories/prod -m service -a "name=AdGuardHome state=restarted"
```

**Service not starting after update**:
```bash
# Check service status
ansible <host> -i ansible/inventories/prod -m command -a "systemctl status AdGuardHome"

# Check logs
ansible <host> -i ansible/inventories/prod -m shell -a "journalctl -u AdGuardHome -n 50"
```

### Post-maintenance verification

`scripts/post-maintenance-verify.sh` is the cluster-health gate that runs after
every maintenance op. It exits 0 on a healthy cluster, 1 on any ERROR.

**When it runs**

- Automatically after each `maintenance-*` CI job — those jobs invoke
  `scripts/maintenance-run-with-verify.sh`, which always runs the verify even
  when the maintenance command itself failed (so a half-finished op cannot skip
  the health check).
- On demand: the manual `maintenance-verify` job in the pipeline, or
  `bash scripts/post-maintenance-verify.sh` locally against a working kubeconfig.

**What it checks**

| check | ERROR when |
|---|---|
| k3s nodes | a node is not `Ready` (`Ready,SchedulingDisabled` passes) |
| pods | any pod unhealthy, CrashLoopBackOff included |
| critical deployments | available replicas below desired |
| Jobs | a Job in `Failed` |
| GitLab | `/-/health` does not return 200 |
| cluster DNS | in-cluster resolution fails (one-shot `busybox` pod, tag pinned to `busybox_version`; `task lint:busybox-version-pin` guards the pin) |

**kured excuse, and its two accepted limitations.** kured reboots nodes
serially during a maintenance run, so NotReady nodes and evicted pods are an
expected transient. The verify excuses them *node-scoped*: an unhealthy pod or
under-replicated Deployment is downgraded to WARN only while kured is mid-reboot
**and** the workload sits on a rebooting node (or is unscheduled). Anything else
is a hard ERROR. Two cases therefore WARN when they arguably should ERROR, and
both are deliberate — the alternative is mis-classifying a real kured transient
as a failure:

- A pod that is unschedulable for an unrelated reason has no node, so it reads
  the same as a drain-evicted pod during a reboot window.
- On a multi-replica Deployment, one replica on a rebooting node excuses the
  whole Deployment for that window.

Re-run the verify after kured settles (`kubectl get nodes` shows no
`weave.works/kured-reboot-in-progress` annotation); the excuse disappears with
the reboot and any WARN that was real becomes an ERROR.

**Terminating pods are not excused outside the runner namespaces.** A stale NFS
handle presents exactly as a pod stuck `Terminating`, so the unhealthy-pod check
treats it as a fault. The excuse is by namespace name prefix only
(`gitlab-runner*`), where a Terminating CI pod is routine.

**Fixed waits.** The verify waits 20s after a not-ready node, 25s after an
unhealthy pod and 10s after an under-replicated Deployment before re-reading.
GitLab gets a 4-minute readiness budget, because its disk sits on an
encryption-gated zvol and only comes up after the pool is unlocked.

**Sync exercise on dns-01.** `task infra:verify` postflight starts one
`adguardhome-sync` run, proving the DynamicUser / LoadCredential /
StateDirectory sandbox can start on the unprivileged dns-01 LXC. An app-level
failure (unreachable origin) is fine. A systemd exec-class `ExecMainStatus` —
217 USER, 226 NAMESPACE, 238 STATE_DIRECTORY, 243 CREDENTIALS — means the
sandbox itself could not be set up, and the assert fires. The run pushes dns-01's
config to dns-02, which the 5-minute timer does anyway; set
`postflight_exercise_sync=false` to skip it while investigating a divergence
between the two resolvers.

---

## K3s Cluster Maintenance

### Updating K3s Cluster

The k3s cluster has two update layers with different mechanisms:

1. **k3s node binary** — Ansible rolling upgrade
2. **Helm charts + workload images** — git push + Flux reconciliation

#### 1. Node Updates (k3s binary)

```bash
# Update k3s version in group_vars/all.yml first
task maintenance:update-version SERVICE=k3s

# Rolling update with pod evacuation
task maintenance:update-k3s-nodes

# Verify cluster health
task k3s:status
```

**Process (per node, serial: 1) — k3s BINARY upgrade only:**
1. Cordons node (prevents new pods during the ~1-2 min upgrade)
2. Upgrades k3s binary via install script
3. Restarts the k3s / k3s-agent service
4. Uncordons node, waits for node Ready

There is **no drain**: a k3s service restart keeps running pods (containerd
persists the containers across a kubelet restart), so eviction is unnecessary —
and draining the node that hosts the CI job's own runner executor pod would
deadlock the run.

**Kernel/OS reboots are NOT done here.** `task maintenance:update-packages` only
`touch`es `/var/run/reboot-required` on a node whose kernel changed; **kured**
(Kubernetes Reboot Daemon, a DaemonSet — `kubernetes/infrastructure/controllers/kured/`)
then reboots each flagged node one at a time (cordon → drain → reboot → uncordon),
coordinated by a cluster-wide lock and gated by a `blockingPodSelector` so it
never reboots the node running the maintenance job until that job has finished.
The Proxmox HOST that runs the executor's VM is handled by a detached
`systemd-run` reboot in `_reboot-if-needed.yml` (it can't reboot itself
synchronously without killing the run).

> **Operator note — kured runs concurrently with the maintenance ops.** During
> `maintenance-run-all`, op 1 flags node sentinels and kured may begin rebooting
> a flagged node ~5 min later, while ops 3–6 are still running. This is safe
> (kured does one node at a time, the cluster tolerates one server down, and the
> ops retry their delegated `kubectl`), but you may see transient delegated-kubectl
> retries in ops 3–6 and a NotReady node mid-run — these are expected, not
> failures. The post-maintenance verify excludes kured-rebooting nodes and their
> transient pods for exactly this reason.
>
> **etcd quorum stays safe — by construction, not by timing.** The 3 etcd
> servers (k3s-srv on pve-nas-01 / pve-laptop-01 / pve-prec-01) are never rebooted
> two-at-once, enforced four ways: (1) kured is `concurrency: 1` (one node at a
> time, cluster-locked); (2) `update-packages` play 1 (synchronous Proxmox-host
> reboots, `serial: 1`) **waits for each rebooted server's k3s node to be Ready
> (etcd rejoined) before advancing** to the next host; (3) `update-k3s-nodes`'
> server play **waits until no server carries the `kured-reboot-in-progress`
> annotation** before restarting a server's k3s (which briefly drops its etcd
> member); and (4) the detached self-host reboot only ever targets an **opt-\*
> host (no etcd member)** — an etcd-server host that is the executor's own host
> defers to the operator instead. With single-fault etcd tolerance, no path puts
> two members down at once.
>
> **kured reboots whenever `/var/run/reboot-required` appears.** On the k3s VMs
> (Debian; no `unattended-upgrades` configured by any role) the maintenance apt
> upgrade is the sole writer, so kured reboots are maintenance-driven. If a future
> out-of-band writer ever sets the sentinel, kured still reboots safely
> (`concurrency: 1` + the `blockingPodSelector` deferral of any node running a CI
> job) — just not on a maintenance schedule.

##### Clearing a stuck kured lock

kured runs with `lockTtl: "0"`, so the reboot lock never expires on its own:
only the holder releases it. A kured pod that dies while holding it blocks every
further reboot, and no alert distinguishes that from "nothing needs rebooting".
Confirm no node is mid-reboot, then clear the annotation:

```bash
kubectl get nodes -o json \
  | jq -r '.items[] | select(.metadata.annotations["weave.works/kured-reboot-in-progress"]) | .metadata.name'
kubectl -n kube-system annotate ds/kured weave.works/kured-node-lock-
```

The next kured period picks up any node still carrying
`/var/run/reboot-required`.

**Special considerations:**
- Servers are updated first, then agents
- If the service restart fails, the always-block uncordons the node

#### 2. Helm Chart + Workload Image Updates (Flux)

All platform Helm charts (MetalLB, Traefik, cert-manager, external-dns,
external-secrets) and all application images (Authentik, downloads, recipes,
gitlab-runner, gitlab-agent) are reconciled by Flux from substitutions in the
`cluster-versions` ConfigMap.

```bash
# 1. Bump versions in all.yml
task maintenance:update-version SERVICE=traefik
task maintenance:update-version SERVICE=sonarr
# (or: task maintenance:update-all-versions)

# 2. Regenerate versions-configmap.yaml
task flux:sync-versions

# 3. Review, commit, push
git diff ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/sources/versions-configmap.yaml
git add ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/sources/versions-configmap.yaml
git commit -m "Bump traefik and sonarr"
git push

# 4. Watch Flux reconcile
task flux:status            # Concise health summary
flux get all -A             # Detailed view
flux get hr -n traefik      # Watch specific release
```

Reconciliation is push-triggered — the GitLab agent's Flux integration
notifies Flux within seconds of a push, with the ~1-minute GitRepository poll
as fallback (see docs/29-flux-operations.md). Helm charts upgrade in-place; Deployments/StatefulSets roll with
the image tag from the substituted `${version}` placeholder. For fast local
iteration without committing, `task flux:dev-apply -- kubernetes/apps/<app>`
applies a rendered Kustomization; Flux will revert to the committed state on
its next reconcile (~1 minute) unless you `flux suspend` the Kustomization.

#### 3. Complete Cluster Update

```bash
# Update all versions in group_vars/all.yml and regenerate ConfigMap
task maintenance:update-all-versions
task flux:sync-versions
git add ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/sources/versions-configmap.yaml
git commit -m "Sweep cluster versions" && git push

# Upgrade k3s node binary (rolling)
task maintenance:update-cluster     # 4-phase: k3s nodes, check-versions, update all.yml, sync ConfigMap
```

### Maintenance Windows

Alerts: MaintenanceRebootDeferred, KuredRebootStuck.

**Recommended schedule:**
- Monthly: OS package updates on all hosts (`task maintenance:update-packages`)
- Quarterly: Full base infrastructure update (`task maintenance:update-full`)
- As needed: k3s cluster updates (`task maintenance:update-cluster`)
- As needed: Individual workload updates

**Downtime expectations:**
- Base infrastructure: 5-10 minutes per host (rolling, minimal DNS impact)
- K3s nodes: 2-5 minutes per node (rolling, workloads migrate)
- Helm charts: 1-2 minutes per chart (rolling updates)
- Workloads: 1-2 minutes per namespace (rolling restart)

#### Rebooting a Proxmox host that runs HA guests

**Drain the node first.** The maintenance playbooks reboot without draining, and
the cluster leaves `shutdown_policy` at the Proxmox default (`conditional`),
which *freezes* HA services across a reboot rather than moving them — they stay
assigned to the node and are DOWN for its whole downtime. For dns-01 that means
losing the primary resolver for the reboot unless you move it first.

```bash
# 1. Drain: HA relocates every service off the node (it may hop more than once)
ssh <host> sudo ha-manager crm-command node-maintenance enable <node>
ssh <host> sudo ha-manager status | grep service:   # wait until none list <node>

# 2. Reboot
ssh <host> sudo systemctl reboot

# 3. Release: services fail BACK to their homes automatically
ssh <host> sudo ha-manager crm-command node-maintenance disable <node>
ssh <host> sudo ha-manager status | grep service:   # homes restored
```

Do NOT try `ha-manager migrate <svc> <node>` to move a service off its home
while the home is up — it is refused with *"resource not allowed on target
node"*. That is correct behaviour, not a fault: with the priority groups in
`proxmox_ha_rules` (home at `:2`, the rest at `:1`) only the highest-priority AVAILABLE
node is a legal placement, and lower-priority nodes become legal exactly when
the home goes away. Maintenance mode is the supported way to make a node
unavailable on purpose. Automatic failover on a real node failure is unaffected.

Leaving `shutdown_policy` at `conditional` is deliberate. Switching it to
`migrate` would not reduce downtime here: dns-01, dns-02 and smtp-relay are LXCs,
and container migration is stop/start regardless, so you would get the same
outage on a different host plus needless shuffling on every kernel reboot. The
redundancy that matters is the dns-01/dns-02 pair plus automatic failover.

Which host homes which guest is the `affinity-*` entries of `proxmox_ha_rules`
in `group_vars/all.yml`.

#### Maintenance reboot routing

`ansible/playbooks/maintenance/_reboot-if-needed.yml` routes a pending reboot
three ways, on three inputs:

| Input | Meaning |
|---|---|
| `host_runs_etcd_server` | the Proxmox host carries a k3s **server** VM (an etcd member) |
| `ci_detection_failed` | the play is running inside a CI executor pod but the node lookup never resolved |
| `proxmox_self_reboot_safe` | per-host, default true; false for pve-nas-01 |

- **Defer** — the operator reboots by hand, and the host publishes
  `maintenance_reboot_deferred` (alert `MaintenanceRebootDeferred`).
- **Detached** — a `systemd-run` timer that survives the CI job, armed and
  disarmed by the job's `after_script`.
- **Synchronous** — reboot in place.

Why each defer exists:

- A **detached** reboot of an etcd-server host cannot coordinate with kured and
  could take a second etcd member down. The synchronous path (play 1 `serial: 1`
  plus the etcd-rejoin wait) stays one member at a time and is fine.
- `ci_detection_failed` covers a pod name that does not match the executor
  prefix, a lookup error, an empty `nodeName`, or a node with no `proxmox_host`
  in inventory. None of those can prove the host is not the self-host, so it
  fails closed.
- `proxmox_self_reboot_safe: false` (pve-nas-01) never auto-reboots at all,
  because of the encrypted pools and the `onboot=0` guests.

**Nested timeout budget on the etcd-rejoin wait.** Rebooting a control-plane
host fails the kube-vip API VIP (.161) over to another server, and during that
window a `kubectl wait` against the VIP can block on a dead TCP session far past
its own `--timeout`. Smallest first: `--timeout=180s` is the logical budget (how
long the node has to go Ready), `--request-timeout=190s` bounds a hung API call
and sits **above** `--timeout` so it never cuts the wait's watch short, and
`timeout --kill-after=10s 200s` is the outer backstop for a socket-level hang.
180 < 190 < 200, three attempts, about ten minutes worst case, then the run
fails. Raise them together.

#### Supervised kube-vip window

A kube-vip manifest change moves the API VIP (10.0.10.161), and the rendered
server config changes with it, so the servers converge one at a time in a
watched window.

1. Open a second shell on a kubeconfig pointed at a server's own address
   (10.0.10.222 or 10.0.10.227). That is the fallback while the VIP is in
   flight.
2. Converge the servers: `task k3s:deploy -- --limit k3s_servers`. Each one
   restarts k3s in turn.
3. Force a leader move: `kubectl -n kube-system delete pod <kube-vip pod on the
   VIP holder>`.
4. Watch the surviving pods take it:
   `kubectl -n kube-system logs -l app.kubernetes.io/name=kube-vip -f | grep "New leader"`,
   with a loop on `kubectl --server https://10.0.10.161:6443 get nodes` beside
   it.
5. Sign the window off once the VIP answers through the new leader.

The escape hatch is `k3s_kube_vip_host_kubeconfig: true` in inventory: kube-vip
mounts the node's cluster-admin kubeconfig instead of its ServiceAccount token.
It re-renders the DaemonSet and restarts the kube-vip pod on every server.

### Rollback Procedures

#### Rolling back k3s version

```bash
# Update group_vars to previous version
task maintenance:update-version SERVICE=k3s
# (or manually edit all.yml to set previous version)

# Re-run node update
task maintenance:update-k3s-nodes
```

#### Rolling back Helm chart or workload image (Flux)

The preferred rollback is `git revert`:

```bash
# Revert the offending commit (typically bumps to all.yml + versions-configmap.yaml)
git revert <commit-sha>
git push

# Flux reconciles the cluster back to the prior state (~1 min)
task flux:reconcile   # Optional: don't wait for the poll interval
task flux:status
```

This is atomic — the ConfigMap flips back, every HelmRelease re-renders with
the prior `${version}`, and helm-controller performs the downgrade.

**Emergency stop** (skip git, pause Flux before reverting):

```bash
# Pause a single HelmRelease (stops Flux from "fixing" your manual work)
task flux:suspend -- traefik/helmrelease/traefik

# Manually roll back with helm while Flux is paused
helm history traefik -n traefik
helm rollback traefik <revision> -n traefik

# After git revert + push, resume
task flux:resume -- traefik/helmrelease/traefik
task flux:reconcile
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

#### HelmRelease upgrade fails (Flux)

```bash
# Check the HelmRelease status
flux get hr -n <namespace>
kubectl describe hr <name> -n <namespace>

# View Flux events
flux events --for HelmRelease/<name> -n <namespace>
kubectl get events -n <namespace> --sort-by='.lastTimestamp'

# Rollback via git revert (preferred):
git revert <commit-sha> && git push
task flux:reconcile

# Or emergency manual rollback while suspending Flux:
task flux:suspend -- <namespace>/helmrelease/<name>
helm history <name> -n <namespace>
helm rollback <name> <revision> -n <namespace>
# Then fix root cause in git, push, and:
task flux:resume -- <namespace>/helmrelease/<name>
```

(See `docs/29-flux-operations.md` for the full Flux troubleshooting tree.)

#### Node drain hangs

```bash
# Check what pods are blocking
kubectl get pods --all-namespaces --field-selector spec.nodeName=<node-name>

# Force drain if necessary (use carefully)
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --force --grace-period=0

# Or uncordon and try again
kubectl uncordon <node-name>
```

#### Pruning unused container images

Alerts: KubeletImageGCIneffective (k3s node root fs above the kubelet's 70%
image-gc-high-threshold, so GC runs continuously and never reaches its low
watermark).

Kubelet's image GC kicks in at the configured threshold (`70/50` cluster-wide
via `k3s_kubelet_args`); it does not run on demand. To free space without
waiting for the threshold (e.g. after a large image churn), use crictl
directly on the affected node:

```bash
ssh <k3s-vm>
# List images and how much space each layer holds
sudo k3s crictl images --quiet | wc -l
sudo du -sh /var/lib/rancher/k3s/agent/containerd/io.containerd.snapshotter.v1.overlayfs

# Prune images not referenced by a running container
sudo k3s crictl rmi --prune

# Re-check
sudo du -sh /var/lib/rancher/k3s/agent/containerd/
```

Safe to run during normal operation — only unused images are removed; pods
re-pull on next deploy if needed. Most useful on the agent that hosts a
lot of churn (Connect, runners, build-agent workloads — currently
`k3s-agt-prec-01`).

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

Exact per-host skip counts vary with every role/task change and are **not** a
health signal — do not treat a specific number as expected. Verify the resulting
state (`task infra:verify`) instead of chasing counts. The qualitative pattern of
what skips where is below.

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
# Run verification
task infra:verify
```

This checks:
- SSH connectivity
- DNS resolution
- NFS exports
- Service status
- Certificate SSH
- AdGuard API health
- ZFS pool health (tank, ssd, nvme, archive)
- SMART disk health (every device in the `smartd_*_disks` lists in `host_vars/pve-nas-01.yml`)
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

   # Example output showing failover (home-assistant expected on pve-prec-01,
   # running on pve-opt-02):
   # VMID   Type  State    Node
   # 154    vm    started  pve-opt-02   <-- MISMATCH: source_node is pve-prec-01
   ```

3. **Check replication status for errors:**
   ```bash
   # Use the HA status task (checks all source nodes)
   task proxmox:ha-status

   # Or SSH to each replication SOURCE node (pvesr only shows local jobs).
   # The source set is whatever `source_node` values `proxmox_ha_replication_jobs`
   # currently carries in group_vars/all.yml.
   ssh pve-prec-01 sudo pvesr status
   ssh pve-opt-01 sudo pvesr status
   # ... etc

   # Look for "error" state or failed last_sync timestamps
   ```

### Reconciliation Procedure

After a failover, you have two options:

#### Option A: Update Configuration (Permanent Migration)

Use this when the original node is offline for extended maintenance or has failed permanently.

1. **Edit `ansible/inventories/prod/group_vars/all.yml`:**
   ```yaml
   # Find the proxmox_ha_replication_jobs section
   # Update source_node for all jobs of the affected VMID

   # Example: home-assistant (VMID 154) failed over from pve-prec-01 to pve-opt-02
   # BEFORE:
   - id: "154-0"
     source_node: pve-prec-01  # <-- old source
     target_node: pve-opt-02

   # AFTER:
   - id: "154-0"
     source_node: pve-opt-02   # <-- new source (where service is now running)
     target_node: pve-prec-01  # <-- swap: old source becomes a target
   ```

   The `proxmox_ha_rules` node-affinity home for the VMID must move with it (`source_node`
   has to track the HA home or `proxmox_ha/replication.yml` cannot manage the jobs).
   `pve-laptop-01` is fallback-only (priority 1, never a home).

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

   # Example: migrate home-assistant back to its home node
   sudo qm migrate 154 pve-prec-01 --online
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
| dns-01 | 150 | pve-prec-01 | `*/15` (0,15,30,45) | Primary DNS; dns-02 provides redundancy |
| smtp-relay | 151 | pve-opt-01 | `3-59/15` (3,18,33,48) | Single instance; brief outage during failover |
| dns-02 | 160 | pve-opt-03 | `6-59/15` (6,21,36,51) | Secondary DNS; dns-01 provides redundancy |
| home-assistant | 154 | pve-prec-01 | `9-59/15` (9,24,39,54) | HAOS VM; check integrations after failover |

`proxmox_ha_rules` and `proxmox_ha_replication_jobs` in `group_vars/all.yml`, plus each
guest's `proxmox_host` in `hosts.yml`, are the source of truth for the current
homes — this table is a convenience copy.

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

## Observability Stack

The in-cluster procedures live in
[docs/31-observability.md § Troubleshooting](./31-observability.md#troubleshooting)
— that copy is canonical and carries the caveats (notably: `retentionSize` must
be set **below** its current `110GB`, never above). Go straight there:

| Symptom | Go to |
|---|---|
| Prometheus CrashLoopBackOff, "no space left on device", WAL write failures | [docs/31 § Prometheus Disk Full](./31-observability.md#prometheus-disk-full) |
| Need a bigger Prometheus/Loki zvol (and the two manifest sizes that must follow) | [docs/31 § Storage Expansion](./31-observability.md#storage-expansion) |
| Loki restarts, ingestion stops, WAL errors | [docs/31 § Loki WAL Issues](./31-observability.md#loki-wal-issues) |
| `up == 0` for a scrape target, dashboard gaps | [docs/31 § Exporter Down](./31-observability.md#exporter-down) |
| Alerts not arriving on Discord or email | [docs/31 § Alert Routing Debug](./31-observability.md#alert-routing-debug) |
| Grafana login fails / redirect loops via Authentik | [docs/31 § Grafana OIDC Issues](./31-observability.md#grafana-oidc-issues) |
| Locked out of Grafana entirely (Authentik down) | [docs/31 § Grafana Break-Glass Access](./31-observability.md#grafana-break-glass-access-authentikoidc-down) |

The two procedures below are host-side and have no home in docs/31, so they stay
here.

### Recovering Space on the Prometheus / Loki zvols

Alerts: DiskUsageWarning, DiskUsageCritical, InodeUsageWarning,
InodeUsageCritical, PVCUsageWarning, PVCUsageCritical, KubeletImageGCIneffective.

The TSDB / Loki chunk stores have their own retention; ext4 frees blocks
inside the zvol but doesn't issue DISCARD by default, so the parent ZFS
dataset never reclaims them. Combined with `zfs-auto-snapshot`, a
148 GiB ext4 with 15 GiB live data can show 280+ GiB allocated on the
host pool. Auto-snapshots are now disabled at the dataset level (see
`host_vars/pve-nas-01.yml`); to reclaim what's already accumulated:

1. **Inside the VM, trim ext4:**
   ```bash
   ssh k3s-agt-nas-01
   sudo fstrim -v /mnt/prometheus-data
   sudo fstrim -v /mnt/loki-data
   ```

2. **On the NAS, drop the existing auto-snapshots if they're still
   present** (after Ansible has applied `auto-snapshot=false`, new
   snapshots stop being created but old ones persist):
   ```bash
   ssh pve-nas-01
   # Inspect what's there
   sudo zfs list -t snapshot -o name,used -s creation \
     | grep -E 'prometheus|loki'

   # List the auto-snapshots first. Filter by name rather than using an
   # unbounded `@%` range, which also matches the archsync-* snapshots.
   for ds in ssd/appdata/prometheus ssd/appdata/loki; do
     sudo zfs list -t snapshot -H -o name -r "$ds" | grep zfs-auto-snap_
   done

   # If that list is what you intended, destroy exactly those.
   for ds in ssd/appdata/prometheus ssd/appdata/loki; do
     sudo zfs list -t snapshot -H -o name -r "$ds" | grep zfs-auto-snap_ \
       | xargs -r -n1 sudo zfs destroy -v
   done
   ```

   `com.sun:auto-snapshot=false` stops creation **and** pruning, so flipping it
   strands every snapshot that already exists. That is why this sweep exists.

   Never destroy an `archsync-*` snapshot on a dataset under an
   `nas_storage_archive_backup_sources` root. It is the incremental base for
   the nightly archive replication, and losing it forces a full re-send of that
   subtree ([docs/42](42-offsite-backup.md)).

3. **Verify space reclaimed:**
   ```bash
   sudo zfs list -o name,used,referenced,usedbysnapshots \
     ssd/appdata/prometheus ssd/appdata/loki
   ```

### Loki break-glass NodePort (host log shipping when the ingress is down)

**Symptoms:** host Alloy cannot push (`HostLogShippingStale` / Alloy `remote_write`
errors) because Traefik, the `*.esweiss.com` cert, or the basic-auth middleware
is broken — not Loki itself.

Host Alloy normally ships to `https://loki.esweiss.com/loki/api/v1/push`. There
is **no NodePort Service in git**: an always-on NodePort is an unauthenticated
push/read path on every node IP that bypasses the IngressRoute and its
basic-auth middleware, and Flux would keep it alive forever. Apply it by hand
for the duration of the outage only.

**Two objects, not one.** The NodePort skips the IngressRoute but not the pod
network: `allow-loki-ingress` admits `:3100` from the `traefik` namespace only
and `netpol-baseline` denies the rest, and NodePort traffic reaches the pod
SNAT'd to the node IP, so every push is dropped until a temporary `ipBlock`
allow is in place too.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Service
metadata:
  name: loki-external
  namespace: observability
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: loki
  ports:
    - name: http
      port: 3100
      targetPort: 3100
      nodePort: 31100
---
# The CIDR is a literal on purpose: kubectl applies this directly and Flux
# never substitutes ${cluster_lan_cidr} for you.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-loki-nodeport-breakglass
  namespace: observability
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: loki
  policyTypes: [Ingress]
  ingress:
    - from:
        - ipBlock:
            cidr: 10.0.10.0/24
      ports:
        - protocol: TCP
          port: 3100
EOF
```

Then point the affected hosts at it and redeploy Alloy:

```bash
# per host, in ansible/inventories/prod/host_vars/<host>.yml
alloy_host_loki_url: http://10.0.10.161:31100/loki/api/v1/push
task infra:deploy -- --limit <host> --tags alloy_host
```

`31100` is already open in the Proxmox firewall (`sg-metrics`, docs/11), so no
firewall change is needed. **Tear it down as soon as the ingress is back** —
revert the `alloy_host_loki_url` override, redeploy, then:

```bash
kubectl -n observability delete networkpolicy allow-loki-nodeport-breakglass
kubectl -n observability delete service loki-external
```

Flux reconciles neither object (both are in no kustomization), so nothing
removes them for you.

### Traefik dashboard break-glass (Authentik down)

**Symptoms:** `https://traefik.esweiss.com` 404s or never reaches a login —
Authentik, its embedded outpost, or the `traefik_dashboard` provider is broken,
and the dashboard is exactly what you want to read while diagnosing a routing
problem.

The dashboard route is chained `lan-tailscale-strict` -> `authentik-auth` ->
`hsts-header`, so there is no password form to fall back to: forward-auth is the
only identity gate. Reach the API directly instead of loosening the chain — the
port-forward is authenticated by your kubeconfig and is gone when you Ctrl-C it:

```bash
# The dashboard/API listens on the chart's own `traefik` entrypoint, whose
# container port has moved between chart majors — read it rather than assume it.
TRAEFIK_PORT=$(kubectl -n traefik get deploy traefik \
  -o jsonpath='{.spec.template.spec.containers[0].ports[?(@.name=="traefik")].containerPort}')
kubectl -n traefik port-forward deploy/traefik 8080:"$TRAEFIK_PORT"
# then, in another shell / a local browser:
curl -s localhost:8080/api/overview | head
open http://localhost:8080/dashboard/
```

Do **not** temporarily drop `authentik-auth` from the IngressRoute: `/api/rawdata`
dumps every internal hostname and ClientIP bypass, and the change would sit live
in git until someone reverts it. Fix the Authentik side instead — the provider,
application, group binding and outpost key are codified in `terraform/authentik`
(docs/40), so re-apply there rather than editing in the UI.

---

## Reading collect-state status

`task collect-state` is the first thing to run on any unexplained estate-wide
symptom; the classifier below is how to read its verdict. (Single-subsystem
entry points: `sudo journalctl -xe`, `sudo pvecm status`,
`sudo pve-firewall status`, and the per-subsystem sections above.)

Both modes share one tri-state classifier (`scripts/collect-state.sh`
header documents the invariant):

| Regular | `--json` | Meaning |
|---|---|---|
| `OK` | `healthy: true` | every gate green: all collected hosts reachable, ALL k3s nodes Ready, zero Flux not-ready, zero non-ONLINE ZFS pools (all hosts), GitLab `/-/health` 200 via the internal VIP, and no non-Watchdog, non-InfoInhibitor alert firing. Recent Warning events are reported in the header but are **advisory only** — they do not gate OK/healthy |
| `PARTIAL` | `degraded: true` | any imperfection with core infra still up (e.g. a host unreachable, a k3s node NotReady, a Flux resource not ready, a degraded ZFS pool, a non-Watchdog alert firing) while the core plane is up. Warning events do not by themselves downgrade green |
| `FAILED` | neither flag | catastrophic: no Proxmox host reachable, k3s API up with zero Ready nodes, or (regular only) host coverage below the floor — `CLUSTER_STATUS.txt` is not overwritten. In `--json` mode, "neither flag" also occurs when node data is simply unavailable (local kubectl/kubeconfig failure leaves `k3s.nodes_ready: 0` / `k3s.nodes_total: 0`) — check `collector_context` to distinguish collector-side from cluster-side |

An alert count of `unknown` (Alertmanager unreachable from the collector)
counts as one firing alert, so a snapshot that could not measure alerts reads
`PARTIAL` / `degraded` rather than green. `--json` emits
`alerts: { firing: <n|null> }`, null meaning the count could not be obtained.

One intentional asymmetry: the "all collected hosts reachable" gate is
regular-mode only (it SSHes DNS/mail/k3s VMs/GitLab); `--json` is a fast
core probe whose host gate covers just the 6 Proxmox hosts — so an
unreachable auxiliary host yields `PARTIAL` from regular while `--json`
can still say `healthy: true`.

The non-Proxmox hosts (DNS, smtp-relay, GitLab VM, k3s VMs, HAOS) are
addressed by IP, not bare hostname — only the 6 Proxmox hosts use SSH-config
host aliases. This keeps remote/Tailscale `--json` runs from false-failing the
coverage gate on DNS resolution (a bare hostname like `gitlab` won't resolve off
the LAN).

Every timestamp collect-state stamps itself is UTC: the `# Generated:` header
and each `=== <host> - ... ===` banner. Command output inside a section (systemd
timers, journal excerpts) stays in the host's local time, so each host banner is
followed by a `Host timezone:` line.

### Warning-event exclusions

The Warning-event count in the artifact header excludes one case:
`FailedScheduling` in a `gitlab-runner*` namespace whose message cites only
`Insufficient cpu` or `Insufficient memory`. That is how the CI pool overflows by
design, and it recurs on every pipeline. A message that also cites a real blocker
(`persistentvolumeclaim`, `exceeded quota`, `volume node affinity conflict`)
still counts, as does any `FailedScheduling` outside those namespaces.

Taint and affinity mentions are deliberately not disqualifying: every normal
overflow message lists the tainted NAS and server nodes, so keying off those
would void the exclusion. The policy lives in `warning_events_filter`
(`scripts/collect-state-lib.sh`) and is unit-tested.

**Section caps.** A cap of 5 on `systemctl list-timers <unit> --all` is the
minimum that does not flag a complete section as truncated: that command renders
4 lines for one unit (header, timer, blank, "N timers listed."). The truncation
marker is the artifact's trust signal, so a cap must never fire on complete
output.

---

## Related documentation

- [docs/17 — Disaster recovery](17-disaster-recovery.md) and [docs/42 — Offsite backup](42-offsite-backup.md)
- [docs/29 — Flux operations](29-flux-operations.md) (in-cluster day-2)
- [docs/31 — Observability](31-observability.md) (the canonical observability troubleshooting)
- [docs/32 — ZFS encryption](32-zfs-encryption.md) and [docs/34 — Bond MAC flapping](34-bond-mac-flapping.md)
- [docs/16 — Next steps](16-next-steps.md) (accepted risks and open work)

## External references

- [Proxmox VE Documentation](https://pve.proxmox.com/pve-docs/)
- [ZFS Administration Guide](https://openzfs.github.io/openzfs-docs/)
- [Ansible Troubleshooting](https://docs.ansible.com/ansible/latest/user_guide/playbooks_startnstep.html)
