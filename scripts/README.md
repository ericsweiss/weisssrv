# scripts/

Every gate, generator, and operational helper the Taskfile and CI invoke. Nothing
here is run by hand as part of the normal flow — `task --list` is the entry
point, and CI calls the same commands.

## Exit codes

Every gate here follows one contract: **0** clean, **1** a finding, **2** the gate
could not inspect its subject. The third code is what keeps
`flux-corpus-gates.sh` and the CI jobs from reporting a broken environment as a
policy violation: an unreadable input, a missing dependency, or a corpus holding
nothing is an operator error, not drift. `test_gate_exit_contract.py` holds the
non-vendored gates to it.

**Origin** tells you where a script is maintained:

| Origin | Meaning |
|---|---|
| local | Maintained here; the library ships nothing equivalent. |
| vendored | A byte-identical copy of a weisssrv-lib file. Fix it upstream, tag, re-vendor — a local edit is reverted by the next re-vendor, and site data belongs in the script's config file. |
| forked | A vendored file this cluster had to diverge from, declared with its reason in the manifest. Re-converge by getting the difference upstream; a library change to a forked file must be ABSORBED, not ignored. |

The inventory of record is this repo's own manifest,
[`vendored-manifest.yml`](vendored-manifest.yml). `test_vendored_byte_identity.py`
enforces it, driving the library's `scripts/check-vendored-copies.py` engine. The
library's `vendorable-paths.yml` bounds what the manifest may name. The Origin
columns below are the readable view; when the two disagree, the manifest wins.
List what it declares with:

```bash
python3 ../weisssrv-lib/scripts/check-vendored-copies.py --repo-root . --list
```

## Policy gates (run by `task lint` and the CI lint stage)

