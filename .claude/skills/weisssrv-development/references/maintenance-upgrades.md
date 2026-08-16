# Maintenance & upgrades

Canonical workflow: `docs/12-runbooks.md` (update workflow). Roadmap /
outstanding decisions: `docs/16-next-steps.md`. All version pins are
single-sourced in `ansible/inventories/prod/group_vars/all.yml` (Home Assistant
OS is the one exception — updated manually via its UI).

## Version-bump flow

1. **Discover** — `task maintenance:check-versions` (or `:check-versions-json`,
   `-- --service <x>`, `-- --category helm`). The task already injects
   `GITHUB_TOKEN` via `op run`; you only need to set it by hand if you invoke
   `scripts/check-versions.py` directly. When deciding what is current, trust the
   **pipeline's**
   `version-check` job output, not a local run — the local cache can be stale
   (this is why bumps ride with an MR revision instead of getting their own).
2. **Edit** — `task maintenance:update-version SERVICE=<x>` or
   `maintenance:update-all-versions` (edits `all.yml`, no deploy). Review
   `git diff` on `all.yml`.
3. **Sync** — `task flux:sync-versions`, then commit **both** `all.yml` and
   `kubernetes/infrastructure/sources/versions-configmap.yaml`. Never hand-edit
   the ConfigMap.
4. **Deploy** — k8s pins reconcile via Flux on merge (~1 min). Base-infra binary
   pins deploy via `task maintenance:update-full`; k3s node binaries via
   `task maintenance:update-k3s-nodes` (rolling cordon → upgrade → uncordon;
   kernel reboots go through kured).
5. **Verify** — `task flux:status`, `task infra:verify`, `task k3s:status`.

Fold every available bump into the current MR revision; do not open a revision
just to bump versions.

**The bot does steps 1-3 weekly on its own.** `version-bump-bot` (scheduled CI
job, `SCHEDULE_TYPE=version-bump`) runs the very same
`task maintenance:update-all-versions` and keeps ONE open MR
(`bot/version-bumps`) in sync with the result: it refreshes that MR rather than
opening a second, closes it when nothing is outdated any more, and **never
merges** — review and merge it like any other MR. Setup, the three outcomes, and
how to trigger a run by hand: `docs/13-ci-cd.md` § "Version bump bot". Its MR
manager, `scripts/version-bump-mr.py`, is vendored byte-identical from
`weisssrv-lib` — fix it upstream, never here. The bot does not change the manual
flow above: if you are already revising an MR, fold the bumps in there and let
the bot close its own MR on the next run.

## Bumping weisssrv-lib (CI templates + the weisssrv.infra collection)

The library pin is not a version pin in `all.yml` — it lives in two places and
both move together, in one MR:

1. `variables.WEISSSRV_LIB_REF` in `.gitlab-ci.yml`, then
   `scripts/check-lib-pins.py --fix` to rewrite every literal `ref:` on the
   include entries (the gate fails on drift or on a branch ref).
2. The collection `version:` in `ansible/requirements.yml`, then
   `ansible-galaxy install -r ansible/requirements.yml --force`.

Two more literals ride the same tag and are NOT in those two files: the
`molecule-test:<tag>` fallbacks in the integration scenarios and
`ansible/TESTING.md` (`scripts/check-molecule-image-pin.py --fix` rewrites them;
CI overrides the image, so a stale one only breaks a local `molecule test`), and
the three Terraform `?ref=` module pins, which are still bumped by hand. Both are
gated by `scripts/test_site_configs.py`.

While the new tag does not exist yet, both the pinned galaxy install and
`task ansible:lint` fail on it. Lint against the library checkout instead:

```bash
WEISSSRV_COLLECTION_PATH=../weisssrv-lib task ansible:lint
```

It installs the checkout's `weisssrv.infra` (plus the pin file's galaxy deps,
minus the not-yet-tagged git entry) into `.ansible-home/collections` and lints
`ansible/` exactly as CI does. Unset it once the tag is cut; the cached copy in
`.ansible-home/` is refreshed by deleting that directory.

Then: re-vendor every file the library registers for this consumer. The
registry is `weisssrv-lib/scripts/vendored-paths.yml` and it covers more than
`scripts/` (the shared lint profiles at the repo root are in it too) — never
work from a remembered list:

```bash
python3 ../weisssrv-lib/scripts/check-vendored-copies.py \
  --consumer weisssrv --repo-root . --list        # what to copy
python3 ../weisssrv-lib/scripts/check-vendored-copies.py \
  --consumer weisssrv --repo-root . --ref <new-tag>   # what still drifts
```

`scripts/README.md`'s Origin column is the human-readable view of the same set;
declared forks are listed there too, and a fork must ABSORB the library's change
rather than ignore it (the gate compares the library side against the fork's
recorded `reconciled_sha256`). Then work the collection's
`MIGRATING.md` for any variable renamed or emptied in that release and land the
inventory edits in the same MR, and run the full gate set. Read the library's
`docs/VERSIONING.md` before assuming a bump is behaviour-neutral — a changed
input default silently changes this pipeline.

## Node / cluster upgrades & reboots

- `task maintenance:update-packages` / `:update-applications` / `:update-full` /
  `:update-full-auto` — base-infra package + app updates.
- `task maintenance:update-k3s-nodes` — rolling k3s binary upgrade.
- `task maintenance:update-cluster` — the 4-phase cluster update.
- Reboots are coordinated by **kured**; the maintenance path gates on a
  reboot-safety check (`docs/12-runbooks.md`, `docs/32-zfs-encryption.md` for NAS unlock).

## Known holds & traps

- **MetalLB is held below the 0.16.x line** — 0.16.x has an apiserver-flood
  regression (upstream `metallb#3063`); unhold only once the fix (`metallb#3079`)
  merges and ships. The exact held pin lives in `all.yml`; check both upstream
  issues before bumping.
- **Helm CRD lifecycle** — a chart with `crds.enabled: false` can DELETE existing
  CRDs across an up/downgrade (this took out MetalLB VIPs once). Verify a chart's
  CRD handling before changing its version or that flag.
- **Reloader** watches ConfigMaps only (`ignoreSecrets: true`); a credential
  Secret rotation is a manual pod restart (`task flux:rotate-secret -- <app>`).
- **A `*_git_sha` pin must be the peeled commit.** For pins that guard a build
  against an upstream tag (Hermes, camofox), an annotated tag's own object SHA is
  not the commit the build checks out — resolve it with
  `git ls-remote <repo> 'refs/tags/<tag>*'` and take the `^{}` line, or the build
  job's SHA guard fails.

## Doc updates that ride with a change

`docs/16-next-steps.md` (mark done / remove from planned), the relevant
`docs/NN-*.md`, `README.md` tables where applicable, `CLAUDE.md` only if a
top-level fact changed, and — whenever the change alters a **workflow, gate,
invariant, or canonical-doc pointer** — `.claude/skills/weisssrv-development/`
(`SKILL.md` and the matching `references/` file) in the SAME MR. Two thin gates
now cover the skill — `docs-link-check` resolves its relative `.md` links and
`scripts/test_doc_inventories.py` asserts every `` `task <ns>:<name>` `` it names
still exists — but nothing validates the *prose*, so a stale procedure is doc
rot CI cannot catch.
