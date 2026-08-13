#!/usr/bin/env bash
# Post-deployment cluster verification, invoked by the deploy-verify CI job
# (.gitlab-ci.yml) as `bash scripts/deploy-verify.sh`.
#
# Contract: runs after .k3s-deploy-base (kubectl + jq) provisioned the runner
# and the inline flux-install step put `flux` on PATH. Reads KUSTOMIZE_VERSION,
# KUSTOMIZE_SHA256, and PYYAML_VERSION from the CI job environment (global
# `variables:` in .gitlab-ci.yml). Exits non-zero on any hard failure.
#
# NOTE: the FLUX_VERSION supply-chain pin stays inline in .gitlab-ci.yml (the
# step before this one) so flux-version-pin-check / task lint:flux-version-pin
# keep grepping it from that file.

# Bind + assert the CI-provided version pins up front: fail loudly if this
# script is ever run outside the CI job that sets them, and give shellcheck a
# visible assignment (no SC2154 for env-sourced vars).
KUSTOMIZE_VERSION="${KUSTOMIZE_VERSION:?deploy-verify.sh requires KUSTOMIZE_VERSION (set in .gitlab-ci.yml variables)}"
KUSTOMIZE_SHA256="${KUSTOMIZE_SHA256:?deploy-verify.sh requires KUSTOMIZE_SHA256 (set in .gitlab-ci.yml variables)}"
PYYAML_VERSION="${PYYAML_VERSION:?deploy-verify.sh requires PYYAML_VERSION (set in .gitlab-ci.yml variables)}"

set -eo pipefail

# Pure pod/HelmRelease/Ready-condition classifiers live in a sourced lib so they
# can be unit-tested without a live cluster (scripts/test_deploy_verify_lib.py).
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/deploy-verify-lib.sh
. "$_SCRIPT_DIR/deploy-verify-lib.sh"

# Install tools needed for server-side dry-run validation.
# These are already in the flux-lint job's before_script but deploy-verify
# extends .k3s-deploy-base (kubectl + jq only), so we install them here.
# stdout is muted, stderr is NOT: under `set -e` a failed install used to abort
# the job with a completely empty log (nothing had been echoed yet), leaving the
# operator to guess.
apt-get install -y -qq gettext-base > /dev/null
pip install --quiet "pyyaml==${PYYAML_VERSION}"
curl -fsSL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" -o /tmp/kustomize.tar.gz
echo "${KUSTOMIZE_SHA256}  /tmp/kustomize.tar.gz" | sha256sum -c -
tar xzf /tmp/kustomize.tar.gz -C /usr/local/bin kustomize

# Bounded retry helper for transient startup states.
# Suppresses output during polling; on timeout, prints the last
# failed attempt's output for diagnostics.
wait_for() {
  local desc="$1" timeout="$2" interval="$3"; shift 3
  local start_ts last_output
  start_ts=$(date +%s)
  while true; do
    last_output=$("$@" 2>&1) && return 0
    local elapsed=$(( $(date +%s) - start_ts ))
    if [ "$elapsed" -ge "$timeout" ]; then
      echo "wait_for: $desc timed out after ${elapsed}s"
      echo "Last check output:"
      echo "$last_output"
      return 1
    fi
    echo "wait_for: $desc not ready (${elapsed}s/${timeout}s)..."
    sleep "$interval"
  done
}

echo "=== Post-Deployment Verification ==="
# JQ_NOT_READY and the pod/HR classifiers come from deploy-verify-lib.sh (sourced
# above).

echo "Checking node status..."
check_nodes_ready() {
  NODE_OUTPUT=$(kubectl get nodes --no-headers)
  [ -z "$NODE_OUTPUT" ] && return 1
  NOT_READY=$(echo "$NODE_OUTPUT" | nodes_not_ready_count)
  [ "$NOT_READY" -eq 0 ]
}
if ! wait_for "all nodes Ready" 60 5 check_nodes_ready; then
  echo "ERROR: Node(s) not Ready after 60s"
  kubectl get nodes --no-headers
  exit 1
fi
kubectl get nodes --no-headers

EXIT=0

