# 37 — Hermes Agent

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

One Deployment (`replicas: 1`, `strategy: Recreate`), two containers off the
same self-built image, sharing one NFS `/opt/data` volume — mirroring upstream's
`docker-compose.yml`, where both the `gateway` and `dashboard` services
bind-mount `~/.hermes` and run with `network_mode: host`:

| Container | Command | Role |
|-----------|---------|------|
| `gateway` | `gateway run` | Always-on agent supervisor. Messaging-platform adapters (Telegram/Discord/Slack/…) register here. Runs even with **zero** platforms configured. |
| `dashboard` | `dashboard --host 0.0.0.0 --no-open` | The user-facing FastAPI web UI on `:9119` (9119 is the image default; upstream's compose binds `--host 127.0.0.1`). |

Because both containers live in one pod they share `localhost` (like the
compose `network_mode: host`), so the dashboard reaches the gateway over
`127.0.0.1` with no extra service. The single RWO NFS volume is why the update
strategy is `Recreate` (never dual-mount across a rolling update).

### Image

Upstream ships **no** published container image. Their `Dockerfile` is a
complete, self-contained multi-stage build (Debian 13 + uv/Python 3.13 + Node 22
+ s6-overlay) meant to be built at a release tag. The `build-hermes-agent` CI
job clones the repo at `hermes_version` and builds *their* Dockerfile, then
layers a thin wrapper ([`docker/hermes-agent/Dockerfile.codex`](../docker/hermes-agent/Dockerfile.codex))
that bakes in the pinned **OpenAI Codex CLI** (`hermes_codex_version`,
`npm i -g @openai/codex`) — Hermes' LLM engine is the Codex app-server runtime
(see §LLM engine below), so the `codex` CLI must be on the image's PATH. Node 22
+ npm are already in the base, so the wrapper only adds the CLI. Full build
details in [`docker/hermes-agent/README.md`](../docker/hermes-agent/README.md).
The in-cluster image ref is
`registry.git.esweiss.com/eric/weisssrv/hermes-agent:${hermes_version}` (the
**internal** registry host — AdGuard rewrite → Traefik `.101` → GitLab VM
registry, no hairpin NAT).

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
  `nas_storage` role (`nas_appdata_dirs`). The export is `all_squash,
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
    dashboard's bundled `basic` auth provider (**mandatory**, see §SSO).
  - `api-server-key` → `API_SERVER_KEY` — bearer key for the gateway's
    in-process API server (its health-probe surface, see §Observability;
    **mandatory** — the server refuses to start without it).
  - `claude-code-oauth-token` → `CLAUDE_CODE_OAUTH_TOKEN` — the Claude Code
    delegate's Max-subscription OAuth token (see §Coding delegates).
  - All five fields must exist on the item or the whole Secret fails to sync.
- **`hermes-registry-pull`** ← 1Password item **Hermes Registry Pull** → a
  `kubernetes.io/dockerconfigjson` Secret for `registry.git.esweiss.com`, used
  by `imagePullSecrets`.

There is **no LLM-provider API key** in these Secrets. The LLM engine is the
Codex app-server runtime, authenticated by a ChatGPT subscription via a one-time
`codex login` (OAuth) — the token lives in `CODEX_HOME` on the NFS volume, not in
1Password (see §LLM engine). Additional Hermes provider keys or messaging-platform
tokens (Telegram, Discord, …) are entered in the **dashboard UI** and written to
`/opt/data/.env` on the encrypted, backed-up NFS volume, so they persist and need
no manifest change. Rotating those is an in-dashboard edit; rotating the
1Password-managed dashboard/registry credentials is a normal ESO rotation
(docs/15).

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

**Hermes' own OAuth is a separate session.** `hermes auth add openai-codex`
writes to `~/.hermes/auth.json` — that is *not* the Codex CLI's token. Run
`codex login` separately (below); the two are independent.

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
- **Git access**: the egress NetworkPolicy carries a single `/32` pinhole to
  the internal Traefik VIP (`192.168.0.101:443`) so delegates can clone/push
  `git.esweiss.com` repos over HTTPS (project access token); SSH remotes are
  deliberately not reachable.
- **Quota note**: delegated runs share the subscriptions' interactive rate
  windows (Max 5-hour/weekly; Codex allowance) — heavy fan-out can starve your
  own interactive sessions.

**One-time setup**: run `claude setup-token` locally, sign in with the Max
account, store the printed `sk-ant-oat01-…` token as `claude-code-oauth-token`
on the 1Password **Hermes Secrets** item (before merging the manifest that
references it), `task hermes:restart`, then verify with
`kubectl exec -n hermes deploy/hermes -c gateway -- claude -p "reply OK"`.

---

## SSO — Authentik forward-auth + dashboard `basic` provider (layered)

Access is layered: an Authentik forward-auth perimeter in front of the
dashboard's own login.

1. **Authentik forward-auth (perimeter).** Both IngressRoutes carry the
   `authentik-auth` middleware, so only members of the Authentik `hermes-users`
   group ever reach the dashboard. This is the login users normally see
   (transparent after the first app in the SSO session).
2. **Traefik-only NetworkPolicy.** Ingress on `:9119` is default-deny except
   from the Traefik namespace, so there is no route to the dashboard that skips
   the forward-auth middleware.
3. **Dashboard `basic` auth provider (in-app, mandatory).** The dashboard stores
   provider API keys, so upstream binds it to `127.0.0.1` by default. This
   deployment binds it to `0.0.0.0` (so Traefik can reach it through the
   Service), and a non-loopback bind engages upstream's **fail-closed** auth
   gate: the dashboard refuses to start unless a `dashboard_auth` provider is
   registered. We satisfy that with the bundled `basic` provider, wired from
   `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `_PASSWORD` / `_SECRET` (1P-sourced
   via ESO — see docs/15 and §Secrets). This is why the dashboard credentials
   are **mandatory, not optional**: without them the container CrashLoopBackOffs.

The dashboard's own login sits behind the Authentik perimeter as
defence-in-depth — and it is what makes the `0.0.0.0` bind legal.

### Authentik setup (manual, one-time — done in the admin UI)

Authentik providers/applications are **not** stored in this repo; create them by
hand in the admin UI at `auth.esweiss.com`:

The dashboard is reachable on **two** hostnames — internal `agent.esweiss.com`
and external `agent.ericsweiss.com` — and each IngressRoute carries the
`authentik-auth` middleware. In forward-auth **single-application** mode a proxy
provider matches one external host, so **each host needs its own provider +
application**. Register both — with only the internal one, the external host
404s at the embedded outpost (the outpost has no provider for that host):

1. **Group** → *Directory ▸ Groups* → create `hermes-users`; add the users who
   should have access.
2. **Providers** → *Applications ▸ Providers ▸ Create* → **Proxy Provider**,
   once per host. Both are identical except for the external host:
   - Names: `hermes-internal` and `hermes-external`
   - Authorization flow: `default-provider-authorization-implicit-consent`
     (or your standard explicit-consent flow)
   - **Forward auth (single application)**
   - External host: `https://agent.esweiss.com` (internal provider) /
     `https://agent.ericsweiss.com` (external provider)
   - (Token validity / cookie settings: leave defaults.)
3. **Applications** → *Applications ▸ Applications ▸ Create*, one per provider:
   - `Hermes Agent (internal)`, slug `hermes-internal`, provider
     `hermes-internal`; and `Hermes Agent (external)`, slug `hermes-external`,
     provider `hermes-external`.
   - *Policy / Group / User Bindings* → bind the `hermes-users` group on **both**
     so only that group is authorized.
4. **Outpost** → *Applications ▸ Outposts* → edit the **embedded outpost** →
   add **both** `hermes-*` applications to its list. (The `authentik-auth`
   middleware points at the embedded outpost's
   `/outpost.goauthentik.io/auth/traefik` endpoint — same as every other
   forward-auth app here.) Adding the external application here is what clears
   the `agent.ericsweiss.com` 404.

No client ID/secret is needed for forward-auth. Verify **both** hosts:
`curl -sI https://agent.esweiss.com` and
`curl -sI https://agent.ericsweiss.com` → each `302` to Authentik when
unauthenticated (a `404` means that host's application is not on the embedded
outpost).

---

## Gateway / messaging-platform onboarding (user follow-up)

Out of the box the `gateway` container runs with **no** messaging platforms — it
idles as a supervisor. To add one (Telegram, Discord, Slack, …):

1. Open the dashboard → configure the platform (bot token, allowed users). The
   dashboard writes the config/token to `/opt/data/.env` + `config.yaml`.
2. Store the bot token in 1Password for the record (extend the **Hermes
   Secrets** item or add a dedicated item) if you prefer manifest-managed keys —
   otherwise the UI-written `.env` is sufficient and is backed up.
3. `task hermes:restart` so the gateway picks up the new platform config.
4. **Gateway health signal — already in place.** The gateway carries an HTTP
   `GET /health` startup + liveness probe against its in-process API server
   (see §Observability), so a wedged supervisor (s6 up, agent stalled) is
   restarted automatically rather than left silently dead. Nothing further is
   needed when onboarding a platform.

All current messaging platforms use HTTPS/WSS on `:443`, which the egress
NetworkPolicy already allows. A platform needing a non-443 port requires an
added egress rule in
[`networkpolicy.yaml`](../kubernetes/apps/hermes/networkpolicy.yaml).

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
  server** is enabled (`API_SERVER_ENABLED=true`, bound `0.0.0.0:8642`) solely
  as its health surface. The kubelet runs an **HTTP `GET /health`**
  startupProbe (10s × 30, covers the slow s6/NFS first boot) and livenessProbe
  (30s × 5 → a truly hung gateway restarts within ~2.5 min). `httpGet` rather
  than `tcpSocket` is deliberate: `/health` is served by the gateway's own
  asyncio loop, so a wedged supervisor (s6 PID1 up, agent stalled) fails the
  probe, which a kernel-level TCP connect would not catch. `/health` is the
  API server's only unauthenticated route; everything else requires the bearer
  key (`API_SERVER_KEY`, ESO-sourced `api-server-key` on **Hermes Secrets** —
  mandatory, the server refuses to start without it). `:8642` is on no
  Service/IngressRoute and the namespace default-deny admits only Traefik on
  `:9119`, so the API server is unreachable in-cluster; kubelet probes are
  node-local and bypass NetworkPolicy.
- The dashboard carries **TCP-connect** probes on `:9119`, not HTTP GETs: a
  listener-up check is sufficient health for the single-process dashboard and
  avoids coupling pod health to any specific HTTP route or status code (the UI
  is a redirect-heavy SPA). Reachability/auth is covered by the blackbox
  `http_sso` probe instead.

No Grafana dashboard is added — there is no dedicated upstream Hermes dashboard,
and the app has no Prometheus `/metrics` endpoint.

---

## Upgrade procedure

1. Pick a newer release tag from
   <https://github.com/NousResearch/hermes-agent/releases> and note the commit it
   points at (the tag page shows the SHA, or read it from a prior CI build log).
2. Set both `hermes_version` and `hermes_git_sha` (the tag's commit) in
   `ansible/inventories/prod/group_vars/all.yml`, run `task flux:sync-versions`.
   The two must move together: `build-hermes-agent` refuses to build if the tag
   resolves to any other commit, so a stale or mismatched `hermes_git_sha` fails
   the pipeline loudly rather than building a moved/compromised tag.
3. Commit both files on a branch → MR → merge.
4. On `main`, `build-hermes-agent` verifies the tag→SHA, rebuilds the image, and
   pushes the new tag (MR pipelines are build-only and publish nothing); Flux then
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
  `crictl pull --creds '<deploy-token-user>:<deploy-token>' registry.git.esweiss.com/eric/weisssrv/hermes-agent:<hermes_version>`.
- **Pod stuck `ContainerCreating` with an NFS mount error**: `/appdata/hermes`
  does not exist on the NAS yet. It is created by the `nas_storage` role
  (`nas_appdata_dirs`) — run `task storage:deploy` (or let the `deploy-storage`
  CI job run on merge). The kubelet retries the mount, so the pod self-heals once
  the subdir exists; no pod action is needed.
- **Dashboard won't start**:
  - `CreateContainerConfigError` → `hermes-secrets` has not synced. Check
    `task hermes:status` and that the **Hermes Secrets** 1Password item exists
    with **all** of `dashboard-username`, `dashboard-password`,
    `dashboard-session-secret` — a missing field fails the whole Secret.
  - `CrashLoopBackOff` with a start-up refusal in the logs → the dashboard's
    `0.0.0.0` bind is fail-closed and no `basic` provider registered. Confirm the
    three `dashboard-*` fields are populated (an empty username/password won't
    register the provider): `task hermes:logs COMPONENT=dashboard`.
- **Can't reach a host (302 loop, or 404 at the outpost)**: the Authentik
  `hermes-*` application/outpost entry or the `hermes-users` binding for that
  host is missing. A `404` on `agent.ericsweiss.com` specifically means the
  `hermes-external` provider/application was never added to the embedded
  outpost (each host needs its own forward-auth provider) — see the SSO setup
  above.
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
