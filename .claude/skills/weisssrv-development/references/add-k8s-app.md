# Add a Kubernetes app (`kubernetes/apps/<app>/`)

Canonical procedure: `docs/29-flux-operations.md` → **Adding a New App**. Autoscaling
tiers: `docs/33-autoscaling.md`. This file is the condensed checklist + the
gotchas that bite. Copy an existing app rather than inventing a shape.

## Exemplars

- **Single app, SSO, DB on zvol**: `kubernetes/apps/authentik/` — the reference
  layout (`namespace.yaml`, `release.yaml`, `externalsecret.yaml`,
  `ingress-route.yaml`, `middleware.yaml`, `certificate.yaml`, `storage.yaml`,
  `networkpolicy.yaml`, `vpa.yaml`, `kustomization.yaml`, `README.md`).
- **Multi-app namespace + HPA + OIDC**: `kubernetes/apps/recipes/` (Mealie +
  Bar Assistant; note `hpa.yaml`).
- **VPN-gatewayed stack**: `kubernetes/apps/download-clients/` (namespace
  `downloads`; Gluetun killswitch, privileged pods with NET_ADMIN justified
  in-comment).

## Checklist

1. **Directory** under `kubernetes/apps/<app>/`, registered in
   `kubernetes/apps/kustomization.yaml` (Kustomize does not auto-discover).
2. **Namespace** with Pod Security Admission labels — `enforce baseline` unless a
   capability (NET_ADMIN etc.) forces `privileged`, in which case justify it with
   an inline comment and keep `warn`/`audit` at `restricted`.
3. **NetworkPolicy**: pull in the shared `kubernetes/components/netpol-baseline`
   component, then an explicit `default-deny-egress` with scoped allows. Standard
   allows: kube-dns; apiserver = the **node IPs** `192.168.0.222/223/227:6443`;
   smtp `192.168.0.151:587` if the app mails; public HTTPS as `0.0.0.0/0`
   **except** the private ranges `[10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16,
   100.64.0.0/10, 169.254.0.0/16]`. Add a **scrape-allow** from the observability
   namespace. Copy the egress shape from `authentik/networkpolicy.yaml` or
   `recipes/networkpolicy.yaml`.
4. **VPA** (`vpa.yaml`, required): `Initial` for stateless; `Off` for
   DB/stateful; memory-only when an HPA owns CPU. See `docs/33-autoscaling.md`.
5. **Certificate** per host (`certificate.yaml`): `issuerRef` ClusterIssuer
   `letsencrypt-prod`, `renewBefore: 720h`.
6. **IngressRoutes**: public → `external-dns.alpha.kubernetes.io/target:
   ericsweiss.com` annotation + `hsts-header` middleware; internal →
   `lan-tailscale-only` + `hsts-header`. Reference platform middlewares from the
   `traefik` namespace.
7. **Secrets** (`externalsecret.yaml`): store `onepassword-homelab`,
   `remoteRef.key` = 1P item title, `remoteRef.property` = field. Add any new 1P
   item to `docs/15-credential-rotation.md`.
8. **Version pin**: add `${<app>_version}` to `group_vars/all.yml`, run
   `task flux:sync-versions`, commit both files. New Helm chart → add a
   `HelmRepository` under `kubernetes/infrastructure/sources/`.
9. **Observability** (mandatory): ServiceMonitor/PodMonitor in the right place +
   scrape NetworkPolicy; a down/stale alert rule added to the appropriate
   `homelab.*` group in
   `kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml`
   (match existing `for`/`severity`/`runbook` style); a blackbox probe for
   user-facing endpoints where no exporter covers reachability. Grafana dashboard
   only if a good upstream one exists (ConfigMap sidecar via `configMapGenerator`
   in `observability/dashboards/`).
10. **DNS**: internal = `adguard_home_rewrites` entry in `group_vars/dns.yml` (answer
    `192.168.0.101` for Traefik-fronted). External = the external-dns annotation
    above (no Terraform edit) unless it needs a nested subdomain / DNS-only
    record (then `terraform/cloudflare/dns.tf`).

## Storage gotchas

- **NFS PVs mount BY HOSTNAME** `pve-nas-01.esweiss.com` with `xprtsec=tls` —
  never by IP (the `*.esweiss.com` cert has no IP SAN; an IP mount fails the TLS
  handshake). Add `kustomize.toolkit.fluxcd.io/force: "Enabled"` on PVs with
  immutable-field risk.
- **zvol-backed PVs**: `storageClassName: ""` (static binding, no provisioner).
  The zvol itself is created host-side via `vm_additional_disks` — see
  `references/add-vm-app.md` and `docs/06-zfs.md`. `ssd/appdata/*` children are
  auto-enrolled in archive backups; a NEW top-level dataset has to be added to
  `nas_storage_archive_backup_sources` in `host_vars/pve-nas-01.yml`.

## Scheduling

- **NAS-avoid** (default for stateless): preferred `nodeAffinity`
  `esweiss.com/nas DoesNotExist` weight 100 + `nodeSelector esweiss.com/general:
  "true"` (+`esweiss.com/cpu: modern|legacy` if the workload needs it).
- **NAS-pin** (needs NFS-local or AVX): required hostname `k3s-agt-nas-01` +
  toleration `esweiss.com/nas=true:PreferNoSchedule`.

## SSO

- OIDC issuer host is **always** `auth.ericsweiss.com` (external).
- Authentik applications/providers/group-bindings are **codified in
  `terraform/authentik/`** (`applications.tf`, `providers_oauth2.tf`,
  `providers_proxy.tf`, `providers_saml.tf`, `groups.tf`,
  `policy_bindings.tf`). Edit the `.tf` files, review the plan line-by-line,
  then run a supervised `op run -- terraform apply` — **never the UI** (UI-created
  objects drift out of state). Its add-an-app recipe and day-2 ops are in
  `terraform/authentik/README.md` + `docs/40-authentik-terraform.md`. Record the
  provider type, redirect URIs, scopes, and group bindings in the app's docs page
  and the MR deploy plan. (`docs/23-recipes-sso-setup.md` is a legacy manual
  walkthrough superseded by the terraform module.)