echo ""
echo "=== Server-side dry-run validation ==="
# Validate rendered manifests against the cluster API without applying.
# Catches CRD field mismatches and API validation issues that kubeconform
# (offline schema) cannot detect. Runs before Flux reconcile so issues
# are surfaced even if Flux itself would fail to apply them.
VERSIONS_CONFIGMAP=kubernetes/infrastructure/sources/versions-configmap.yaml
if [ ! -f "$VERSIONS_CONFIGMAP" ]; then
  echo "ERROR: $VERSIONS_CONFIGMAP not found"
  exit 1
fi
# Shared extraction (scripts/flux-render.sh) — same block flux-lint uses.
VARS=$(bash scripts/flux-render.sh export-versions "$VERSIONS_CONFIGMAP") \
  || { echo "ERROR: failed to extract version keys for dry-run"; exit 1; }
eval "$VARS"
if [ -n "$FLUX_ENVSUBST_VARS" ]; then
  for ks in kubernetes/clusters/weisssrv/*.yaml; do
    NAME=$(basename "$ks" .yaml)
    [ "$NAME" = "kustomization" ] && continue
    # Python source must reach python3 -c with NO leading whitespace, or it
    # raises IndentationError at module level — so the heredoc lines below stay
    # column-0, not aligned with the surrounding bash if-block.
    if ! SRCPATH=$(python3 -c "
import yaml
with open('$ks') as f:
    doc = yaml.safe_load(f)
print(doc.get('spec',{}).get('path','') or '')
"); then
      echo "    ERROR: failed to parse $ks"
      EXIT=1
      continue
    fi
    [ -z "$SRCPATH" ] && continue
    SRCPATH=${SRCPATH#./}
    echo "  dry-run: $SRCPATH"
    # `printf '%s\n'`, NEVER `echo`, when piping captured YAML: bash's echo
    # expands the thousands of `\n` literals inside the Grafana dashboard block
    # scalars into real newlines and breaks the parser mid-stream.
    # kustomize stderr goes to a tmpfile so deprecation notices cannot
    # contaminate the YAML stream feeding envsubst/kubectl.
    KS_ERR=$(mktemp)
    if ! RAW=$(kustomize build "$SRCPATH" 2>"$KS_ERR"); then
      echo "    FAIL: kustomize build failed for $SRCPATH"
      head -120 <"$KS_ERR"
      rm -f "$KS_ERR"
      EXIT=1
      continue
    fi
    rm -f "$KS_ERR"
    RENDERED=$(printf '%s\n' "$RAW" | envsubst "$FLUX_ENVSUBST_VARS")
    # SSA, not plain `apply --dry-run=server`: the latter needs the legacy
    # last-applied-configuration annotation, which Flux-owned resources lack.
    # --force-conflicts lets the dry-run win field-ownership disputes with
    # kustomize-controller; nothing is persisted.
    # The `if` wrapper keeps a non-zero kubectl exit from aborting under set -e
    # before DRYRUN_RC is checked.
    if DRYRUN_ERR=$(printf '%s\n' "$RENDERED" | kubectl apply \
      --server-side --dry-run=server --force-conflicts \
      --field-manager=ci-deploy-verify -f - 2>&1); then
      DRYRUN_RC=0
    else
      DRYRUN_RC=$?
    fi
    if [ "$DRYRUN_RC" -ne 0 ]; then
      echo "    FAIL: server-side dry-run rejected manifests from $SRCPATH"
      # Filter for actual error lines; fall back to raw output if no
      # match. `|| true` keeps grep's no-match exit (1) from aborting
      # the script under `set -o pipefail`. Also catches kubectl
      # error patterns we didn't enumerate (e.g. "forbidden",
      # "timeout") via the fallback path.
      FILTERED_ERR=$(grep -iE "error|invalid|did not find|cannot|forbidden|timeout" \
        <<<"$DRYRUN_ERR" || true)
      # Use here-string (<<<) instead of `printf | head` so head's
      # early stdin close doesn't SIGPIPE the writer and trip
      # `set -o pipefail`.
      if [ -n "$FILTERED_ERR" ]; then
        head -30 <<<"$FILTERED_ERR"
      else
        head -30 <<<"$DRYRUN_ERR"
      fi
      EXIT=1
    fi
  done
fi

# --- Known-transitional carve-out: the metrics-server AddOn cutover ---------
#
# `infrastructure-metrics-server` is the one Flux stage whose Ready=False is a
# DESIGNED, operator-paced state rather than a fault: the HelmRelease cannot
# install while k3s's packaged AddOn still owns v1beta1.metrics.k8s.io, and the
# `--disable=metrics-server` that removes it lands from Ansible, never from the
# merge pipeline (docs/33 § metrics-server).
#
# Without this carve-out that one stage poisons STEADY_STATE below, which
# silently DOWNGRADES six unrelated failure classes to WARNING (non-Ready
# ExternalSecrets, an empty observability namespace, failing/unready
# observability pods, missing/failing observability HelmReleases) and collapses
# their wait budgets — on every main pipeline, for as long as the window is
# open. So the two known objects are excluded by name, loudly, and NOTHING else
# changes: every other resource keeps its normal severity and the job still
# fails on it.
#
# The carve-out is keyed to LIVE evidence that the cutover is still open — the
# AddOn stamps its objects with Rancher objectset annotations — so it expires by
# itself the moment the k3s deploy lands. While the window is open, ANY fault in
# these two objects (cutover-related or not) is deferred to a NOTICE; the gates
# re-arm when the APIService loses its objectset stamp, so a masked defect
# surfaces at cutover close rather than being lost.
CUTOVER_KS="flux-system/infrastructure-metrics-server"
CUTOVER_HR="kube-system/metrics-server"
CUTOVER_EXCLUDE=""
metrics_server_addon_owned() {
  kubectl get apiservice v1beta1.metrics.k8s.io -o json 2>/dev/null \
    | jq -e '[((.metadata.annotations // {}) + (.metadata.labels // {}) | keys[])
             | select(startswith("objectset.rio.cattle.io/"))] | length > 0' >/dev/null 2>&1
}
if metrics_server_addon_owned; then
  CUTOVER_EXCLUDE="$CUTOVER_KS $CUTOVER_HR"
  echo ""
  echo "NOTICE: metrics-server AddOn cutover is OPEN — v1beta1.metrics.k8s.io is still"
  echo "        owned by the k3s packaged AddOn (objectset.rio.cattle.io/* present)."
  echo "        $CUTOVER_KS and $CUTOVER_HR are Ready=False BY DESIGN"
  echo "        and are excluded from the readiness gates below. Close the window with"
  echo "        \`task k3s:deploy\` (docs/33 § metrics-server; docs/16 § Open follow-ups)."
  echo "        Every other resource keeps its normal severity."
fi

# Snapshot pre-reconcile state to distinguish steady-state from bootstrap.
# If all Kustomizations are already Ready, this is a steady-state push and
# non-Ready ExternalSecrets should be treated as failures. During bootstrap
# or recovery (some Kustomizations not yet Ready), ESO may still be starting
# and ES non-readiness is expected.
# The trailing || echo 999 guards the WHOLE pipeline: under pipefail a
# kubectl failure would otherwise propagate out of the substitution and
# set -e would kill the script — the guard degrades to bootstrap mode instead.
PRE_KS_NOT_READY=$(kubectl get kustomizations.kustomize.toolkit.fluxcd.io -A -o json 2>/dev/null \
  | without_items "$CUTOVER_EXCLUDE" | count_not_ready || echo "999")
STEADY_STATE=$(steady_state "$PRE_KS_NOT_READY")
if [ "$STEADY_STATE" = "true" ]; then
  echo "Cluster is steady-state (all Kustomizations were Ready pre-reconcile)"
else
  echo "Cluster is in bootstrap/recovery ($PRE_KS_NOT_READY Kustomization(s) not Ready pre-reconcile)"
fi

echo ""
echo "=== Triggering Flux reconciliation ==="
# Force Flux to reconcile the current commit before checking status.
# Without this, we would check stale state from the previous reconcile cycle.
echo "Triggering Flux source reconciliation..."
if ! flux reconcile source git flux-system --timeout=2m; then
  echo "ERROR: Flux source reconciliation failed or timed out"
  EXIT=1
fi
echo "Triggering root kustomization reconciliation..."
if ! flux reconcile kustomization flux-system --timeout=3m --with-source; then
  echo "ERROR: Flux root kustomization reconciliation failed or timed out"
  EXIT=1
fi
# Allow downstream kustomizations time to reconcile after the root
sleep 15

echo ""
echo "Flux controllers:"
flux check

echo ""
echo "Flux Kustomizations:"
flux get kustomizations -A

echo ""
echo "Flux HelmReleases:"
flux get helmreleases -A

echo ""
echo "ExternalSecrets:"
kubectl get externalsecrets -A

echo ""
echo "ExternalSecret readiness:"
check_externalsecrets_ready() {
  ES_COUNT=$(kubectl get externalsecrets -A -o json 2>/dev/null | count_not_ready || echo "999")
  [ "$ES_COUNT" -eq 0 ]
}
if ! wait_for "ExternalSecrets ready" 90 10 check_externalsecrets_ready; then
  # Outer guards: under pipefail a kubectl failure would otherwise escape the
  # substitution/pipeline and set -e would kill the script mid-diagnostics.
  ES_NOT_READY=$(kubectl get externalsecrets -A -o json 2>/dev/null | count_not_ready || echo "999")
  { kubectl get externalsecrets -A -o json 2>/dev/null || echo '{"items":[]}'; } | not_ready_ns_names
  if [ "$STEADY_STATE" = "true" ]; then
    echo "ERROR: $ES_NOT_READY ExternalSecret(s) not Ready on a steady-state cluster"
    EXIT=1
  else
    echo "WARNING: $ES_NOT_READY ExternalSecret(s) not Ready (bootstrap/recovery)"
  fi
else
  echo "All ExternalSecrets ready"
fi

# Fail if any Flux resource (Kustomization, HelmRelease, GitRepository,
# HelmRepository, ...) is not Ready. Queried as CRDs via kubectl -o json
# because `flux get all` emits only tabular output (fluxcd/flux2 #1904/#3535),
# and text parsing counted stderr warnings as outages.
echo ""
echo "Checking for non-Ready Flux resources..."
FLUX_KINDS="kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io,gitrepositories.source.toolkit.fluxcd.io,helmrepositories.source.toolkit.fluxcd.io,ocirepositories.source.toolkit.fluxcd.io"
check_flux_resources_ready() {
  FLUX_ALL=$(kubectl get "$FLUX_KINDS" -A -o json 2>/dev/null) || return 1
  NOT_READY=$(echo "$FLUX_ALL" | without_items "$CUTOVER_EXCLUDE" | count_not_ready)
  [ "$NOT_READY" -eq 0 ]
}
# 5 min headroom — major chart bumps (kube-prometheus-stack, Loki) can
# take 2-4 min to reconcile through HelmRelease + child CRD updates +
# rollout, and verify runs immediately after `git push` of those bumps.
if ! wait_for "Flux resources ready" 300 10 check_flux_resources_ready; then
  FLUX_ALL=$(kubectl get "$FLUX_KINDS" -A -o json 2>/dev/null | without_items "$CUTOVER_EXCLUDE" \
    || echo '{"items":[]}')
  NOT_READY_COUNT=$(echo "$FLUX_ALL" | count_not_ready)
  echo "ERROR: $NOT_READY_COUNT Flux resource(s) not Ready:"
  echo "$FLUX_ALL" | jq -r ".items[] | $JQ_NOT_READY | \"  \(.kind)/\(.metadata.namespace)/\(.metadata.name): \((.status.conditions // []) | map(select(.type == \"Ready\")) | .[0].message // \"no Ready condition\")\""
  EXIT=1
else
  echo "All Flux resources ready"
fi

# Gate specifically on the top-level Flux Kustomizations — if any is
# stuck (any child HelmRelease failing), the above broad check catches
# it too, but naming them explicitly yields clear failure signals in
# the pipeline output.
# Children derived from kubernetes/clusters/weisssrv/*.yaml in dependsOn order,
# never hand-listed — a missing stage (e.g. infrastructure-crds) would leave a
# wedged CRD stage with no named failure signal here.
TOP_KUSTOMIZATIONS=$(python3 "$_SCRIPT_DIR/flux-child-kustomizations.py")
if [ -z "$TOP_KUSTOMIZATIONS" ]; then
  echo "ERROR: could not derive the child Kustomization list from kubernetes/clusters/weisssrv/"
  exit 1
fi
# The open metrics-server cutover (above) is the one excluded name; it is still
# printed, as a NOTICE, so it never disappears from the pipeline output.
gated_kustomizations() {
  for ks_name in $TOP_KUSTOMIZATIONS; do
    case " $CUTOVER_EXCLUDE " in
      *" flux-system/$ks_name "*) continue ;;
    esac
    echo "$ks_name"
  done
}
GATED_KUSTOMIZATIONS=$(gated_kustomizations)
echo "Top-level Kustomizations under gate: $(echo "$GATED_KUSTOMIZATIONS" | tr '\n' ' ')"
check_top_kustomizations_ready() {
  for ks_name in $GATED_KUSTOMIZATIONS; do
    KS_READY=$(kubectl -n flux-system get kustomization "$ks_name" \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
    if [ "$KS_READY" != "True" ]; then return 1; fi
  done
}
if ! wait_for "top-level Kustomizations ready" 180 5 check_top_kustomizations_ready; then
  for ks_name in $GATED_KUSTOMIZATIONS; do
    KS_READY=$(kubectl -n flux-system get kustomization "$ks_name" \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
    if [ "$KS_READY" != "True" ]; then
      echo "ERROR: flux-system/$ks_name Kustomization not Ready (status=$KS_READY)"
      EXIT=1
    fi
  done
else
  echo "All top-level Kustomizations ready"
fi
for ks_name in $TOP_KUSTOMIZATIONS; do
  case " $CUTOVER_EXCLUDE " in
    *" flux-system/$ks_name "*)
      KS_READY=$(kubectl -n flux-system get kustomization "$ks_name" \
        -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
      echo "NOTICE: flux-system/$ks_name not Ready (status=$KS_READY) — expected: metrics-server cutover open"
      ;;
  esac
done

echo ""
echo "=== Observability Stack ==="
check_obs_pods_healthy() {
  OBS_PODS=$(kubectl get pods -n observability --no-headers 2>/dev/null || true)
  [ -z "$OBS_PODS" ] && return 1
  [ -z "$(echo "$OBS_PODS" | pods_not_running_or_completed)" ] || return 1
  [ -z "$(echo "$OBS_PODS" | pods_running_unready)" ] || return 1
}
# Long timeout in steady state covers chart-upgrade rollouts; short
# timeout in bootstrap/recovery so we drop into the WARNING branch
# below quickly instead of burning the full budget when the namespace
# is legitimately empty.
OBS_PODS_WAIT=240
[ "$STEADY_STATE" != "true" ] && OBS_PODS_WAIT=30
if ! wait_for "observability pods healthy" "$OBS_PODS_WAIT" 10 check_obs_pods_healthy; then
  OBS_PODS=$(kubectl get pods -n observability --no-headers 2>/dev/null || true)
  if [ -z "$OBS_PODS" ]; then
    if [ "$STEADY_STATE" = "true" ]; then
      echo "ERROR: No pods in observability namespace on a steady-state cluster"
      EXIT=1
    else
      echo "WARNING: No pods in observability namespace (bootstrap/recovery)"
    fi
  else
    OBS_BAD=$(echo "$OBS_PODS" | pods_not_running_or_completed)
    if [ -n "$OBS_BAD" ]; then
      # In bootstrap/recovery, pods may legitimately be Pending,
      # ContainerCreating, or initializing mid-rollout — warn for
      # those. Anything not on the transient-state allowlist (any
      # form of CrashLoopBackOff, ImagePullBackOff, InvalidImageName,
      # OOMKilled, etc.) is a real bug even during bootstrap.
      # Steady-state always fails on any non-Running pod.
      OBS_NON_TRANSIENT=$(echo "$OBS_BAD" | pods_non_transient)
      if [ "$STEADY_STATE" = "true" ] || [ -n "$OBS_NON_TRANSIENT" ]; then
        echo "ERROR: Observability pods in failing state:"
        echo "$OBS_BAD"
        EXIT=1
      else
        echo "WARNING: Some observability pods not yet Running (bootstrap/recovery):"
        echo "$OBS_BAD"
      fi
    else
      OBS_UNREADY=$(echo "$OBS_PODS" | pods_running_unready)
      if [ -n "$OBS_UNREADY" ]; then
        echo "WARNING: Some observability pods have unready containers:"
        echo "$OBS_UNREADY"
        if [ "$STEADY_STATE" = "true" ]; then EXIT=1; fi
      fi
    fi
  fi
else
  echo "All observability pods healthy"
fi
check_obs_helmreleases_ready() {
  OBS_HR_JSON=$(kubectl get helmreleases -n observability -o json 2>/dev/null || echo '{"items":[]}')
  OBS_HR_COUNT=$(echo "$OBS_HR_JSON" | jq '.items | length' 2>/dev/null || echo "0")
  [ "${OBS_HR_COUNT:-0}" -eq 0 ] && return 1
  OBS_HR_BAD=$(echo "$OBS_HR_JSON" | helmreleases_not_ready_names)
  [ -z "$OBS_HR_BAD" ]
}
# Same pattern as the pods wait above: long budget in steady state for
# chart-upgrade reconciliation, short budget in bootstrap/recovery so
# the WARNING branch handles the legitimately-empty case without delay.
OBS_HR_WAIT=300
[ "$STEADY_STATE" != "true" ] && OBS_HR_WAIT=30
if ! wait_for "observability HelmReleases ready" "$OBS_HR_WAIT" 10 check_obs_helmreleases_ready; then
  OBS_HR_JSON=$(kubectl get helmreleases -n observability -o json 2>/dev/null || echo '{"items":[]}')
  OBS_HR_COUNT=$(echo "$OBS_HR_JSON" | jq '.items | length' 2>/dev/null || echo "0")
  if [ "${OBS_HR_COUNT:-0}" -eq 0 ]; then
    if [ "$STEADY_STATE" = "true" ]; then
      echo "ERROR: No HelmReleases in observability namespace on a steady-state cluster"
      EXIT=1
    else
      echo "WARNING: No HelmReleases in observability namespace (bootstrap/recovery)"
    fi
  else
    OBS_HR_BAD=$(echo "$OBS_HR_JSON" | helmreleases_not_ready_names)
    # In bootstrap/recovery, HelmReleases may still be reconciling on
    # first install — warn instead of failing hard. But hard-failure
    # reasons (InstallFailed/UpgradeFailed/etc.) AND any HR with a
    # non-zero .status.failures (controller has retried at least once)
    # are real bugs even during bootstrap, so always fail on those
    # (helmreleases_hard_failed in deploy-verify-lib.sh).
    OBS_HR_FAILED=$(echo "$OBS_HR_JSON" | helmreleases_hard_failed)
    if [ "$STEADY_STATE" = "true" ] || [ -n "$OBS_HR_FAILED" ]; then
      echo "ERROR: Not all observability HelmReleases are Ready:"
      echo "$OBS_HR_BAD"
      EXIT=1
    else
      echo "WARNING: Some observability HelmReleases still reconciling (bootstrap/recovery):"
      echo "$OBS_HR_BAD"
    fi
  fi
else
  echo "All observability HelmReleases ready"
fi

echo ""
echo "LoadBalancer services:"
kubectl get svc -A | grep LoadBalancer || true

echo ""
echo "Checking GitLab health..."
# Shared internal-first/external-fallback probe (deploy-verify-lib.sh), the same
# one post-maintenance-verify.sh uses; only the retry budget differs.
check_gitlab_ready() {
  [ "$(gitlab_health_code /-/readiness)" = "200" ]
}
if wait_for "GitLab readiness" 60 3 check_gitlab_ready; then
  echo "GitLab API: OK"
else
  echo "ERROR: GitLab health check failed after 60s of retries"
  EXIT=1
fi

echo ""
echo "=== Verification Complete (exit=$EXIT) ==="
exit $EXIT
