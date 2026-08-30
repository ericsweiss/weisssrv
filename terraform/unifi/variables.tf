variable "unifi_api_url" {
  description = "Base URL of the UniFi controller's API — the gateway's own LAN address, no /api path (1Password item 'UniFi Controller', field 'url')."
  type        = string

  validation {
    # A port is allowed: a DR bootstrap against a console reached through an SSH
    # tunnel (https://127.0.0.1:8443) is exactly what `terraform import` needs
    # when the management VLAN is not routable from the operator's machine yet.
    condition     = can(regex("^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$", var.unifi_api_url))
    error_message = "unifi_api_url must be a bare https:// host with an optional port, and no path or trailing slash — the SDK appends the API path itself."
  }
}

variable "unifi_api_key" {
  description = "UniFi API key (1Password item 'UniFi Controller', field 'api-key'). Created under Control Plane -> Integrations for a Limited Admin with Local Access Only."
  type        = string
  sensitive   = true

  validation {
    # A renamed 1Password field yields an empty string, which authenticates as
    # nobody: the plan then fails at the first data read with an authorization
    # error that names neither the item nor the field.
    condition     = length(var.unifi_api_key) >= 16
    error_message = "unifi_api_key looks empty or truncated; check the op:// reference in the Taskfile and the unifi-drift-plan job."
  }
}

variable "unifi_allow_insecure" {
  description = <<-EOT
    Skip TLS verification against the controller. Defaults ON because the
    console serves its own self-signed certificate on the LAN address and
    `unifi_api_url` IS that address — there is no name to issue a real cert for,
    so verification would fail every plan.

    It is an input rather than a literal so the two cases that CAN verify are a
    `TF_VAR_unifi_allow_insecure=false` away: a console fronted by a real
    certificate, and a DR bootstrap through an SSH tunnel whose far end presents
    one. Every plan and apply sends the API key and, on a WLAN change, the four
    PSKs over this session.
  EOT
  type        = bool
  default     = true
}

# WLAN pre-shared keys
# One sensitive variable per SSID, injected by the Taskfile / CI via `op run`
# from the `WiFi <ssid>` items in the Homelab vault
# (docs/15-credential-rotation.md "Required 1Password Items"). Never committed,
# never defaulted, and never assembled into one map variable — a single
# `TF_VAR_*` cannot carry four separate `op://` references.
#
# The bounds are WPA-PSK's own (8-63 characters). The floor is what matters: an
# empty value would be a syntactically valid plan whose diff is hidden by
# `sensitive`, and applying it resets that SSID's key — every device on the
# VLAN drops off at once and the fix needs physical access to each one.

variable "wlan_passphrase_home" {
  description = "PSK for the TheRevengers SSID, VLAN 20 (1Password item 'WiFi TheRevengers', field 'password')."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[\\x20-\\x7e]{8,63}$", var.wlan_passphrase_home))
    error_message = "wlan_passphrase_home must be 8-63 printable ASCII characters (WPA-PSK counts octets, and the passphrase charset is printable ASCII — a multibyte character would pass a character count while failing the 802.11 rule); check the op:// reference in the Taskfile and the unifi-drift-plan job."
  }
}

variable "wlan_passphrase_iot" {
  description = "PSK for the Panopticon SSID, VLAN 30 (1Password item 'WiFi Panopticon', field 'password')."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[\\x20-\\x7e]{8,63}$", var.wlan_passphrase_iot))
    error_message = "wlan_passphrase_iot must be 8-63 printable ASCII characters (WPA-PSK counts octets, and the passphrase charset is printable ASCII — a multibyte character would pass a character count while failing the 802.11 rule); check the op:// reference in the Taskfile and the unifi-drift-plan job."
  }
}

variable "wlan_passphrase_guest" {
  description = "PSK for the kugel-tikka-masala SSID, VLAN 40 (1Password item 'WiFi kugel-tikka-masala', field 'password')."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[\\x20-\\x7e]{8,63}$", var.wlan_passphrase_guest))
    error_message = "wlan_passphrase_guest must be 8-63 printable ASCII characters (WPA-PSK counts octets, and the passphrase charset is printable ASCII — a multibyte character would pass a character count while failing the 802.11 rule); check the op:// reference in the Taskfile and the unifi-drift-plan job."
  }
}

variable "wlan_passphrase_work" {
  description = "PSK for the DunderMiffLAN SSID, VLAN 50 (1Password item 'WiFi DunderMiffLAN', field 'password')."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[\\x20-\\x7e]{8,63}$", var.wlan_passphrase_work))
    error_message = "wlan_passphrase_work must be 8-63 printable ASCII characters (WPA-PSK counts octets, and the passphrase charset is printable ASCII — a multibyte character would pass a character count while failing the 802.11 rule); check the op:// reference in the Taskfile and the unifi-drift-plan job."
  }
}
