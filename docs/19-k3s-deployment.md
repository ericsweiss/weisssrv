# K3s Cluster Deployment Guide

This guide walks through deploying the 9-node k3s cluster (3 servers + 6 agents) and the Flux GitOps platform that manages every workload on top of it.

## Quick Reference: Complete Deployment Workflow

K3s deployment is a three-phase approach:

- **Phase 1 (Ansible)**: Provisions the 9 k3s VMs on Proxmox.
- **Phase 2 (Ansible)**: Deploys k3s + kube-vip to servers/agents.
- **Phase 3 (Flux bootstrap)**: Bootstraps Flux, which then reconciles every
  platform component and every application from this repo — see
  `docs/29-flux-operations.md` for the stage chain and each stage's
  `kustomization.yaml` for the current membership (autoscaling details in
  docs/33-autoscaling.md).

**All Ansible tasks are idempotent** — safe to re-run at any time.

### What Each Step Does

| Step | Component | Mechanism |
|------|-----------|-----------|
| `task k3s:provision-vms` | Debian VMs | Ansible + Proxmox API |
| `task k3s:deploy` | K3s + kube-vip | Ansible (server, agents, node labels/taints) |
| `task flux:bootstrap-onepassword` | `op-credentials` + `onepassword-connect-token` Secrets (bootstrap only) | Prints instructions; `flux:bootstrap-onepassword-apply` creates them |
| `task flux:bootstrap` | Flux controllers committed to `kubernetes/clusters/weisssrv/flux-system/` | `flux bootstrap gitlab` |
| (none — automatic) | All platform + apps reconcile from `kubernetes/infrastructure/` and `kubernetes/apps/` | Flux |

Everything under `kubernetes/` is Flux-managed. To deploy or update a component,
commit the YAML and push — the GitLab agent's Flux module triggers an immediate
reconcile on push (the ~1-minute GitRepository poll remains as the fallback).
`task flux:reconcile` triggers a sync manually.

### Complete Command Sequence (Initial Install)

```bash
# === PHASE 1: VM Provisioning (Ansible) ===
task k3s:provision-vms

# === PHASE 2: K3s Cluster (Ansible) ===
task k3s:deploy
task k3s:kubeconfig
export KUBECONFIG=~/.kube/config-k3s
kubectl get nodes  # Verify cluster

# === PHASE 3: Flux bootstrap ===
# 3a. Create the bootstrap secrets (1P Connect credentials for ESO)
# Run `task flux:bootstrap-onepassword` for instructions, then create manually.
task flux:bootstrap-onepassword

# 3b. Bootstrap Flux (reads Flux GitLab PAT from 1P, commits flux-system/ to this repo)
task flux:bootstrap

# 3c. Wait for reconciliation
task flux:status      # watch until everything reports Ready: True
flux get all -A       # detailed view

# === VERIFY ===
task k3s:status       # cluster health
task flux:verify      # flux check + get all -A
```

### Ongoing Operations

Any change to a workload (image tag, replicas, env var, ingress host, middleware, etc.)
is now a git commit under `kubernetes/`:

```bash
# Edit the YAML (e.g., kubernetes/apps/authentik/release.yaml), then:
git add kubernetes/apps/authentik/release.yaml
git commit -m "Authentik: increase worker replicas"
git push

# The GitLab agent's Flux module triggers reconciliation on push (fallback:
# ~1-minute poll). Force a sync manually:
task flux:reconcile

# For fast local iteration without committing:
task flux:dev-apply -- kubernetes/apps/authentik
# (Flux will revert to the committed state on its next reconcile.)
```

For version bumps that flow through `all.yml`:

```bash
task maintenance:update-version SERVICE=authentik
task flux:sync-versions    # regenerates kubernetes/infrastructure/sources/versions-configmap.yaml
git add ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/sources/versions-configmap.yaml
git commit -m "Bump Authentik"
git push
```

See `docs/29-flux-operations.md` for the full operator guide (bootstrap, adopt
Helm releases, rotate secrets, add an app, suspend/resume, troubleshoot).

