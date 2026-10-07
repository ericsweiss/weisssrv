#!/usr/bin/env bash
# Refresh one app's ExternalSecret and restart the pods that consume it.
# Usage: flux-rotate-secret.sh <app>. `set -e` gates the restart on a good
# refresh, and every restart is waited on before exit. See docs/29.
set -eo pipefail

APP="${1:-}"
if [ -z "$APP" ]; then
    echo "Usage: task flux:rotate-secret -- <app>"
    echo "Known apps: authentik, ci-cache, downloads, hermes, homarr, recipes, gitlab-runner, gitlab-runner-privileged, gitlab-agent, registry-cache, wg-easy, observability-exporters"
    exit 1
fi

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Every workload that reads the rotated Secret, derived from the cluster rather
# than from the per-app selectors below: a consumer the arm does not cover keeps
# the stale value until someone notices, so it is named here.
warn_unlisted_consumers() {
    local ns=$1 secret=$2 selector=${3:-}
    local workloads derived covered
    workloads=$(kubectl get deployment,statefulset,daemonset -n "$ns" -o json 2>/dev/null) || {
        echo "WARNING: could not list $ns workloads; consumer coverage is unknown" >&2
        return 0
    }
    derived=$(printf '%s' "$workloads" \
        | python3 "$_SCRIPT_DIR/flux-secret-consumers.py" "$secret" 2>/dev/null \
        | cut -f1 | sort) || return 0
    [ -n "$derived" ] || return 0
    if [ -n "$selector" ]; then
        covered=$(kubectl get deployment,statefulset,daemonset -n "$ns" -l "$selector" \
            -o name 2>/dev/null | sed 's#\.apps/#/#' | sort)
    else
        covered=$(kubectl get deployment,statefulset,daemonset -n "$ns" \
            -o name 2>/dev/null | sed 's#\.apps/#/#' | sort)
    fi
    local extra
    extra=$(comm -23 <(printf '%s\n' "$derived") <(printf '%s\n' "$covered"))
    [ -z "$extra" ] && return 0
    echo "WARNING: $ns/$secret is also read by workloads this arm does not restart:" >&2
    printf '%s\n' "$extra" | sed 's/^/  /' >&2
}

# The kustomize-managed exporter Deployments that read
# observability-exporter-secrets. Both adguard-exporter Deployments share
# app.kubernetes.io/name=adguard-exporter.
OBSERVABILITY_EXPORTERS='app.kubernetes.io/name in (proxmox-exporter,adguard-exporter,exportarr-sonarr,exportarr-radarr,exportarr-lidarr,exportarr-prowlarr)'

# The recipes workloads that read recipes-secrets. The mealie-pg-dump CronJob
# picks the new value up on its next run.
RECIPES_CONSUMERS='app.kubernetes.io/name in (mealie,mealie-postgres,bar-assistant,bar-assistant-meilisearch)'

