# compose_app

Shared scaffolding for the single-project **docker-compose app guests** —
`immich` (VM), `immich_ml` (LXC), and `nextcloud` (VM). Extracts the pieces
those three roles previously duplicated so they stay in lockstep, while every
app-specific detail remains a parameter (or stays in the caller's role).

This role is **not** run standalone in production; each app role includes its
task files at the right point in its own flow (mirroring how `immich`/`nextcloud`
already `include_role: zvol_mount`).

## What it shares

| Seam | File | How the caller uses it |
|---|---|---|
| Docker Engine | `tasks/docker.yml` | `include_role: compose_app  tasks_from: docker.yml` — installs `docker_engine` (gated). |
| Compose unit | `tasks/main.yml` + `templates/compose.service.j2` | `include_role: compose_app` (default) — deploys `<name>.service`, enables, flushes, starts. |
| Backup metrics | `tasks/backup_lib.yml` + `templates/write_prom_metrics.sh.j2` | `include_role: compose_app  tasks_from: backup_lib.yml` — deploys the sourceable `write_prom_metrics` lib. |
| Host nginx | `tasks/nginx.yml` | `include_role: compose_app  tasks_from: nginx.yml` — install/cert/deploy-site/`nginx -t`/reload. |

One compose systemd unit template serves all three guests, with `RemainAfterExit`
standardised to `yes` (systemd treats `yes` and `true` identically). The
backup-metrics `.prom` output is **byte-identical** per app (the metric-name
prefix + `.prom` path are call args; only the size-gauge HELP noun is set per app
at render time).
The nginx `.conf` files stay in each caller's role, byte-unchanged (only the
**task flow** is shared) — the site template is passed in by absolute path
(captured eagerly with `set_fact` in the caller, since `role_path` in an
`include_role`'s `vars:` resolves to the *included* role).

## Key parameters

- `compose_app_skip_install` — the caller's own render-only flag
  (`immich_skip_install` / `skip_immich_ml_deploy` / `skip_nextcloud_deploy`).
- `compose_app_service_name`, `compose_app_description`,
  `compose_app_working_directory` — the compose unit.
- `compose_app_requires_mounts_for` (list; **empty ⇒ the unit omits
  `RequiresMountsFor` and its comment**), `compose_app_requires_mounts_comment`,
  `compose_app_reconcile_comment` — the per-app comment/mount lines, passed so
  the rendered unit stays byte-identical.
- `compose_app_backup_lib_dest`, `compose_app_backup_size_help_object` — the
  helper library.
- `compose_app_nginx_*` — cert dir/paths/mode, self-signed placeholder subj/SAN/
  days, site name + template path, and `compose_app_nginx_bootstrap_cert`.

Full defaults + inline docs: `defaults/main.yml`.

## Molecule

`molecule/default` is a render/contract scenario (`compose_app_skip_install:
true`): it renders the compose unit for **both** the `RequiresMountsFor`-present
and `-absent` cases, renders the `write_prom_metrics` lib and runs it end-to-end
(success + failure/preserve-timestamp), and asserts the emitted metric series.

## Related

- `docker_engine` — the shared pinned Docker Engine install (included from
  `tasks/docker.yml`).
- `immich` / `immich_ml` / `nextcloud` — the three consumers.
