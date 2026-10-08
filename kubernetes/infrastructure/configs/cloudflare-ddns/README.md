# cloudflare-ddns

Keeps the external zone's A records pointed at the site's current public IPv4.
The CronJob here runs `cloudflare-ddns.py` every 5 minutes, mounted from the
`configMapGenerator` in `kustomization.yaml`.

## Environment

The program reads no site data of its own. Every value arrives as an env var the
CronJob spells as a `cluster-config` placeholder.

| Variable | Source | Meaning |
|---|---|---|
| `CF_API_TOKEN` | `cloudflare-api-token` Secret | Token with DNS edit on the zone. |
| `DDNS_ZONE` | `${cluster_external_domain}` | The zone name. |
| `DDNS_RECORDS` | `${cluster_ddns_records}` | Comma-separated `label[:proxied]` list. `@` is the apex. `proxied` is `true` or `false` and defaults to `true`. |

Both are validated before the first API call. An empty or unsubstituted
`DDNS_ZONE` fails the run, because `GET /zones?name=` with no name returns an
unfiltered list. `DDNS_RECORDS` fails the run on an entry with more than one
colon, an empty or dot-edged label, or a proxied flag that is not `true` or
`false`: a malformed flag would otherwise publish a record through the
Cloudflare proxy when a direct record was asked for.

`proxied` seeds creation only. On update the record's live value wins, so this
job and Terraform cannot flip-flop it on every cycle.

## Record ownership

Three owners write to this zone and their record sets are disjoint. The split and
the reason it has to stay disjoint are in `docs/08-dns.md`.

| Owner | Records |
|---|---|
| Terraform (`terraform/cloudflare/`) | the static set |
| external-dns | records derived from Ingress and Service objects, tagged with its own TXT owner id |
| this CronJob | the `content` of the records named in `DDNS_RECORDS` |
