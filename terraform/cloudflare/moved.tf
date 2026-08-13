# State-address migration for the move onto the library `cloudflare-zone` module
# (main.tf). Every record kept its configuration byte-for-byte; only its address
# changed, from a root-module resource to a keyed instance of one of the module's
# four lifecycle-class resources:
#
#   protected                  -> module.zone.cloudflare_record.protected
#   protected + DDNS content   -> module.zone.cloudflare_record.protected_external_content
#
# Without these blocks Terraform would plan destroy+create for all 19 records —
# i.e. drop them from public DNS — so keep them. `moved` is not affected by
# `prevent_destroy`, and re-applying is a no-op once state carries the new
# addresses.

moved {
  from = cloudflare_zone_settings_override.external
  to   = module.zone.cloudflare_zone_settings_override.this[0]
}

# Apex + the three DDNS-tracked A records (content ignored, prevent_destroy).

moved {
  from = cloudflare_record.root
  to   = module.zone.cloudflare_record.protected_external_content["root"]
}

moved {
  from = cloudflare_record.git
  to   = module.zone.cloudflare_record.protected_external_content["git"]
}

moved {
  from = cloudflare_record.direct
  to   = module.zone.cloudflare_record.protected_external_content["direct"]
}

moved {
  from = cloudflare_record.vpn
  to   = module.zone.cloudflare_record.protected_external_content["vpn"]
}

# CAA set. The module flattens the old `caa` for_each into the shared record map,
# so each key gains the `caa_` prefix it already carried in the resource name.

moved {
  from = cloudflare_record.caa["issue_letsencrypt"]
  to   = module.zone.cloudflare_record.protected["caa_issue_letsencrypt"]
}

moved {
  from = cloudflare_record.caa["issuewild_letsencrypt"]
  to   = module.zone.cloudflare_record.protected["caa_issuewild_letsencrypt"]
}

moved {
  from = cloudflare_record.caa["issue_pki_goog"]
  to   = module.zone.cloudflare_record.protected["caa_issue_pki_goog"]
}

moved {
  from = cloudflare_record.caa["issuewild_pki_goog"]
  to   = module.zone.cloudflare_record.protected["caa_issuewild_pki_goog"]
}

moved {
  from = cloudflare_record.caa["issue_ssl_com"]
  to   = module.zone.cloudflare_record.protected["caa_issue_ssl_com"]
}

moved {
  from = cloudflare_record.caa["issuewild_ssl_com"]
  to   = module.zone.cloudflare_record.protected["caa_issuewild_ssl_com"]
}

moved {
  from = cloudflare_record.caa["iodef"]
  to   = module.zone.cloudflare_record.protected["caa_iodef"]
}

# SPF / DMARC / Immich.

moved {
  from = cloudflare_record.spf
  to   = module.zone.cloudflare_record.protected["spf"]
}

moved {
  from = cloudflare_record.dmarc
  to   = module.zone.cloudflare_record.protected["dmarc"]
}

moved {
  from = cloudflare_record.photos
  to   = module.zone.cloudflare_record.protected["photos"]
}

# GitLab nested CNAMEs. The keys are unchanged (they are the record names).

moved {
  from = cloudflare_record.gitlab_direct["registry.git"]
  to   = module.zone.cloudflare_record.protected["registry.git"]
}

moved {
  from = cloudflare_record.gitlab_direct["pages.git"]
  to   = module.zone.cloudflare_record.protected["pages.git"]
}

moved {
  from = cloudflare_record.gitlab_direct["*.pages.git"]
  to   = module.zone.cloudflare_record.protected["*.pages.git"]
}

moved {
  from = cloudflare_record.gitlab_direct["ide.git"]
  to   = module.zone.cloudflare_record.protected["ide.git"]
}

moved {
  from = cloudflare_record.gitlab_direct["*.ide.git"]
  to   = module.zone.cloudflare_record.protected["*.ide.git"]
}
