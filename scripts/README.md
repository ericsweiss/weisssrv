# scripts/

Every gate, generator, and operational helper the Taskfile and CI invoke. Nothing
here is run by hand as part of the normal flow — `task --list` is the entry
point, and CI calls the same commands.

**Origin** tells you where a script is maintained:

| Origin | Meaning |
|---|---|
| local | Maintained here; the library ships nothing equivalent. |
| vendored | A byte-identical copy of a weisssrv-lib file. Fix it upstream, tag, re-vendor — a local edit is reverted by the next re-vendor, and site data belongs in the script's config file. |
| forked | A vendored file this cluster had to diverge from, declared with its reason in the registry. Re-converge by getting the difference upstream; a library change to a forked file must be ABSORBED, not ignored. |

The **inventory of record is the library's registry**,
`weisssrv-lib/scripts/vendored-paths.yml`, enforced here by
`test_vendored_byte_identity.py` (which drives the library's
`scripts/check-vendored-copies.py`). The Origin columns below are the readable
view of it; when the two disagree, the registry wins. List what is registered
with:

```bash
python3 ../weisssrv-lib/scripts/check-vendored-copies.py --consumer weisssrv --list
```

## Policy gates (run by `task lint` and the CI lint stage)

| Script | What it asserts | Origin |
|---|---|---|
| `check-backup-artifact-apps.py` | The backup-artifact app list and its alert arms stay paired | local |
| `check-ci-pin-parity.sh` | Every `include:` input repeats its `variables:` pin exactly — `include:` resolves before `variables:` exists, so the literals are copies that have to be kept equal | local |
| `check-collection-pin-trigger.py` | Every playbook-running `deploy-*` job also triggers on `ansible/requirements.yml`, the only in-repo signal that a role's content changed | local |
| `check-deploy-coverage.sh` | Every changed deploy input is covered by a `deploy-*` job | vendored |
| `check-deploy-host-coverage.py` | Each CI-deployed playbook's roles really reach every host it claims | local |
| `check-doc-links.py` | Every relative Markdown link in every tracked `*.md` resolves | vendored |
| `check-alertmanager-behaviour.py` | Each alert reaches the receiver it should, and every inhibit pair still binds | local |
| `check-cluster-literals.py` | The substituted trees spell cluster identity as `cluster-config` placeholders, and that ConfigMap agrees with the Ansible inventory | local |
| `check-default-deny-coverage.py` | Every workload-owning namespace carries an ingress default-deny (reasoned exemptions in the script) | local |
| `check-hpa-vpa-invariant.py` | No workload has both an HPA and a CPU-controlling VPA | vendored |
| `check-integration-matrix-coverage.sh` | Every integration-test dir has a CI matrix entry | local |
| `check-kubectl-version-pin.py` | The CI kubectl pin stays within ±1 minor of `k3s_version` | vendored |
| `check-lib-pins.py` | Every weisssrv-lib `include:` pins `WEISSSRV_LIB_REF`, and it is a release tag (`--fix` rewrites) | vendored |
| `check-netpol-except-parity.py` | Every public-egress NetworkPolicy uses the canonical reserved-CIDR except-list | local |
| `check-pvc-storageclass.py` | Every claim pins a `storageClassName` | local |
| `check-scrape-netpol.py` | Every scraped namespace admits Prometheus through its NetworkPolicies | local |
| `check-secretstore-scope.py` | Each ClusterSecretStore is namespace-scoped and covers its consumers | local |
| `check-taskfile.sh` | Taskfile references resolve (tasks, scripts, files) | vendored |
| `kubeconform-skipped.py` | The kubeconform skip-list stays justified | vendored |
| `validate-helm-values.py` | The value-heavy Flux HelmReleases still `helm template` cleanly | vendored |
| `lint-prometheus-config.sh` | promtool/amtool over the alert rules and the rule unit tests | vendored |

## Live-cluster gates (need a kubeconfig)

| Script | What it asserts | Origin |
|---|---|---|
| `check-live-cpu-limits.py` | The live cluster imposes no CPU limits (docs/33) | local |
| `check-unmanaged-secrets.py` | Every live Secret is owned by ESO, Flux, Helm, or a controller | local |
| `b2-bucket-drift.py` | The Backblaze B2 bucket's settings match the declared ones (with a supervised apply) | vendored |

## Generators and sync

| Script | Output | Origin |
|---|---|---|
| `generate-versions-configmap.py` | `kubernetes/infrastructure/sources/versions-configmap.yaml` from `all.yml` (`task flux:sync-versions`) | vendored |
| `generate-hosts-env.py` | `scripts/hosts.env` from `hosts.yml` (`task hosts:sync`) | vendored |
| `extract-prometheus-config.py` | Standalone rule + Alertmanager files for promtool/amtool — unions the HelmRelease with `observability/rules/` | forked |
| `flux-render.sh` | The substitution exports + schema version for a Flux render (ONE ConfigMap) | vendored |
| `flux-env.sh` | The same entry point over BOTH substitution ConfigMaps — what `task flux:lint` and the CI flux-lint job call | vendored |
| `flux-child-kustomizations.py` | The cluster's child Kustomizations in `dependsOn` order | local |