| Script | What it asserts | Origin |
|---|---|---|
| `check-backup-artifact-apps.py` | The backup-artifact app list and its alert arms stay paired | vendored |
| `check-ci-pin-parity.sh` | Every `include:` input repeats its `variables:` pin exactly — `include:` resolves before `variables:` exists, so the literals are copies that have to be kept equal | local |
| `check-collection-pin-trigger.py` | Every playbook-running `deploy-*` job also triggers on `ansible/requirements.yml`, the only in-repo signal that a role's content changed | local |
| `check-deploy-coverage.sh` | Every changed deploy input is covered by a `deploy-*` job | vendored |
| `check-deploy-host-coverage.py` | Each CI-deployed playbook's roles really reach every host it claims | local |
| `check-deploy-preflight.py` | Offline dry run of the Ansible deploy jobs: every playbook exists, every `--tags` selection reaches a real task and every `--limit` hits a play | vendored |
| `check-doc-links.py` | Every relative Markdown link in every tracked `*.md` resolves | vendored |
| `check-alertmanager-behaviour.py` | Each alert reaches the receiver it should, and every inhibit pair still binds | vendored |
| `check-ansible-service-names.py` | Every service/systemd task names a systemd unit, not an Ansible role FQCN | local |
| `check-cluster-invariants.py` | The inventory's addresses are internally consistent: no duplicate vmid or address, no host on a VIP or outside the LAN CIDR, and every alert-rule `instance="..."` is a host | vendored |
| `check-cluster-literals.py` | The substituted trees spell cluster identity as `cluster-config` placeholders, and that ConfigMap agrees with the Ansible inventory | local |
| `check-comment-length.py` | No comment block runs past three content lines, or eight when it opens `CRITICAL:`; `comment-length.yaml` carries the paths and the vendored-copy excludes | vendored |
| `check-dashboards.py` | Every Grafana dashboard JSON parses, carries no import placeholder, names a provisioned datasource uid and matches its configMapGenerator | vendored |
| `check-default-deny-coverage.py` | Every workload-owning namespace carries an ingress default-deny (reasoned exemptions in the script) | vendored |
| `check-dockerconfigjson.py` | Every hand-built `.dockerconfigjson` payload parses as a docker config, with its registry hosts resolved and each credential complete | local |
| `check-ephemeral-storage-cap.py` | Every container mounting a sized `emptyDir` declares an `ephemeral-storage` request and limit that covers the volumes it mounts | vendored |
| `check-helm-repo-parity.py` | Every chart-repo URL in `helm-values-releases.yaml` and the version registry equals the HelmRepository Flux pulls from | vendored |
| `check-hpa-vpa-invariant.py` | No workload has both an HPA and a CPU-controlling VPA | vendored |
| `check-flux-version-pin.py` | The CI `flux` CLI pin, `flux_version` in the versions ConfigMap and every `gotk-components.yaml` header name one version and the four stock controllers | forked |
| `check-image-gc-threshold.py` | `KubeletImageGCIneffective` fires at the kubelet `image-gc-high-threshold` from `group_vars/k3s.yml` | local |
| `check-integration-matrix-coverage.py` | Every integration-test dir has a CI matrix entry, and every matrix entry names a dir | local |
| `check-kubectl-version-pin.py` | The CI kubectl pin stays within ±1 minor of `k3s_version` | vendored |
| `check-kustomization-coverage.py` | Every manifest beside a kustomization is listed by it, every listed name is on disk, and no kustomization renders nothing | local |
| `check-lib-pins.py` | Every weisssrv-lib `include:` pins `WEISSSRV_LIB_REF`, and it is a release tag (`--fix` rewrites) | vendored |
| `check-molecule-image-pin.py` | Every `molecule-test:<tag>` fallback in the integration scenarios and `ansible/TESTING.md` equals `WEISSSRV_LIB_REF` (`--fix` rewrites) — CI overrides the image, so only local runs read the literal | vendored |
| `check-netpol-except-parity.py` | Every public-egress NetworkPolicy uses the canonical reserved-CIDR except-list | vendored |
| `check-nfs-tls.py` | Every NFS PersistentVolume mounts `xprtsec=tls`, by hostname (the cert has no IP SAN) | vendored |
| `check-pvc-storageclass.py` | Every claim pins a `storageClassName` | vendored |
| `check-role-default-flips.py` | No role default changes between the installed and the new `weisssrv.infra` tag without the inventory declaring it — the collection's variables are default-guarded, so a flip is adopted silently (`--from`/`--to`, run at a pin bump) | local |
| `check-role-inputs.py` | An opt-in collection role invoked with its flag set nowhere, and an asserted role input with no default and no assignment (`task lint:role-inputs` and the CI check-role-inputs job) | vendored |
| `check-runbook-anchors.py` | Every alert's `runbook_url` resolves to a real docs file and heading | vendored |
| `check-guest-endpoint-parity.py` | Every LAN address a hand-written EndpointSlice names is a real `ansible_host` in the prod inventory, or the LAN gateway | local |
| `check-secret-rotation-coverage.py` | Every `remoteRef.key` is named in docs/15, and every ExternalSecret is reached by a rotation path or declared manual | forked |
| `check-scrape-netpol.py` | Every scraped namespace admits Prometheus through its NetworkPolicies | vendored |
| `check-secretstore-scope.py` | Each ClusterSecretStore is namespace-scoped and covers its consumers | vendored |
| `check-tailnet-dns-parity.py` | The tailnet-dns override zone holds exactly the AdGuard rewrites that do not answer with the internal Traefik VIP | local |
| `check-tailscale-policy.py` | `policy.hujson` parses, every `tag:` it references has a `tagOwners` entry, and `autoApprovers.routes` matches the inventory's `tailscale_advertise_routes` | local |
| `check-taskfile.sh` | Taskfile references resolve (tasks, scripts, files) | vendored |
| `flux-corpus-gates.sh` | Runs every weisssrv-local gate over the full rendered Flux corpus, so `task flux:lint` and the CI flux-lint job cannot drift | local |
| `check-tenant-traefik-isolation.py` | No tenant is wired into `clusters/weisssrv/tenants/` while Traefik still sets `allowCrossNamespace: true` (docs/30 pre-onboarding checklist, as a gate) | local |
| `check-tenant-wiring.py` | Every tenant wiring file carries its `serviceAccountName`, `prune`, `targetNamespace`, `dependsOn`, namespace labels, the ResourceQuota and LimitRange caps, both RoleBindings, and its `kustomization.yaml` entry, and every namespaced document names the tenant namespace | local |
| `check-upstream-rule-mirror.py` | Every hand-copied upstream rule mirror names the chart version it was taken at, and that version matches the pin | local |
| `check-unifi-doc-parity.py` | docs/46 § Site settings documents the same values `terraform/unifi/main.tf` sets | local |
| `check-vpn-provider-parity.py` | The VPN provider → required-credential-key map is identical in `vpn-credcheck.sh`, the Gluetun sidecar, and the alias map in `downloads-vpn-provider.sh` | local |
| `check-skill-refs.py` | Every backticked repo path the `weisssrv-development` skill cites still exists — the skill carries no Markdown links, so `check-doc-links.py` sees nothing in it | local |
| `validate-helm-values.py` | The value-heavy Flux HelmReleases still `helm template` cleanly | vendored |
| `lint-prometheus-config.sh` | promtool/amtool over the alert rules and the rule unit tests | vendored |

