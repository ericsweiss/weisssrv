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
   component, then an explicit `default-deny-egress` with scoped allows —
   kube-dns, the apiserver node IPs, smtp if the app mails, public HTTPS with the
   reserved-CIDR except-list. The addresses and the except-list are written out
   once, in `docs/29-flux-operations.md` § Adding a New App; copy them from there
   (or from a neighbour), never from memory. Add a **scrape-allow** from the
   observability namespace. Copy the egress shape from `authentik/networkpolicy.yaml` or
   `recipes/networkpolicy.yaml`. If an allow really is namespace-wide (every pod
   gets it), take the shared component instead — `netpol-egress-dns`,
   `netpol-egress-apiserver`, `netpol-egress-public`; each ships one
   `podSelector: {}` policy, so it is wrong for a per-app rule. Addresses here
   stay LITERAL: the parity gate reads NetworkPolicies straight from git.
4. **VPA** (`vpa.yaml`, required): `Initial` for stateless; `Off` for
   DB/stateful; memory-only when an HPA owns CPU. See `docs/33-autoscaling.md`.
5. **Certificate** per host (`certificate.yaml`): issuer and `renewBefore` per
   docs/29 § Adding a New App; copy a neighbour's `certificate.yaml`.
6. **IngressRoutes**: public → `external-dns.alpha.kubernetes.io/target:
   ${cluster_external_domain}` annotation + `hsts-header` middleware; internal →
   `lan-tailscale-only` + `hsts-header`. Reference platform middlewares from the
   `traefik` namespace.
7. **Secrets** (`externalsecret.yaml`): the ClusterSecretStore name and the
   reference format are in docs/29 § Adding a New App — `remoteRef.key` is the 1P
   item TITLE and `remoteRef.property` the field, which is the part people get
   wrong. Add any new 1P item to `docs/15-credential-rotation.md`.
8. **Version pin**: add `${<app>_version}` to `group_vars/all.yml`, run
   `task flux:sync-versions`, commit both files. New Helm chart → add a
   `HelmRepository` under `kubernetes/infrastructure/sources/`.
9. **Observability** (mandatory): ServiceMonitor/PodMonitor in the right place +
   scrape NetworkPolicy; a down/stale alert rule (§ Alert rules below); a
   blackbox probe for user-facing endpoints where no exporter covers
   reachability. Grafana dashboard only if a good upstream one exists (ConfigMap
   sidecar via `configMapGenerator` in `observability/dashboards/`).
10. **DNS**: internal = `adguard_home_rewrites` entry in `group_vars/dns.yml` (answer
    `192.168.0.101` for Traefik-fronted). External = the external-dns annotation
    above (no Terraform edit) unless it needs a nested subdomain / DNS-only
    record — then one entry in `local.dns_records` in `terraform/cloudflare/dns.tf`
    (`protected = true`; the resources themselves live in the library module).
11. **Docs**: a `docs/NN-*.md` deployment page (next free number) + its row in the
    `README.md` docs index and the application table in `CLAUDE.md`, the per-app
    `kubernetes/apps/<app>/README.md` (required by
    `kubernetes/apps/kustomization.yaml`), and `docs/16-next-steps.md` updated
    (mark done / remove from planned).

## Alert rules

Custom alerts are **standalone `PrometheusRule` CRs** under
`kubernetes/infrastructure/observability/rules/` — one file per area, each
holding `spec.groups[].name: homelab.<area>`, all registered in
`rules/kustomization.yaml`. The kube-prometheus-stack HelmRelease carries no
`additionalPrometheusRules`; it only *disables* upstream default rules.

For a new alert:

1. Add it to the existing `rules/<area>.yaml` whose `homelab.<area>` group it
   belongs to, or add a new file **and** register it in
   `rules/kustomization.yaml`.
2. `annotations.runbook_url` is **required** — point at the `docs/` section that
   says what to do, matching the `for`/`severity`/`summary` style of the
   neighbouring rules.
3. Ship a **promtool unit test** in `scripts/prometheus-rule-tests/` (one
   `*.test.yaml` per area, mirroring the rule file) and prove it with
   `task lint:prometheus-config` — that task extracts the live corpus via
   `scripts/extract-prometheus-config.py` and runs promtool + amtool over it.
   `scripts/test_prometheus_rule_coverage.py` fails if a new alert has neither a
   unit test nor an entry in its `UNTESTED` dict.

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
- **GPU**: `nodeSelector esweiss.com/gpu: "nvidia"` plus an
  `nvidia.com/gpu` resource request (the device plugin is time-sliced, so a
  request of 1 is a slice, not exclusivity) — `docs/43-gpu-passthrough.md`.
- **Priority**: leave `priorityClassName` unset for an ordinary app — priority 0
  already outranks CI job pods. The two repo classes in
  `kubernetes/infrastructure/sources/priorityclasses.yaml` are `platform` (must
  win against apps and CI) and `ci-jobs` (negative, never preempts; set via
  `[runners.kubernetes] priority_class_name` in the runner TOMLs).

## SSO

- OIDC issuer host is **always** `auth.ericsweiss.com` (external).
- Authentik applications/providers/group-bindings are **codified in
  `terraform/authentik/`** (`applications.tf`, `providers_oauth2.tf`,
  `providers_proxy.tf`, `providers_saml.tf`, `groups.tf`,
  `policy_bindings.tf`) — each file is a `locals` MAP fed to the weisssrv-lib
  `authentik-sso` module, not a set of resources, so adding an app is a map
  entry. Edit the `.tf` files, review the plan line-by-line,
  then run a supervised `op run -- terraform apply` — **never the UI** (UI-created
  objects drift out of state). Its add-an-app recipe and day-2 ops are in
  `terraform/authentik/README.md` + `docs/40-authentik-terraform.md`. Record the
  provider type, redirect URIs, scopes, and group bindings in the app's docs page
  and the MR deploy plan. (`docs/23-recipes-sso-setup.md` records the exact Mealie
  / Bar Assistant provider values — redirect URIs, scopes, env vars — that the
  Terraform maps must agree with.)
