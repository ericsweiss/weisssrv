# hermes

NousResearch [Hermes Agent](https://github.com/NousResearch/hermes-agent) —
autonomous AI agent + web dashboard, running in the `hermes` namespace.

- **Dashboard**: `agent.ericsweiss.com` (external) / `agent.esweiss.com`
  (internal), behind layered SSO — an Authentik forward-auth perimeter
  (`hermes-users` group; a Traefik-only NetworkPolicy makes the middleware the
  only path to it) in front of the dashboard's own login: Authentik OIDC
  (primary) + `basic` break-glass (its 0.0.0.0 bind is fail-closed and needs a
  registered provider). Authentik objects live in `terraform/authentik`
  (docs/40).
- **Workload**: one Deployment, two containers (`gateway` + `dashboard`) off the
  self-built image, sharing an NFS `/opt/data` volume. NAS-avoiding, modern-CPU.
- **Image**: built by the `build-hermes-agent` CI job — see
  [`docker/hermes-agent/README.md`](../../../docker/hermes-agent/README.md).
- **Storage**: NFS `/appdata/hermes` (encrypted `ssd/appdata`, archive-backed).
- **LLM engine**: the bundled Codex app-server runtime — Hermes delegates
  OpenAI/Codex turns to the `codex` CLI (baked into the image), authenticated by
  a one-time `codex login` (ChatGPT-subscription OAuth; token persisted in
  `CODEX_HOME=/opt/data/.codex` on NFS). No LLM API key. See docs/37.
- **Secrets**: `hermes-secrets` (dashboard `basic` + OIDC creds, gateway
  API-server key, Discord bot token) and `hermes-registry-pull` (registry
  pull) via ESO / 1Password.

Full architecture, SSO model, gateway/platform onboarding, backup, and
upgrade procedure: **[`docs/37-hermes.md`](../../../docs/37-hermes.md)**.

Ops: `task hermes:status`, `task hermes:logs [COMPONENT=dashboard|gateway]`,
`task hermes:restart`, `task hermes:shell`, `task hermes:codex-login`.
