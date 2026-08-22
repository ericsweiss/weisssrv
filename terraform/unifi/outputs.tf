# Controller-assigned ids, re-exported from the module. They are what the
# import commands, the API cross-checks and the drift triage in docs/46 need,
# and none of them is derivable from this configuration.
#
# ADDING an output makes the next plan non-empty ("Changes to Outputs"), which
# on a drift-plan job reads as drift. These land with the initial supervised
# apply that creates the whole site, so every `unifi-drift-plan` run after it is
# clean — but adding one LATER costs one yellow drift plan, so do it together
# with a change that is being applied anyway.

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
