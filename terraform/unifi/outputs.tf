# Controller-assigned ids, re-exported from the module for the import commands
# and the drift triage in docs/46. Adding one makes the next plan non-empty
# ("Changes to Outputs"), so add it alongside a change being applied anyway.

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
