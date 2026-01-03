# Next Steps and TODO

This document tracks remaining work and planned improvements.

## Immediate TODO

### Ansible Parity

Before deploying, verify these items match current state:

- [ ] **SSH Keys**: Add actual authorized_keys to inventory
  ```bash
  # Run on workstation to get public key
  cat ~/.ssh/id_ed25519.pub
  ```

- [ ] **1Password Items**: Verify these items exist in 1Password Homelab vault:
  - `Cloudflare DNS Token` (credential, account_id)
  - `SMTP Relay Gmail` (username, password)
  - `AdGuard Home` (password)
  - `Tailscale Auth Key` (credential)

- [ ] **Test Connectivity**: Before first deploy
  ```bash
  task ansible:ping
  ```

- [ ] **Dry Run**: Check what would change
  ```bash
  task deploy:check
  ```

### Known Issues to Fix

- [ ] **dns-02 AdGuardHome.sig**: File has world-writable permissions
  ```bash
  chmod 644 /opt/AdGuardHome/AdGuardHome.sig
  ```

- [ ] **OpenRGB**: Present but disabled on Proxmox hosts
  - Verify systemd unit exists and is disabled
  - Document config location

## Phase 1: Base Parity Complete

Once Ansible converges with no changes on all hosts, base parity is achieved.

Test with:
```bash
task deploy:all
# Should show "changed=0" for all tasks
```

## Phase 2: K3s Cluster Bootstrap

See [14-post-base-plan.md](14-post-base-plan.md) for detailed plan.

### Bootstrap Order

1. **kube-vip** - API server HA (192.168.0.161)
2. **MetalLB** - LoadBalancer services
3. **Traefik** - Ingress controller
4. **cert-manager** - ACME certificates
5. **external-dns** - Cloudflare DNS automation
6. **Authentik** - SSO/Identity

### Kubernetes Directories

```
kubernetes/
  bootstrap/     # Initial cluster setup
  flux/          # GitOps controller
  apps/          # Application manifests
    base/        # Base resources
    production/  # Production overlays
```

## Phase 3: Applications

After k3s infrastructure is stable:

- [ ] Migrate media services (Plex, *arr stack)
- [ ] Deploy Authentik
- [ ] Set up monitoring (Prometheus/Grafana)
- [ ] Centralized logging
- [ ] Backup solution (Velero or similar)

## Improvements Backlog

### Security

- [ ] Add fail2ban to Proxmox hosts
- [ ] Implement network segmentation with VLANs
- [ ] Set up intrusion detection
- [ ] Regular security audits

### Automation

- [ ] Automatic Proxmox updates
- [ ] ZFS auto-scrub notifications
- [ ] Backup verification testing
- [ ] Uptime monitoring (Uptime Kuma)

### Documentation

- [ ] Add network diagrams
- [ ] Create runbook for common failures
- [ ] Document recovery procedures
- [ ] Add troubleshooting flowcharts

## Commands Reference

```bash
# Deploy everything
task deploy:all

# Deploy specific components
task deploy:base
task deploy:dns
task deploy:storage

# Check what would change
task deploy:check

# Collect current state
task collect-state

# Run linters
task lint

# Terraform operations
task terraform:plan
task terraform:apply
```

## Validation Checklist

After deployment, verify:

- [ ] SSH access works to all hosts
- [ ] DNS resolution works (internal and external)
- [ ] NFS mounts are accessible
- [ ] Samba shares are accessible
- [ ] Mail delivery works (send test email)
- [ ] TLS certificates are valid
- [ ] Proxmox web UI is accessible
- [ ] AdGuard Home web UI is accessible
- [ ] ZFS pool is healthy