## Version tracking

| Script | Purpose | Origin |
|---|---|---|
| `check-versions.py` | Version discovery across GitHub / Docker Hub / GHCR / Helm / apt against the pins in `all.yml` | vendored |
| `version-check-ci.py` | CI wrapper: report artifact + MR comment | vendored |
| `version-bump-mr.py` | Keeps exactly one open bot MR in sync with the bumped pins | vendored |

## Deploy, verification, and maintenance

| Script | Purpose | Origin |
|---|---|---|
| `deploy-verify.sh` | Post-deployment cluster verification (the `deploy-verify` CI job) | local |
| `post-maintenance-verify.sh` | Health check run at the end of every maintenance op | local |
| `maintenance-all-ops.sh` | All maintenance ops in canonical order, aborting at the first failure | local |
| `maintenance-run-with-verify.sh` | Runs one maintenance command and always follows it with the verify | local |
| `maintenance-ha-restart.sh` | Restarts the HA-managed Home Assistant VM and waits for it | local |
| `maintenance-rearm-self-reboot.sh` | Re-arms a detached self-reboot from a job's `after_script` | local |
| `collect-state.sh` | Redacted full cluster snapshot to `CLUSTER_STATUS.txt` (`task collect-state`) | local |
| `molecule-retry.sh` | `molecule test` with in-job destroy + jittered retry | vendored |
| `sanitize-junit-expected-failures.py` | Downgrades declared negative-path junit failures | vendored |

## Operator helpers

| Script | Purpose | Origin |
|---|---|---|
| `bootstrap-proxmox-host.sh` | Prepares a fresh Proxmox host for Ansible management | local |
| `diagnose-network-issues.sh` | Cross-host network diagnostics (`task diagnose:network`) | local |
| `find-pve-host-for-vm.sh` | Which Proxmox host currently runs a VM ID | vendored |
| `find-reachable-host.sh` | First reachable SSH target from a list | vendored |
| `vpn-credcheck.sh` | Which `vpn-credentials` keys the configured VPN provider is missing | local |
| `resolve-tool.sh` | Resolves how to invoke a Python-based dev tool (PATH, then pyenv) | vendored |

## Shared libraries (sourced, never executed)

`shell-lib.sh` (timeouts + SSH probes, vendored), `collect-state-lib.sh`
(redaction + the tri-state verdict), `deploy-verify-lib.sh` (result
classification), `maintenance-lib.sh` (maintenance-op helpers). Function-only:
no top-level side effects, so they stay unit-testable.

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
| `netpol-except.yaml` | `check-netpol-except-parity.py` | The reserved-CIDR except-list + the peer-less egress policies allowed to omit it |
| `alertmanager-behaviour.yaml` | `check-alertmanager-behaviour.py` | The alert→receiver route cases and the upstream alerts that must stay routable |

## Vendored files outside `scripts/`

The registry covers more than this directory — the shared lint profiles live at
the repo root, discovered there by the tools' conventional names. They are gated
by the same test, so a library bump that tightens a shared profile cannot
silently not apply here.

| File | Library path | Origin |
|---|---|---|
| `../ruff.toml` | `lint/ruff.toml` | vendored |
| `../.gitleaks.toml` | `lint/gitleaks.toml` | forked (per-repo path exclusions + fixture anchors) |
| `../.gitlab/secret-detection-ruleset.toml` | `lint/secret-detection-ruleset.toml` | forked (names this repo's scanned paths) |
| `../.editorconfig` | `lint/editorconfig` | forked (per-repo file-type sections) |
| `../.pre-commit-config.yaml` | `lint/pre-commit-config.yaml` | forked (per-repo hook set) |

## Tests and data

`test_*.py` are pytest unit tests — `task scripts:test`, also the CI
`python-tests` job. The exhaustive suites for the vendored scripts live in
weisssrv-lib next to the code; what runs here is `test_vendored_smoke.py` (the
copies are runnable), `test_vendored_byte_identity.py` (they are unmodified —
it drives the library's `check-vendored-copies.py` against a weisssrv-lib
checkout at the pinned ref, and never skips when that checkout is missing),
`test_scripts_have_tests.py` (every local script is exercised by some suite) and
`test_site_configs.py` (the config files above). `prometheus-rule-tests/` holds
the promtool rule unit tests consumed by `lint-prometheus-config.sh`, with
`test_prometheus_rule_coverage.py` asserting every alert has one or a declared
exemption. `hosts.env` is generated, not edited (`task hosts:sync`).

## Related documentation

- [../docs/13-ci-cd.md](../docs/13-ci-cd.md) — which job runs which script, and
  what comes from the shared CI library
- [../CLAUDE.md](../CLAUDE.md) § Repo family — the pin-and-vendor contract
