#!/usr/bin/env bash
# Toggle a download client's Gluetun VPN live, GitOps-safely.
# Usage: downloads-vpn.sh <nzbget|qbittorrent> <on|off> [KEY=value ...], where the
# KEY=value form comes from `task downloads:vpn` and overrides the positionals.
set -eo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

APP="${1:-}"
STATE="${2:-}"
shift 2 2>/dev/null || true

usage() {
    echo "Usage: task downloads:vpn -- APP=<nzbget|qbittorrent> STATE=<on|off>"
    echo "  e.g. task downloads:vpn -- APP=nzbget STATE=on"
}

for kv in "$@"; do
    case "$kv" in
        APP=*) APP="${kv#APP=}" ;;
        STATE=*) STATE="${kv#STATE=}" ;;
        *) echo "ERROR: unexpected argument '$kv' (expected KEY=value)"; usage; exit 1 ;;
    esac
done

# vpn-credcheck.sh owns the app allowlist; --list-apps prints it one per line.
# Capture then test, not `| grep -q`: grep exits on the first match and pipefail
# turns the producer's SIGPIPE into a false rejection of a valid APP.
APPS="$("$_SCRIPT_DIR/vpn-credcheck.sh" --list-apps | tr '\n' ' ')"
APPS="${APPS% }"
case " $APPS " in
    *" $APP "*) ;;
    *) echo "ERROR: APP must be one of: $APPS"; usage; exit 1 ;;
esac
case "$STATE" in
    on) VAL=true ;;
    off) VAL=false ;;
    *) echo "ERROR: STATE must be on or off"; usage; exit 1 ;;
esac

CM="$APP-vpn-config"
CUR=$(kubectl get configmap "$CM" -n downloads -o jsonpath='{.data.vpn_enabled}' 2>/dev/null || echo "")
if [ "$CUR" = "$VAL" ]; then
    echo "$APP VPN already $STATE (vpn_enabled=$VAL) — nothing to do."
    exit 0
fi

# Pre-flight the provider credentials BEFORE enabling: turning the VPN on rolls
# the pod through gluetun's settings validation, and an unwired provider
# CrashLoopBackOffs the app, fully DOWN under Recreate until reverted.
if [ "$VAL" = "true" ]; then
    set +e
    MISSING=$("$_SCRIPT_DIR/vpn-credcheck.sh" "$APP")
    CRED_RC=$?
    set -e
    # rc=2 is a refusal, not "nothing missing": the check reads the LIVE
    # vpn_provider, so a hand-patched or blanked provider lands here.
    if [ "$CRED_RC" -ne 0 ]; then
        PROV=$(kubectl get configmap "$CM" -n downloads -o jsonpath='{.data.vpn_provider}' 2>/dev/null || echo "")
        echo "ERROR: cannot enable VPN — credential pre-flight failed (rc=$CRED_RC) for provider '$PROV'."
        echo "       Fix the ConfigMap's vpn_provider (or teach scripts/vpn-credcheck.sh"
        echo "       the provider's required keys) before enabling; gluetun would fail"
        echo "       settings validation and $APP would be fully down under Recreate."
        exit 1
    fi
    if [ -n "$MISSING" ]; then
        PROV=$(kubectl get configmap "$CM" -n downloads -o jsonpath='{.data.vpn_provider}' 2>/dev/null || echo "")
        echo "ERROR: cannot enable VPN — provider '$PROV' is not fully wired;"
        echo "       vpn-credentials is missing: $MISSING"
        echo "       Enabling now would roll $APP into CrashLoopBackOff (gluetun"
        echo "       settings validation fails on the missing OPENVPN_*_SECRETFILE)."
        echo "       Populate the provider's 1Password item and its entries in"
        echo "       download-clients/externalsecret.yaml (docs/21), or pick a"
        echo "       wired provider:"
        echo "         task downloads:vpn-provider -- APP=$APP PROVIDER=<privadovpn>"
        exit 1
    fi
fi

if [ "$APP" = "qbittorrent" ] && [ "$VAL" = "false" ]; then
    echo "WARNING: qbittorrent keeps its gluetun-exporter + PodMonitor when the VPN is"
    echo "         off, so gluetun_vpn_status=0 and VPNDown will fire after 15m. This"
    echo "         live toggle is for short-lived ops; for a durable VPN-off qbittorrent"
    echo "         also drop the exporter + PodMonitor in git (README § Per-App VPN Control)."
fi

GEN_BEFORE=$(kubectl get deployment "$APP" -n downloads -o jsonpath='{.metadata.generation}') || {
    echo "ERROR: could not read deployment/$APP's generation in namespace downloads."
    echo "       Refusing to patch $CM: the Reloader roll could not be verified."
    exit 1
}
echo "Patching $CM: vpn_enabled=$VAL ..."
kubectl patch configmap "$CM" -n downloads --type merge -p "{\"data\":{\"vpn_enabled\":\"$VAL\"}}"
echo "Waiting for Reloader to roll deployment/$APP ..."
"$_SCRIPT_DIR/wait-for-reloader-roll.sh" downloads "$APP" "$GEN_BEFORE" 60 vpn_enabled
kubectl rollout status deployment/"$APP" -n downloads --timeout=180s
echo "$APP VPN is now: vpn_enabled=$(kubectl get configmap "$CM" -n downloads -o jsonpath='{.data.vpn_enabled}')"
if [ "$VAL" = "true" ]; then
    echo "Verify egress with: task downloads:verify-vpn"
fi
