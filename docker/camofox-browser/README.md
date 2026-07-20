# camofox-browser image build

[jo-inc/camofox-browser](https://github.com/jo-inc/camofox-browser) — the
Camoufox (hardened-Firefox) anti-detection browser server that Hermes' camofox
browser tool drives over HTTP (`CAMOFOX_URL`) — publishes **no** container
image. So this repo builds its own into the GitLab Container Registry.

Unlike the hermes-agent image there is **no wrapper layer**: upstream's
`Dockerfile` is built **unmodified**. It is self-contained (Node 22-slim +
Firefox runtime deps + Xvfb + Mesa software-GL), and bakes everything in at
build time — the pinned Camoufox browser binary (downloaded from
`daijro/camoufox` releases), `yt-dlp`, and the default plugin dependencies —
so the runtime needs no downloads and the image is complete as shipped
(`EXPOSE 9377`, `CAMOFOX_PORT=9377`).

The `build-camofox-browser` CI job (`.gitlab-ci.yml`) does the build.

## Pins (`ansible/inventories/prod/group_vars/all.yml`)

- **`hermes_camofox_version`** — the bare semver (e.g. `1.12.1`), used as the
  built image tag; the upstream git tag carries the leading `v`
  (`git clone --branch v<pin>`).
- **`hermes_camofox_git_sha`** — the immutable commit the tag must resolve to.
  The build refuses to run if the clone resolves elsewhere, defending against
  a moved/compromised upstream tag (this container browses the web with
  persistent cookies — supply-chain caution is warranted). Bump in lockstep
  with `hermes_camofox_version`.

## How it works

- **Build**: `build-camofox-browser` (DinD, infrastructure runner) clones
  upstream at `v<hermes_camofox_version>`, verifies the SHA, and runs
  `docker build` on the clone (their Dockerfile, their context). No build args.
- **Push**: tagged
  `registry.git.ericsweiss.com/eric/weisssrv/camofox-browser:<version>`. MR
  pipelines build-only; the load-bearing `:<version>` and `:latest` tags are
  pushed only on `main`.
- **Pull**: the `camofox` container of the hermes pod
  (`kubernetes/apps/hermes/deployment.yaml`) references
  `registry.git.esweiss.com/eric/weisssrv/camofox-browser:${hermes_camofox_version}`
  — the **internal** registry host — via the existing `hermes-registry-pull`
  deploy-token Secret (project-scoped, covers every image path under the
  project).

## Upgrading

1. Pick a newer stable tag from
   <https://github.com/jo-inc/camofox-browser/releases> and note its commit
   (`git ls-remote --tags` — for an annotated tag, take the `^{}` peeled SHA).
2. Set `hermes_camofox_version` (bare semver) **and** `hermes_camofox_git_sha`
   in `all.yml`, run `task flux:sync-versions`, commit both files → MR.
3. On merge, CI verifies tag→SHA, rebuilds, pushes; Flux rolls the hermes pod.
   Profiles/cookies persist (`/opt/data/.camofox` on NFS).

Runtime configuration (env, probes, resources, persistence) lives on the
`camofox` container in the hermes Deployment — see docs/37 §Browser tooling.
