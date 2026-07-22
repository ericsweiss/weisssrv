# hermes

NousResearch [Hermes Agent](https://github.com/NousResearch/hermes-agent) —
autonomous AI agent + web dashboard, running in the `hermes` namespace.

- **Dashboard**: `agent.ericsweiss.com` (external) / `agent.esweiss.com`
  (internal), SSO via the dashboard's own Authentik OIDC login — the sole
  registered provider on its fail-closed 0.0.0.0 bind, gated by the Authentik
  `hermes-users` group, with a Traefik-only NetworkPolicy on :9119. One
  Authentik login, both hostnames (per-request redirect_uri). Authentik
  objects live in `terraform/authentik` (docs/40).
- **Workload**: one Deployment, three containers — `gateway` + `dashboard` off
  the self-built hermes-agent image, plus the `camofox` anti-detection browser
  server (self-built `camofox-browser` image, driven by the gateway over
  pod-localhost) — sharing an NFS `/opt/data` volume. NAS-avoiding, modern-CPU.
- **Images**: built by the `build-hermes-agent` / `build-camofox-browser` CI
  jobs — see [`docker/hermes-agent/README.md`](../../../docker/hermes-agent/README.md)
  and [`docker/camofox-browser/README.md`](../../../docker/camofox-browser/README.md).
- **Storage**: NFS `/appdata/hermes` (encrypted `ssd/appdata`, archive-backed).
- **Memory backend**: the optional Hindsight app
  ([`kubernetes/apps/hindsight/`](../hindsight/)) — enabled at runtime via
  `memory.provider` (docs/37 §Memory backend), not in git.
- **API route**: `agent-api.esweiss.com` (internal-only, LAN/Tailscale-scoped,
  bearer-keyed — no Authentik) fronts the gateway's OpenAI-compatible API
  server for the Home Assistant Voice pipeline (docs/37 §Home Assistant).
- **LLM engine**: the bundled Codex app-server runtime — Hermes delegates
  OpenAI/Codex turns to the `codex` CLI (baked into the image), authenticated by
  a one-time `codex login` (ChatGPT-subscription OAuth; token persisted in
  `CODEX_HOME=/opt/data/.codex` on NFS). No LLM API key. See docs/37.
- **Secrets**: `hermes-secrets` (dashboard OIDC client secret, gateway
  API-server + Claude Code creds, Discord bot token, Home Assistant token —
  dashboard `basic` values stay on the 1P item but are deliberately not synced,
  see `externalsecret.yaml`) and `hermes-registry-pull` (registry pull) via
  ESO / 1Password.

Full architecture, SSO model, gateway/platform onboarding, backup, and
upgrade procedure: **[`docs/37-hermes.md`](../../../docs/37-hermes.md)**.

Ops: `task hermes:status`, `task hermes:logs [COMPONENT=dashboard|gateway]`,
`task hermes:restart`, `task hermes:shell`, `task hermes:codex-login`.
