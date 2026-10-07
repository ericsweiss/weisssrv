# State addresses that predate the move onto the library `cloudflare-zone`
# module. Re-applying is a no-op once state carries the new addresses; a block
# removed BEFORE that apply plans destroy+create, dropping a record from DNS.

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

# CAA set: each key carries the `caa_` prefix in the shared record map.

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

# GitLab nested CNAMEs, keyed by record name.

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
