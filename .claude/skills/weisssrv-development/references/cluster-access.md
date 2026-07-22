# Cluster access, secrets & conventions

## kubeconfig

- Fetch with `task k3s:kubeconfig` — it SSHes the first reachable k3s server,
  rewrites the API endpoint to the VIP `192.168.0.161`, and writes
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
   `op://Homelab/<Item Title>/<field>` references at runtime. The references live
   in the `secrets:` dict of `group_vars/all.yml`; Taskfile env anchors thread
   them into commands. Never print a value; `task secrets:show` lists refs only.
2. **In-cluster (ESO)** — `ExternalSecret` against ClusterSecretStore
   `onepassword-homelab` (1Password Connect, in-cluster, reads a local cache not
   the cloud). `remoteRef.key` = 1P item **title**, `remoteRef.property` =
   **field**. No `op://`, no item IDs.
3. **CI** — `.gitlab-ci.yml` uses `op read` / `op run` with
   `OP_SERVICE_ACCOUNT_TOKEN`. Token-guarded jobs (AI review, deploy, maintenance)
   are simply not created without it.

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
