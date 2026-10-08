#!/usr/bin/env bash
# Waits for Reloader to roll a Deployment after its ConfigMap was patched.
# Usage: <namespace> <deployment> <generation-before> [timeout] [what-patched].
# Contract: weisssrv-lib docs/SCRIPTS.md - Shell helpers.

# CRITICAL: a generation that has not moved when the timeout expires is a
# FAILURE. The caller's `kubectl rollout status` would otherwise run against
# the unchanged generation and report success before the pod adopts the value.
set -euo pipefail

NS="${1:?usage: wait-for-reloader-roll.sh <namespace> <deployment> <generation-before> [timeout] [what]}"
DEPLOY="${2:?deployment name required}"
GEN_BEFORE="${3:?generation-before required}"
TIMEOUT="${4:-60}"
WHAT="${5:-config}"
POLL_INTERVAL="${RELOADER_POLL_INTERVAL:-2}"

# Wall-clock deadline, not an iteration count: the kubectl round trip costs real
# time, and a zero poll interval must still terminate.
deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
    # An unreadable generation reads as unchanged, so an API blip keeps polling
    # and the deadline fails loudly instead of reporting a roll never observed.
    if ! gen_now=$(kubectl get deployment "$DEPLOY" -n "$NS" \
        -o jsonpath='{.metadata.generation}' 2>/dev/null) || [ -z "$gen_now" ]; then
        gen_now="$GEN_BEFORE"
    fi
    if [ "$gen_now" != "$GEN_BEFORE" ]; then
        exit 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
        break
    fi
    sleep "$POLL_INTERVAL"
done

echo "ERROR: Reloader did not roll deployment/$DEPLOY within ${TIMEOUT}s (generation still $GEN_BEFORE)."
echo "       The $WHAT patch applied, but the pod has NOT restarted to adopt it."
echo "       Check Reloader (kubectl logs -n reloader deploy/reloader-reloader), then force it:"
# Delete, never `rollout restart`: the restartedAt annotation reads as drift to
# the kustomize-controller, which reverts it and un-does the restart.
echo "         kubectl delete pod -n $NS -l app.kubernetes.io/name=$DEPLOY"
echo "         kubectl wait -n $NS --for=condition=ready pod -l app.kubernetes.io/name=$DEPLOY --timeout=300s"
exit 1
