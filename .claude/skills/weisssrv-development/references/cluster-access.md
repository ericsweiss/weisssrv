# Cluster access, secrets & conventions

## Local toolchain

- `pip install -r requirements.txt` (Ansible, Molecule, ansible-lint, yamllint)
  plus `pytest` + `pyyaml` for the script tests.
- **A weisssrv-lib checkout is required for `task lint`**, not optional: a
  sibling `../weisssrv-lib`, or `$WEISSSRV_LIB_PATH` pointing at one. The
  vendored-copy gate (`scripts/test_vendored_byte_identity.py`, run by
  `task scripts:test` inside `task lint`) **never skips** — a gate that
  disables itself when it cannot find its comparison source is not a gate — so a
  single-repo clone fails it with an actionable AssertionError rather than a
  green pass. It compares at the ref `.gitlab-ci.yml` pins and falls back to the
  checkout's working tree when that tag has not been cut yet, announcing the
  fallback.
- `ansible-galaxy install -r ansible/requirements.yml` pulls the `weisssrv.infra`
  collection at the pinned tag — nothing Ansible-side resolves without it, and it
  needs read access to `git.ericsweiss.com/eric/weisssrv-lib`. Re-run with
  `--force` after a pin bump. While a new tag is being prepared,
  `WEISSSRV_COLLECTION_PATH=../weisssrv-lib task ansible:lint` lints against the
  checkout instead (`references/maintenance-upgrades.md`).
- `pre-commit install` once per clone — gitleaks, yamllint, whitespace/EOF,
  `check-taskfile.sh` and `check-doc-links.py` then run on every commit. The
  hook set and its pinned revs are `.pre-commit-config.yaml`.
- The binary toolchain the heavier gates need (kustomize, kubeconform, helm,
  flux, promtool, amtool, shellcheck, terraform) is listed in `README.md` §
  Prerequisites; each task fails fast naming the tool it could not find.
- `op` (1Password CLI) signed in; `task op:check` confirms the session.

## kubeconfig

- Fetch with `task k3s:kubeconfig` — it SSHes the first reachable k3s server,
  rewrites the API endpoint to the VIP `10.0.10.161`, and writes
  `~/.kube/config-k3s` (mode 600).
- Use it per-command or per-shell: `export KUBECONFIG=~/.kube/config-k3s`. The
  app/observability task wrappers assume this is set and fail fast otherwise.
- Read-only `kubectl get/describe/logs/top` and `flux get/check/logs` are the
  safe inspection verbs. Do not mutate cluster state — Flux owns `kubernetes/`.

## SSH host naming

- Guests resolve by name (`ssh pve-nas-01`, `ssh dns-01`, `ssh dns-02`,
  `ssh smtp-relay`, `ssh k3s-srv-nas-01`, `ssh k3s-agt-nas-01`, …). The authoritative
  IP ↔ host ↔ Proxmox-placement table is `docs/01-overview.md`; the inventory of
  record is `ansible/inventories/prod/hosts.yml`.
- Everything uses user `eric` with passwordless sudo (`docs/03-ssh-users.md`).

## Secrets — three consumers, one Homelab vault

1. **Host tooling (Ansible/Terraform/Task)** — `op run --` injects
   `op://Homelab/<Item Title>/<field>` references at runtime. The references are
   the literal op:// strings in the Taskfile's per-task `env:` blocks (and the
   equivalent CI job variables); `docs/15-credential-rotation.md` is the
   authoritative item inventory. Never print a value; `task secrets:show` lists
   refs only.
2. **In-cluster (ESO)** — `ExternalSecret` against ClusterSecretStore
   `onepassword-homelab` (1Password Connect, in-cluster, reads a local cache not
   the cloud). `remoteRef.key` = 1P item **title**, `remoteRef.property` =
   **field**. No `op://`, no item IDs.
3. **CI** — `.gitlab-ci.yml` uses `op read` / `op run` with
   `OP_SERVICE_ACCOUNT_TOKEN`, which **must be masked and protected** (a GitLab
   project setting — docs/13 § the credential note): protected means it is
   absent on merge-request refs, so no MR job can read the vault with a
   branch's own code and every op:// consumer is main-only. Token-guarded jobs (deploy,
   maintenance, the drift plans) are simply not created without it — including
   `terraform-plan`, whose MR rule is therefore inert. `pr-agent-review` gates
   on `$OPENAI__KEY` instead, which is why it still runs on MRs.

The canonical, authoritative inventory of every expected 1P item (titles +
fields) is `docs/15-credential-rotation.md` → "Required 1Password Items". Add new
items there, with exact field names; the operator creates the item itself.

The only manually-created in-cluster Secrets are the bootstrap pair
`op-credentials` + `onepassword-connect-token` (`task flux:bootstrap-onepassword`).
Every other in-cluster Secret is produced by ESO from an `ExternalSecret`.

## Remote access

- Tailscale runs on the Proxmox hosts for off-LAN admin (`docs/05-tailscale.md`);
  the `admin_ts` firewall IPSet scopes admin ports to the tailnet. The Tailscale
  ACL is policy-as-code in `terraform/tailscale/`.

## Handy read-only checks

- `task flux:status` / `task flux:verify` — Flux + managed-resource health.
- `task k3s:status`, `task infra:verify` — cluster / base-infra health.
- `task <ns>:status` — per-app namespace summary.
- `task op:check` — confirm the local `op` session.
- `task collect-state` — redacted full snapshot to the gitignored
  `CLUSTER_STATUS.txt`.