---

## Idempotency and Upgrades

**All k3s Ansible tasks are idempotent** — safe to re-run at any time without side effects.

### Ansible Tasks (`task k3s:deploy`)

| Operation | Idempotency Mechanism |
|-----------|----------------------|
| K3s server install | Version check - installs/upgrades when version differs |
| K3s agent install | Version check - installs/upgrades when version differs |
| Config files | Template tasks with notify - only restarts on change |
| Node labels/taints | `--overwrite` flag - safe to re-apply |
| kube-vip manifest | Template task - only updates if changed |
| Package installation | `state: present` - no-op if installed |

### K3s Version Upgrades

To upgrade k3s to a new version:

1. **Update the version** in `ansible/inventories/prod/group_vars/all.yml`:
   ```yaml
   k3s_version: "v<new>+k3s1"  # New version (must be >= the current pin in all.yml)
   ```

2. **Run the node upgrade task** - it drains and upgrades each node in turn:
   ```bash
   task maintenance:update-k3s-nodes
   ```

### Flux-Managed Workloads

| Operation | Mechanism |
|-----------|-----------|
| HelmReleases | helm-controller via `flux reconcile` (every 30 minutes by default) |
| Kustomizations | kustomize-controller via `flux reconcile` (every 10 minutes by default) |
| ExternalSecrets | ESO polls 1Password (24h refresh by default) or on-demand via `task flux:refresh-secret -- <ns>/<name>` |
| Substitutions | Flux re-renders every reconcile using the `cluster-versions` ConfigMap |

Flux is itself idempotent — safe to run `task flux:reconcile` anytime.

---

## Overview

Cluster topology: 9 nodes — 3 servers (etcd quorum, .222/.223/.227) + 6 agents
(.202-.207). The canonical node-by-node list with IPs, host placement, and
roles lives in `docs/01-overview.md`; `ansible/inventories/prod/hosts.yml` is
the machine-readable source.

Cluster features:
- **kube-vip** - API VIP at 192.168.0.161
- **MetalLB** - LoadBalancer IPs; pools are defined in
  `kubernetes/infrastructure/configs/metallb-ip-pools.yaml` (.100 public, .101
  internal, .99 wg-easy UDP endpoint)
- **Traefik** - Ingress controller
- **external-dns** - Automatic Cloudflare DNS management

## Prerequisites

### 1. 1Password Setup

Create the K3s Cluster Token in 1Password:

```bash
# Generate a secure random token
openssl rand -base64 32

# Store in 1Password
# Vault: Homelab
# Item Name: K3s Cluster Token
# Field: credential
# Value: <generated token>
```

#### Agent token (lower-privilege worker join)

Agents join with a **separate, lower-privilege worker token** rather than the
cluster (server) token, which would also grant control-plane join and cluster-CA
access. The **K3s Agent Token** 1Password item is therefore **required** on a
rebuild — `K3S_AGENT_TOKEN: op://Homelab/K3s Agent Token/credential` is wired into
`task k3s:deploy`, the `maintenance-k3s-provision` CI job, and the node-upgrade
path (`maintenance:update-k3s-nodes` + its CI job, so a binary upgrade keeps
agents on the lower-privilege token).

```bash
openssl rand -base64 32
# Store in 1Password — Vault: Homelab, Item: "K3s Agent Token", Field: credential
```

The server config advertises it via `agent-token`, and the role reconciles each
agent's `K3S_TOKEN` on the next deploy; the token is only used at join time, so
existing nodes stay registered through a change. If `K3S_AGENT_TOKEN` is unset
the role falls back to the cluster token, but every wired call site passes it —
a missing item hard-fails `op run`.

**Rollout order:** deploy servers first (they advertise the agent token via a
serial control-plane restart), then agents; verify all nodes return to `Ready`
before proceeding.

### 2. DNS Configuration

DNS records are codified in `ansible/inventories/prod/group_vars/dns.yml` and deployed automatically via AdGuard Home API:

```bash
# Deploy DNS configuration (dns-02 syncs automatically)
task dns:deploy -- --limit dns-01
```