## Live-cluster gates (need a kubeconfig)

| Script | What it asserts | Origin |
|---|---|---|
| `check-live-cpu-limits.py` | The live cluster imposes no CPU limits (docs/33) | vendored |
| `check-unmanaged-secrets.py` | Every live Secret is owned by ESO, Flux, Helm, or a controller | local |
| `b2-bucket-drift.py` | The Backblaze B2 bucket's settings match the declared ones (with a supervised apply) | vendored |
| `unifi-settings-drift.py` | The live UniFi console still holds the IPS posture terraform sets only at create time (docs/46) | vendored |

## Generators and sync

| Script | Output | Origin |
|---|---|---|
| `generate-versions-configmap.py` | `kubernetes/infrastructure/sources/versions-configmap.yaml` from `all.yml` (`task flux:sync-versions`) | vendored |
| `generate-hosts-env.py` | `scripts/hosts.env` from `hosts.yml` (`task hosts:sync`) | vendored |
| `generate-host-log-staleness.py` | `kubernetes/infrastructure/observability/loki/host-log-staleness.yaml` from the `alloy_host` play (`--check` fails on drift) | local |
| `cluster-config-value.sh` | One key out of `cluster-config.yaml` — how host-side tooling reads the VIPs, which `hosts.env` cannot carry (they are not inventory hosts) | vendored |
| `extract-prometheus-config.py` | Standalone rule + Alertmanager files for promtool/amtool — unions the HelmRelease with `observability/rules/` | vendored |
| `flux-render.sh` | The substitution exports + schema version for a Flux render (ONE ConfigMap) | vendored |
| `flux-env.sh` | The same entry point over BOTH substitution ConfigMaps — what `task flux:lint` and the CI flux-lint job call | vendored |
| `flux-child-kustomizations.py` | The cluster's child Kustomizations in `dependsOn` order (`--paths` adds each stage's `spec.path`, and fails on a stage that declares none unless `--allow-missing-paths`) | forked |
| `kubeconform-skipped.py` | The distinct kinds kubeconform could not schema-validate — informational, printed by flux-lint | vendored |

## Version tracking

| Script | Purpose | Origin |
|---|---|---|
| `check-versions.py` | Version discovery across GitHub / Docker Hub / GHCR / Helm / apt against the pins in `all.yml` | vendored |
| `check-version-checksums.py` | Every checksum-coupled pin still hashes to the artefact its registry entry names (downloads each one) | vendored |
| `version-check-ci.py` | CI wrapper: report artifact + MR comment | vendored |
| `version-bump-mr.py` | Keeps exactly one open bot MR in sync with the bumped pins | vendored |

## Deploy, verification, and maintenance

| Script | Purpose | Origin |
|---|---|---|
| `deploy-verify.sh` | Post-deployment cluster verification (the `deploy-verify` CI job) | local |
| `post-maintenance-verify.sh` | Health check run at the end of every maintenance op | local |
| `maintenance-all-ops.sh` | All maintenance ops in canonical order, aborting at the first failure | local |
| `maintenance-run-with-verify.sh` | Runs one maintenance command and always follows it with the verify | vendored |
| `maintenance-ha-restart.sh` | Restarts the HA-managed Home Assistant VM and waits for it | local |
| `maintenance-rearm-self-reboot.sh` | Re-arms a detached self-reboot from a job's `after_script` | local |
| `collect-state.sh` | Redacted full cluster snapshot to `CLUSTER_STATUS.txt` (`task collect-state`) | local |
| `verify-gitlab.sh` | GitLab smoke tests: web UI on both hosts, registry, pages, SSH, readiness (`task gitlab:verify`) | local |
| `verify-immich.sh` | Immich smoke tests: web UI, health, metrics, database, mounts (`task immich:verify`) | local |
| `verify-immich-ml.sh` | Immich ML LXC smoke tests: compose stack, `/dev/dri` passthrough, `/ping` (`task immich-ml:verify`) | local |
| `verify-nextcloud.sh` | Nextcloud smoke tests: web UI, `occ status`, exporter metrics (`task nextcloud:verify`) | local |
| `verify-windows.sh` | Windows VM smoke test: RDP reachability (`task windows:verify`) | local |
| `wait-for-reloader-roll.sh` | Waits for Reloader to roll a Deployment after its ConfigMap was patched | vendored |
| `flux-rotate-secret.sh` | Refreshes one app's ExternalSecret and restarts the pods that consume it (`task flux:rotate-secret`) | local |
| `flux-secret-consumers.py` | The workloads in one namespace that read a named Secret, from a `kubectl get deployment,statefulset,daemonset -o json` dump; `flux-rotate-secret.sh` uses it to name a consumer its arm does not restart | local |
| `molecule-retry.sh` | `molecule test` with in-job destroy + jittered retry | vendored |
| `sanitize-junit-expected-failures.py` | Downgrades declared negative-path junit failures | vendored |
| `ci-fetch-tools.py` | Installs the pinned CI tool binaries into `$CI_PROJECT_DIR/.bin`, each verified by sha256 | vendored |

