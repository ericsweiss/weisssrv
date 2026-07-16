# Role: apt_signed_repo

Shared pipeline to add a **fingerprint-verified signed APT repository**. It owns
the boilerplate that drifted across the roles that install upstream packages
from a vendor apt repo: ensure the keyring dir, stat the keyring, (optionally)
install gnupg, download the signing key, verify its **primary-key fingerprint**
exactly, dearmor it into a binary keyring, clean up, then add the `deb
[signed-by=...]` source.

Backs **`alloy_host`** (Grafana), **`plex`** (Plex v2), and **`gitlab`** (GitLab
EE). Each caller keeps its own key URL, fingerprint, keyring path, repo line,
and apt filename — the role only removes the duplicated mechanics.

The keyring is the persistent artifact: the download → verify → dearmor →
cleanup sub-sequence is gated on its absence so the role is idempotent. Key
rotation is delete-the-keyring-first; callers do their own legacy-keyring
cleanup (old `.asc`/`.list` files) before the include.

## Roles that stay standalone

**`tailscale`** shares only the fingerprint-verify snippet and is intentionally
**not** converted. It force-refetches a pre-dearmored `.noarmor.gpg` into a
staging path on every run (so a rotated upstream key is picked up without manual
file removal), verifies the fingerprint, then copies it into the trusted keyring
— a different rotation model than this role's stat-gated download. Folding it in
would regress that behavior.

## How callers invoke it

```yaml
- name: Install <vendor> signed APT repository
  ansible.builtin.include_role:
    name: apt_signed_repo
  vars:
    apt_signed_repo_key_url: https://vendor.example/gpg.key
    apt_signed_repo_fingerprint: 0123456789ABCDEF0123456789ABCDEF01234567
    apt_signed_repo_keyring_path: /etc/apt/keyrings/vendor.gpg
    apt_signed_repo_repo_line: "deb [signed-by=/etc/apt/keyrings/vendor.gpg] https://vendor.example stable main"
    apt_signed_repo_filename: vendor
```

## Parameters

| Variable | Meaning | Required |
|---|---|---|
| `apt_signed_repo_key_url` | URL of the (ASCII-armored) signing key | yes |
| `apt_signed_repo_fingerprint` | Expected primary-key fingerprint (no spaces) | yes |
| `apt_signed_repo_keyring_path` | Dearmored binary keyring destination | yes |
| `apt_signed_repo_repo_line` | Full `deb [signed-by=...] ...` sources line | yes |
| `apt_signed_repo_filename` | apt `sources.list.d` filename (no extension) | yes |
| `apt_signed_repo_when` | Extra gate threaded through every task (e.g. a skip flag) | no (default `true`) |
| `apt_signed_repo_install_gnupg` | Install gnupg on every run (the existing-keyring re-verify also needs gpg) when nothing else guarantees it | no (default `false`) |
| `apt_signed_repo_keyring_mode` | Explicit keyring mode; empty leaves gpg's default | no (default `""`) |
| `apt_signed_repo_tmp_key` | Staging path for the download | no (default `/tmp/<keyring-basename>.download`) |
| `apt_signed_repo_update_cache` | Refresh the apt cache when the repo is added; set `false` for hermetic tests/staged rollouts | no (default `true`) |

## See also

- `ansible/roles/alloy_host/`, `ansible/roles/plex/`, `ansible/roles/gitlab/` — the callers
- `ansible/roles/prometheus_exporter/` — the analogous shared-pipeline role