This configures:
- Forward DNS (A records) for all k3s nodes and VIPs
- Reverse DNS (PTR records) for proper name resolution
- Internal service routes via Traefik VIP (.101)

**Note**: See `docs/08-dns.md` for the complete DNS record list. Records sync from dns-01 to dns-02 automatically every 5 minutes via adguardhome-sync.

### 3. Verify Prerequisites

```bash
# Verify 1Password authentication
task op:check

# Verify Ansible connectivity to Proxmox hosts
task ansible:ping -- --limit proxmox
```

## Phase 1: VM Provisioning

### Step 1: Provision VMs

```bash
# Provision all 9 k3s VMs (3 servers + 6 agents)
task k3s:provision-vms

# This will:
# - Download Debian 13 cloud image
# - Create server VMs: k3s-srv-nas-01 (.222), k3s-srv-laptop-01 (.223), k3s-srv-prec-01 (.227)
# - Create agent VMs: k3s-agt-nas-01 (.202), k3s-agt-laptop-01 (.203),
#   k3s-agt-opt-01 (.204), k3s-agt-opt-02 (.205), k3s-agt-opt-03 (.206), k3s-agt-prec-01 (.207)
# - Configure cloud-init with SSH keys
# - Start VMs and wait for SSH
# To provision a subset: task k3s:provision-vms -- --limit k3s-srv-nas-01,k3s-agt-nas-01
```

### Step 2: Verify VM Provisioning

```bash
# Test SSH access to all VMs
ssh eric@192.168.0.222  # k3s-srv-nas-01
ssh eric@192.168.0.202  # k3s-agt-nas-01
ssh eric@192.168.0.206  # k3s-agt-opt-03

# Verify VMs can reach the internet
ssh eric@192.168.0.222 "ping -c 3 1.1.1.1"

# Verify DNS resolution
ssh eric@192.168.0.222 "dig google.com"
```

## Phase 2: K3s Cluster Deployment

### Step 3: Deploy K3s Cluster

```bash
# Deploy full k3s cluster
task k3s:deploy

# This will:
# 1. Deploy base configuration (base, qol, postfix, tailscale)
# 2. Deploy k3s servers (k3s-srv-nas-01, k3s-srv-laptop-01, k3s-srv-prec-01) with kube-vip
# 3. Deploy k3s agents (k3s-agt-nas-01, k3s-agt-laptop-01, k3s-agt-opt-01/02/03, k3s-agt-prec-01)
# 4. Apply node labels and taints
# 5. Verify cluster health
```

### Step 4: Get Kubeconfig

```bash
# Fetch kubeconfig from cluster
task k3s:kubeconfig

# Set KUBECONFIG environment variable
export KUBECONFIG=~/.kube/config-k3s

# Verify cluster access
kubectl get nodes
kubectl get nodes -o wide
```

### Step 5: Verify Cluster

```bash
# Check all nodes are Ready
kubectl get nodes

# Check kube-vip VIP is active
ping -c 3 192.168.0.161

# Check system pods
kubectl get pods -A

# Verify kube-vip pod
kubectl get pods -n kube-system | grep kube-vip

# Check node labels
kubectl get nodes --show-labels

# Verify taints on NAS node
kubectl describe node k3s-agt-nas-01 | grep -A 5 Taints
```

> **GPU node:** `k3s-agt-prec-01` is additionally a GPU node — `k3s_gpu_node:
> true` in `hosts.yml` applies the `esweiss.com/gpu=nvidia` label + compute taint
> and `tasks/gpu.yml` installs the NVIDIA driver + container toolkit. The GPU
> passthrough + driver window is a separate supervised procedure — see
> [docs/43-gpu-passthrough.md](43-gpu-passthrough.md).

## Phase 3: Flux Bootstrap + Platform Reconciliation

Once the k3s cluster is up and `kubectl get nodes` shows all 9 nodes Ready, bootstrap
Flux. Flux then reconciles every platform component and every application from this repo
— there are no per-component deploy tasks.

