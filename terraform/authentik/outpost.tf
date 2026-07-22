# The embedded outpost — ADOPTED into Terraform (imported, imports.tf) so the
# provider-assignment step that used to be a manual Admin-UI click is code.
# The outpost object is authentik-managed (managed:
# goauthentik.io/outposts/embedded); its *provider list* is the one
# user-touched knob, and that is exactly what this resource pins. Rationale
# for adopting rather than documenting the manual step: every forward-auth
# app needs the assignment, and a forgotten click surfaces as a 404 at the
# outpost (docs/37 runbooks) — codifying it makes `terraform plan` catch it.
#
# Deliberately UNCONFIGURED fields (zero-diff discipline):
# - config: Optional+Computed in the provider schema — left unset so the
#   authentik-managed outpost settings JSON (authentik_host, kubernetes_*,
#   ...) is never diffed or rewritten; updates round-trip the live value.
#   A UI change inside that JSON stays invisible to the plan, same as the
#   other deliberately-unmanaged knobs (README "What is deliberately
#   UNMANAGED").
# - service_connection: the live embedded outpost has none (null).
#
# protocol_providers is an ORDERED list preserving live API insertion order
# (original UI assignments pks 5,7,10,4,17,8,9,6 — the Hermes proxy provider,
# pk 16, was removed when the dashboard went OIDC-only — then the AdGuard
# providers). New providers are appended at the end — the API preserves
# insertion order.
resource "authentik_outpost" "embedded" {
  name = "authentik Embedded Outpost"
  type = "proxy"

  protocol_providers = [
    authentik_provider_proxy.radarr.id,
    authentik_provider_proxy.qbittorrent.id,
    authentik_provider_proxy.pulsarr.id,
    authentik_provider_proxy.sonarr.id,
    authentik_provider_proxy.wireguard_easy.id,
    authentik_provider_proxy.nzbget.id,
    authentik_provider_proxy.prowlarr.id,
    authentik_provider_proxy.lidarr.id,
    authentik_provider_proxy.adguard_01.id,
    authentik_provider_proxy.adguard_02.id,
  ]
}
