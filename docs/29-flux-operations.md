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
8. [Webhook Setup](#webhook-setup)
9. [References](#references)

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
- **notification-controller** — receives GitLab webhooks, triggers on-demand reconciliation.

Top-level Kustomizations that Flux owns (all in `flux-system` namespace), reconciled in `dependsOn` order:

1. `infrastructure-sources` → `kubernetes/infrastructure/sources/` (HelmRepository CRs + `cluster-versions` ConfigMap). No dependencies. No postBuild substitution (no placeholders).
2. `infrastructure-controllers` → `kubernetes/infrastructure/controllers/` (HelmReleases for ESO, MetalLB, cert-manager, Traefik, external-dns). `dependsOn: infrastructure-sources`. Substitutes chart versions from `cluster-versions`.
3. `infrastructure-configs` → `kubernetes/infrastructure/configs/` (ClusterSecretStore, ClusterIssuer, MetalLB IP pools, wildcard certs, CoreDNS override, DDNS CronJob, shared Cloudflare secrets). `dependsOn: infrastructure-controllers` (CRDs must exist). Substitutes from `cluster-versions`.
4. `apps` → `kubernetes/apps/` (Authentik, download-clients, recipes, gitlab-runner, gitlab-runner-privileged, gitlab-agent, vm-ingress). `dependsOn: infrastructure-configs`. Substitutes image/chart versions from `cluster-versions`.

The three-way infrastructure split ensures CRDs are installed before CRD-dependent configs are applied — first bootstrap converges cleanly without "no matches for kind" transient errors.

Tenant Kustomizations (external repos) live in `kubernetes/clusters/weisssrv/tenants/<repo>.yaml` and are reconciled by the root cluster Kustomization. See `docs/30-multi-repo-onboarding.md`.

### Reconciliation Cadence

- **GitRepository poll**: 1 minute (source-controller checks GitLab for new commits).
- **Kustomization interval**: 10 minutes (forced full re-reconcile even without new commits — corrects drift from manual `kubectl apply` or cluster-side edits).
- **HelmRelease interval**: 30 minutes (values re-render + chart upgrade check).
- **ExternalSecret refreshInterval**: 24 hours (ESO re-reads 1Password and updates the k8s Secret if changed).
- **Webhook-triggered**: sub-second on `git push` to any watched branch (once the webhook is registered — see [Webhook Setup](#webhook-setup)).

Worst case (no webhook): a pushed change reaches the cluster inside ~1 minute (poll) + reconcile time.

---

## Daily Operations

### Deploying a Change

The flow is always the same:

1. Edit YAML in `kubernetes/`.
2. `git commit` + `git push`.
3. Wait ~1 minute (or a few seconds with webhook).
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

- **One manually-created bootstrap secret**: `onepassword-sdk-token` in the `external-secrets` namespace. Created once by `task flux:bootstrap-onepassword`. Contains the `weisssrv` 1Password service account token.
- **Everything else**: `ExternalSecret` CR → ESO reads from 1Password → writes a `Secret` into the app namespace → app consumes it normally.

There are no other manually-created Secrets in the cluster. `op run -- kubectl create secret` is no longer part of the workflow.

### 1Password SDK Provider Reference Format

The ESO 1Password SDK provider uses a specific format for `remoteRef.key`:

```yaml
remoteRef:
  key: <1P-item-id>/<field-name>
```

- `<1P-item-id>` is the 26-character ID of the 1Password item, not its title. You can find it in the 1Password URL when viewing an item, or via `op item get "<title>" --format json | jq -r '.id'`.
- `<field-name>` is the field label (`password`, `credential`, `username`, custom field names, etc.).

**Common mistakes that break parsing**:

- Using `op://Homelab/...` style prefix — wrong. The SDK provider uses its own format.
- Using a `property:` field alongside `key:` — the 1Password provider doesn't use it.
- Spaces in an item title and referencing by title — spaces break the parser. Always use the item ID.

Example from `kubernetes/apps/authentik/externalsecret.yaml`:

```yaml
- secretKey: secret-key
  remoteRef:
    key: yssxkcr2ggovqbh2j5m3p3ji2i/secret-key  # Authentik Secrets
```

The trailing comment records the human-readable 1P title — keep it. It's the only way to grep for "where does the Authentik secret key come from" without logging into 1Password.

### Adding a Secret to an App

1. Create or extend an `ExternalSecret` YAML in the app folder.
2. Reference the 1P item by ID.
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
        key: <1P-ITEM-ID>/api-key  # MyApp Secrets
    - secretKey: db-password
      remoteRef:
        key: <1P-ITEM-ID>/password  # MyApp DB
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

Current footprint: 10 ExternalSecrets spanning ~23 distinct fields
(authentik 5, recipes 9, vpn 2, runner tokens 3, cloudflare token 3). The
1Password SDK provider reads fields on each refresh, not whole items, so the
per-refresh cost is one read per field, not one read per ExternalSecret.
On the default `refreshInterval: 24h` that's ~20 reads/day — comfortable
headroom against the 1,000 limit.

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
   └── kustomization.yaml       # Aggregates the above
   ```

2. **Wire it into the apps Kustomization**:

   ```yaml
   # kubernetes/apps/kustomization.yaml
   resources:
     - authentik
     - download-clients
     - recipes
     - gitlab-runner
     - gitlab-runner-privileged
     - gitlab-agent
     - vm-ingress
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
task flux:suspend -- flux-system/kustomization/infrastructure-configs
task flux:suspend -- flux-system/kustomization/infrastructure-controllers
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

- **Bad 1P reference format**: the `remoteRef.key` has `op://` prefix, a space, or a `property:` field. Fix: use `<item-id>/<field>` only.
- **Item moved vaults**: ESO's ClusterSecretStore is scoped to `Homelab`. If someone moved an item to a different vault, ESO can't see it.
- **Bootstrap token missing/wrong**: check `kubectl get secret onepassword-sdk-token -n external-secrets`. If absent or stale, re-run `task flux:bootstrap-onepassword`.
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
- **Install/upgrade failure**: the chart itself is rejecting values. `helm status` shows the error. Fix values in `release.yaml`, commit, push.
- **Timeout**: workload didn't become Ready in time. Usually a pod-level issue — `kubectl get pods -n <ns>` and `kubectl describe pod <pod> -n <ns>`.

After fixing:

```bash
flux reconcile helmrelease <name> -n <ns> --with-source
```

### Kustomization stuck `Reconciling`

A Kustomization (`apps`, `infrastructure-sources`, `infrastructure-controllers`, or `infrastructure-configs`) is in progress but never reaches Ready.

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

- **ConfigMap missing or key typo**: `kubectl get configmap cluster-versions -n flux-system -o yaml` — confirm the key exists.
- **`substituteFrom` missing on the Kustomization**: check `kubernetes/clusters/weisssrv/{apps,infrastructure-controllers,infrastructure-configs}.yaml` all have the `postBuild.substituteFrom` block referencing `cluster-versions`. (`infrastructure-sources.yaml` intentionally does NOT — sources/ defines the ConfigMap itself and has no placeholders.)
- **ConfigMap not yet reconciled**: the ConfigMap lives in `kubernetes/infrastructure/sources/versions-configmap.yaml` and is created by the `infrastructure-sources` Flux Kustomization. On a cold bootstrap, if that Kustomization hasn't reconciled yet, controllers/configs substitution fails loudly (`optional: false`) — check `flux get ks infrastructure-sources -n flux-system`.

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

## Webhook Setup

One-time setup to replace the default 1-minute GitRepository poll with sub-second push-triggered reconciliation.

**Status**: the Flux `Receiver` manifest and the IngressRoute exposing it are not yet deployed (planned follow-up). When they land (`kubernetes/infrastructure/configs/flux-receiver.yaml` and `kubernetes/apps/vm-ingress/flux-webhook.yaml`), the sequence is:

1. Apply the Receiver manifest (via Flux, naturally — just push).
2. The Receiver exposes a token-protected endpoint at `flux-webhook.ericsweiss.com`.
3. Register the GitLab webhook:

   ```bash
   task flux:webhook-register
   ```

   This task reads the Receiver's generated path + the `Flux Webhook Token` from 1Password and creates the GitLab project webhook via the API.

4. Push a commit — GitLab hits the Receiver — source-controller refreshes immediately — Flux reconciles.

Until webhook is live, the 1-minute poll is the latency floor. That's fine for day-to-day work.

---

## First-time Flux bootstrap: delete pre-existing manually-created Secrets

Before running `task flux:bootstrap` for the first time, delete any Secrets
that were previously created imperatively (e.g., via `op run -- kubectl create
secret ...`). ESO's `ExternalSecret` resources in this repo use `creationPolicy:
Owner`, which means ESO refuses to take over Secrets it didn't create. If the
pre-existing Secrets are left in place, every `ExternalSecret` goes `NotReady`
with `SecretAlreadyExists`/`NotManaged`, and consumer workloads keep using the
stale (or re-rolled-at-cluster-time-unknown) values.

This applies only to the initial migration from the imperative world. On a
green-field cluster (fresh `task k3s:deploy`), this section is a no-op.

Run these BEFORE `task flux:bootstrap`:

```bash
# ESO / 1Password bootstrap token is the only hand-managed Secret in the
# post-Flux model — do NOT delete it.
# Everything else below is safe to delete; Flux will recreate via ESO from
# 1Password on the first reconcile.

kubectl delete secret authentik-secrets -n authentik --ignore-not-found
kubectl delete secret vpn-credentials -n downloads --ignore-not-found
kubectl delete secret recipes-secrets -n recipes --ignore-not-found
kubectl delete secret mealie-secrets -n recipes --ignore-not-found  # legacy name if present
kubectl delete secret bar-assistant-secrets -n recipes --ignore-not-found  # legacy
kubectl delete secret gitlab-runner-token -n gitlab-runner --ignore-not-found
kubectl delete secret gitlab-runner-privileged-token -n gitlab-runner --ignore-not-found
kubectl delete secret gitlab-agent-token -n gitlab-agent --ignore-not-found

# The cloudflare-api-token Secret is replicated by ESO into three namespaces.
# Delete from all three so ESO can re-create owned copies from 1Password.
kubectl delete secret cloudflare-api-token -n cert-manager --ignore-not-found
kubectl delete secret cloudflare-api-token -n external-dns --ignore-not-found
kubectl delete secret cloudflare-api-token -n cloudflare-ddns --ignore-not-found
```

Pods using the deleted Secrets will not actually restart until next rollout
— Kubernetes only re-reads Secrets on pod restart, not on Secret update.
You can leave them running on the old (cached) values while ESO re-populates;
then `task flux:rotate-secret -- <app>` (or a pod rollout) picks up the
ESO-managed replacement.

If you suspect any are missing from this list, a quick inventory:
```bash
# Anything not owned by an ExternalSecret (no ESO managed-by labels)
# in an app namespace likely needs the same treatment.
kubectl get secret -A -o json \
  | jq -r '.items[] | select(.metadata.labels["reconcile.external-secrets.io/created-by"]==null)
    | "\(.metadata.namespace)/\(.metadata.name)  \(.type)"'
```

---

## Post-merge branch switch (ONE-TIME, DO THIS AFTER THE MIGRATION MR MERGES)

The initial Flux bootstrap pointed the `flux-system` `GitRepository` at the
`flux/migration` branch (the branch the migration happened on). After the
migration MR merges to `main`, Flux must be repointed at `main` — otherwise
it keeps reconciling the pre-merge branch (or fails outright if that branch
is deleted).

**Do NOT** change `kubernetes/clusters/weisssrv/flux-system/gotk-sync.yaml` to
`branch: main` *before* the MR merges — Flux would immediately reconcile
`main`, which at that point still contains the pre-migration state, and
revert the cluster.

**Correct sequence after MR merge**:

Important context: at merge time, the cluster's `flux-system` GitRepository
is still tracking `flux/migration`. If you push the `branch: main` change
directly to `main`, Flux never sees it because it's not watching `main` yet.
If you then delete `flux/migration`, Flux starts failing with "couldn't find
remote ref refs/heads/flux/migration". The only safe way is to push the
branch-ref change to `flux/migration` FIRST (the branch Flux is currently
watching) so Flux self-migrates, then delete `flux/migration`.

1. Merge the migration MR to `main` via the GitLab UI (ff or squash merge).
2. Locally, push the branch-ref switch commit to `flux/migration` (Flux picks it up):
   ```bash
   git checkout flux/migration
   git merge main --ff-only          # bring flux/migration to the merged state
   $EDITOR kubernetes/clusters/weisssrv/flux-system/gotk-sync.yaml
   # change spec.ref.branch: flux/migration → main
   git commit -am "Flux: switch GitRepository ref from flux/migration to main"
   git push origin flux/migration
   ```
3. Force reconcile so Flux picks up the ref change while still watching `flux/migration`:
   ```bash
   task flux:reconcile
   ```
4. Verify the GitRepository now tracks main:
   ```bash
   kubectl -n flux-system get gitrepository flux-system -o jsonpath='{.spec.ref.branch}'
   # expected: main
   ```
5. Now push the SAME commit to `main` so the branches agree and post-merge
   git history shows the switch:
   ```bash
   git checkout main
   git merge flux/migration --ff-only
   git push origin main
   ```
6. Delete the migration branch on GitLab (Flux no longer tracks it):
   ```bash
   git push origin --delete flux/migration
   ```
7. Tidy the local branch:
   ```bash
   git branch -D flux/migration
   ```

**Alternative**: if step 2 is awkward (e.g., the branch was already deleted),
you can patch the GitRepository directly in-cluster and then commit the
matching change to `main`:
```bash
kubectl patch -n flux-system gitrepository flux-system --type=merge \
  -p '{"spec":{"ref":{"branch":"main"}}}'
# Then edit gotk-sync.yaml on main to match, commit, push.
```

Post-switch, all further GitOps work targets `main` as usual.

---

## References

- Flux documentation: https://fluxcd.io/flux/
- External Secrets Operator: https://external-secrets.io/latest/
- 1Password SDK provider: https://external-secrets.io/latest/provider/1password-sdk/
- Flux bootstrap (GitLab): https://fluxcd.io/flux/installation/bootstrap/gitlab/
- Multi-repo onboarding: `docs/30-multi-repo-onboarding.md`
- K3s deployment workflow: `docs/19-k3s-deployment.md`
- Disaster recovery: `docs/17-disaster-recovery.md`
