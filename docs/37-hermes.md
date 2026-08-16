# Hermes Agent

NousResearch [Hermes Agent](https://github.com/NousResearch/hermes-agent) is an
autonomous AI agent platform (skill-learning loop, persistent memory,
multi-platform messaging, 40+ tools, MCP support) with a FastAPI **web
dashboard** for configuration, API-key management, and session monitoring.

This deployment runs Hermes as a k3s workload in the `hermes` namespace and
exposes the dashboard at **`agent.ericsweiss.com`** (external) /
**`agent.esweiss.com`** (internal), gated by Authentik.

- Namespace: `hermes`
- Manifests: [`kubernetes/apps/hermes/`](../kubernetes/apps/hermes/)
- Image build: [`docker/hermes-agent/README.md`](../docker/hermes-agent/README.md)
- Ops: `task hermes:status | logs | restart | shell | codex-login`

---

## Architecture

One Deployment (`replicas: 1`, `strategy: Recreate`), three containers sharing
one NFS `/opt/data` volume — the first two off the same self-built image,
mirroring upstream's `docker-compose.yml`, where both the `gateway` and
`dashboard` services bind-mount `~/.hermes` and run with `network_mode: host`:

| Container | Command / image | Role |
|-----------|-----------------|------|
| `gateway` | `gateway run` | Always-on agent supervisor. Messaging-platform adapters (Telegram/Discord/Slack/…) register here. Runs even with **zero** platforms configured. |
| `dashboard` | `dashboard --host 0.0.0.0 --no-open` | The user-facing FastAPI web UI on `:9119` (9119 is the image default; upstream's compose binds `--host 127.0.0.1`). |
| `camofox` | self-built `camofox-browser` image | Anti-detection browser server on `:9377`, driven by the gateway's camofox browser tool over pod-localhost (see §Browser tooling). |

Because the containers live in one pod they share `localhost` (like the
compose `network_mode: host`), so the dashboard reaches the gateway — and the
gateway reaches the browser — over `127.0.0.1` with no extra service. The
single RWO NFS volume is why the update strategy is `Recreate` (never
dual-mount across a rolling update).

A companion app, **Hindsight** (`kubernetes/apps/hindsight/`), provides the
optional long-term memory backend — see §Memory backend.

### Image

Upstream ships **no** published container image. Their `Dockerfile` is a
complete, self-contained multi-stage build (Debian 13 + uv/Python 3.13 + Node 22
+ s6-overlay) meant to be built at a release tag. The `build-hermes-agent` CI
job clones the repo at `hermes_version` and builds *their* Dockerfile, then
layers a thin wrapper ([`docker/hermes-agent/Dockerfile.codex`](../docker/hermes-agent/Dockerfile.codex))
that bakes in the pinned **OpenAI Codex CLI** (`hermes_codex_version`,
`npm i -g @openai/codex`) — Hermes' LLM engine is the Codex app-server runtime
(see §LLM engine below), so the `codex` CLI must be on the image's PATH — plus
the **Claude Code CLI** (`hermes_claude_version`) for the coding-delegate path
and the **1Password CLI** (`hermes_op_version`) for the 1Password skill. Node 22
+ npm are already in the base, so the wrapper only adds the CLIs. Full build
details in [`docker/hermes-agent/README.md`](../docker/hermes-agent/README.md).

The in-cluster image ref is
`registry.git.esweiss.com/eric/weisssrv/hermes-agent:${hermes_image_version}` (the
**internal** registry host — AdGuard rewrite → Traefik `.101` → GitLab VM
registry, no hairpin NAT). Note the pulled tag is `hermes_image_version`
(`<hermes_version>-r<N>`), **not** `hermes_version`: the image is upstream plus
this repo's reviewed patches, so a patch-only change still needs a fresh tag to
defeat the nodes' `IfNotPresent` cache. CI fails the build if the two drift.

### Runtime user / security context

The image is an **s6-overlay** build that starts as root, remaps its internal
`hermes` user to `HERMES_UID`/`HERMES_GID` (we set `1000`/`2000`), chowns
`/opt/data`, then drops every service to that user via `s6-setuidgid`. This is
exactly the LinuxServer.io `PUID`/`PGID` pattern used by the `*arr` apps in the
`downloads` namespace, so the pod uses the **same** hardening subset they do:
`seccompProfile: RuntimeDefault` + `automountServiceAccountToken: false`, and
**not** `runAsNonRoot` / drop-ALL / `allowPrivilegeEscalation: false` (those
would break the s6 privilege drop). PSA is therefore `baseline` (warn/audit
`restricted` flag the root start without blocking it).

### Scheduling & storage

- **NAS-avoiding**: `preferred nodeAffinity esweiss.com/nas DoesNotExist` +
  `nodeSelector esweiss.com/general=true,esweiss.com/cpu=modern`. The image
  bundles a Node/Chromium/uv toolchain, so it stays on the modern-CPU general
  nodes and off the NAS and the legacy nodes.
- **Storage**: NFS `/appdata/hermes` on the encrypted `ssd/appdata` dataset,
  mounted `nfsvers=4.2,hard,noatime,xprtsec=tls` by hostname
  `pve-nas-01.esweiss.com`. The subdir is created + owned `1000:2000` by the
  `nas_storage` role (`nas_storage_appdata_dirs`). The export is `all_squash,
  anonuid=1000,anongid=2000`, so every container write lands `1000:2000` on the
  NAS regardless of the in-container UID.

---

## Encryption & backups

- **At rest**: `/opt/data` lives on `ssd/appdata`, a natively-encrypted ZFS
  dataset (passphrase-from-Connect at boot — see docs/32).
- **Backups**: `ssd/appdata` is replicated nightly to `archive/appdata` as a
  raw ZFS stream. `/appdata/hermes` is a child of that root, so it is backed up
  automatically — **no `SRC_LIST` edit needed**. All persistent state (config,
  `.env` with the provider keys, sessions, skills, memories) is under
  `/opt/data`, so it survives pod/node recreation and is covered by the backup.

Restore = restore the `ssd/appdata` dataset (or the `hermes/` subtree) from the
`archive` replica; the PV rebinds to the same path.

---

## Secrets

Two ESO ExternalSecrets ([`externalsecret.yaml`](../kubernetes/apps/hermes/externalsecret.yaml)),
both from the `onepassword-homelab` ClusterSecretStore:

- **`hermes-secrets`** ← 1Password item **Hermes Secrets**
  - `dashboard-username` / `dashboard-password` / `dashboard-session-secret` →
    `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `_PASSWORD` / `_SECRET` — the
    dashboard's retired `basic` provider — retained on the 1P item as
    emergency-revert values only, NOT synced (see §SSO).
  - `hermes-dashboard-oidc-client-secret` →
    `HERMES_DASHBOARD_OIDC_CLIENT_SECRET` — the dashboard's Authentik OIDC
    client secret (see §SSO). `terraform/authentik` injects the **same** 1P
    field into the authentik provider (docs/40), so authentik and the
    dashboard can never disagree.
  - `api-server-key` → `API_SERVER_KEY` — bearer key for the gateway's
    in-process API server (its health-probe surface, see §Observability;
    **mandatory** — the server refuses to start without it).
  - `claude-code-oauth-token` → `CLAUDE_CODE_OAUTH_TOKEN` — the Claude Code
    delegate's Max-subscription OAuth token (see §Coding delegates).
  - `discord-bot-token` → upserted into `/opt/data/.env` by the init
    container, **not** gateway container env (see §Gateway platform config —
    gateway config never reads `os.environ`).
  - `hass-token` → a Home Assistant long-lived access token, upserted into
    `/opt/data/.env` by the init container like the Discord token; its
    presence auto-enables the homeassistant tool (see §Home Assistant).
  - The five SYNCED fields (oidc secret, api-server-key, claude/discord
    tokens, hass-token) must exist on the item or the whole Secret fails to
    sync; the three retired dashboard-* fields are reserve-only.
- **`hermes-registry-pull`** ← 1Password item **Hermes Registry Pull** → a
  `kubernetes.io/dockerconfigjson` Secret for `registry.git.esweiss.com`, used
  by `imagePullSecrets`.

There is **no LLM-provider API key** in these Secrets. The LLM engine is the
Codex app-server runtime, authenticated by a ChatGPT subscription via a one-time
`codex login` (OAuth) — the token lives in `CODEX_HOME` on the NFS volume, not in
1Password (see §LLM engine). Other Hermes provider keys or messaging-platform
tokens (Telegram, Slack, …) can be entered in the **dashboard UI**, which writes
them to `/opt/data/.env` on the encrypted, backed-up NFS volume — the gateway's
canonical config store (see §Gateway platform config). Rotating a UI-entered key
is an in-dashboard edit; rotating the 1Password-managed fields is a normal ESO
rotation (docs/15) — for `discord-bot-token` the init container re-syncs `.env`
on the next pod start, so `task hermes:restart` completes the rotation.

---

## LLM engine — Codex app-server runtime (ChatGPT subscription)

Hermes' LLM engine here is the **Codex app-server runtime**, not a metered API
key. When it is on, Hermes delegates OpenAI/Codex turns to the bundled `codex`
CLI (baked into the image, see §Image) over JSON-RPC stdio; the CLI runs the tool
loop while Hermes keeps the session, memory, skills, and gateway. Auth is a
**ChatGPT subscription** via `codex login` — usage is billed to the subscription,
**not** as API tokens. (Requires Hermes ≥ 2026.5 and Codex CLI ≥ 0.130.0; both
are satisfied by the pinned `hermes_version` / `hermes_codex_version`.)

**State persistence.** Both containers set `CODEX_HOME=/opt/data/.codex`, so the
OAuth token (`auth.json`), `config.toml`, and Codex sessions live on the
encrypted, archive-backed NFS volume and survive pod restarts. The container's
own `$HOME` is `/opt/data` too, but we set `CODEX_HOME` explicitly so the
persisted path is unambiguous.

**Two deliberately separate credential stores — both held.**

| Login | Store | Serves |
|---|---|---|
| `hermes auth add openai-codex` | Hermes' **own** store, `~/.hermes/auth.json` | the `codex_responses` runtime mode (Hermes calls the Responses API itself) |
| `codex login` | the **Codex CLI's** store, `CODEX_HOME/auth.json` (`/opt/data/.codex/auth.json` here) | the `codex_app_server` runtime **and** the codex MCP delegate (§Coding delegates) |

Completing one login never satisfies the other — the stores are independent by
design. Both credentials are currently in place, so either runtime mode works
and the MCP delegate stays authenticated regardless of the mode selected.

### One-time operator setup

Do this once, after the pod is `Running`. It is an interactive step (browser
OAuth) — nothing in git triggers it.

1. **`codex login`** inside the gateway container. Because the pod is headless,
   use Codex's device-code flow (no port-forward needed):

   ```
   kubectl exec -it -n hermes deploy/hermes -c gateway -- codex login --device-auth
   ```

   Codex prints a URL and a code; open the URL in your browser, sign in to your
   ChatGPT account, and enter the code. On success it writes
   `/opt/data/.codex/auth.json` (persisted on NFS). `task hermes:codex-login` is a
   shortcut for this exec. Verify with:
   `kubectl exec -n hermes deploy/hermes -c gateway -- codex login status`.

2. **Enable the runtime in Hermes.** In the Hermes dashboard/TUI run:

   ```
   /codex-runtime codex_app_server
   ```

   (`/codex-runtime` with no arg shows the current state; `on` / `off` / `auto`
   are synonyms.) Equivalently, set `openai_runtime: codex_app_server` under
   `model:` in Hermes' config on `/opt/data` — the setting persists on NFS
   either way. After enabling, `task hermes:restart` so the gateway picks it up.

Once both steps are done, OpenAI/Codex turns route through your ChatGPT
subscription. The login is a one-time action per token lifetime — re-run
`codex login --device-auth` only if the token is revoked or expires.

---

## Coding delegates — Claude Code + Codex (subscription-billed)

Hermes **orchestrates**; coding tasks are **delegated** to CLIs baked into the
image. The key property: each delegate CLI holds and presents **its own
subscription credential** — Hermes only reads stdout, so the delegated work
bills the respective subscription, never an API meter, and no provider secret
enters the model context.

- **Claude Code** (`@anthropic-ai/claude-code`, pinned as
  `hermes_claude_version` in all.yml): Hermes runs headless `claude -p "<task>"`
  via its `terminal` tool, steered by the bundled `claude-code` skill
  (`--output-format json` returns structured results + a `session_id` for
  `--resume` follow-ups). Auth is the long-lived OAuth token from
  `claude setup-token` (**Claude Max subscription**) delivered as
  `CLAUDE_CODE_OAUTH_TOKEN` via ESO; CLI state persists in
  `CLAUDE_CONFIG_DIR=/opt/data/.claude` on NFS. **Never set
  `ANTHROPIC_API_KEY`** in this pod — its mere presence flips the CLI to
  metered API billing.
- **Codex** (already the LLM engine's CLI): registered as an MCP tool server in
  `/opt/data/config.yaml` (`mcp_servers.codex` → `codex mcp-server`, generous
  `timeout: 3600` for long runs), surfacing `mcp_codex_codex` /
  `mcp_codex_codex-reply` tools with `sandbox` / `approval-policy` / `cwd`
  parameters. Billed to the **ChatGPT subscription** via the existing
  `CODEX_HOME` auth.json.
- **Workspaces**: delegates work under `/opt/data/workspace/<repo>` (NFS —
  survives pod recreation, encrypted at rest, archive-backed). Prefer scoped
  permissions (`--allowedTools` / `sandbox: workspace-write`) over
  bypass-everything modes: the same volume holds Hermes' own `.env` and OAuth
  tokens.
- **Git access**: delegates use `git.ericsweiss.com` HTTPS remotes (project
  access token) — the external hostname resolves to the WAN IP, a *public*
  address, so clones/pushes ride the existing public-`:443` egress allowance
  via the router hairpin. The internal hostname (`git.esweiss.com` → Traefik
  VIP `.101`) is **not** reachable from the pod and cannot be pinholed: from a
  pod, kube-proxy DNATs a LoadBalancer VIP to the backend *pod* IP before
  NetworkPolicy evaluation, so a VIP `/32` ipBlock rule never matches. SSH
  remotes are deliberately not reachable.
- **Quota note**: delegated runs share the subscriptions' interactive rate
  windows (Max 5-hour/weekly; Codex allowance) — heavy fan-out can starve your
  own interactive sessions.

**One-time setup**: run `claude setup-token` locally, sign in with the Max
account, store the printed `sk-ant-oat01-…` token as `claude-code-oauth-token`
on the 1Password **Hermes Secrets** item (before merging the manifest that
references it), `task hermes:restart`, then verify with
`kubectl exec -n hermes deploy/hermes -c gateway -- claude -p "reply OK"`.

---

## SSO — dashboard Authentik OIDC (OIDC-only)

Access is the dashboard's own Authentik OIDC login — one Authentik prompt,
both hostnames. (A forward-auth perimeter used to sit in front of it; that
layer was removed as redundant once the dashboard's login became Authentik
OIDC itself.)

1. **Dashboard OIDC (the auth layer).** The dashboard's `self_hosted`
   generic-OIDC `dashboard_auth` provider points at Authentik: issuer
   `https://auth.ericsweiss.com/application/o/agent/`, client
   `hermes-dashboard`, confidential (non-empty client secret) with PKCE always
   on, standard discovery from `{issuer}/.well-known/openid-configuration`,
   callback `GET /auth/callback`. Config is env-only in this deployment
   (`HERMES_DASHBOARD_OIDC_*` — env wins over `config.yaml`). No
   `PUBLIC_URL` pin: the redirect_uri is reconstructed per-request from
   Traefik's `X-Forwarded-Host`/`-Proto` (`FORWARDED_ALLOW_IPS` makes uvicorn
   trust them — safe because the NetworkPolicy admits only Traefik, which
   overwrites those headers), so each hostname round-trips to its own
   `/auth/callback` — both are **strict** allowed redirect URIs on the
   authentik provider, the same dual-host pattern as immich/nextcloud.
   Authorization requires membership of the `hermes-users` group (the
   `agent` application's policy binding).
2. **Traefik-only NetworkPolicy.** Ingress on `:9119` is default-deny except
   from the Traefik namespace, so the OIDC-gated dashboard is the only path.
3. **OIDC-only — the `basic` password provider is retired.** The dashboard
   binds `0.0.0.0` (so Traefik can reach it), engaging upstream's
   **fail-closed** auth gate — at least one `dashboard_auth` provider must be
   registered; the `self_hosted` OIDC provider satisfies it alone. With
   exactly one session provider the auth middleware **auto-launches** the
   login flow, and for OIDC that is the intended **silent-SSO redirect**: a
   live Authentik session means no chooser click and no visible prompt.
   (Historical note: a chooser existed briefly while `basic` was registered
   alongside OIDC, because auto-launch on a password-only provider raises
   `NotImplementedError` → HTTP 500 — retiring `basic` removes the chooser
   AND the hazard.) When Authentik is fully down the dashboard UI is
   unreachable by design — operate via `kubectl exec … hermes` (CLI), or
   emergency-revert by re-adding the `HERMES_DASHBOARD_BASIC_AUTH_*` env
   trio + the three ESO entries (values retained on the 1P item).

### Authentik objects (Terraform)

The Authentik side lives in [`terraform/authentik`](../terraform/authentik/)
(docs/40) — nothing is created in the admin UI:

- `authentik_provider_oauth2.hermes_dashboard` +
  `authentik_application.app["agent"]` — the dashboard's OIDC client on the
  `agent` library tile (slug `agent` → issuer path `/application/o/agent/`),
  with both hostnames' `/auth/callback` as strict redirect URIs.
- The `hermes-users` group (`groups.tf`) + the `agent` policy binding
  (`policy_bindings.tf`).

Changes flow branch → MR → supervised `terraform apply` (docs/40). The OAuth2
provider needs no outpost assignment. Verify:
`curl -sI https://agent.ericsweiss.com` → `302` into the dashboard's login
flow when unauthenticated (both hostnames behave identically).

---

## Gateway platform config — `/opt/data/.env` is canonical

**Why not container env:** the gateway's config loader (`_getenv`) prefers the
**active profile's secret scope** and never falls back to `os.environ` —
container env vars are **invisible to gateway config**. `/opt/data/.env` (on
the encrypted, archive-backed NFS volume) is the operative store for platform
credentials. (The **dashboard** is different: its `HERMES_DASHBOARD_*` settings
read real container env — see §SSO.)

Two write paths into `.env`:

- **Codified (Discord + Codex home + camofox + Home Assistant).** The
  `init-data` initContainer
  ([`deployment.yaml`](../kubernetes/apps/hermes/deployment.yaml)) upserts
  `DISCORD_BOT_TOKEN` (ESO ← 1P `discord-bot-token`), `DISCORD_ALLOWED_USERS`
  (manifest literal — an identifier, not a credential), `CODEX_HOME`,
  `CAMOFOX_URL` (manifest literal — the sidecar's pod-localhost address, see
  §Browser tooling), `HASS_URL` (manifest literal) and `HASS_TOKEN` (ESO ← 1P
  `hass-token`, see §Home Assistant) into `.env` on **every pod start**,
  remove-then-append per key. An ESO rotation of a token therefore propagates
  on the next start (`task hermes:restart` completes a rotation), values
  persist on NFS between starts, and keys outside the sync list are never
  touched.
- **Dashboard UI.** Other platform settings (e.g. `DISCORD_HOME_CHANNEL` /
  `DISCORD_HOME_CHANNEL_THREAD_ID`) are written by the UI to the same `.env`
  (+ `config.yaml`) and persist there.

To onboard a **new** platform (Telegram, Slack, …):

1. Configure it in the dashboard UI (writes `.env` + `config.yaml`), then
   `task hermes:restart` so the gateway picks it up.
2. Optionally codify its token like Discord's: add the field to the **Hermes
   Secrets** 1P item (docs/15), an entry in
   [`externalsecret.yaml`](../kubernetes/apps/hermes/externalsecret.yaml), and
   an `upsert` line + env in the initContainer — then rotations are ESO-driven.
3. **Gateway health signal — already in place.** The gateway carries an HTTP
   `GET /health` startup + liveness probe against its in-process API server
   (see §Observability), so a wedged supervisor (s6 up, agent stalled) is
   restarted automatically rather than left silently dead.

All current messaging platforms use HTTPS/WSS on `:443`, which the egress
NetworkPolicy already allows. A platform needing a non-443 port requires an
added egress rule in
[`networkpolicy.yaml`](../kubernetes/apps/hermes/networkpolicy.yaml).

---

## Memory backend — Hindsight (runtime-enabled)

[Hindsight](https://github.com/vectorize-io/hindsight) replaces Hermes'
built-in memory with a knowledge-graph store (entity resolution, observation
consolidation, multi-strategy recall). It runs as its own app —
[`kubernetes/apps/hindsight/`](../kubernetes/apps/hindsight/) (see that
README for the two-container hindsight + llama.cpp architecture, the fully
local LLM, and the Postgres-on-NFS storage decision). Hermes talks to it via
the bundled `hindsight` memory plugin in **`local_external`** mode.

The `llama.cpp` sidecar offloads inference to the **GTX 1660 Ti passed through to
the prec-01 agent** (`server-cuda-` image + `-ngl 99` + `nvidia.com/gpu`), which
cuts extraction latency ~10× vs the prior CPU build; the CPU-era
timeout/thread tuning is annotated as legacy pending GPU re-measurement. Full
mechanics + the driver/CUDA compatibility risk are in
[docs/43-gpu-passthrough.md](43-gpu-passthrough.md).

**Enablement is runtime config, deliberately NOT in git**: `memory.provider`
lives in Hermes' config on the NFS volume (like the platform tokens), so git
carries the infrastructure and the operator flips the switch.

One-time operator steps, after the hindsight pod is `Running`:

1. **Plugin config.** Write the plugin's profile-scoped config file
   (`$HERMES_HOME/hindsight/config.json` — `/opt/data/hindsight/config.json`
   here):

   ```bash
   kubectl exec -n hermes deploy/hermes -c gateway -- sh -c \
     'mkdir -p /opt/data/hindsight && cat > /opt/data/hindsight/config.json' <<'EOF'
   {
     "mode": "local_external",
     "api_url": "http://hindsight.hindsight.svc.cluster.local:8888",
     "memory_mode": "hybrid"
   }
   EOF
   ```

   `memory_mode: hybrid` = automatic per-turn context injection **and**
   explicit `hindsight_*` tools for the LLM. No API key — the hindsight
   NetworkPolicy admits only this namespace.

2. **Select the provider + restart.** The first enable pip-installs the
   plugin's `hindsight-client` dependency via `uv` (rides the existing
   public-`:443` egress):

   ```bash
   kubectl exec -n hermes deploy/hermes -c gateway -- hermes config set memory.provider hindsight
   task hermes:restart
   ```

3. **Step-0 char-limit relief (recommended).** Hindsight's recalled
   observations are denser than the built-in memory the defaults were sized
   for — double the injection budgets (defaults 2200/1375):

   ```bash
   kubectl exec -n hermes deploy/hermes -c gateway -- hermes config set memory.memory_char_limit 4400
   kubectl exec -n hermes deploy/hermes -c gateway -- hermes config set memory.user_char_limit 2750
   task hermes:restart
   ```

**Rollback**: remove the provider key (`hermes config set memory.provider
builtin` or delete the `memory.provider` line from the config) + `task
hermes:restart` — the built-in memory store always runs, so this is a pure
config flip; nothing in the cluster changes. Retained Hindsight memories stay
in its PostgreSQL for a re-enable.

**Multi-user (future)**: Hindsight partitions memory into **banks** (`bank_id`
on every retain/recall — default bank `hermes`). The plugin's
`bank_id_template` config (placeholders `{profile}`/`{platform}`/`{user}`/…,
e.g. `"bank_id_template": "hermes-{user}"`) derives a bank per platform user,
giving per-user memory segregation with no server-side change.

---

## Browser tooling — camofox sidecar

Hermes' preferred browser tool is **camofox**
([jo-inc/camofox-browser](https://github.com/jo-inc/camofox-browser)) — a
Camoufox (hardened-Firefox) automation server with anti-detection
fingerprinting, driven over HTTP. It runs as the third container of the hermes
pod (self-built image — `build-camofox-browser` CI job,
[`docker/camofox-browser/README.md`](../docker/camofox-browser/README.md)):

- **Wiring**: the gateway reads `CAMOFOX_URL=http://127.0.0.1:9377` from
  `/opt/data/.env` (init-container upsert — §Gateway platform config); its
  presence makes the camofox tool available. `:9377` is on no Service and has
  no ingress allow — only the gateway (pod-localhost) can drive the browser.
- **Persistence**: profiles/cookies/traces under `/opt/data/.camofox/*` on the
  NFS volume (`CAMOFOX_PROFILE_DIR`/`CAMOFOX_COOKIES_DIR`/`CAMOFOX_TRACES_DIR`)
  — logged-in browser sessions survive pod recreation, encrypted at rest,
  archive-backed.
- **Guardrails**: `MAX_SESSIONS=5` (upstream default 50 — far more Firefox
  than the pod's memory budget; 5 is plenty for one agent),
  `CAMOFOX_CRASH_REPORT_ENABLED=false` (upstream defaults crash reporting ON —
  this is a private deployment). Memory: 1Gi request / 3Gi limit,
  VPA-excluded (browser memory tracks open sessions, not history — see
  vpa.yaml).
- **Egress**: browsing rides the public `:443` egress rule; `:80` is also
  allowed (page loads routinely enter on a plain-http URL before the https
  redirect). RFC1918/LAN stays blocked — the agent's browser cannot reach
  internal services.
- **Health**: `GET /health` startup + liveness probes — camofox reports 503
  while the browser process is dead/recovering (it also warm-restarts the
  browser itself), so the kubelet only restarts the container when the server
  loop is truly gone.

**Smoke test**: ask the agent (Discord or dashboard) —
*"Using the browser, open https://example.com and tell me the page title."*
Expect "Example Domain"; `task hermes:logs COMPONENT=camofox` shows the
navigation.

---

## Home Assistant — control tool + voice assistant

### Control (live): the homeassistant tool

The gateway's `homeassistant` tool auto-enables when `HASS_TOKEN` is present
in its env store: the init container upserts `HASS_URL=https://home.esweiss.com`
and `HASS_TOKEN` (ESO ← 1P **Hermes Secrets**/`hass-token`) into
`/opt/data/.env` (§Gateway platform config). The agent can then query and
control HA (states, services, automations) as the HA user the token belongs
to.

- **Path**: `home.esweiss.com` → AdGuard rewrite → Traefik `.101` → HAOS
  `.154`. From the pod, kube-proxy DNATs the VIP to the Traefik **pod** IP, so
  the NetworkPolicy allows it with a post-DNAT traefik-**namespace** selector
  (a `.101/32` ipBlock can never match — see networkpolicy.yaml).
- **No SSO conflict**: the `home.esweiss.com` IngressRoute carries no
  forward-auth middleware (hass-openid runs *inside* HA), and
  `lan-tailscale-only` allowlists the pod CIDR — bearer API calls pass
  straight through (verified against
  `kubernetes/apps/vm-ingress/home-assistant.yaml`).
- **Setup (one-time)**: create a dedicated HA user (Settings → People — give
  it only the access the agent should have; don't lend the agent your admin
  account), log in as it, profile → Security → **Long-lived access tokens** →
  create; store as `hass-token` on the 1P item **before** the manifest
  merges (a missing field fails the whole Secret sync), then
  `task hermes:restart`.
- **Verify**:
  `kubectl exec -n hermes deploy/hermes -c gateway -- sh -c 'curl -fsS -H "Authorization: Bearer $(sed -n "s/^HASS_TOKEN=//p" /opt/data/.env)" https://home.esweiss.com/api/'`
  → `{"message": "API running."}`, then ask the agent for a light/sensor
  state.

### Voice assistant (plumbing live, HA side pending)

The long-term goal is Hermes as the conversation agent behind a **Home
Assistant Voice PE** puck. The cluster side is fully plumbed and inert until
HA is configured:

- The gateway's OpenAI-compatible API server (`:8642`, bearer-keyed —
  §Observability) is exposed **internally only** at
  **`https://agent-api.esweiss.com/v1`** (`hermes-api` Service + IngressRoute;
  AdGuard rewrite → `.101`; `lan-tailscale-only` + `hsts-header`, deliberately
  **no** Authentik — forward-auth 302s would break OpenAI-protocol clients,
  and the API server's own mandatory bearer key is the auth).
- HA-side plan (post-merge, manual): install **Extended OpenAI Conversation**
  (HACS), configure base URL `https://agent-api.esweiss.com/v1`, API key = the
  `api-server-key` value from 1P **Hermes Secrets**, model **`hermes-agent`**
  (the id the server exposes on `/v1/models` — verified live), then select it
  as the conversation agent in a voice pipeline (Settings → Voice assistants)
  and point the Voice PE at that pipeline.
- Sanity check from any LAN host:
  `curl -H "Authorization: Bearer <api-server-key>" https://agent-api.esweiss.com/v1/models`
  → `{"data": [{"id": "hermes-agent", …}]}`.

---

## 1Password — first-party skill over the official `op` CLI

Hermes can read and write secrets in a **dedicated, isolated Agent vault** via
1Password's own service accounts. Chosen over an MCP server deliberately:
Hermes ships **no** native 1Password *toolset*, and the only official 1Password
MCP (the "Environments" MCP) is desktop-app + per-access-prompt bound and never
returns secret values — a non-fit for a headless agent. The fit is Hermes'
**first-party 1Password skill** (upstream `optional-skills/security/1password`)
driving the **official `op` CLI**: first-party on both ends, nothing third-party
or self-authored.

**What ships here (the plumbing):**
- The `op` CLI is **baked into the image** (`docker/hermes-agent/Dockerfile.codex`,
  pinned `hermes_op_version`) from 1Password's signed apt repo.
- `OP_SERVICE_ACCOUNT_TOKEN` is delivered by ESO from the **dedicated** 1P item
  *Hermes Agent 1P Service Account* → the `hermes-secrets` Secret
  (`op-service-account-token`) → the **gateway container env** (plus an
  `/opt/data/.env` upsert). It is a *separate* item from **Hermes Secrets** so
  its blast radius and rotation are isolated (docs/15).

**Why gateway container env (not just `.env`):** the agent runs `op` through the
**terminal tool**, whose backend seeds the child env from `os.environ`
(`tools/environments/local.py` `_make_run_env`). `OP_SERVICE_ACCOUNT_TOKEN` is
not in that backend's name-based blocklist and matches no internal-secret
pattern, so it passes through and `op` authenticates. The **`code_execution`**
tool, by contrast, strips any `*TOKEN*` var by substring — so the skill drives
`op` via the terminal tool by design.

**Enable it (runtime, after this deploys):** ask Hermes to enable the
**1Password skill** (same as the Home Assistant skill). Confirm the plumbing
from a gateway shell: `kubectl exec -n hermes deploy/hermes -c gateway -- op vault ls`
should list only the Agent vault.

**Security model:** the service account authenticates to the 1Password **cloud**
(`*.1password.com:443`, already allowed by the egress NetworkPolicy — not the
in-cluster Connect that backs ESO). Scope it in the 1Password service-accounts
console to **only** the isolated Agent vault (read+write). Two consequences to
accept: (1) the raw token sits in the gateway env / `.env`, readable by the
agent's shell — but the agent already has broad execution (Claude Code / Codex
delegates) and can read every other `/opt/data/.env` token, and the SA reaches
only the low-stakes Agent vault, so this adds no new class of exposure; (2)
secrets the agent reads/writes flow through its LLM provider — so the Agent vault
is for **disposable, agent-scoped** secrets only, never infra credentials.

---

## Observability

- **Logs**: pod stdout → the in-cluster Alloy DaemonSet → Loki (automatic).
  `task hermes:logs [COMPONENT=dashboard|gateway]`.
- **Reachability**: a blackbox HTTP probe on `https://agent.esweiss.com`
  (`module: http_sso`, added to `exporters/blackbox-exporter.yaml`). The generic
  **`EndpointDown`** alert (`probe_success == 0` for 5m, warning) covers it —
  the same pattern every other web app here relies on; no per-app rule is added.
- **Crash-loops**: covered by the standard kube-state-metrics alerts on both
  containers.
- **Gateway liveness**: the gateway's in-process **OpenAI-compatible API
  server** (`API_SERVER_ENABLED=true`, bound `0.0.0.0:8642`) doubles as its
  health surface. The kubelet runs an **HTTP `GET /health`**
  startupProbe (10s × 30, covers the slow s6/NFS first boot) and livenessProbe
  (30s × 5 → a truly hung gateway restarts within ~2.5 min). `httpGet` rather
  than `tcpSocket` is deliberate: `/health` is served by the gateway's own
  asyncio loop, so a wedged supervisor (s6 PID1 up, agent stalled) fails the
  probe, which a kernel-level TCP connect would not catch. `/health` is the
  API server's only unauthenticated route; everything else requires the bearer
  key (`API_SERVER_KEY`, ESO-sourced `api-server-key` on **Hermes Secrets** —
  mandatory, the server refuses to start without it). `:8642` is served
  in-cluster only via the `hermes-api` Service behind the
  `agent-api.esweiss.com` IngressRoute (LAN/Tailscale-scoped, bearer-keyed —
  §Home Assistant), and the namespace NetworkPolicy admits only Traefik to it;
  kubelet probes are node-local and bypass NetworkPolicy.
- The dashboard carries **TCP-connect** probes on `:9119`, not HTTP GETs: a
  listener-up check is sufficient health for the single-process dashboard and
  avoids coupling pod health to any specific HTTP route or status code (the UI
  is a redirect-heavy SPA). Reachability/auth is covered by the blackbox
  `http_sso` probe instead.

No Grafana dashboard is added — there is no dedicated upstream Hermes dashboard,
and the app has no Prometheus `/metrics` endpoint. (The **Hindsight** memory
backend does: native `/metrics` on its API port, scraped via
`observability/service-monitors/hindsight.yaml`, with the `HindsightDown`
kube-state alert — see `kubernetes/apps/hindsight/README.md`.)

---

## Upgrade procedure

1. Pick a newer release tag from
   <https://github.com/NousResearch/hermes-agent/releases> and note the commit it
   points at (the tag page shows the SHA, or read it from a prior CI build log).
2. Set `hermes_version`, `hermes_git_sha` (the tag's commit) **and**
   `hermes_image_version` (`<new hermes_version>-r1`) in
   `ansible/inventories/prod/group_vars/all.yml`, run `task flux:sync-versions`.
   All three must move together: `build-hermes-agent` refuses to build if the tag
   resolves to any other commit — so a stale or mismatched `hermes_git_sha` fails
   the pipeline loudly rather than building a moved/compromised tag — and it also
   hard-fails unless `hermes_image_version` is exactly `${hermes_version}-r<N>`.
   A patches-only change bumps just the `-rN`.
3. Commit both files on a branch → MR → merge.
4. On `main`, `build-hermes-agent` verifies the tag→SHA, rebuilds the image,
   pushes the new tags and then verifies the registry resolves
   `:${hermes_image_version}` (MR pipelines are build-only and publish nothing); Flux then
   rolls the Deployment. `/opt/data` is preserved (config migrations run
   automatically via the image's s6 boot hook). The Codex OAuth token in
   `CODEX_HOME=/opt/data/.codex` persists across the roll — no re-login needed.

To move the bundled **Codex CLI** independently, bump `hermes_codex_version` (from
<https://github.com/openai/codex/releases>, a stable `rust-vX.Y.Z` tag → the bare
`X.Y.Z`), `task flux:sync-versions`, commit → MR → merge; the wrapper rebuilds
with the new CLI and Flux rolls the pods. `task maintenance:check-versions`
reports both pins.

---

## Runbooks

- **`ImagePullBackOff` right after first merge**: the image is built by the
  `main` pipeline's `build-hermes-agent` job, which races Flux's reconcile. The
  pod pulls successfully once the job finishes; `task hermes:restart` or a
  `task flux:reconcile` speeds it up. Persistent `ImagePullBackOff` (build long
  finished) points at the registry-pull path: the node pulls from
  `registry.git.esweiss.com` (internal `.101`) but the Docker v2 **token realm**
  lives on `git.ericsweiss.com`, which resolves only over the node's public
  egress. Sanity-check the whole dance from a k3s agent before blaming Flux:
  `crictl pull --creds '<deploy-token-user>:<deploy-token>' registry.git.esweiss.com/eric/weisssrv/hermes-agent:<hermes_image_version>`
  — use the `-rN` tag the Deployment actually references; `:<hermes_version>` is
  pushed too but is not what the pod pulls.
- **Pod stuck `ContainerCreating` with an NFS mount error**: `/appdata/hermes`
  does not exist on the NAS yet. It is created by the `nas_storage` role
  (`nas_storage_appdata_dirs`) — run `task storage:deploy` (or let the `deploy-storage`
  CI job run on merge). The kubelet retries the mount, so the pod self-heals once
  the subdir exists; no pod action is needed.
- **Dashboard won't start**:
  - `CreateContainerConfigError` → `hermes-secrets` has not synced. Check
    `task hermes:status` and that the **Hermes Secrets** 1Password item exists
    with **all eight** fields (docs/15) — a missing field fails the whole
    Secret, including a not-yet-created `hermes-dashboard-oidc-client-secret`
    or `hass-token`.
  - `CrashLoopBackOff` with a start-up refusal in the logs → the dashboard's
    `0.0.0.0` bind is fail-closed and no auth provider registered. Confirm the
    `HERMES_DASHBOARD_OIDC_*` env (issuer, client id, client secret) is
    populated: `task hermes:logs COMPONENT=dashboard`.
- **Can't reach a host / access denied after login**: the `hermes-users`
  policy binding denies the user (`terraform/authentik/policy_bindings.tf`),
  or the `agent` application drifted on the authentik side (`terraform plan`
  surfaces it). See §SSO.
- **OIDC login fails with a redirect_uri error**: the provider allows exactly
  two strict URIs — `https://agent.ericsweiss.com/auth/callback` and
  `https://agent.esweiss.com/auth/callback`. The dashboard reconstructs the
  redirect_uri per-request from `X-Forwarded-Host`/`-Proto`, which uvicorn
  only trusts because of `FORWARDED_ALLOW_IPS` (deployment.yaml) — if that
  env is missing the callback degrades to the pod-local URL and mismatches.
  Discovery failures point at the issuer:
  `https://auth.ericsweiss.com/application/o/agent/` must serve
  `.well-known/openid-configuration`, which requires the `agent` application
  applied on the authentik side (docs/40 — supervised apply).
  Break-glass meanwhile: `kubectl exec` into the pod and use the hermes CLI.
- **LLM turns fail / "not authenticated" from Codex**: the one-time Codex login
  or runtime enable is missing or the token expired. Check
  `kubectl exec -n hermes deploy/hermes -c gateway -- codex login status`; if not
  logged in, re-run `task hermes:codex-login` (`codex login --device-auth`), then
  confirm `/codex-runtime` reports `codex_app_server` and `task hermes:restart`
  (see §LLM engine). The token lives on the NFS volume (`CODEX_HOME=/opt/data/.codex`),
  so it survives pod restarts — a sudden failure usually means a revoked/expired
  ChatGPT-subscription session, fixed by logging in again.
- **Provider key changes not taking effect**: the gateway needs a restart after
  `.env` edits — `task hermes:restart`.

## Related documentation

- [docs/29-flux-operations.md](29-flux-operations.md) - how the manifests reconcile
- [docs/31-observability.md](31-observability.md) - metrics, logs and alerts
- [docs/33-autoscaling.md](33-autoscaling.md) - the VPA tiers that size these pods
- [docs/40-authentik-terraform.md](40-authentik-terraform.md) - the dashboard's OIDC provider as code
- [docs/43-gpu-passthrough.md](43-gpu-passthrough.md) - the GPU Hindsight offloads to