## Operator helpers

| Script | Purpose | Origin |
|---|---|---|
| `authentik-add-user.py` | Scaffolds a managed user into `terraform/authentik/users.tf` — identity only, membership stays in `groups.tf` — and prints the supervised-apply + enrollment steps (`task authentik:add-user`) | local |
| `bootstrap-proxmox-host.sh` | Prepares a fresh Proxmox host for Ansible management | local |
| `diagnose-network-issues.sh` | Cross-host network diagnostics (`task diagnose:network`) | local |
| `downloads-vpn.sh` | Toggles a download client's Gluetun VPN live, GitOps-safely (`task downloads:vpn`) | local |
| `downloads-vpn-provider.sh` | Switches a download client's Gluetun VPN provider live (`task downloads:vpn-provider`) | local |
| `supervised-apply-guard.sh` | The confirmation ceremony the supervised terraform applies share | vendored |
| `find-pve-host-for-vm.sh` | Which Proxmox host currently runs a VM ID | vendored |
| `find-reachable-host.sh` | First reachable SSH target from a list | vendored |
| `vpn-credcheck.sh` | Which `vpn-credentials` keys the configured VPN provider is missing | local |
| `resolve-tool.sh` | Resolves how to invoke a Python-based dev tool (PATH, then pyenv) | vendored |

## Shared libraries (sourced, never executed)

`shell-lib.sh` (timeouts + SSH probes, vendored), `collect-state-lib.sh`
(redaction + the tri-state verdict), `deploy-verify-lib.sh` (result
classification), `maintenance-lib.sh` (maintenance-op helpers), `smoke-lib.sh`
(the probe/counter helpers the `verify-*.sh` scripts share). Function-only: no
top-level side effects, so they stay unit-testable.

`taskfile_tree.py` (local) is the Python equivalent: it flattens the
`Taskfile.yml` `includes:` tree into one `{task name: definition}` map, so the
suites that walk task names see the whole tree rather than the root file.

`inventory_tree.py` (vendored) loads `ansible/inventories/prod/hosts.yml` into
hosts, addresses and merged host vars, so the inventory-parity gates and
generators read inventory shape one way. `ci_playbook_invocations.py` (vendored)
pulls each deploy job's `ansible-playbook` calls out of a pipeline file, and
`ci_yaml.py` (vendored) is the loader that resolves GitLab's `!reference` tags
for it. Every one is imported, never executed.

`gate_common.py` (vendored) holds the corpus and live-input loaders every
manifest gate shares, so an empty or unparseable subject is one operator error
rather than a per-gate branch. It must sit beside any gate that imports it.

`script_loader.py` (vendored) imports a hyphenated gate by path. The suites call
`load_script("check-foo.py")` rather than each repeating the importlib dance;
it raises `ImportError` when the spec or its loader is missing.

## Site configuration

The vendored scripts take every site-specific value from a config file, so a
re-vendor never has to be re-edited. Each is covered by `test_site_configs.py`.

