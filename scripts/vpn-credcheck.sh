#!/usr/bin/env bash
# Print the vpn-credentials Secret keys required by a download client's VPN
# provider that are missing, space-separated on stdout. Single source of the
# provider -> required-keys map for the `task downloads:vpn*` pre-flights.

# Usage: scripts/vpn-credcheck.sh <nzbget|qbittorrent> [gluetun-provider-string]
#        scripts/vpn-credcheck.sh --list-apps
# The provider defaults to the app's <app>-vpn-config ConfigMap (docs/21).

# CRITICAL: exit 1 is a usage or bad-app error, exit 2 an empty or unknown
# provider. A caller must refuse on 2 rather than read empty output as
# "nothing missing" — a blanked vpn_provider is what this pre-flight catches.
set -euo pipefail

UNKNOWN_PROVIDER_RC=2

NS=downloads

# The VPN-capable download clients. Callers read this list with --list-apps
# rather than restating it.
APPS="nzbget qbittorrent"

if [ "${1:-}" = "--list-apps" ]; then
    # shellcheck disable=SC2086  # word splitting is how the list becomes lines
    printf '%s\n' $APPS
    exit 0
fi

app="${1:-}"
provider="${2-}"

case " $APPS " in
    *" $app "*) ;;
    *)
        echo "Usage: $0 <$(echo "$APPS" | tr ' ' '|')> [gluetun-provider-string]" >&2
        exit 1
        ;;
esac

cm="$app-vpn-config"

# Fall back to the ConfigMap's current vpn_provider when no provider was passed.
if [ -z "$provider" ]; then
    provider="$(kubectl get configmap "$cm" -n "$NS" \
        -o jsonpath='{.data.vpn_provider}' 2>/dev/null || true)"
fi

# Map gluetun's provider string to the vpn-credentials keys its settings
# validation requires. An unknown or empty provider is a hard error, never an
# empty requirement list, which would read as "fully wired".
case "$provider" in
    privado)
        req_keys="privadovpn-user privadovpn-password"
        ;;
    *)
        echo "ERROR: unknown VPN provider '${provider}' for app '${app}'." >&2
        echo "       Known providers: privado. Add a new provider's required" >&2
        echo "       vpn-credentials keys here before enabling it (docs/21)." >&2
        exit "$UNKNOWN_PROVIDER_RC"
        ;;
esac

# Prove the Secret read before treating an empty key as missing: an unreachable
# cluster would otherwise be reported as "provider not fully wired", sending the
# operator to 1Password for a credential that is already there.
secret_exists=1
if ! read_err="$(kubectl get secret vpn-credentials -n "$NS" -o name 2>&1)"; then
    if printf '%s' "$read_err" | grep -qi 'not found'; then
        secret_exists=0
    else
        echo "ERROR: could not read the vpn-credentials Secret in $NS:" >&2
        printf '       %s\n' "$read_err" >&2
        exit "$UNKNOWN_PROVIDER_RC"
    fi
fi

missing=""
for k in $req_keys; do
    v=""
    if [ "$secret_exists" -eq 1 ]; then
        v="$(kubectl get secret vpn-credentials -n "$NS" \
            -o "jsonpath={.data['$k']}" 2>/dev/null || true)"
    fi
    [ -z "$v" ] && missing="$missing $k"
done

# Trim the leading space; empty when nothing is missing.
echo "${missing# }"
