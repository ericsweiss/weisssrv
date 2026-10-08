#!/usr/bin/env bash
# Gates over the full rendered Flux corpus, shared by `task flux:lint` and the
# CI flux-lint job. Every gate runs even after one fails, so no `set -e`; exit
# 1 means a finding, exit 2 an unusable corpus or a gate that could not run.

# Usage: flux-corpus-gates.sh <rendered-corpus> [<versions-configmap>]
# Without the second argument the HelmRelease values validation is skipped.
set -uo pipefail

RENDER_ALL=${1:-}
VERSIONS_CONFIGMAP=${2:-}

if [ -z "$RENDER_ALL" ] || [ ! -f "$RENDER_ALL" ]; then
    echo "usage: $0 <rendered-corpus> [<versions-configmap>]" >&2
    exit 2
fi

# CRITICAL: an empty corpus passes every gate below vacuously. Zero rendered
# documents means the caller's per-Kustomization loop found nothing.
if [ ! -s "$RENDER_ALL" ]; then
    echo "ERROR: rendered corpus $RENDER_ALL is empty — check the cluster directory and each Kustomization's spec.path" >&2
    exit 2
fi

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$_SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 2

WORST=0
MERGED_CM=""
cleanup() { [ -n "$MERGED_CM" ] && rm -f "$MERGED_CM"; }
trap cleanup EXIT

# Every exit routes through here so an rc-2 gate is always explained.
finish() {
    if [ "$WORST" -eq 2 ]; then
        echo "corpus gates: a gate reported an OPERATOR ERROR, not a finding" >&2
    fi
    exit "$WORST"
}

# CRITICAL: a gate's rc 2 or more is an operator error (empty corpus,
# unparseable allowlist, missing dependency), never a policy finding — collapsed
# into 1 it reads as a NetworkPolicy violation.
note_rc() {
    if [ "$2" -ge 2 ]; then
        echo "ERROR: $1 exited $2 — operator error, not a finding" >&2
        WORST=2
    elif [ "$2" -ne 0 ] && [ "$WORST" -eq 0 ]; then
        WORST=1
    fi
}

# The tenants tree is a Kustomize aggregator, not a Flux Kustomization with a
# spec.path, so the caller's per-Kustomization loop never renders it and these
# gates would never see a tenant's own SecretStore or NetworkPolicy.
echo "=== Adding the tenants tree to the corpus ==="
printf '\n---\n' >> "$RENDER_ALL"
if ! kustomize build kubernetes/clusters/weisssrv/tenants >> "$RENDER_ALL"; then
    echo "ERROR: kustomize build failed for kubernetes/clusters/weisssrv/tenants — the corpus is incomplete" >&2
    WORST=2
fi
printf '\n---\n' >> "$RENDER_ALL"

# The bootstrap flux-system Kustomization's path is the cluster root, which
# flux-child-kustomizations.py never enumerates, so the Flux controllers are
# ungated without this. tenants/ repeats above; gates key on namespace/kind/name.
echo "=== Adding the cluster root to the corpus ==="
printf '\n---\n' >> "$RENDER_ALL"
if ! kustomize build kubernetes/clusters/weisssrv >> "$RENDER_ALL"; then
    echo "ERROR: kustomize build failed for kubernetes/clusters/weisssrv — the corpus is incomplete" >&2
    WORST=2
fi
printf '\n---\n' >> "$RENDER_ALL"

# CRITICAL: the appends above write `---` separators, which make the file
# non-empty without adding an object, so the size check up front cannot see a
# corpus of nothing but separators. Every gate below would report clean over it.
if ! grep -qE '^kind:' "$RENDER_ALL"; then
    echo "ERROR: rendered corpus holds no Kubernetes object — the gates below would inspect nothing" >&2
    exit 2
fi

# No workload may have both an HPA and a VPA driving the same resource
# (docs/33). --require-chart-native-vpas also asserts that the chart-native-HPA
# workloads each carry a memory-only VPA in the rendered corpus.
echo "=== Checking HPA/VPA invariant ==="
RC=0
# --allow-unjudged-vpa-caps: a chart renders most of these targets, so the
# corpus carries no limit to compare the cap against. validate-helm-values.py
# judges those caps against the chart-rendered limits.
python3 scripts/check-hpa-vpa-invariant.py --require-chart-native-vpas \
    --allow-unjudged-vpa-caps \
    --policy-config scripts/autoscaling-policy.yaml < "$RENDER_ALL" || RC=$?
note_rc "check-hpa-vpa-invariant.py" "$RC"