case "$APP" in
    authentik)
        task flux:refresh-secret -- authentik/authentik-secrets
        # HelmRelease-managed. The Bitnami postgresql subchart labels its
        # StatefulSet app.kubernetes.io/name=postgresql (NOT authentik), so
        # match by instance to catch both server and postgres.
        warn_unlisted_consumers authentik authentik-secrets app.kubernetes.io/instance=authentik
        kubectl rollout restart deployment -n authentik -l app.kubernetes.io/instance=authentik
        kubectl rollout restart statefulset -n authentik -l app.kubernetes.io/instance=authentik
        for obj in $(kubectl get deployment,statefulset -n authentik -l app.kubernetes.io/instance=authentik -o name); do
            kubectl rollout status "$obj" -n authentik --timeout=300s
        done
        ;;
    downloads | vpn)
        task flux:refresh-secret -- downloads/vpn-credentials
        # gluetun-control-auth is a SEPARATE ExternalSecret with a 24h
        # refreshInterval and Reloader ignores Secret changes, so without this
        # force-sync a pod restart just re-reads the stale apikey.
        task flux:refresh-secret -- downloads/gluetun-control-auth
        # Only nzbget + qbittorrent consume these Secrets (Gluetun sidecars plus
        # the qbittorrent gluetun-exporter).
        warn_unlisted_consumers downloads vpn-credentials 'app.kubernetes.io/name in (nzbget,qbittorrent)'
        kubectl delete pod -n downloads -l 'app.kubernetes.io/name in (nzbget,qbittorrent)'
        kubectl wait --namespace downloads --for=condition=ready pod \
            -l 'app.kubernetes.io/name in (nzbget,qbittorrent)' --timeout=300s
        ;;
    recipes)
        task flux:refresh-secret -- recipes/recipes-secrets
        # By selector: salt-rim and bar-assistant-redis do not read the Secret,
        # and the mealie-pg-dump Job pods never reach Ready.
        warn_unlisted_consumers recipes recipes-secrets "$RECIPES_CONSUMERS"
        kubectl delete pod -n recipes -l "$RECIPES_CONSUMERS"
        kubectl wait --namespace recipes --for=condition=ready pod \
            -l "$RECIPES_CONSUMERS" --timeout=300s
        ;;
    gitlab-runner)
        task flux:refresh-secret -- gitlab-runner/gitlab-runner-token
        warn_unlisted_consumers gitlab-runner gitlab-runner-token
        kubectl rollout restart deployment/gitlab-runner -n gitlab-runner
        kubectl rollout status deployment/gitlab-runner -n gitlab-runner --timeout=300s
        ;;
    gitlab-runner-privileged)
        task flux:refresh-secret -- gitlab-runner-privileged/gitlab-runner-privileged-token
        warn_unlisted_consumers gitlab-runner-privileged gitlab-runner-privileged-token
        kubectl rollout restart deployment/gitlab-runner-privileged -n gitlab-runner-privileged
        kubectl rollout status deployment/gitlab-runner-privileged -n gitlab-runner-privileged --timeout=300s
        ;;
    gitlab-agent)
        task flux:refresh-secret -- gitlab-agent/gitlab-agent-token
        warn_unlisted_consumers gitlab-agent gitlab-agent-token
        kubectl rollout restart deployment -n gitlab-agent
        for obj in $(kubectl get deployment -n gitlab-agent -o name); do
            kubectl rollout status "$obj" -n gitlab-agent --timeout=300s
        done
        ;;
    registry-cache)
        task flux:refresh-secret -- registry-cache/registry-cache-secrets
        warn_unlisted_consumers registry-cache registry-cache-secrets app.kubernetes.io/name=registry-cache
        kubectl delete pod -n registry-cache -l app.kubernetes.io/name=registry-cache
        kubectl wait --namespace registry-cache --for=condition=ready pod \
            -l app.kubernetes.io/name=registry-cache --timeout=300s
        ;;
    ci-cache)
        task flux:refresh-secret -- ci-cache/ci-cache-garage-secrets
        task flux:refresh-secret -- gitlab-runner/s3access
        task flux:refresh-secret -- gitlab-runner-privileged/s3access
        # Prometheus reads the metrics bearer from its mounted Secret via the
        # config-reloader, so it is refreshed but nothing there is restarted.
        task flux:refresh-secret -- observability/observability-exporter-secrets
        # Recreate + emptyDir: the pod delete wipes the metadata dir, so the new
        # pod re-imports the rotated key pair from GARAGE_DEFAULT_*.
        warn_unlisted_consumers ci-cache ci-cache-garage-secrets app.kubernetes.io/name=ci-cache
        kubectl delete pod -n ci-cache -l app.kubernetes.io/name=ci-cache
        kubectl wait --namespace ci-cache --for=condition=ready pod \
            -l app.kubernetes.io/name=ci-cache --timeout=300s
        kubectl rollout restart deployment/gitlab-runner -n gitlab-runner
        kubectl rollout restart deployment/gitlab-runner-privileged -n gitlab-runner-privileged
        kubectl rollout status deployment/gitlab-runner -n gitlab-runner --timeout=300s
        kubectl rollout status deployment/gitlab-runner-privileged -n gitlab-runner-privileged --timeout=300s
        ;;
    hermes)
        task flux:refresh-secret -- hermes/hermes-secrets
        task flux:refresh-secret -- hermes/hermes-registry-pull
        warn_unlisted_consumers hermes hermes-secrets app.kubernetes.io/name=hermes
        kubectl delete pod -n hermes -l app.kubernetes.io/name=hermes
        kubectl wait --namespace hermes --for=condition=ready pod \
            -l app.kubernetes.io/name=hermes --timeout=300s
        ;;
    homarr)
        task flux:refresh-secret -- homarr/homarr-secrets
        warn_unlisted_consumers homarr homarr-secrets app.kubernetes.io/name=homarr
        kubectl delete pod -n homarr -l app.kubernetes.io/name=homarr
        kubectl wait --namespace homarr --for=condition=ready pod \
            -l app.kubernetes.io/name=homarr --timeout=300s
        ;;
    wg-easy)
        task flux:refresh-secret -- wg-easy/wg-easy-secrets
        warn_unlisted_consumers wg-easy wg-easy-secrets app.kubernetes.io/name=wg-easy
        kubectl delete pod -n wg-easy -l app.kubernetes.io/name=wg-easy
        kubectl wait --namespace wg-easy --for=condition=ready pod \
            -l app.kubernetes.io/name=wg-easy --timeout=300s
        ;;
    observability-exporters)
        task flux:refresh-secret -- observability/observability-exporter-secrets
        # By selector: the namespace also holds grafana/prometheus/loki.
        # Consumers read the value via env secretKeyRef, so a refresh is
        # invisible until the pod restarts; Prometheus self-reloads.
        warn_unlisted_consumers observability observability-exporter-secrets "$OBSERVABILITY_EXPORTERS"
        kubectl delete pod -n observability -l "$OBSERVABILITY_EXPORTERS"
        kubectl wait --namespace observability --for=condition=ready pod \
            -l "$OBSERVABILITY_EXPORTERS" --timeout=300s
        ;;
    *)
        echo "Unknown app: $APP"
        exit 1
        ;;
esac
