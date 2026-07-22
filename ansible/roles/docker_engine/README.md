# Role: docker_engine

Shared **pinned Docker Engine (CE)** install pipeline for the docker-compose app
VMs. It owns the boilerplate that had drifted across three near-identical
`docker.yml` task files: install the apt prerequisites, add the
fingerprint-verified `download.docker.com` apt repo (via `apt_signed_repo`),
install the exact-pinned engine + CLI + containerd + buildx + compose plugins,
`dpkg`-**hold** them so the maintenance apt-upgrade never bumps the engine out
from under a running stack, and write the journald `/etc/docker/daemon.json`.

Backs **`immich`**, **`immich_ml`**, and **`nextcloud`**. Extracting it fixed a
real defect: the Nextcloud copy installed the pinned engine but never applied the
`dpkg` hold, so `task maintenance:update-packages` (`apt upgrade: safe` on
`app_servers`) could silently bump Docker under the running Nextcloud stack. The
shared role holds uniformly, so the miss cannot recur.

## Versions

Engine + plugin versions are the single shared pins in
`group_vars/all.yml` — `docker_ce_version`, `containerd_version`,
`docker_buildx_plugin_version`, `docker_compose_plugin_version`. Bump them there
(the maintenance version-check flow tracks them), never per-role.

## How callers invoke it

```yaml
- name: Install Docker Engine + compose plugin
  ansible.builtin.include_role:
    name: docker_engine
  when: not (<role>_skip_install | default(false))
```

The `daemon.json` task notifies this role's own `Restart docker` handler, so the
caller does not need to carry one.

## Parameters

| Variable | Meaning | Default |
|---|---|---|
| `docker_engine_skip_install` | Skip every step needing the real apt repo / Docker binary / running daemon (render-only); the `/etc/docker` dir + `daemon.json` still render | `false` |
| `docker_engine_key_url` | download.docker.com signing key URL | download.docker.com/linux/debian/gpg |
| `docker_engine_key_fingerprint` | Expected primary-key fingerprint | Docker's `9DC8…CD88` |
| `docker_engine_repo_url` | apt repo base URL | download.docker.com/linux/debian |
| `docker_engine_keyring_path` | Dearmored keyring destination | `/etc/apt/keyrings/docker.gpg` |
| `docker_engine_packages` | Pinned `name=version` list to install | engine + CLI + containerd + buildx + compose |
| `docker_engine_hold_packages` | Package names to `dpkg`-hold | same five, unversioned |
| `docker_engine_daemon_config` | dict rendered to `/etc/docker/daemon.json` | journald log driver + live-restore |

## See also

- `ansible/roles/apt_signed_repo/` — the signed-repo helper this role includes
- `ansible/roles/immich/`, `ansible/roles/immich_ml/`, `ansible/roles/nextcloud/` — the callers