### Step 6: Create the Bootstrap Secrets (One Time)

Flux's ExternalSecret ClusterSecretStore uses the 1Password Connect provider, which needs
two bootstrap Secrets: `op-credentials` (the Connect server's credentials file) and
`onepassword-connect-token` (the Connect access token). These are the *only* Secrets
ever created by `kubectl create secret`.

```bash
# Print instructions for creating the bootstrap secrets
task flux:bootstrap-onepassword

# After `op connect server create` has produced ./1password-credentials.json,
# create the token + both Secrets in one step:
task flux:bootstrap-onepassword-apply
#   op-credentials            — from 1password-credentials.json
#   onepassword-connect-token — from Connect access token (minted via
#                               `op connect token create`; no vault item exists)
```

### Step 7: Bootstrap Flux

```bash
task flux:bootstrap
```

This:
1. Reads `op://Homelab/Flux GitLab PAT/credential` (Maintainer role, scopes
   `api,read_repository,write_repository`).
2. Runs `flux bootstrap gitlab` against this repo and path
   `kubernetes/clusters/weisssrv`.
3. Installs the Flux controllers (source, kustomize, helm, notification) into
   `flux-system`.
4. Commits `flux-system/gotk-components.yaml`, `gotk-sync.yaml`,
   `kustomization.yaml` to the current branch.
5. Creates the `GitRepository` and top-level `Kustomization` CRs that watch this repo.

After bootstrap, Flux reconciles six chained Kustomizations in `dependsOn` order:
`infrastructure-sources` → `infrastructure-crds` →
`infrastructure-controllers` → `infrastructure-configs`, which fans out to
`infrastructure-observability` and `apps` in parallel (apps deliberately do not
gate on observability health). A seventh stage,
`infrastructure-metrics-server`, sits **off** that chain — it dependsOn
`sources` only, so a metrics-server problem cannot stall the platform (see the
header of `kubernetes/clusters/weisssrv/infrastructure-metrics-server.yaml` and
docs/33 § metrics-server). The
canonical description of each stage's role and membership lives in
`docs/29-flux-operations.md`; each stage's `kustomization.yaml` under
`kubernetes/infrastructure/` and `kubernetes/apps/` is the current set.

### Step 8: Push-Triggered Reconciliation (Already Live)

Push-triggered reconciliation needs no separate setup: the GitLab agent
(`kubernetes/apps/gitlab-agent/`, deployed by Flux in the `apps` stage) runs
its Flux module, which triggers an immediate reconcile of the `GitRepository`
on every push to the watched project. The 1-minute GitRepository poll remains
as the fallback while the agent is down or before the `apps` stage first
converges. No GitLab webhook or Flux `Receiver` is required.

### Step 9: Verify

Watch reconciliation until everything is Ready:

```bash
task flux:status
# or
flux get all -A
```

Expected state (Flux Kustomization stages reconcile in dependsOn order):

- `flux-system` `GitRepository` — Ready
- `infrastructure-sources` `Kustomization` — Ready
- `infrastructure-crds` `Kustomization` — Ready (after sources; installs the
  prometheus-operator CRDs, `wait: true`)
- `infrastructure-controllers` `Kustomization` — Ready (after sources + crds)
- `infrastructure-configs` `Kustomization` — Ready (after controllers)
- `infrastructure-observability` `Kustomization` — Ready (after configs)
- `apps` `Kustomization` — Ready (after infrastructure-configs)
- `infrastructure-metrics-server` `Kustomization` — Ready (after sources only —
  off the dependsOn chain)
- Every `HelmRelease` — Ready
- Every `ExternalSecret` — `SecretSynced: True`
- Every `IngressRoute` resolved and responding

Verify key endpoints:

```bash
# LoadBalancer VIPs
kubectl get svc -n traefik    # EXTERNAL-IP 192.168.0.100
curl -k http://192.168.0.100  # Traefik 404
curl -k http://192.168.0.101  # Traefik 404

# CoreDNS
kubectl get pods -n kube-system -l k8s-app=kube-dns

# cert-manager issued certificates
kubectl get certificate -A    # all Ready: True

# Authentik
kubectl get pods -n authentik
curl -k https://auth.esweiss.com  # login page
```

## Verification

### Quick Status Check

```bash
# Cluster health (nodes, etcd, kube-vip, kubelet readiness)
task k3s:status

# Flux health (GitRepository, Kustomizations, HelmReleases, ExternalSecrets)
task flux:status
```

### Detailed Cluster Health

```bash
# Check all nodes
kubectl get nodes -o wide

# Check all pods
kubectl get pods -A

# Check services
kubectl get svc -A

# Check MetalLB IP pools
kubectl get ipaddresspool -n metallb-system

# Check Traefik LoadBalancer
kubectl get svc traefik -n traefik

# Check certificates
kubectl get certificate -A
```

### Network Connectivity

```bash
# Test API VIP
curl -k https://192.168.0.161:6443

# Test Traefik LoadBalancer
curl http://192.168.0.100

# Ping from external host
ping 192.168.0.161  # API VIP
ping 192.168.0.100  # Traefik LoadBalancer
```

### DNS

```bash
# Internal DNS (AdGuard Home)
dig k3s-srv-nas-01.esweiss.com @192.168.0.150
dig k3s.esweiss.com @192.168.0.150

# External DNS (verify external-dns logs)
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns -f
```

## Post-Deployment

### Tailscale (nothing to do here)

Tailscale runs on the **Proxmox hosts only** — they are the subnet routers that
advertise `192.168.0.0/24` to the tailnet, so the k3s VMs are reachable over
Tailscale without running the daemon themselves. Do not run `tailscale up
--accept-routes` on a subnet router: `group_vars/proxmox.yml` and
`docs/05-tailscale.md` forbid accepting routes on these hosts.

### Collect Cluster State

```bash
# Collect updated cluster state
task collect-state
```

## Flannel backend

`group_vars/k3s.yml` sets `k3s_flannel_backend: wireguard-native`, which the
server config template renders as `flannel-backend:` in
`/etc/rancher/k3s/config.yaml`. All nine nodes carry a `flannel-wg` interface, so
pod-to-pod packets across nodes are WireGuard-encrypted (UDP/51820, allowed
between `k3s_nodes` by `sg-k3s-core`).

**Changing the backend on a live cluster is disruptive** — pods on each node lose
connectivity while flanneld reconfigures. If it is ever necessary: flip the
variable, then `task k3s:deploy -- --limit <host>` one server at a time (watch for
`Using backend type:` in `journalctl -u k3s`), then the remaining servers and
agents. Two things bite:

- Verify with `sudo wg show` on every node, not just the first — a partial
  migration is silent.
- The retired `flannel.1` device lingers with stale per-subnet `/24` routes that
  out-rank the new `/16 dev flannel-wg` route, keeping some node pairs on
  unmanaged VXLAN. Clean it up cluster-wide once every node has migrated:

  ```bash
  ansible k3s -m ansible.builtin.shell \
    -a 'ip link del flannel.1 2>/dev/null; ip route | grep -c flannel.1'
  # every host should report 0
  ```

A rollback to `vxlan` also needs the UDP/8472 rule re-added to `sg-k3s-core` in
`cluster.fw.j2` and the firewall redeployed.


## kube-apiserver audit logging

`group_vars/k3s.yml` sets `k3s_audit_enabled: true`. k3s ships apiserver audit
logging **off**, so before this the cluster kept no record of who read a Secret
or granted themselves a ClusterRole. The k3s role renders a `kube-apiserver-arg`
block into `/etc/rancher/k3s/config.yaml` on the three servers and writes the
policy it points at.

| Path (per server) | What |
|---|---|
| `/var/lib/rancher/k3s/server/audit-policy.yaml` | the `audit.k8s.io/v1` Policy, root-owned `0600` |
| `/var/lib/rancher/k3s/server/logs/audit.log` | the log itself, in a root-only `0700` directory |

**What is captured.** Rules are evaluated in order, first match wins, and there
is deliberately **no catch-all** — anything matching no rule is not logged:

- `Metadata` on core `secrets`, `configmaps`, `serviceaccounts` — who touched
  which credential object, as whom. Metadata, never the body: a body-level rule
  would write Secret contents into a plaintext file on disk.
- `RequestResponse` on `rbac.authorization.k8s.io` **writes** (create/update/
  patch/delete/deletecollection) — the full before/after of any privilege grant.
- `Metadata` on RBAC reads — enumeration is worth recording, but a
  `RequestResponse` on `list clusterroles` would dump the whole RBAC tree on
  every controller resync.
- `None` (first, so they win) on health/version/metrics/openapi endpoints and on
  `kube-system` leader-election leases — pure churn.

**Rotation and retention.** The apiserver rotates the file itself: 100 MB per
file, 10 backups kept, discarded after 30 days — worst case ~1.1 GB per server.
The log stays on that server's local disk; `alloy_host` reads journald only, so
nothing ships it to Loki (see
[docs/31-observability.md](31-observability.md#kube-apiserver-audit-log)). Read it
on the node:

```bash
ssh k3s-srv-nas-01 'sudo tail -n 200 /var/lib/rancher/k3s/server/logs/audit.log' | \
  jq -r '[.requestReceivedTimestamp, .user.username, .verb,
          .objectRef.resource, .objectRef.namespace, .objectRef.name] | @tsv'
```

**Deploy implication.** The policy file and the config block are both read once
at apiserver startup, so enabling this — or later editing the policy — notifies
`Restart k3s` + `Wait for k3s API healthy`: a rolling control-plane bounce with
an API-VIP failover per server. The `k3s_servers` play is `serial: 1`, so they
go one at a time. Run it in a deliberate window with a healthy etcd quorum, and
never with a server node down. The role writes the policy **before** the config
that references it, because an apiserver with a missing or unparseable
`--audit-policy-file` exits at startup; it also asserts the policy variable is a
well-formed `audit/v1` Policy first, so a bad override fails the play rather than
crash-looping the control plane.

Turning it off again is another rolling restart, and leaves the unreferenced
policy file on disk (inert).


## Troubleshooting

### VMs Failed to Provision

```bash
# Check Proxmox logs on host
ssh pve-nas-01 "journalctl -u pve-cluster -f"

# Manually check VM status
ssh pve-nas-01 "qm status 222"

# Check cloud-init logs on VM
ssh eric@192.168.0.222 "sudo cat /var/log/cloud-init-output.log"
```

### K3s Installation Failed

```bash
# Check k3s service on server
ssh eric@192.168.0.222 "sudo systemctl status k3s"
ssh eric@192.168.0.222 "sudo journalctl -u k3s -n 50"

# Check k3s-agent service on agents
ssh eric@192.168.0.202 "sudo systemctl status k3s-agent"
ssh eric@192.168.0.202 "sudo journalctl -u k3s-agent -n 50"

# Check kube-vip manifest
ssh eric@192.168.0.222 "sudo cat /var/lib/rancher/k3s/server/manifests/kube-vip.yaml"
```

### kube-vip Not Assigning VIP

```bash
# Check kube-vip pod logs
kubectl logs -n kube-system -l app=kube-vip

# Check kube-vip pod status
kubectl get pods -n kube-system | grep kube-vip

# Manually ping VIP
ping 192.168.0.161
```

### MetalLB Not Assigning IPs

```bash
# Check MetalLB controller logs
kubectl logs -n metallb-system -l app.kubernetes.io/component=controller

# Check MetalLB speaker logs
kubectl logs -n metallb-system -l app.kubernetes.io/component=speaker

# Verify IP pools
kubectl get ipaddresspool -n metallb-system -o yaml

# Check service events
kubectl describe svc traefik -n traefik
```

### Traefik Not Accessible

```bash
# Check Traefik pods
kubectl get pods -n traefik

# Check Traefik logs
kubectl logs -n traefik -l app.kubernetes.io/name=traefik

# Check service
kubectl get svc traefik -n traefik -o yaml

# Verify LoadBalancer IP assignment
kubectl describe svc traefik -n traefik
```

### CI molecule flake: "Too many open files" (inotify exhaustion)

Applies to any Molecule job on these nodes — the `integration-tests` matrix here
and the role-scenario matrix in `weisssrv-lib`, which runs on the same runner.

**Symptom.** A Molecule job fails at the prepare step:

```
TASK [Wait for systemd to be ready]
fatal: [<role>-test]: FAILED! => {"attempts": 3, ...,
  "stderr": "Error response from daemon: Container <id> is not running"}
CRITICAL Ansible return code was 2 ... prepare-common.yml
```

The container is created and passes the creation-wait, then dies within ~7s.
The failure is `script_failure` (rc 2), which the job's `retry` policy
deliberately does **not** auto-retry, so it fails the pipeline and has
historically needed a manual "retry job". It is intermittent, hits at any
concurrency (even a single job), and clusters on **one node at a time**.

**Root cause.** systemd PID 1 inside the test container can't allocate its
control-group inotify watch:

```
Failed to create control group inotify object: Too many open files
Failed to allocate manager object: Too many open files
[!!!!!!] Failed to allocate manager object.
Exiting PID 1...
```

`fs.inotify.max_user_instances` is a **per-UID, host-global** limit (the runner
pods share the host user namespace). Its kernel default of **128** is drawn down
by every uid-0 container on the node — kubelet, containerd, Flux, Prometheus,
Alloy, and each pod's root process — all heavy inotify users. On a
container-dense node the pool is exhausted, so the *next* systemd-in-Docker
molecule container's `inotify_init()` returns `EMFILE` and PID 1 exits before
molecule's prepare can reach it. Whichever node is nearest its cap fails, which
is why it appears to roam across nodes between pipelines.

**Fix (codified).** The `k3s` role raises the ceilings on every node via
`/etc/sysctl.d/90-k3s-inotify.conf` (`k3s_inotify_*` in the role defaults):
`fs.inotify.max_user_instances = 8192`, `fs.inotify.max_user_watches =
1048576`. Redeploy with `task k3s:deploy` (or a role-scoped run) to apply.

**Diagnose / verify limits per node:**

```bash
# Current ceiling on a node (128 = unpatched default; 8192 = fixed):
ssh <k3s-node> cat /proc/sys/fs/inotify/max_user_instances

# Apply on all nodes without a full role redeploy. max_user_instances is the
# exhausted limit that fixes the symptom (inotify_init -> EMFILE); max_user_watches
# is hardening the role also raises — run the second invocation for parity. Writing
# sysctl_file= persists the same drop-in the role manages, so this survives reboot;
# `task k3s:deploy` remains the canonical owner.
ansible k3s_servers:k3s_agents -b -m ansible.posix.sysctl \
  -a "name=fs.inotify.max_user_instances value=8192 sysctl_file=/etc/sysctl.d/90-k3s-inotify.conf sysctl_set=true reload=true"
ansible k3s_servers:k3s_agents -b -m ansible.posix.sysctl \
  -a "name=fs.inotify.max_user_watches value=1048576 sysctl_file=/etc/sysctl.d/90-k3s-inotify.conf sysctl_set=true reload=true"
```

## Rebuilding a Server Node

To rebuild a lost or corrupted server VM (including the first server) while
the rest of the cluster is healthy:

```bash
# 1. Wipe/recreate the VM (destroys the old guest if it still exists).
task k3s:provision-vms -- --limit <server>   # e.g. k3s-srv-nas-01

# 2. Re-run the deploy scoped to that server — it joins the existing cluster.
task k3s:deploy -- --limit <server>

# 3. Verify it rejoins the etcd quorum.
task k3s:status
```

The role guards against the classic first-server rebuild footgun:
`k3s_is_first_server` is pinned in `hosts.yml`, so a wiped first server would
otherwise render `cluster-init: true` and bootstrap a NEW single-node etcd
cluster (fresh CA) while the surviving servers still hold the old quorum. The
role checks for local etcd data (`/var/lib/rancher/k3s/server/db/etcd`) and
probes the API VIP; if the node has no etcd data but the VIP already serves a
cluster, it renders the join stanza instead. `cluster-init` is only rendered
on a genuine first bootstrap (no local data AND a dead VIP), so the scoped
deploy above is safe for any server. If ALL three servers are lost, that is a
cluster restore, not a node rebuild — see `docs/17-disaster-recovery.md`.

## Expanding Beyond 3 Server Nodes

The current 3-server cluster (k3s-srv-nas-01, k3s-srv-laptop-01, k3s-srv-prec-01) with 6 agents provides etcd quorum tolerating 1 server failure. For full HA tolerating 2 server failures, expand to 5 server nodes.

### Candidate nodes for servers #4 and #5

The cluster uses a split IP scheme: servers in the .22X range, agents in the .20X
range. `hosts.yml` is the source of truth for the current fleet; the two
addresses below are the reserved slots a 3 → 5 server expansion would claim.

| Node | IP | VMID | Proxmox Host | Purpose |
|------|-----|------|--------------|---------|
| k3s-srv-opt-01 | 192.168.0.224 | 224 | pve-opt-01 | HA server #4 |
| k3s-srv-opt-02 | 192.168.0.225 | 225 | pve-opt-02 | HA server #5 |

Both hosts already carry an agent, so an expansion means adding a **second**
guest on each — check the memory budget for those 14-15 GiB hosts first
(docs/06 § ARC).

### Step 1: Add New Server Nodes to Inventory

Edit `ansible/inventories/prod/hosts.yml` and add entries for the new server nodes under `k3s_servers`. Set `k3s_is_first_server: false` on all new servers.

### Step 2: Provision and Deploy

```bash
# Provision VMs for new servers only
task k3s:provision-vms -- --limit <new-server-hosts>

# Deploy k3s to new servers (joins existing cluster)
task k3s:deploy -- --limit <new-server-hosts>
```

### Step 3: Verify Expanded Cluster

```bash
# All servers should show as control-plane with etcd role
kubectl get nodes
# Current: 3 servers (srv-nas-01, srv-laptop-01, srv-prec-01) + 6 agents

# Verify etcd cluster health
kubectl get pods -n kube-system | grep etcd

# Test API VIP failover (optional)
# SSH to current VIP holder, reboot, verify VIP moves to another server
```

### HA Considerations

- **Odd number of servers**: etcd requires odd number for quorum (3 or 5)
- **VIP failover**: kube-vip automatically handles API VIP failover
- **Rolling updates**: Deploy one server at a time using `serial: 1`
- **etcd backup**: Consider backing up etcd before expansion
- **5-node HA**: For 2-failure tolerance, add 2 more servers at .224 and .225

## Next Steps

1. **Add an application** - follow `docs/29-flux-operations.md` § Adding a New
   App (not every app is Flux-managed: Nextcloud and Immich are docker-compose
   stacks on dedicated VMs — docs/35, docs/36)
2. **Backups** - already covered by the archive/restic/vzdump chain and the
   per-app logical dumps (docs/17, docs/42); Velero is deliberately not used

## Related documentation

- `docs/29-flux-operations.md` - Flux operator guide: bootstrap, adopt, rotate secrets, add an app, suspend, rollback
- `docs/30-multi-repo-onboarding.md` - Adding external repos that deploy into this cluster
- `docs/31-observability.md` - Observability stack (Prometheus, Grafana, Loki, Alloy)
- `docs/43-gpu-passthrough.md` - GPU node (pve-prec-01): VFIO passthrough, NVIDIA driver/toolkit, DCGM
- `kubernetes/README.md` - Top-level k8s layout guide (Flux-aware)
- `kubernetes/infrastructure/` - Platform components (sources, crds, controllers, configs, observability)
- `kubernetes/apps/` - Applications (authentik, download-clients, hermes, hindsight, homarr, recipes, gitlab-*, registry-cache, tailnet-dns, vm-ingress, wg-easy)
