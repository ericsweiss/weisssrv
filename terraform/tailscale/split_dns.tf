# Split-DNS: esweiss.com queries from Tailscale clients go to the in-cluster
# CoreDNS resolver (the "ts-dns" operator device), which CNAMEs Traefik-fronted
# names to traefik-tailnet (mesh path) and forwards the rest to AdGuard.
# See kubernetes/apps/tailnet-dns and docs/05-tailscale.md.

# Resolve the ts-dns device by its deterministic hostname (the Service's
# tailscale.com/hostname annotation) so the 100.x is derived at apply time and
# self-heals if the device is rebuilt.
data "tailscale_device" "ts_dns" {
  hostname = "ts-dns"
  wait_for = "60s"
}

resource "tailscale_dns_split_nameservers" "esweiss" {
  domain = "esweiss.com"
  # Filter to the IPv4 (100.x) address explicitly: an ordering change in the
  # provider/API must not repoint tailnet esweiss.com resolution.
  nameservers = [one([for a in data.tailscale_device.ts_dns.addresses : a if !strcontains(a, ":")])]

  # Same reasoning as the ACL in main.tf: dropping this entry silently breaks
  # *.esweiss.com resolution for every tailnet client (the mesh path in
  # docs/05-tailscale.md), so removing it has to be an explicit break-glass
  # edit rather than a side effect of a refactor.
  lifecycle {
    prevent_destroy = true
  }
}