| File | Read by | Holds |
|---|---|---|
| `version-registry.py` | `check-versions.py` | Every tracked pin, its upstream, and how a bump rolls out |
| `deploy-coverage.conf` | `check-deploy-coverage.sh` | Directory layout + the intentionally-unmapped assets, each with a rationale |
| `autoscaling-policy.yaml` | `check-hpa-vpa-invariant.py`, `validate-helm-values.py` | Chart-native HPA targets + the CPU-limit allowlist |
| `helm-values-releases.yaml` | `validate-helm-values.py` | Which HelmReleases get `helm template`d |
| `hosts-env-map.yml` | `generate-hosts-env.py` | Inventory group → `hosts.env` variable |
| `b2-bucket.json` | `b2-bucket-drift.py` | Bucket identity + its declared settings |
| `unifi-settings.json` | `unifi-settings-drift.py` | The console-owned UniFi site settings and their expected values |
| `netpol-except.yaml` | `check-netpol-except-parity.py` | The reserved-CIDR except-list + the peer-less egress policies allowed to omit it |
| `alertmanager-behaviour.yaml` | `check-alertmanager-behaviour.py` | The alert→receiver route cases and the upstream alerts that must stay routable |
| `kubeconform-expected-skipped.txt` | `kubeconform-skipped.py` | The apiVersion/Kind pairs flux-lint accepts unvalidated |

## Vendored files outside `scripts/`

The registry covers more than this directory. The shared lint profiles live at
the repo root, discovered there by the tools' conventional names; the yamllint
profile sits under `lint/` instead, because a root `.yamllint` would replace
ansible-lint's own yaml rule. The molecule scaffolding the integration scenarios
include lives under `ansible/molecule/`. All of it is gated by the same test, so
a library bump that tightens a shared file cannot silently not apply here.

| File | Library path | Origin |
|---|---|---|
| `../ruff.toml` | `lint/ruff.toml` | vendored |
| `../lint/yamllint-relaxed.yml` | `lint/yamllint-relaxed.yml` | vendored |
| `../.gitleaks.toml` | `lint/gitleaks.toml` | forked (per-repo path exclusions + fixture anchors) |
| `../.gitlab/secret-detection-ruleset.toml` | `lint/secret-detection-ruleset.toml` | forked (names this repo's scanned paths) |
| `../.editorconfig` | `lint/editorconfig` | forked (per-repo file-type sections) |
| `../.pre-commit-config.yaml` | `lint/pre-commit-config.yaml` | forked (per-repo hook set) |
| `../.gitattributes` | `lint/gitattributes` | vendored |
| `../requirements.txt` | `docker/molecule-ci/requirements.txt` | forked (host toolchain on the ansible-core 2.18 line, plus pytest and ruff) |
| `../ansible/molecule/prepare-common.yml` | `ansible_collections/weisssrv/infra/molecule-shared/prepare-common.yml` | vendored |
| `../ansible/molecule/tasks/container-warmup.yml` | `ansible_collections/weisssrv/infra/molecule-shared/tasks/container-warmup.yml` | vendored |
| `../ansible/molecule/tasks/prepare-apt-disable.yml` | `ansible_collections/weisssrv/infra/molecule-shared/tasks/prepare-apt-disable.yml` | vendored |
| `../ansible/molecule/tasks/prepare-base.yml` | `ansible_collections/weisssrv/infra/molecule-shared/tasks/prepare-base.yml` | vendored |

## Tests and data

`test_*.py` are pytest unit tests, run by `task scripts:test` and the CI
`python-tests` job. The exhaustive suites for the vendored scripts live in
weisssrv-lib next to the code. What runs here:

| Test | What it asserts |
|---|---|
| `test_vendored_smoke.py` | The vendored copies are runnable |
| `test_vendored_byte_identity.py` | The vendored copies are unmodified. Drives the library's `check-vendored-copies.py` against a weisssrv-lib checkout at the pinned ref, and never skips when that checkout is missing |
| `test_scripts_have_tests.py` | Every local script is exercised by some suite |
| `test_site_configs.py` | The site configuration files above parse and match their consumers |
| `test_prometheus_rule_coverage.py` | Every alert has a promtool rule unit test in `prometheus-rule-tests/`, or a declared exemption |

`prometheus-rule-tests/` holds those unit tests; its
[README](prometheus-rule-tests/README.md) carries the harness contract.
`hosts.env` is generated, not edited (`task hosts:sync`).

## Related documentation

- [../docs/13-ci-cd.md](../docs/13-ci-cd.md) — which job runs which script, and
  what comes from the shared CI library
- [../CLAUDE.md](../CLAUDE.md) § Repo family — the pin-and-vendor contract
