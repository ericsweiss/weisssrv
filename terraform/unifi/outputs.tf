# Controller-assigned ids, re-exported from the module. They are what the
# import commands, the API cross-checks and the drift triage in docs/46 need,
# and none of them is derivable from this configuration.
#
# Outputs make the FIRST plan non-empty ("Changes to Outputs"), which is why
# terraform/authentik has none — but this root's first plan creates the whole
# site anyway, so the outputs land with the initial supervised apply and every
# `unifi-drift-plan` run after it is clean. Do not add an output later without
# expecting one non-empty plan.

output "network_ids" {
  description = "Controller network id per `local.networks` key."
  value       = module.network.network_ids
}

output "zone_ids" {
  description = "Firewall-zone id per zone key — the custom `local.zones` keys and the built-in short names in one map, the same namespace the policies resolve against."
  value       = module.network.zone_ids
}

output "wlan_ids" {
  description = "WLAN id per SSID key."
  value       = module.network.wlan_ids
}
