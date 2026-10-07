# Site data: tailnet clients resolve esweiss.com through the in-cluster CoreDNS
# resolver (the `ts-dns` device). `prevent_destroy` at the pinned module ref
# makes removing a key a hard plan error — README.md § Split-DNS before editing.
locals {
  split_dns = {
    "esweiss.com" = { device_hostname = "ts-dns" }
  }
}
