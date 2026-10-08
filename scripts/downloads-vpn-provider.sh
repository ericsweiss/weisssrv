#!/usr/bin/env bash
# Switch a download client's Gluetun VPN provider live, GitOps-safely.
# Usage: downloads-vpn-provider.sh <app> <provider> <countries> [KEY=value ...]
# app is nzbget or qbittorrent; trailing KEY=value is the `task` wrapper form.
set -eo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

APP="${1:-}"
PROVIDER="${2:-}"
COUNTRIES="${3:-}"
shift 3 2>/dev/null || true

usage() {
    echo "Usage: task downloads:vpn-provider -- APP=<nzbget|qbittorrent> PROVIDER=<privadovpn> [COUNTRIES=<country[,country]>]"
    echo "  privadovpn  -> gluetun 'privado'       (OpenVPN user/pass)"
    echo "  Adding a second provider: docs/21 § VPN Management."
}

for kv in "$@"; do
    case "$kv" in
        APP=*) APP="${kv#APP=}" ;;
        PROVIDER=*) PROVIDER="${kv#PROVIDER=}" ;;
        COUNTRIES=*) COUNTRIES="${kv#COUNTRIES=}" ;;
        # A bare token means a multi-word COUNTRIES value was word-split by the
        # `--` form. Unhandled it truncates the country, which gluetun cannot
        # match — rolling a VPN-on app onto a server-less config.
        *)
            echo "ERROR: unexpected token '$kv' — a multi-word COUNTRIES value is word-split by the '--' form."
            echo "       Use the native quoted form for multi-word countries, e.g.:"
            echo "         task downloads:vpn-provider APP=qbittorrent PROVIDER=privadovpn COUNTRIES=\"United States\""
            usage
            exit 1
            ;;
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

# Alias -> gluetun's exact string, so only a known value reaches the patch. A
# gluetun provider name can contain a space, so GLUE is always quoted.
case "$PROVIDER" in
    privado | privadovpn) GLUE="privado" ;;
    *) echo "ERROR: unknown/unwired PROVIDER '$PROVIDER'"; usage; exit 1 ;;
esac

# COUNTRIES is the one free-form value that reaches the JSON patch. Strip the
# allowed country-name charset and reject if anything survives.
if [ -n "$COUNTRIES" ]; then
    BAD=$(printf '%s' "$COUNTRIES" | tr -d "A-Za-z0-9 ,.'-")
    if [ -n "$BAD" ]; then
        echo "ERROR: COUNTRIES contains unsupported characters: '$BAD'"
        echo "       Allowed: letters, digits, spaces, commas, '.', '-', apostrophe."
        echo "       e.g. COUNTRIES=Netherlands  or  COUNTRIES=\"United States,Canada\""
        usage
        exit 1
    fi
fi

CM="$APP-vpn-config"
VPN_ENABLED=$(kubectl get configmap "$CM" -n downloads -o jsonpath='{.data.vpn_enabled}' 2>/dev/null || echo "")

# Pre-flight the TARGET provider against the credentials actually wired, not
# just the alias allowlist: switching a VPN-on app to a provider with no creds
# takes it fully down. Refuse while the app is ON; when OFF, stage with a warning.
set +e
MISSING=$("$_SCRIPT_DIR/vpn-credcheck.sh" "$APP" "$GLUE")
CRED_RC=$?
set -e
if [ "$CRED_RC" -ne 0 ]; then
    # rc=2: the alias map above resolved PROVIDER to a gluetun string the
    # credential check does not know — the two lists have drifted.
    echo "ERROR: credential pre-flight failed (rc=$CRED_RC) for gluetun provider '$GLUE'."
    echo "       The PROVIDER alias map and scripts/vpn-credcheck.sh have drifted;"
    echo "       add '$GLUE' and its required vpn-credentials keys there (docs/21)."
    exit 1
fi
if [ -n "$MISSING" ]; then
    if [ "$VPN_ENABLED" = "true" ]; then
        echo "ERROR: provider '$GLUE' is not fully wired — vpn-credentials is missing: $MISSING"
        echo "       $APP has the VPN ON, so switching now would roll it into CrashLoopBackOff"
        echo "       (gluetun settings validation fails on the missing OPENVPN_*_SECRETFILE)."
        echo "       Populate 1Password + uncomment the provider block in externalsecret.yaml first (docs/21)."
        exit 1
    fi
    echo "WARNING: provider '$GLUE' is not fully wired — vpn-credentials is missing: $MISSING"
    echo "         Saving the selection, but 'task downloads:vpn -- APP=$APP STATE=on' will"
    echo "         crash-loop until 1Password is populated (docs/21)."
fi

# There is no early idempotency exit here, so a no-op re-set leaves the
# deployment generation unchanged — that must not read as a failed roll.
CUR_PROVIDER=$(kubectl get configmap "$CM" -n downloads -o jsonpath='{.data.vpn_provider}' 2>/dev/null || echo "")
CUR_COUNTRIES=$(kubectl get configmap "$CM" -n downloads -o jsonpath='{.data.server_countries}' 2>/dev/null || echo "")
CHANGED=false
if [ "$CUR_PROVIDER" != "$GLUE" ]; then CHANGED=true; fi
if [ -n "$COUNTRIES" ] && [ "$CUR_COUNTRIES" != "$COUNTRIES" ]; then CHANGED=true; fi
# Read before the patch, and only on the path that waits on it: a failure here
# would otherwise block merely saving a provider selection.
GEN_BEFORE=0
if [ "$CHANGED" = "true" ] && [ "$VPN_ENABLED" = "true" ]; then
    GEN_BEFORE=$(kubectl get deployment "$APP" -n downloads -o jsonpath='{.metadata.generation}') || {
        echo "ERROR: could not read deployment/$APP's generation in namespace downloads."
        echo "       Refusing to patch $CM: the Reloader roll could not be verified."
        exit 1
    }
fi
if [ -n "$COUNTRIES" ]; then
    PATCH="{\"data\":{\"vpn_provider\":\"$GLUE\",\"server_countries\":\"$COUNTRIES\"}}"
else
    PATCH="{\"data\":{\"vpn_provider\":\"$GLUE\"}}"
fi
echo "Patching $CM: vpn_provider=$GLUE${COUNTRIES:+, server_countries=$COUNTRIES} ..."
kubectl patch configmap "$CM" -n downloads --type merge -p "$PATCH"
if [ "$VPN_ENABLED" != "true" ]; then
    echo "$APP vpn_enabled=$VPN_ENABLED — provider saved; turn the VPN on with: task downloads:vpn -- APP=$APP STATE=on"
    exit 0
fi
if [ "$CHANGED" = "true" ]; then
    echo "Waiting for Reloader to roll deployment/$APP ..."
    "$_SCRIPT_DIR/wait-for-reloader-roll.sh" downloads "$APP" "$GEN_BEFORE" 60 provider
    # A switch to an unpopulated provider makes gluetun fail to start; rollout
    # status then times out and this exits non-zero (the app is briefly down
    # under Recreate).
    kubectl rollout status deployment/"$APP" -n downloads --timeout=180s
else
    echo "$APP already on provider '$GLUE'${COUNTRIES:+ / '$COUNTRIES'} — no roll needed."
fi
echo "Provider now: $(kubectl get configmap "$CM" -n downloads -o jsonpath='{.data.vpn_provider}')"
echo "Public IP via $APP's tunnel:"
kubectl exec -n downloads deployment/"$APP" -c gluetun -- wget -qO- -T 10 https://checkip.amazonaws.com 2>/dev/null ||
    echo "  (could not fetch public IP — see task downloads:vpn-status)"
