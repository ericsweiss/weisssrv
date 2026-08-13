# Flux Operations Guide

Operator-facing guide for running the Flux GitOps system that reconciles every Kubernetes workload in this cluster. Covers the daily loop (commit → reconcile), secret management via External Secrets Operator (ESO) + 1Password, adding new apps, suspending/resuming, rollback, and troubleshooting.

## Table of Contents

1. [Overview](#overview)
2. [Daily Operations](#daily-operations)
3. [Secret Operations](#secret-operations)
4. [Adding a New App](#adding-a-new-app)
5. [Suspending and Resuming](#suspending-and-resuming)
6. [Rollback](#rollback)
7. [Troubleshooting](#troubleshooting)
8. [GitLab outage: pointing Flux at the GitHub mirror](#gitlab-outage-pointing-flux-at-the-github-mirror)
9. [Push-Triggered Reconciliation](#push-triggered-reconciliation)
10. [Upgrading the Flux Distribution](#upgrading-the-flux-distribution)
11. [Appendix: completed one-time migrations](#appendix-completed-one-time-migrations)
12. [Related documentation](#related-documentation)

---

## Overview

### What Flux Does

Flux is the sole deploy mechanism for Kubernetes workloads in this cluster. The contract:

- Source of truth: `kubernetes/` in this repo.
- Cluster state: whatever Flux reconciles from that directory.
- Operator action: edit YAML, commit, push. Flux does the rest.

`kubectl apply` and `helm upgrade` are no longer part of the deploy path. They still work for diagnostics (`kubectl describe`, `kubectl logs`) and emergency break-glass, but Flux will revert anything that drifts from the committed state on the next reconcile.

### Topology

Flux runs in the `flux-system` namespace. Four controllers:

- **source-controller** — polls the Git repository, produces `GitRepository` artifacts for other controllers to read.
- **kustomize-controller** — reconciles `Kustomization` CRs (server-side apply of rendered manifests, with drift correction and prune).
- **helm-controller** — reconciles `HelmRelease` CRs (renders chart + values, installs/upgrades, tracks history).
- **notification-controller** — dispatches events/alerts and hosts webhook `Receiver`s. The one Receiver in the cluster is created **by the GitLab agent**, not by git — see [Push-Triggered Reconciliation](#push-triggered-reconciliation).

Top-level Kustomizations that Flux owns (all in `flux-system` namespace), reconciled in `dependsOn` order. Each stage's `kustomization.yaml` is the authoritative membership list; the summaries below are indicative:

1. `infrastructure-sources` → `kubernetes/infrastructure/sources/` (HelmRepository CRs + the `cluster-versions` and `cluster-config` ConfigMaps). No dependencies. No postBuild substitution (it defines the ConfigMaps and has no placeholders).
2. `infrastructure-crds` → `kubernetes/infrastructure/crds/` (the `prometheus-operator-crds` HelmRelease — the `monitoring.coreos.com` CRDs). `dependsOn: infrastructure-sources`. `wait: true` so controllers do not start until the CRDs are Established. Substitutes the chart version from `cluster-versions` (+ `cluster-config`).
3. `infrastructure-controllers` → `kubernetes/infrastructure/controllers/` (HelmReleases for ESO, 1Password Connect, MetalLB, cert-manager, Traefik, external-dns, VPA, kured, reloader, tailscale-operator). `dependsOn: infrastructure-sources` **and** `infrastructure-crds` (so a controller ServiceMonitor renders against existing CRDs). Substitutes chart versions from `cluster-versions` and cluster identity from `cluster-config`.
4. `infrastructure-configs` → `kubernetes/infrastructure/configs/` (ClusterSecretStore, ClusterIssuer, MetalLB IP pools, wildcard certs, CoreDNS override, DDNS CronJob, shared Cloudflare secrets, Traefik middlewares + TLS options, VPA policies, 1Password Connect certificate + ingress, default-namespace config). `dependsOn: infrastructure-controllers` (CRDs must exist). Substitutes from `cluster-versions` + `cluster-config`.
5. `infrastructure-observability` → `kubernetes/infrastructure/observability/` (kube-prometheus-stack, Loki, Alloy, exporters, service monitors, dashboards, ingress). `dependsOn: infrastructure-configs`. Substitutes from `cluster-versions` + `cluster-config`. kube-prometheus-stack runs with `crds.enabled: false` + `install/upgrade.crds: Skip` — the monitoring CRDs are owned by the `infrastructure-crds` stage, not this chart.
6. `apps` → `kubernetes/apps/` (Authentik, download-clients, hermes, homarr, hindsight, recipes, gitlab-runner, gitlab-runner-privileged, gitlab-runner-reaper, gitlab-agent, registry-cache, tailnet-dns, vm-ingress, wg-easy). `dependsOn: infrastructure-configs` — deliberately parallel to observability, so a failed observability upgrade cannot freeze app reconciliation. Substitutes image/chart versions from `cluster-versions` and cluster identity from `cluster-config`.
7. `infrastructure-metrics-server` → `kubernetes/infrastructure/controllers/metrics-server/` — **off the chain**: `dependsOn: infrastructure-sources` only, and nothing dependsOn it. It sits inside the controllers directory but is reconciled separately because its install is expected to fail until the Ansible-side `--disable=metrics-server` lands, and a failing HelmRelease inside the `wait: true` controllers stage would freeze configs, observability and apps behind it. Nothing needs `metrics.k8s.io` to be *served* before it can be applied — HPAs and the VPA recommender read it at runtime. See the file's header and docs/33 § metrics-server.

`scripts/flux-child-kustomizations.py` prints this list in `dependsOn` order, derived from `kubernetes/clusters/weisssrv/*.yaml`; `task flux:reconcile` and `scripts/deploy-verify.sh` both consume it, so adding a stage is a one-file change.

The five-way infrastructure split ensures the monitoring CRDs (`infrastructure-crds`) exist before any controller renders a ServiceMonitor, and CRD-dependent configs run after the controllers that install their CRDs. Apps branch off `infrastructure-configs` in parallel with observability: with the monitoring CRDs now installed up-front by `infrastructure-crds`, the monitoring CRs under `apps/` and observability render cleanly on a fresh bootstrap, and in steady state observability failures no longer block apps.

Tenant Kustomizations (external repos) live in `kubernetes/clusters/weisssrv/tenants/<repo>.yaml` and are reconciled by the root cluster Kustomization. See `docs/30-multi-repo-onboarding.md`.

### Fresh bootstrap / disaster recovery

The `monitoring.coreos.com` CRDs (`servicemonitors`, `podmonitors`,
`prometheusrules`, …) are installed by the dedicated **`infrastructure-crds`**
stage — the `prometheus-operator-crds` HelmRelease (`kubernetes/infrastructure/crds/`),
pinned via `helm_chart_versions_prometheus_operator_crds` in `all.yml`. That stage
`dependsOn: infrastructure-sources` with `wait: true`, and `infrastructure-controllers`
now `dependsOn: infrastructure-crds`, so on a **fresh** cluster (or full DR rebuild)
the CRDs are Established before any controller renders a ServiceMonitor. This
removes the previous ordering caveat where Traefik (which always emits a
`ServiceMonitor`, unlike cert-manager's `.Capabilities` guard) blocked the
first-boot controllers stage until the CRDs arrived two stages later.
kube-prometheus-stack runs with `crds.enabled: false` + `install/upgrade.crds: Skip`,
so it no longer ships its own copies (which would tug ownership with the CRD stage).

**On a truly fresh cluster nothing manual is needed** — the CRD stage installs
the CRDs cleanly before controllers.

**Adopting pre-existing CRDs.** On a cluster whose monitoring CRDs were
installed by kube-prometheus-stack's `crds/` directory, they carry no Helm
ownership metadata, so the first reconcile of the `prometheus-operator-crds`
HelmRelease hits Helm's "invalid ownership metadata … cannot be imported" guard.
The fix is a **metadata-only** adoption (labels/annotations only — the CRD spec is
untouched, so existing CRs are unaffected). This has been applied here; the recipe
is in the [historical appendix](#appendix-completed-one-time-migrations) for a
rebuild that starts from an older cluster.


**Keep the CRD pin in lockstep with kube-prometheus-stack** (`prometheus_operator_crds`
↔ `kube_prometheus_stack` in `all.yml`) so the CRD set stays version-matched to the
operator kps deploys.

### Loki PV storageClass guard (local-path drift)

Every stateful PV in this cluster is a statically-provisioned zvol/NFS PV with
`storageClassName: ""`; nothing may use the cluster-default `local-path` class
(it lands on the stateless k3s VM bootdisk, excluded from all backups). The Loki
StatefulSet enforces this via `singleBinary.persistence.storageClass: "-"` (the
chart sentinel that renders a literal `storageClassName: ""`; a plain `""` is
falsy to the chart's `with` guard and drops the field, letting local-path capture
the claim — see the comment in `loki/release.yaml`). The **`LocalPathPVExists`**
alert (kube-prometheus-stack) fires if any PV lands on `local-path`. If the live
`storage-loki-0` PVC ever drifts onto a local-path PV (StatefulSet
volumeClaimTemplates are immutable, so a git-side fix does not rebind an existing
PVC), the runbook is: scale loki to 0, `rsync` the data from the drifted local-path
PV to the intended `loki-data` zvol PV, delete the drifted PVC/PV, recreate the PVC
bound to `loki-data` (`volumeName`), scale back up. Do this in a maintenance window
before the manifest reconciles the corrected template.

Two preventive controls now sit in front of that runbook, because the alert only
fires *after* data is already being written to an unbacked-up disk:

- `local-storage` is in `k3s_disable` (`group_vars/k3s.yml`), so k3s no longer
  ships local-path-provisioner and **no default StorageClass exists** — a claim
  that omits `storageClassName` stays Pending instead of silently binding.
  Applying this restarts the k3s servers, so it needs a healthy etcd quorum.
- `scripts/check-pvc-storageclass.py` (run by `task flux:lint`) fails CI on any
  PVC, `volumeClaimTemplate`, or chart persistence block that sizes a volume
  without naming a class.

### Reconciliation Cadence

- **Push-triggered (live)**: the GitLab agent's Flux module triggers an immediate `GitRepository` reconcile on every push — see [Push-Triggered Reconciliation](#push-triggered-reconciliation).
- **GitRepository poll**: 1 minute (source-controller checks GitLab for new commits — the fallback when the agent is down).
- **Kustomization interval**: 10 minutes (forced full re-reconcile even without new commits — corrects drift from manual `kubectl apply` or cluster-side edits).
- **HelmRelease interval**: 30 minutes (values re-render + chart upgrade check).
- **ExternalSecret refreshInterval**: 24 hours (ESO re-reads 1Password and updates the k8s Secret if changed).

Worst case (agent down, poll fallback): a pushed change reaches the cluster inside ~1 minute (poll) + reconcile time.

---

## Daily Operations

### Deploying a Change

The flow is always the same:

1. Edit YAML in `kubernetes/`.
2. `git commit` + `git push`.
3. Wait a few seconds (GitLab agent push trigger; worst case ~1 minute via the poll fallback).
4. Verify.

Example — bump the Authentik chart version:

```bash
# 1. Bump the canonical version in all.yml
$EDITOR ansible/inventories/prod/group_vars/all.yml

# 2. Regenerate the cluster-versions ConfigMap
task flux:sync-versions

# 3. Commit + push both files together
git add ansible/inventories/prod/group_vars/all.yml \
        kubernetes/infrastructure/sources/versions-configmap.yaml
git commit -m "Bump Authentik to <new-version>"
git push

# 4. Watch the reconcile
task flux:status
```

Never edit `versions-configmap.yaml` by hand. CI fails when it drifts from `all.yml`.

### Checking Status

```bash
# Concise summary — run this first
task flux:status

# Full cluster check (controller health + CRD presence)
task flux:verify

# Raw Flux output — every resource Flux manages
flux get all -A

# Targeted
flux get kustomization -n flux-system
flux get helmrelease -A
flux get source git -A
kubectl get externalsecret -A
```

Healthy state: every row shows `READY=True` and `STATUS=*Applied revision <sha>*` or `Release reconciliation succeeded`.

### Forcing Reconciliation

Used when you don't want to wait the default interval, or you just cleared a transient error:

```bash
# Force source refresh + reconcile everything (apps + infrastructure)
task flux:reconcile

# Target a single Kustomization
flux reconcile kustomization apps -n flux-system --with-source

# Target a single HelmRelease
flux reconcile helmrelease authentik -n authentik --with-source
```

`--with-source` forces the GitRepository to re-fetch before reconciling. Without it, Flux reconciles the last-fetched revision.

### Fast Local Iteration

When rapidly iterating on a manifest and you don't want to push every keystroke:

```bash
# Apply a local path directly. Flux will revert on the next reconcile
# (10m default, or sooner if you run task flux:reconcile) — so this is
# strictly for quick feedback, not durable changes.
task flux:dev-apply -- kubernetes/apps/<app>
```

Workflow:

1. Edit.
2. `task flux:dev-apply -- kubernetes/apps/<app>` — see it working.
3. Commit + push when happy.
4. Or: do nothing — Flux reverts in the next cycle and nothing is lost.

Any change made via `dev-apply` that isn't committed is lost at the next reconcile. That's the feature, not the bug — use it as guardrails.

---

## Secret Operations

### Secret Model

- **Two manually-created bootstrap secrets**: `op-credentials` and `onepassword-connect-token` in the `external-secrets` namespace. Created once during initial setup — `task flux:bootstrap-onepassword` prints the procedure, and `task flux:bootstrap-onepassword-apply` executes it (requires `op` auth, a reachable cluster, and `./1password-credentials.json` from `op connect server create`; the Connect token is minted via `op connect token create` — deliberately not an `op read`, no vault item exists for it). These authenticate the 1Password Connect server.
- **Everything else**: `ExternalSecret` CR → ESO reads from 1Password via Connect → writes a `Secret` into the app namespace → app consumes it normally.

There are no other manually-created Secrets in the cluster. `op run -- kubectl create secret` is no longer part of the workflow.

`scripts/check-unmanaged-secrets.py` enforces that: it reads every live Secret and
fails on any that carries no ownership marker (ESO ownerReference, Flux/Helm
labels, a controller's own label) and is not in the script's documented allowlist.
A hand-applied Secret is invisible to `task flux:rotate-secret` and to docs/15, so
a superseded credential value can sit in the cluster indefinitely — the check is
what makes "there are no other manually-created Secrets" true rather than
aspirational. `task flux:verify` runs it as a **warning** (that task is the
post-deploy/DR gate and must be able to go green); `task flux:verify-unmanaged-secrets`
runs the same check standalone and exits non-zero, which is the form to use when
confirming a cleanup.

**Scope of the store**: `onepassword-homelab` is a `ClusterSecretStore`, so it also
declares `spec.conditions` listing the namespaces allowed to use it
(`kubernetes/infrastructure/configs/cluster-secret-store.yaml`). Without that list
any namespace could mint any item in the Homelab vault. A new app therefore needs
its namespace added there as well as its ExternalSecret — `task flux:lint`
(`scripts/check-secretstore-scope.py`) fails the build if the two disagree.

### 1Password Connect Provider Reference Format

The ESO 1Password Connect provider uses `remoteRef.key` for the item title and `remoteRef.property` for the field name:

```yaml
remoteRef:
  key: <1P-item-title>
  property: <field-name>
```

- `<1P-item-title>` is the human-readable title of the 1Password item (e.g. `Authentik Secrets`).
- `<field-name>` is the field label (`password`, `credential`, `username`, custom field names, etc.).

**Common mistakes that break parsing**:

- Using `op://Homelab/...` style prefix — wrong. The Connect provider uses its own format.
- Putting the field name after a slash in `key:` — wrong. Use `property:` for the field name.

Example from `kubernetes/apps/authentik/externalsecret.yaml`:

```yaml
- secretKey: secret-key
  remoteRef:
    key: Authentik Secrets
    property: secret-key
```

### Adding a Secret to an App

1. Create or extend an `ExternalSecret` YAML in the app folder.
2. Reference the 1P item by title (`key`) and field name (`property`).
3. Wire the consuming Deployment/HelmRelease to the resulting Secret via `valueFrom.secretKeyRef` or the chart's `existingSecret` field.
4. Commit + push.

Template:

```yaml
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: myapp-secrets
  namespace: myapp
spec:
  refreshInterval: 24h
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-homelab
  target:
    name: myapp-secrets
    creationPolicy: Owner
  data:
    - secretKey: api-key
      remoteRef:
        key: MyApp Secrets
        property: api-key
    - secretKey: db-password
      remoteRef:
        key: MyApp DB
        property: password
```

After pushing, verify:

```bash
kubectl get externalsecret -n myapp
# Expect: STATUS=SecretSynced, READY=True
kubectl get secret myapp-secrets -n myapp -o yaml
```

### Rotating a Secret

1. Change the value in 1Password.
2. Either wait up to 24 hours for the automatic refresh, or force it:

```bash
# Force refresh + roll consuming pods
task flux:rotate-secret -- <app>

# Force a single ExternalSecret only (no pod restart)
task flux:refresh-secret -- <namespace>/<externalsecret-name>
```

`rotate-secret` annotates the ExternalSecret with a force-sync annotation (ESO picks it up immediately), waits for the Secret to update, then rolls the Deployments/StatefulSets that consume it.

### Rate Limits

1Password Families plan: **1,000 reads per day, account-wide**.

Current footprint: **20 ExternalSecrets across 15 namespaces, ~56 fields**
(authentik 5, recipes 8, hermes 6+1, downloads 2+1, homarr 2, wg-easy 2,
tailscale 2, registry-cache 2, runner/agent tokens 3, cloudflare 3,
observability-secrets 2, observability-exporter-secrets 13, alertmanager-config 3,
loki-push-auth 1). The 1Password Connect provider syncs the entire vault into a
local encrypted cache periodically — individual field reads from ExternalSecrets
hit this cache, not the 1Password cloud API. Rate limits apply to the vault-sync
operations, not per-field reads, so the headroom is generous. Two ExternalSecrets
use `refreshInterval: 1h` (`observability/alertmanager-config` and
`observability/loki-push-auth`); every other one uses `24h`. Run
`kubectl get externalsecrets -A` for the current set — that command, not this
paragraph, is the source of truth.

Every manual `task flux:refresh-secret` or `task flux:rotate-secret` adds
fields_in_that_ExternalSecret extra reads. Rotating a single app a few times
a day is fine. Loops are not — if you find yourself scripting refreshes,
raise the refreshInterval instead.

Adding tenants on the 1Password backend (see `docs/30-multi-repo-onboarding.md`) shares this budget. Friends without 1Password accounts should use the GitLab-variables path.

---

## Adding a New App

The canonical app pattern is `kubernetes/apps/authentik/` — copy its structure.

1. **Create the folder and resources**:

   ```
   kubernetes/apps/<name>/
   ├── namespace.yaml           # Namespace (one per app)
   ├── release.yaml             # HelmRelease — OR plain Deployment/StatefulSet manifests
   ├── externalsecret.yaml      # ExternalSecret referencing onepassword-homelab
   ├── ingress-route.yaml       # Traefik IngressRoute (if HTTP-exposed)
   ├── certificate.yaml         # cert-manager Certificate (wildcard is usually sufficient)
   ├── storage.yaml             # PVCs / PersistentVolumes (if stateful)
   ├── networkpolicy.yaml       # default-deny + explicit allowlist (every app ships one)
   ├── vpa.yaml                 # VerticalPodAutoscaler, Initial tier (see docs/33-autoscaling.md)
   └── kustomization.yaml       # Aggregates the above
   ```

   Do not skip `networkpolicy.yaml` (a new app must not open its namespace)
   or `vpa.yaml` (every app workload carries at least an Initial-tier VPA —
   tier guidance in `docs/33-autoscaling.md`).

   Platform requirements every app folder has to satisfy:

   - **Namespace**: Pod Security Admission labels — `enforce baseline` unless a
     capability (NET_ADMIN etc.) forces `privileged`, in which case justify it
     with an inline comment and keep `warn`/`audit` at `restricted`.
   - **NetworkPolicy**: pull in the shared
     `kubernetes/components/netpol-baseline` component (default-deny-ingress)
     via `components:` in the app's `kustomization.yaml`, then add an explicit
     `default-deny-egress` with scoped allows. Standard allows: kube-dns; the
     apiserver as the **node IPs** `192.168.0.222/223/227:6443` (not the
     service VIP); `192.168.0.151:587` if the app sends mail; public HTTPS as
     `0.0.0.0/0` **except** `[10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16,
     100.64.0.0/10, 169.254.0.0/16]`. Add a scrape-allow from the
     `observability` namespace. Copy the shape from
     `authentik/networkpolicy.yaml` or `recipes/networkpolicy.yaml`.

     Two namespaces are exempt from the ingress default-deny, and only two
     — see § Network policy exceptions at the end of this section.
   - **Certificate**: one per host, `issuerRef` ClusterIssuer
     `letsencrypt-prod`, `renewBefore: 720h`.
   - **IngressRoutes**: public → `external-dns.alpha.kubernetes.io/target:
     ericsweiss.com` annotation + the `hsts-header` middleware; internal →
     `lan-tailscale-only` + `hsts-header` (both middlewares live in the
     `traefik` namespace).
   - **Observability (mandatory)**: a ServiceMonitor/PodMonitor plus the scrape
     NetworkPolicy above; a down/stale alert rule in the matching `homelab.*`
     group under `kubernetes/infrastructure/observability/rules/` (one
     PrometheusRule manifest per group — match the neighbouring
     `for`/`severity`/`runbook_url` style); and a blackbox
     probe for user-facing endpoints where no exporter covers reachability. A
     Grafana dashboard only if a good upstream one exists (ConfigMap sidecar via
     `configMapGenerator` in `observability/dashboards/`).
   - **DNS**: internal = an `adguard_home_rewrites` entry in `group_vars/dns.yml`
     (answer `192.168.0.101` for anything Traefik-fronted). External = the
     external-dns annotation above — no Terraform edit — unless it needs a
     nested subdomain or a DNS-only record, which goes in
     `terraform/cloudflare/dns.tf`.
   - **Storage**: NFS PVs mount **by hostname** (`pve-nas-01.esweiss.com`) with
     `xprtsec=tls` — never by IP, since the `*.esweiss.com` cert has no IP SAN
     and an IP mount fails the handshake; add
     `kustomize.toolkit.fluxcd.io/force: "Enabled"` on PVs with immutable-field
     risk. zvol-backed PVs use `storageClassName: ""` (static binding, no
     provisioner); the zvol itself is created host-side via
     `vm_additional_disks` (docs/06). A brand-new top-level dataset also needs
     an `SRC_LIST` edit in
     weisssrv-lib `ansible_collections/weisssrv/infra/roles/nas_storage/templates/archive-backupctl.sh.j2`.
   - **Scheduling**: NAS-avoid is the default for stateless workloads
     (preferred `nodeAffinity` `esweiss.com/nas DoesNotExist` weight 100 +
     `nodeSelector esweiss.com/general: "true"`, plus
     `esweiss.com/cpu: modern|legacy` where the binary demands it); NAS-pin
     (required hostname `k3s-agt-nas-01` + toleration
     `esweiss.com/nas=true:PreferNoSchedule`) only when the workload needs
     NFS-local I/O or AVX.
   - **SSO**: the OIDC issuer host is **always** `auth.ericsweiss.com`
     (external). Authentik applications/providers/group-bindings are codified in
     `terraform/authentik/` — edit the `.tf`, review the plan, run a supervised
     `op run -- terraform apply`; never the Authentik UI (UI-created objects
     drift out of state). See `docs/40-authentik-terraform.md`.

2. **Wire it into the apps Kustomization**:

   ```yaml
   # kubernetes/apps/kustomization.yaml
   resources:
     - authentik
     - download-clients
     - hermes
     - homarr
     - hindsight
     - recipes
     - gitlab-runner
     - gitlab-runner-privileged
     - gitlab-runner-reaper
     - gitlab-agent
     - registry-cache
     - tailnet-dns
     - vm-ingress
     - wg-easy
     - <name>   # <-- add this line
   ```

3. **If the Helm chart isn't already sourced**, add a HelmRepository:

   ```yaml
   # kubernetes/infrastructure/sources/<name>.yaml
   ---
   apiVersion: source.toolkit.fluxcd.io/v1
   kind: HelmRepository
   metadata:
     name: <name>
     namespace: flux-system
   spec:
     interval: 1h
     url: https://charts.example.com
   ```

   Then add `<name>.yaml` to `kubernetes/infrastructure/sources/kustomization.yaml`.

4. **Version pinning via `${var_name}`**: every chart version or image tag must be a placeholder like `${myapp_version}`, matching a key in `cluster-versions`. Add the key to `ansible/inventories/prod/group_vars/all.yml`, then:

   ```bash
   task flux:sync-versions   # regenerates versions-configmap.yaml
   ```

   **Cluster identity comes from the OTHER ConfigMap.** Hostnames, CIDRs and VIPs
   are placeholders too — `app.${cluster_internal_domain}`,
   `${cluster_metallb_internal_vip}` — resolved from `cluster-config`
   (`kubernetes/infrastructure/sources/cluster-config.yaml`), which every stage
   after `sources` substitutes from alongside `cluster-versions`. That file is
   hand-edited, not generated. `scripts/check-cluster-literals.py` (in
   `task lint`) fails a manifest that hard-codes an adopted value, and
   cross-checks the ConfigMap against the Ansible inventory. Four things stay
   literal on purpose — NetworkPolicy `ipBlock` CIDRs, everything under
   `observability/rules/`, backslash-escaped regex spellings, and per-guest or
   per-node addresses — because a tool parses them BEFORE Flux substitutes; the
   ConfigMap's own header carries the full reasoning.

5. **Commit, push, verify**:

   ```bash
   git add kubernetes/apps/<name>/ kubernetes/apps/kustomization.yaml
   git add ansible/inventories/prod/group_vars/all.yml \
           kubernetes/infrastructure/sources/versions-configmap.yaml
   git commit -m "Add <name> app"
   git push

   task flux:status
   kubectl get pods -n <name>
   ```

### Network policy exceptions

This is the canonical list — CLAUDE.md and `kubernetes/components/README.md`
both point here.

Two namespaces are documented exceptions to the "ingress default-deny in every
namespace" rule:

| Namespace | Why |
|---|---|
| `downloads` (dir `kubernetes/apps/download-clients/`) | Ships its own default-deny covering ingress **and** egress, so the component would be redundant |
| `flux-system` | Upstream gotk manifests ship their own policies; we do not patch them |

A *third* unfenced namespace is a bug, not a precedent, and
`scripts/check-default-deny-coverage.py` (in `task flux:lint` and the CI
flux-lint job) is what fails it: over the same rendered corpus it collects every
namespace that owns a workload — including the ones a HelmRelease targets, whose
pods never appear in a kustomize build — and requires each to carry a
namespace-wide NetworkPolicy with `Ingress` in `policyTypes`. The exceptions
above live in that script as a reasoned map, so adding one is a code change with
a written justification. `downloads` needs no entry: its own policy is
namespace-wide and satisfies the invariant outright.

`scripts/check-scrape-netpol.py` cannot cover this and is not the place to try:
it only inspects namespaces that already run an ingress-deny policy, so an
unfenced one is invisible to it — an `--exempt` for such a namespace would be
inert today (the flag only suppresses a namespace that is already restricted)
while masking a real regression later.

#### kube-system is fenced, not excepted

It used to be the third exception. It is not any more: it carries the same
`netpol-baseline` default-deny as everything else, plus an enumerated allow set,
all in `kubernetes/infrastructure/configs/kube-system-policies/`. That directory
is deliberately the *only* place kube-system policies live — a deny is only safe
if its allow set is complete, so the two must be reviewed together and land in
the same reconcile, even though kured and metrics-server are reconciled by other
Kustomizations.

| Resident | Ingress allowed | Notes |
|---|---|---|
| CoreDNS (`k8s-app: kube-dns`) | 53/UDP+TCP from `namespaceSelector: {}`, the pod CIDR and the LAN CIDR; 9153/TCP from `observability` | The pod-CIDR peer is not redundant: a query aimed at the kube-dns ClusterIP from outside the pod network is DNAT'd **and masqueraded**, so it can arrive as the sending node's flannel gateway address. The LAN peer is defence in depth with no evidenced consumer today — the hostNetwork DaemonSets (kube-vip, node-exporter, metallb-speaker) run `dnsPolicy: ClusterFirst`, which the kubelet demotes to `Default` under hostNetwork, so they resolve via the node's resolv.conf (AdGuard), not CoreDNS. It is kept so host-netns resolution cannot silently break, and is safe because neither pod IPs nor the ClusterIP are LAN-routable |
| metrics-server | 10250/TCP, no source peer | Same trade as the cert-manager / vpa-system / metallb-system webhook policies: the aggregation-layer call arrives post-DNAT from whichever server is active, so no source IP is pinnable, and the component's own TLS is the gate. **Two** label-scoped policies, not one namespace-wide one — the k3s AddOn (`k8s-app: metrics-server`) and the chart (`app.kubernetes.io/name: metrics-server`) label differently and both are live through the cutover window; one podSelector cannot OR across two keys, but policies are additive. Drop the AddOn one when the cutover closes |
| kured | 8080/TCP from `observability` | Its ServiceMonitor is chart-native via `metrics.create`, which `check-scrape-netpol.py` cannot see (it matches `serviceMonitor.enabled` only), so this allow is pinned by `scripts/test_check_default_deny_coverage.py` instead (teaching the gate the `metrics.create` spelling is a weisssrv-lib change) |
| kube-vip | n/a | hostNetwork — NetworkPolicy never gates a hostNetwork pod as a target |

`kube-public` and `kube-node-lease` also carry a fail-closed `default-deny-all`
(`configs/builtin-namespace-policies.yaml`). Both are pod-free, so this changes
no traffic; it exists so a pod created there by hand is not unguarded.

Verification after a reconcile that touches any of this:

```bash
kubectl get netpol -n kube-system

# DNS still resolves — exec into an existing workload, do NOT `kubectl run` a
# throwaway pod. A bare `kubectl run` lands in `default`, which is PSA
# `enforce: restricted` (a stock busybox is rejected at admission) AND carries
# its own `default-deny-all` with policyTypes [Ingress, Egress] — so the probe
# fails for two reasons that have nothing to do with kube-system, and reads as
# a false "DNS is broken". Grafana's image is alpine, so busybox nslookup/wget
# are already there; ask for the FQDN, since busybox does not walk the search
# list. Expect an answer from 10.43.0.10.
kubectl -n observability exec deploy/kube-prometheus-stack-grafana -c grafana \
  -- nslookup kubernetes.default.svc.cluster.local

# The two kube-system scrape allows still work. `up == 1` per target is the
# proof; an empty result vector or a 0 means the 9153 (CoreDNS) or 8080 (kured)
# allow is wrong, and TargetDown follows a few minutes later.
kubectl -n observability exec deploy/kube-prometheus-stack-grafana -c grafana \
  -- wget -qO- 'http://prometheus-operated:9090/api/v1/query?query=up{job=~"coredns|kured"}'
```

Rollback, if DNS breaks: `flux suspend kustomization infrastructure-configs`
then `kubectl delete netpol default-deny-ingress -n kube-system`. Deleting the
deny restores the previous (unfenced) behaviour immediately; the allows are
additive and harmless on their own.

---

## Suspending and Resuming

### Emergency Stop

Suspending a resource freezes Flux's reconciliation of it — drift won't be corrected, upgrades won't happen. Use when you need to investigate, or when a reconcile loop is making things worse.

```bash
# Suspend a specific HelmRelease
task flux:suspend -- authentik/helmrelease/authentik

# Suspend a Kustomization
task flux:suspend -- flux-system/kustomization/apps

# Resume with the same args
task flux:resume -- authentik/helmrelease/authentik
```

The argument is `<namespace>/<kind>/<name>`. Kind must be one of `kustomization` or `helmrelease` -- these are the only single-word Flux resource kinds that the task wrapper handles correctly (it splits on `/` and passes the kind to `flux suspend <kind>`). Multi-word kinds like `source git` or `source helm` contain a space that would break the slash-delimited parsing. `externalsecret` is not a Flux resource (it belongs to ESO and cannot be suspended via the `flux` CLI). For other Flux resources, use the `flux` CLI directly (e.g., `flux suspend source git flux-system -n flux-system`).

### Top-Level Suspend

For a full cluster freeze (maintenance window, investigating a broad issue):

```bash
# Pause all app reconciles (infrastructure keeps going)
task flux:suspend -- flux-system/kustomization/apps

# Pause everything, including platform
# Suspend from leaf to root so no Kustomization reconciles into an
# inconsistent half-suspended state.
task flux:suspend -- flux-system/kustomization/apps
task flux:suspend -- flux-system/kustomization/infrastructure-observability
task flux:suspend -- flux-system/kustomization/infrastructure-configs
task flux:suspend -- flux-system/kustomization/infrastructure-controllers
task flux:suspend -- flux-system/kustomization/infrastructure-crds
task flux:suspend -- flux-system/kustomization/infrastructure-sources
```

While suspended:

- No new commits are applied.
- Drift is not corrected.
- ExternalSecret refresh still runs (ESO is independent of Flux).

Resume reverses the state.

---

## Rollback

### Revert a Bad Commit

This is the primary rollback path. Git is the source of truth — revert the commit and Flux applies the reversal.

```bash
git revert <sha-of-bad-commit>
git push

# Watch it reconcile
task flux:status
```

Flux treats the revert like any other change. Inside ~1 minute the cluster is back to the pre-commit state.

### What Survives a Revert

- **PVCs**: not deleted by Flux prune (they have `pvc-protection` finalizers and are covered by `prune: false` guards on storage resources where needed). Data on Authentik postgres, Mealie postgres, download-clients data, etc. is safe.
- **StatefulSet pods**: the StatefulSet may be recreated but PVCs are preserved, so data persists.
- **HelmRelease history**: `helm history <name> -n <ns>` shows every revision, including the reverted one. You can `helm rollback` manually if Flux is also down.

### HelmRelease History

```bash
helm history authentik -n authentik
# Look for REVISION, STATUS, APP VERSION, CHART

# If Flux is failing to upgrade, manual rollback to a known-good revision:
helm rollback authentik <revision> -n authentik
```

Don't do this while Flux is healthy — Flux will revert you. Use it only when Flux itself is stuck and you're breaking glass.

### Flux Remediation

`HelmRelease` specs have:

```yaml
install:
  remediation:
    retries: 3
upgrade:
  remediation:
    retries: 3
    remediateLastFailure: true
```

Flux automatically retries failed upgrades and rolls back on repeated failure. Check `flux get hr -n <ns>` for the current status and `flux logs --level=error --kind=HelmRelease` for the error chain.

---

## Troubleshooting

### ExternalSecret stuck in `SecretSyncError`

```bash
kubectl describe externalsecret <name> -n <ns>
```

Common causes:

- **Bad 1P reference format**: the `remoteRef.key` has `op://` prefix or uses the old `<item-id>/<field>` format. Fix: use `key: <item-title>` with `property: <field-name>`.
- **Item moved vaults**: ESO's ClusterSecretStore is scoped to `Homelab`. If someone moved an item to a different vault, ESO can't see it.
- **Bootstrap secrets missing/wrong**: check `kubectl get secret op-credentials onepassword-connect-token -n external-secrets`. If absent or stale, see `task flux:bootstrap-onepassword` for instructions.
- **Rate limit hit**: rare. Error message mentions 429. Raise refreshIntervals or pare back manual refreshes.

Force a retry after fixing:

```bash
task flux:refresh-secret -- <ns>/<name>
```

### HelmRelease `Ready=False`

```bash
flux get hr -n <ns>
kubectl describe helmrelease <name> -n <ns>
helm status <releaseName> -n <ns>
flux logs --kind=HelmRelease --name=<name> --namespace=<ns>
```

Patterns:

- **Values rendering failure**: substitution variable missing. `kubectl describe kustomization apps -n flux-system` shows substitution errors. Check `versions-configmap.yaml` has the key.
- **Chart pull failure**: source HelmRepository is broken. `flux get source helm -A` — check Ready.
- **`InvalidChartReference` for a version that exists upstream**: source-controller's cached chart index is stale. HelmRepository `interval` is the bound on how long this can last (1h cluster-wide). Force-refresh:

  ```bash
  flux reconcile source helm <repo> -n flux-system
  flux reconcile helmrelease <name> -n <ns>
  ```

- **Install/upgrade failure**: the chart itself is rejecting values. `helm status` shows the error. Fix values in `release.yaml`, commit, push.
- **Timeout**: workload didn't become Ready in time. Usually a pod-level issue — `kubectl get pods -n <ns>` and `kubectl describe pod <pod> -n <ns>`.

After fixing:

```bash
flux reconcile helmrelease <name> -n <ns> --with-source
```

### Kustomization stuck `Reconciling`

A Kustomization (`apps`, `infrastructure-sources`, `infrastructure-crds`, `infrastructure-controllers`, `infrastructure-configs`, `infrastructure-observability`, or `infrastructure-metrics-server`) is in progress but never reaches Ready. `scripts/flux-child-kustomizations.py` prints the current set in `dependsOn` order — it derives them from `kubernetes/clusters/weisssrv/*.yaml`, so it is never stale.

```bash
kubectl describe kustomization <name> -n flux-system
```

Most common cause: `wait: true` + a health check failing. The Kustomization waits for every child resource to report Ready, and one of them is stuck.

- Find the stuck child: `flux get all -A | grep -v True`.
- Fix the child (usually a HelmRelease or a stuck Deployment).
- Suspend/resume cycle sometimes unsticks the parent:

  ```bash
  task flux:suspend -- flux-system/kustomization/<name>
  task flux:resume  -- flux-system/kustomization/<name>
  ```

### Substitution Not Applied (Literal `${var_name}` in a Rendered Object)

A placeholder like `${authentik_version}` is showing up as a literal string in a deployed resource.

- **Which ConfigMap**: version pins live in `cluster-versions`, cluster identity (domains, CIDRs, VIPs) in `cluster-config`. Both are substituted by every stage after `sources`.
- **ConfigMap missing or key typo**: `kubectl get configmap cluster-versions cluster-config -n flux-system -o yaml` — confirm the key exists.
- **`substituteFrom` missing on the Kustomization**: check `kubernetes/clusters/weisssrv/{apps,infrastructure-crds,infrastructure-controllers,infrastructure-configs,infrastructure-observability}.yaml` all have the `postBuild.substituteFrom` block referencing BOTH ConfigMaps. (`infrastructure-sources.yaml` intentionally does NOT — sources/ defines them and has no placeholders.)
- **ConfigMap not yet reconciled**: both live in `kubernetes/infrastructure/sources/` and are created by the `infrastructure-sources` Flux Kustomization. On a cold bootstrap, if that Kustomization hasn't reconciled yet, controllers/configs substitution fails loudly (`optional: false`) — check `flux get ks infrastructure-sources -n flux-system`.

Regenerate from scratch if in doubt:

```bash
task flux:sync-versions
git diff kubernetes/infrastructure/sources/versions-configmap.yaml
```

### Flux Logs

```bash
flux logs --all-namespaces --level=error --since=10m
flux logs --kind=Kustomization --name=apps --namespace=flux-system
flux logs --kind=HelmRelease --name=authentik --namespace=authentik
```

The `flux logs` command aggregates controller logs by resource, which is far more useful than raw `kubectl logs` against the controller pods.

---

## GitLab outage: pointing Flux at the GitHub mirror

Flux's only Git source is the self-hosted GitLab (`gotk-sync.yaml`), so a
GitLab VM outage freezes desired-state delivery (running workloads are
unaffected). The read-only GitHub mirror is the failover source:

```bash
# Temporary, until GitLab is back (Flux will NOT push back; mirror is RO):
kubectl -n flux-system patch gitrepository flux-system --type merge \
  -p '{"spec":{"url":"https://github.com/ericsweiss/weisssrv"}}'
task flux:reconcile

# Revert once GitLab is healthy (or just let the next `flux bootstrap`/
# git-controlled gotk-sync change win):
kubectl -n flux-system patch gitrepository flux-system --type merge \
  -p '{"spec":{"url":"https://git.ericsweiss.com/eric/weisssrv.git"}}'
```

Caveats: the mirror lags by its sync cadence, and the flux-system
Kustomization will revert the patched URL on its next reconcile of
`gotk-sync.yaml` — for a long outage, suspend it first
(`task flux:suspend -- flux-system/kustomization/flux-system`).

## Push-Triggered Reconciliation

**Status: live — no setup required.** Push-triggered reconciliation comes
from the GitLab agent (`kubernetes/apps/gitlab-agent/`), whose built-in
**Flux module** watches for pushes to the project referenced by the
`flux-system` `GitRepository` (over the agent's existing KAS connection) and
triggers an immediate reconcile. Properties:

- **The agent creates its own in-cluster `Receiver` and HMAC Secret**, and they
  are deliberately **not in git**: `flux-system/gitlab-flux-system` (type
  `generic-hmac`, annotated `app.kubernetes.io/managed-by: gitlab`) plus the
  `gitlab-receiver-flux-system` Secret holding the KAS-minted trigger token.
  Declaring either in git would fight the agent for the same objects and the
  token is not ours to store, so ownership stays with GitLab (recorded in
  docs/15 and in `scripts/check-unmanaged-secrets.py`'s ALLOWLIST). Removing the
  agent removes them.
- Still **no inbound webhook endpoint** and no `flux:webhook-register` step: the
  Receiver has no Ingress/IngressRoute in front of it (`kubectl -n flux-system
  get ingressroute,ingress` is empty) and the agent's KAS connection is
  outbound-only. The trigger arrives over that connection, not from the
  internet.
- If the agent is down (or before the `apps` stage first converges on a
  fresh bootstrap), the 1-minute GitRepository poll is the fallback latency
  floor.
- Verify it's working: push a trivial commit and watch
  `flux get source git -n flux-system` pick up the new revision within
  seconds; the agent pod logs (`kubectl logs -n gitlab-agent deploy/weisssrv-k3s-gitlab-agent-v2`)
  show the flux module's activity.

---

## Upgrading the Flux Distribution

The in-cluster Flux components are pinned by `flux_version` in `all.yml` and
committed as `kubernetes/clusters/weisssrv/flux-system/gotk-components.yaml`.
The CI `deploy-verify` job installs the matching CLI from a hardcoded
version + tarball sha256 pin pair in `.gitlab-ci.yml` (a guard job fails the
pipeline when the pin drifts from `versions-configmap.yaml`). Upgrade steps:

1. Bump `flux_version` in `ansible/inventories/prod/group_vars/all.yml`, then
   `task flux:sync-versions`.
2. Update the `FLUX_VERSION="x.y.z"` + sha256 pin pair in `.gitlab-ci.yml`
   (deploy-verify job) — fetch the new tarball checksum from the flux2
   release's `*_checksums.txt`.
3. Regenerate the components manifest with the **matching** CLI version:

   ```bash
   flux install --export > kubernetes/clusters/weisssrv/flux-system/gotk-components.yaml
   ```

4. Commit all four files together and push; Flux upgrades itself on
   reconcile.
5. Verify: `flux check` and `task flux:status`.

`check-versions.py` marks `flux_version` as `held`, so
`task maintenance:check-versions` (and the CI version-check job) report a newer
Flux CLI release as `HELD` and never count it as an actionable update. That is
deliberate: bumping the GitOps control plane is a bootstrap-tested manual step
(the sequence above), not an automated pin bump. `check-versions.py --update flux`
prints the same rationale and refuses to write the new version.

## Appendix: completed one-time migrations

Both procedures below have been applied to this cluster. They are kept for a
rebuild that starts from an older cluster state, not as standing operations.

### Monitoring CRD Helm adoption

```bash
for crd in alertmanagerconfigs alertmanagers podmonitors probes prometheusagents \
           prometheuses prometheusrules scrapeconfigs servicemonitors thanosrulers; do
  kubectl label  crd ${crd}.monitoring.coreos.com app.kubernetes.io/managed-by=Helm --overwrite
  kubectl annotate crd ${crd}.monitoring.coreos.com \
    meta.helm.sh/release-name=prometheus-operator-crds \
    meta.helm.sh/release-namespace=prometheus-operator-crds --overwrite
done
task flux:reconcile
```

Afterwards helm-controller applies identical CRD content — a steady-state no-op.
Verify `infrastructure-crds` and the `prometheus-operator-crds` HelmRelease are
Ready, that all ten `monitoring.coreos.com` CRDs carry an
`operator.prometheus.io/version` annotation matching the pinned chart's operator
version, and that `kube-prometheus-stack` is still Ready with no object churn.

### Traefik NodePort de-allocation

The Traefik Service sets `allocateLoadBalancerNodePorts: false` (MetalLB L2
announces the VIP directly). Flipping that flag does not release NodePorts already
allocated on an existing Service — neither Helm nor Flux SSA owns
`spec.ports[*].nodePort` — so a cluster predating the flag needs one json-patch
`remove` per port entry:

```bash
kubectl -n traefik patch svc traefik --type json \
  -p '[{"op":"remove","path":"/spec/ports/0/nodePort"},{"op":"remove","path":"/spec/ports/1/nodePort"}]'
```

`healthCheckNodePort` stays — `externalTrafficPolicy: Local` requires it.

---

## Related documentation

- Flux documentation: https://fluxcd.io/flux/
- External Secrets Operator: https://external-secrets.io/latest/
- 1Password Connect provider: https://external-secrets.io/latest/provider/1password-automation/
- Flux bootstrap (GitLab): https://fluxcd.io/flux/installation/bootstrap/gitlab/
- Multi-repo onboarding: `docs/30-multi-repo-onboarding.md`
- K3s deployment workflow: `docs/19-k3s-deployment.md`
- Disaster recovery: `docs/17-disaster-recovery.md`