# A namespace that enables a ServiceMonitor/PodMonitor while running an
# ingress-deny policy must also admit the observability namespace, or the scrape
# is REJECTed and only TargetDown reveals it.
echo "=== Checking scrape/NetworkPolicy invariant ==="
RC=0
python3 scripts/check-scrape-netpol.py < "$RENDER_ALL" || RC=$?
note_rc "check-scrape-netpol.py" "$RC"

# Every namespace that owns a workload must carry a namespace-wide ingress-deny
# policy. The scrape gate above only inspects namespaces that already run one,
# so an unfenced namespace is invisible to it.
echo "=== Checking ingress default-deny coverage ==="
RC=0
python3 scripts/check-default-deny-coverage.py < "$RENDER_ALL" || RC=$?
note_rc "check-default-deny-coverage.py" "$RC"

# Cluster-scoped stores must declare spec.conditions, and those conditions must
# admit every namespace that consumes the store.
echo "=== Checking ClusterSecretStore scoping ==="
RC=0
python3 scripts/check-secretstore-scope.py < "$RENDER_ALL" || RC=$?
note_rc "check-secretstore-scope.py" "$RC"

# Every claim must pin its class. An omitted field is rewritten by the
# DefaultStorageClass admission plugin at create time, landing the PVC on the
# unbacked-up VM bootdisk.
echo "=== Checking PVC storageClassName ==="
RC=0
python3 scripts/check-pvc-storageclass.py < "$RENDER_ALL" || RC=$?
note_rc "check-pvc-storageclass.py" "$RC"

# A sized emptyDir outside its container's ephemeral-storage limit is evicted
# before the volume it sized ever fills.
echo "=== Checking sized emptyDir vs ephemeral-storage limits ==="
RC=0
python3 scripts/check-ephemeral-storage-cap.py < "$RENDER_ALL" || RC=$?
note_rc "check-ephemeral-storage-cap.py" "$RC"

# A registry-pull payload is assembled as a string inside a block scalar, so
# kustomize, kubeconform and yamllint all see an opaque blob: a lost brace ships
# a Secret the kubelet rejects as ImagePullBackOff on the next pull.
echo "=== Checking .dockerconfigjson payloads ==="
RC=0
python3 scripts/check-dockerconfigjson.py < "$RENDER_ALL" || RC=$?
note_rc "check-dockerconfigjson.py" "$RC"

# The nas_storage exports REJECT plaintext and the server certificate has no IP
# SAN, so a PV that drops xprtsec=tls or names the NAS by IP does not degrade —
# it fails to mount, after the pod is already scheduled.
echo "=== Checking NFS PersistentVolume TLS ==="
RC=0
# --cert-domain only names the wildcard domain in the IP-server message, so a
# config this cannot read costs wording, never the verdict.
CERT_DOMAIN=$(scripts/cluster-config-value.sh cluster_internal_domain || true)
python3 scripts/check-nfs-tls.py --cert-domain "$CERT_DOMAIN" < "$RENDER_ALL" || RC=$?
note_rc "check-nfs-tls.py" "$RC"

if [ -z "$VERSIONS_CONFIGMAP" ]; then
    echo "=== Skipping HelmRelease values validation (no versions ConfigMap argument) ==="
    finish
fi

# kustomize build emits HelmReleases verbatim, so a typo in a chart values key
# slips past kubeconform. --versions-configmap takes ONE file, so hand it the
# union of the two: a values block spells versions AND hostnames as placeholders.
echo "=== Schema-validating HelmRelease values blocks (helm template) ==="
MERGED_CM=$(mktemp)
if ! scripts/flux-env.sh merged-configmap "$VERSIONS_CONFIGMAP" > "$MERGED_CM"; then
    echo "ERROR: could not merge the substitution ConfigMaps" >&2
    WORST=2
elif [ ! -s "$MERGED_CM" ]; then
    # CRITICAL: an empty merge substitutes nothing, so every values block
    # validates with its ${...} placeholders intact and the gate passes
    # vacuously.
    echo "ERROR: scripts/flux-env.sh produced an empty merged ConfigMap from $VERSIONS_CONFIGMAP — a gate run over no substitutions is not a gate" >&2
    WORST=2
else
    RC=0
    python3 scripts/validate-helm-values.py --kubeconform \
        --releases scripts/helm-values-releases.yaml \
        --versions-configmap "$MERGED_CM" \
        --policy-config scripts/autoscaling-policy.yaml || RC=$?
    note_rc "validate-helm-values.py" "$RC"
fi

finish
