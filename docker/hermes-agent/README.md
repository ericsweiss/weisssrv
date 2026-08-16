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
2. **CLI wrapper** — [`Dockerfile.codex`](Dockerfile.codex), a thin
   `FROM <upstream base>` layer that bakes in the three pinned CLIs: the OpenAI
   Codex CLI (`npm i -g @openai/codex@<hermes_codex_version>`), the Claude Code
   CLI (`@anthropic-ai/claude-code@<hermes_claude_version>`, the coding-delegate
   path) and the 1Password CLI (`op`, `<hermes_op_version>`, from 1Password's
   signed apt repo, whose signing key the wrapper fingerprint-pins against the
   same constant weisssrv-lib's `.install-1password` CI fragment uses —
   docs/37 §1Password). Node 22 + npm are already in
   the base and its final stage is `USER root` (s6 `/init` remaps + drops
   privileges at runtime), so the npm CLIs install to `/usr/local/bin` (on PATH)
   and the runtime user model is unchanged. This wrapper is the **final
   published image**.

Both coding CLIs are billed by *subscription*, not by API token, and each holds
its own credential: `codex` authenticates against ChatGPT via `codex login`
(persisted in `CODEX_HOME=/opt/data/.codex`), and `claude` runs headless (`claude
-p`, via Hermes' terminal tool / bundled claude-code skill) against a Claude Max
`claude setup-token` OAuth token supplied as `CLAUDE_CODE_OAUTH_TOKEN`.

The `build-hermes-agent` CI job (`.gitlab-ci.yml`) does both stages.

## Pins (`ansible/inventories/prod/group_vars/all.yml`)

- **`hermes_version`** — the upstream **release tag** (e.g. `v2026.7.7.2`, which
  is release `0.18.2`). Used verbatim as `git clone --branch` and as the built
  image tag.
- **`hermes_git_sha`** — the immutable commit the tag must resolve to. The build
  refuses to run if `git clone --branch hermes_version` resolves elsewhere,
  defending against a moved/compromised upstream tag. Bump in lockstep with
  `hermes_version`.
- **`hermes_image_version`** — the tag the **cluster actually pulls**, always
  `<hermes_version>-r<N>`. The `-rN` local revision exists because the image is
  upstream *plus* the reviewed `patches/`: a patch change with no upstream bump
  still has to produce a new tag, or the nodes' `IfNotPresent` cache keeps
  serving the unpatched image. Bump `-rN` when the patches change; reset to
  `-r1` on a `hermes_version` bump. CI hard-fails the build if the prefix does
  not match `hermes_version`, so the two cannot drift.
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
  at the tag, verifies the SHA, applies `patches/*.patch`, and runs `docker build
  --build-arg HERMES_GIT_SHA=<sha>` to a local intermediate tag, then builds
  `Dockerfile.codex` with `--build-arg HERMES_IMAGE=<intermediate>
  --build-arg HERMES_CODEX_VERSION=<pin> --build-arg HERMES_CLAUDE_VERSION=<pin>
  --build-arg HERMES_OP_VERSION=<pin>` (plus `BUILDKIT_INLINE_CACHE=1`) as the
  final image. `HERMES_GIT_SHA` is baked in so `hermes dump` / the banner report
  the exact upstream commit.
- **Push**: MR pipelines build-only (nothing is pushed); on `main` the image is
  pushed as `:<hermes_image_version>` (the tag the cluster pulls) alongside
  `:<hermes_version>`, `:latest` and `:<commit-sha>`, and the job then verifies
  the registry actually resolves `:<hermes_image_version>` before finishing.
- **Pull**: the in-cluster Deployment
  (`kubernetes/apps/hermes/deployment.yaml`) references
  `registry.git.esweiss.com/eric/weisssrv/hermes-agent:${hermes_image_version}` — the
  **internal** registry host (AdGuard rewrite → Traefik `.101` → GitLab VM
  registry, same backend as the external host, no hairpin NAT). Auth is a
  `read_registry` GitLab deploy token rendered into a `dockerconfigjson` Secret
  by the `hermes-registry-pull` ExternalSecret.

## Upgrading

1. **Hermes**: pick a newer tag from
   <https://github.com/NousResearch/hermes-agent/releases>, set `hermes_version`
   **and** `hermes_git_sha` (the tag's commit), **and** `hermes_image_version`
   to `<new hermes_version>-r1`. All three move together — CI fails the build if
   the third does not match the first.
2. **Patch-only change** (no upstream bump): leave `hermes_version` alone and
   bump only the `-rN` in `hermes_image_version`, or the nodes keep the cached
   image.
3. **Codex CLI** (optional, independent): pick a newer stable release from
   <https://github.com/openai/codex/releases> (`rust-vX.Y.Z` → the bare `X.Y.Z`)
   and set `hermes_codex_version`. `hermes_claude_version` and `hermes_op_version`
   bump the same way — but any of them changes the built image, so bump `-rN`
   with them.
4. Run `task flux:sync-versions`, commit the changed files on a branch, open an
   MR, merge.
5. On `main`, `build-hermes-agent` rebuilds and pushes the new tag; Flux then
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
