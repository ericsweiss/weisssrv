#!/usr/bin/env bash
# Print the vpn-credentials Secret keys REQUIRED by a download client's VPN
# provider that are currently MISSING (absent or empty) — a space-separated list
# on stdout, empty output when every required key is present.
#
# This is the SINGLE source of truth for the provider -> required-keys mapping
# and the Secret lookup, shared by two Taskfile commands so the pre-flight can
# never drift between them:
#   * `task downloads:vpn -- APP=<app> STATE=on`  (provider read from the CM)
#   * `task downloads:vpn-provider -- APP=<app> PROVIDER=<p>` (provider passed in)
# Turning a VPN on — or switching to a provider — whose credentials aren't wired
# rolls the pod into gluetun settings-validation failure / CrashLoopBackOff, so
# both callers pre-flight with this check and refuse before patching.
#
# Usage: scripts/vpn-credcheck.sh <nzbget|qbittorrent> [gluetun-provider-string]
#   The provider string, when given, is gluetun's exact VPN_SERVICE_PROVIDER
#   value (e.g. "privado", "vpn unlimited"). When omitted it is read from the
#   app's <app>-vpn-config ConfigMap (data.vpn_provider) — i.e. the provider the
#   pod would actually start with.
#
# Exit status:
#   0  check ran (missing keys, if any, on stdout)
#   1  usage / bad app argument (message on stderr)
#   2  the provider is empty or unknown to this script — callers MUST refuse,
#      never treat it as "nothing missing": a hand-patched or blanked
#      vpn_provider is exactly the input this pre-flight exists to catch.
set -euo pipefail

UNKNOWN_PROVIDER_RC=2

NS=downloads

app="${1:-}"
provider="${2-}"

case "$app" in
    nzbget | qbittorrent) ;;
    *)
        echo "Usage: $0 <nzbget|qbittorrent> [gluetun-provider-string]" >&2
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
# validation requires. An unknown/empty provider is a HARD ERROR, not an empty
# requirement list: `task downloads:vpn` calls this with no provider argument,
# so the value comes unvalidated from the live ConfigMap and "no required keys"
# would read as "fully wired".
case "$provider" in
    privado)
        req_keys="privadovpn-user privadovpn-password"
        ;;
    "vpn unlimited")
        req_keys="vpnunlimited-user vpnunlimited-password \
            vpnunlimited-clientcrt vpnunlimited-clientkey"
        ;;
    *)
        echo "ERROR: unknown VPN provider '${provider}' for app '${app}'." >&2
        echo "       Known providers: privado, 'vpn unlimited'. Add its required" >&2
        echo "       vpn-credentials keys here before enabling it (docs/21)." >&2
        exit "$UNKNOWN_PROVIDER_RC"
        ;;
esac

missing=""
for k in $req_keys; do
    v="$(kubectl get secret vpn-credentials -n "$NS" \
        -o "jsonpath={.data['$k']}" 2>/dev/null || true)"
    [ -z "$v" ] && missing="$missing $k"
done

# Trim the leading space; empty when nothing is missing.
echo "${missing# }"
