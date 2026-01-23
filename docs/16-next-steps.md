# Next Steps and TODO

This document tracks remaining work and planned improvements.

## Completed Phases

### Phase 1: Base Infrastructure (COMPLETE)

- [x] Proxmox hosts configured (pve-nas-01, pve-opt-03)
- [x] ZFS storage pools configured
- [x] DNS stack (AdGuard Home + Unbound)
- [x] SMTP relay
- [x] Certificates (acme.sh)
- [x] Firewall rules
- [x] Tailscale VPN

### Phase 2: K3s Platform (COMPLETE)

- [x] K3s cluster deployed (3 nodes initial)
- [x] kube-vip for API HA (192.168.0.161)
- [x] MetalLB for LoadBalancer (192.168.0.100-101)
- [x] Traefik ingress controller
- [x] cert-manager with Let's Encrypt
- [x] external-dns for Cloudflare
- [x] Authentik SSO

### Phase 3: Applications (COMPLETE)

- [x] Plex Media Server (LXC on NAS with GPU passthrough)
- [x] Downloads stack deployed:
  - VPN-protected download clients (Gluetun + NZBGet + qBittorrent)
  - Media managers (Sonarr, Radarr, Lidarr, Prowlarr)
  - Plex Watchlist automation (Pulsarr)
  - All services with Authentik SSO protection

## Immediate TODO

### Downloads Stack Configuration

- [ ] **Prowlarr**: Add indexers and connect to *arr apps
- [ ] **Sonarr/Radarr/Lidarr**: Set root folders and quality profiles
- [ ] **NZBGet/qBittorrent**: Configure download paths and credentials
- [ ] **Pulsarr**: Configure Plex token and connect to Sonarr/Radarr
- [ ] **External Auth**: Enable external authentication in *arr apps to eliminate double-login

### Configuration Migration

- [ ] Migrate Windows Sonarr/Radarr databases (optional)
- [ ] Import existing torrent files to qBittorrent (optional)

## Planned Improvements

### Phase 4: GitOps

- [ ] Bootstrap Flux CD
- [ ] Convert manual kubectl applies to GitOps
- [ ] Automated deployments via Git pushes

### Phase 5: Monitoring & Observability

- [ ] Deploy Prometheus/Grafana stack
- [ ] Configure alerting (PagerDuty/Discord/etc)
- [ ] Set up centralized logging (Loki)
- [ ] Uptime monitoring (Uptime Kuma)

### Phase 6: Additional Apps

- [ ] Immich (photo management)
- [ ] Nextcloud (file sync)
- [ ] Overseerr/Jellyseerr (request management)

### Infrastructure Improvements

- [ ] Add fail2ban to Proxmox hosts
- [ ] Network segmentation with VLANs
- [ ] Expand k3s cluster (add 2 more servers for 5-node HA)
- [ ] ZFS auto-scrub notifications
- [ ] Backup verification testing

### Documentation

- [ ] Network topology diagrams
- [ ] Disaster recovery runbook updates
- [ ] Troubleshooting flowcharts

## Commands Reference

```bash
# Base infrastructure
task deploy:all           # Deploy base infrastructure
task deploy:check         # Dry-run

# K3s cluster
task k3s:deploy           # Deploy k3s cluster
task k3s:deploy-workloads # Deploy all platform workloads
task k3s:status           # Show cluster status

# Downloads stack
task downloads:deploy     # Deploy downloads stack
task downloads:status     # Show stack status
task downloads:vpn-status # Check VPN connection
task downloads:vpn-switch # Switch VPN providers
task downloads:logs       # View app logs

# Plex
task deploy:plex          # Deploy Plex LXC

# Maintenance
task maintenance:update-full  # Full system update
task collect-state            # Generate cluster snapshot
```

## Validation Checklist

After deployment, verify:

- [x] SSH access works to all hosts
- [x] DNS resolution works (internal and external)
- [x] NFS mounts are accessible
- [x] Samba shares are accessible
- [x] Mail delivery works
- [x] TLS certificates are valid
- [x] Proxmox web UI is accessible
- [x] AdGuard Home web UI is accessible
- [x] ZFS pool is healthy
- [x] K3s cluster is healthy
- [x] All pods running
- [x] IngressRoutes accessible
- [x] VPN connected for download clients
