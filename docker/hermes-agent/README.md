# hermes-agent image build

NousResearch [Hermes Agent](https://github.com/NousResearch/hermes-agent)
publishes no container image for self-hosters to pin, and its LLM engine here is
the **Codex app-server runtime** (Hermes delegates OpenAI/Codex turns to the
`codex` CLI — ChatGPT-subscription OAuth, not API billing), which upstream's
image does not bundle. So this repo builds its own image into the GitLab
Container Registry, in two stages:

1. **Upstream base** — clone `NousResearch/hermes-agent` at the pinned release
   tag and build *their* `Dockerfile`. It is a complete, self-contained
   multi-stage build (Debian 13 + uv/Python 3.13 + **Node 22** + s6-overlay) meant
   to be built at a tag; vendoring a copy would only add drift.
2. **Codex wrapper** — [`Dockerfile.codex`](Dockerfile.codex), a thin
   `FROM <upstream base>` layer that installs the pinned OpenAI Codex CLI
   (`npm i -g @openai/codex@<hermes_codex_version>`). Node 22 + npm are already in
   the base and its final stage is `USER root` (s6 `/init` remaps + drops
   privileges at runtime), so the CLI installs to `/usr/local/bin` (on PATH) and
   the runtime user model is unchanged. This wrapper is the **final published
   image**.

The `build-hermes-agent` CI job (`.gitlab-ci.yml`) does both stages.

## Pins (`ansible/inventories/prod/group_vars/all.yml`)

- **`hermes_version`** — the upstream **release tag** (e.g. `v2026.7.7.2`, which
  is release `0.18.2`). Used verbatim as `git clone --branch` and as the built
  image tag.
- **`hermes_git_sha`** — the immutable commit the tag must resolve to. The build
  refuses to run if `git clone --branch hermes_version` resolves elsewhere,
  defending against a moved/compromised upstream tag. Bump in lockstep with
  `hermes_version`.
- **`hermes_codex_version`** — the OpenAI Codex CLI version baked in by the
  wrapper (e.g. `0.144.5`; requires `>= 0.130.0` for the Hermes runtime). Bumped
  independently of the Hermes release.
- **`hermes_claude_version`** — the Claude Code CLI version baked in alongside
  Codex (`npm i -g @anthropic-ai/claude-code@<version>`) for the coding-delegate
  path. Bumped independently.
- **`hermes_op_version`** — the official 1Password CLI (`op`) DEB version baked
  in for Hermes' 1Password skill (docs/37 §1Password); installed from 1Password's
  signed apt repo. Full deb version string (not the bare `op --version` semver).
  Bumped independently.

## How it works

- **Build**: `build-hermes-agent` (DinD, infrastructure runner) clones upstream
  at the tag, verifies the SHA, and runs `docker build --build-arg
  HERMES_GIT_SHA=<sha>` to a local intermediate tag, then builds `Dockerfile.codex`
  with `--build-arg HERMES_IMAGE=<intermediate> --build-arg
  HERMES_CODEX_VERSION=<pin>` as the final image. `HERMES_GIT_SHA` is baked in so
  `hermes dump` / the banner report the exact upstream commit.
- **Push**: the final image is tagged
  `registry.git.ericsweiss.com/eric/weisssrv/hermes-agent:<hermes_version>`. MR
  pipelines build-only (nothing is pushed); the `:<hermes_version>`, `:latest`,
  and `:<commit-sha>` tags are all pushed only on `main`.
- **Pull**: the in-cluster Deployment
  (`kubernetes/apps/hermes/deployment.yaml`) references
  `registry.git.esweiss.com/eric/weisssrv/hermes-agent:${hermes_version}` — the
  **internal** registry host (AdGuard rewrite → Traefik `.101` → GitLab VM
  registry, same backend as the external host, no hairpin NAT). Auth is a
  `read_registry` GitLab deploy token rendered into a `dockerconfigjson` Secret
  by the `hermes-registry-pull` ExternalSecret.

## Upgrading

1. **Hermes**: pick a newer tag from
   <https://github.com/NousResearch/hermes-agent/releases>, set `hermes_version`
   **and** `hermes_git_sha` (the tag's commit).
2. **Codex CLI** (optional, independent): pick a newer stable release from
   <https://github.com/openai/codex/releases> (`rust-vX.Y.Z` → the bare `X.Y.Z`)
   and set `hermes_codex_version`.
3. Run `task flux:sync-versions`, commit the changed files on a branch, open an
   MR, merge.
4. On `main`, `build-hermes-agent` rebuilds and pushes the new tag; Flux then
   rolls the Deployment. The Codex OAuth token in `CODEX_HOME=/opt/data/.codex`
   persists across the roll — no re-login needed.

See `docs/37-hermes.md` for the full architecture, the Codex runtime setup, SSO,
and runbooks.

## Local patches (`patches/*.patch`)

Applied by `build-hermes-agent` to the SHA-verified upstream tree before the
build (`git apply -p1`, loud failure on drift). Each patch documents the
upstream version it targets; **re-verify every patch on a hermes version
bump** and drop any that upstreamed.

- `0001-hindsight-manual-retain-async.patch` — the manual `hindsight_retain`
  tool path omitted `retain_async`, running synchronously against minutes-long
  CPU extraction and blowing Hermes's 120s tool timeout; the patch honors the
  configured flag (default async) like the automatic retention path.
