#!/usr/bin/env bash
# Verify every changed Ansible role under ansible/roles/, every changed
# playbook under ansible/playbooks/, and every changed inventory file
# under ansible/inventories/prod/ matches at least one deploy-* job's
# `changes:` list in .gitlab-ci.yml. Fires in MR CI; a failure means
# an Ansible asset was modified but no deploy job will pick the change
# up — a silent no-op deploy unless the operator either (a) adds the
# path to the relevant deploy-* rule, or (b) acknowledges the asset is
# intentionally outside CI deploy by adding it to the appropriate
# INTENTIONALLY_UNMAPPED_* list below.
#
# The unmapped gate exists because deploy-trigger mappings in
# .gitlab-ci.yml can lag the change surface: a role can be refactored,
# a playbook renamed, or an inventory path added without anyone
# updating the deploy-* job's `changes:` list, and the rollout would
# then silently no-op. This script forces an explicit acknowledgment
# in the same MR — either wire the asset into a deploy job, or list it
# in the relevant INTENTIONALLY_UNMAPPED_* array if it ships via a
# manual task wrapper.
#
# Implementation note: deploy-path mappings are extracted by parsing
# .gitlab-ci.yml as YAML (python3 + PyYAML) and walking only jobs whose
# name starts with "deploy-" AND whose `stage:` is "deploy". This scope
# is deliberate: a global `grep -oE 'ansible/roles/foo/**' .gitlab-ci.yml`
# would match the path inside a `lint`/`test`/`yaml-lint` job's rules
# and report "mapped" even though no deploy job will fire — false
# confidence. The deploy-stage filter excludes the deploy-coverage-check
# job itself (stage: lint) and any other lint/test job whose name
# happens to start with "deploy-".
#
# The walker assumes each deploy-* job declares `stage: deploy` LITERALLY
# on the job (every current one does); a stage inherited only via
# `extends:` is not resolved and would silently drop that job's paths
# from coverage credit (a loud false failure on the next matching edit,
# not a silent pass). `changes:` is accepted in both GitLab forms — the
# plain list and the `changes: {paths: [...]}` mapping.
#
# ## Policy: the intentionally-unmapped lists
#
# Some paths are deployed by human-in-the-loop `task` wrappers (k3s,
# proxmox_ha, zfs_encryption, proxmox_vm/lxc) or affect every deploy globally
# (hosts.yml — group memberships drive role targeting). Mapping those to one
# deploy-* job mis-fans-out; fanning them into every job produces redeploys the
# operator should review host-by-host instead.
#
# What that gives up: a change to a listed path does NOT fail this gate, so the
# operator must re-run the right wrapper (`task plex:deploy`, `task k3s:deploy`,
# …) by hand. CI will not catch it.
#
# Rule for additions: every entry below carries a TRAILING rationale comment
# naming what deploys it instead. No rationale => wire the path into a deploy-*
# job's `changes:` list instead of listing it here.

set -euo pipefail

# Roles not mapped to a CI deploy job (deployment needs human-in-the-loop work).
INTENTIONALLY_UNMAPPED_ROLES=(
    k3s             # node lifecycle (rolling cordon/upgrade, kured reboots): task k3s:deploy / maintenance:update-k3s-nodes
    proxmox_vm      # VM provisioning: task k3s:provision-vms and friends
    proxmox_lxc     # LXC provisioning: same reasoning as proxmox_vm
    proxmox_ha      # HA rules / replication, sensitive: task proxmox:ha
    zfs_encryption  # ZFS passphrase activation, cold-boot sensitive: task zfs:encrypt
)

# Playbooks not mapped to a CI deploy job, by path relative to ansible/playbooks/.
INTENTIONALLY_UNMAPPED_PLAYBOOKS=(
    site.yml                        # broad fan-out; each deploy-* job lists its own role/inventory triggers
    k3s.yml                         # node lifecycle: task k3s:deploy, never CI-driven
    k3s-provision-vms.yml           # VM provisioning: task k3s:provision-vms
    windows.yml                     # Windows VM shell + guest firewall: task windows:provision, then an interactive install
    zfs-encryption.yml              # cold-boot passphrase activation: task zfs:encrypt
    proxmox-ha.yml                  # HA rules / replication: task proxmox:ha
    proxmox-enable-autostart.yml    # one-shot after cluster expansion, manual
    postflight.yml                  # operator-run post-deploy verification helper
    rotate-mail-credential.yml      # credential rotation: task mail:rotate-credential, never CI-driven (docs/15)
    show-cert-host-keys.yml         # operator helper for host_vars/dns-01.yml host_key fields
    bootstrap/storage-bootstrap.yml # one-shot ZFS pool bootstrap; pool creation is never automated
    maintenance/_ensure-nfs-server-healthy.yml  # helper included by the maintenance-* CI jobs
    maintenance/_reboot-if-needed.yml           # helper included by the maintenance-* CI jobs
    maintenance/_uncordon-and-wait-ready.yml    # helper included by the maintenance-* CI jobs
    maintenance/_wait-no-kured-server-reboot.yml # helper included by the maintenance-* CI jobs
    tasks/_check-mode-reachable.yml # shared guard imported by app playbooks; ships with whichever deploy job runs them
    _reachability-probe.yml         # import_playbook'd by site.yml + base.yml; never run alone
    _reachability-gate.yml          # import_playbook'd by site.yml + base.yml; never run alone
    maintenance/update-applications.yml # run by the manual maintenance-* jobs, which carry their own rules
    maintenance/update-full.yml         # run by the manual maintenance-* jobs
    maintenance/update-helm-charts.yml  # run by the manual maintenance-* jobs
    maintenance/update-k3s-nodes.yml    # run by the manual maintenance-* jobs
    maintenance/update-packages.yml     # run by the manual maintenance-* jobs
)

# Inventory paths not mapped to a CI deploy job, by path relative to
# ansible/inventories/prod/.
#
# hosts.yml EXCEPTION, do not undo: per-guest firewall assignments
# (guest_security_groups / firewall_ipsets) live ONLY in hosts.yml, so
# deploy-ansible-firewall explicitly watches ansible/inventories/prod/hosts.yml
# in its rules.changes. That path is the only trigger for an inventory-only
# firewall edit — removing it silently drops firewall deploys.
INTENTIONALLY_UNMAPPED_INVENTORY_PATHS=(
    hosts.yml                # affects every deploy (group membership drives role targeting); operator picks which deploy-* jobs to re-run
    group_vars/k3s.yml       # k3s cluster vars: task k3s:deploy
    host_vars/plex.yml       # consumed by plex.yml, which deploy-plex already watches
    host_vars/smtp-relay.yml # mail role + playbook changes are the deploy-ansible-mail gate
)

# Resolve the diff base in priority order:
#   1. MR pipeline: GitLab provides CI_MERGE_REQUEST_DIFF_BASE_SHA (the
#      target branch tip at MR open).
#   2. Local invocation: positional $1 wins.
#   3. Branch pipeline: CI_COMMIT_BEFORE_SHA = parent of the pushed
#      commit. GitLab sets this to all-zeros on the first push to a
#      brand-new branch — treat that as "fall through" since diff
#      against a null SHA is meaningless.
#   4. Last resort: origin/main. This works locally and in any CI
#      checkout where `origin` is the git remote.
BASE_REF="${CI_MERGE_REQUEST_DIFF_BASE_SHA:-}"
[ -z "$BASE_REF" ] && BASE_REF="${1:-}"
if [ -z "$BASE_REF" ]; then
    if [ -n "${CI_COMMIT_BEFORE_SHA:-}" ] && [ "$CI_COMMIT_BEFORE_SHA" != "0000000000000000000000000000000000000000" ]; then
        BASE_REF="$CI_COMMIT_BEFORE_SHA"
    else
        BASE_REF="origin/main"
    fi
fi

# Hard-fail on a bad ref instead of silently treating "no diff" as
# "everything covered". An invalid BASE_REF used to swallow into
# `git diff … 2>/dev/null` returning empty, which made the script
# exit 0 with "skipped" and gave CI/cron a false-clear signal.
if ! git rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
    {
        echo "ERROR: BASE_REF '$BASE_REF' is not a valid git ref or commit."
        echo "       Set CI_MERGE_REQUEST_DIFF_BASE_SHA, pass a valid ref as \$1,"
        echo "       or ensure 'origin/main' is fetched in this checkout."
    } >&2
    exit 2
fi

# Reject unrelated histories. `git diff "$BASE_REF"...HEAD` returns an
# empty diff if BASE_REF and HEAD share no common ancestor (e.g. a
# shallow clone that doesn't reach the MR base, or BASE_REF pointing at
# a sibling repo's tip). Without this guard the script reports "no
# changes — skipped", giving CI a false-clear signal on every commit.
if ! git merge-base --is-ancestor "$BASE_REF" HEAD 2>/dev/null \
   && ! git merge-base "$BASE_REF" HEAD >/dev/null 2>&1; then
    {
        echo "ERROR: BASE_REF '$BASE_REF' shares no common ancestor with HEAD."
        echo "       This is usually a shallow-clone problem in CI (the MR base"
        echo "       commit isn't in the local history). Fetch deeper or unshallow."
    } >&2
    exit 2
fi

# Cache the diff once. The `|| true` at the end of each pipe handles the
# legitimate "nothing changed in this category" case (grep exits 1 on
# no matches, which under `set -e + pipefail` would otherwise abort).
# Real git diff failures already short-circuited at the rev-parse check.
#
# --diff-filter=d EXCLUDES deletions: a removed role/playbook/inventory file
# has no deploy-coverage obligation (there's nothing left to roll out), so it
# must not be flagged as "changed but unmapped" — that would force the operator
# to re-add a just-deleted asset to an INTENTIONALLY_UNMAPPED_* list. Renames
# still surface via their added (non-deleted) path.
DIFF_FILES=$(git diff --name-only --diff-filter=d "$BASE_REF"...HEAD)

# Extract changed roles (path component after ansible/roles/).
CHANGED_ROLES=$(
    printf '%s\n' "$DIFF_FILES" \
        | grep -oE '^ansible/roles/[A-Za-z0-9_-]+' \
        | sed 's|^ansible/roles/||' \
        | sort -u \
        || true
)

# Extract changed playbooks. Match anything ending in .yml (or .yaml)
# under ansible/playbooks/ at any depth. Identifier is the path
# relative to ansible/playbooks/.
CHANGED_PLAYBOOKS=$(
    printf '%s\n' "$DIFF_FILES" \
        | grep -E '^ansible/playbooks/.+\.ya?ml$' \
        | sed 's|^ansible/playbooks/||' \
        | sort -u \
        || true
)

# Extract changed inventory paths under ansible/inventories/prod/ at
# any depth. Covers group_vars/, host_vars/, the top-level hosts.yml,
# and any other top-level *.yml/*.yaml that may be added (inventory
# plugin configs, group/host membership files, etc). Identifier is
# the path relative to ansible/inventories/prod/.
CHANGED_INVENTORY_PATHS=$(
    printf '%s\n' "$DIFF_FILES" \
        | grep -E '^ansible/inventories/prod/.+\.ya?ml$' \
        | sed 's|^ansible/inventories/prod/||' \
        | sort -u \
        || true
)

if [ -z "$CHANGED_ROLES" ] && [ -z "$CHANGED_PLAYBOOKS" ] && [ -z "$CHANGED_INVENTORY_PATHS" ]; then
    echo "No Ansible role/playbook/inventory changes in this diff; deploy coverage check skipped."
    exit 0
fi

# Extract every path string under `rules: -> changes:` from every
# .gitlab-ci.yml job whose name starts with "deploy-" AND whose
# `stage:` is "deploy". This is the deploy-coverage gate's source of
# truth for "what paths trigger a CI deploy?".
#
# Why YAML-parse rather than `grep -oE`?
#   A raw `grep -oE 'ansible/roles/foo/**' .gitlab-ci.yml` produces
#   false confidence: if a `lint`, `test`, or `yaml-lint` job mentions
#   `ansible/roles/foo/**` in its rules, the gate would treat foo as
#   "mapped" even though no deploy job triggers on it. Walking the
#   YAML structure and filtering by job name + stage scopes the
#   mapping to deploy jobs only.
#
# `!reference` and other custom YAML tags appear in rules: lists
# (e.g. `- !reference [deploy-gitlab, rules]`). PyYAML's safe_loader
# would refuse those by default, so we register a multi-constructor
# that returns a sentinel value for any tag — the rule entry becomes
# a non-dict, our walker skips it, and we still collect the original
# job's `changes:` list independently from its own block. (deploy-verify
# and deploy-gitlab-verify use !reference to inherit from other
# deploy-* jobs; their referenced jobs are parsed directly.)
DEPLOY_PATHS=$(
    python3 - .gitlab-ci.yml <<'PYEOF'
import sys
import yaml


def _tag_passthrough(loader, tag_suffix, node):
    # Custom tags (e.g. !reference [job, key]) cannot be safely
    # represented as Python objects without a tag-specific schema;
    # for the deploy-coverage gate we don't care about their value,
    # only that they don't crash the parse. Returning None turns the
    # entry into a non-dict that the walker below cleanly skips.
    return None


# Register handler for any !-tagged scalar/sequence/mapping. Empty
# suffix on add_multi_constructor catches every '!<anything>' tag.
yaml.SafeLoader.add_multi_constructor("!", _tag_passthrough)

with open(sys.argv[1]) as f:
    ci = yaml.safe_load(f)

paths = set()
for job_name, job in ci.items():
    if not isinstance(job, dict):
        continue
    if not job_name.startswith("deploy-"):
        continue
    if job.get("stage") != "deploy":
        # Excludes deploy-coverage-check (stage: lint) and any
        # future lint/test job whose name starts with "deploy-".
        continue
    rules = job.get("rules", [])
    if not isinstance(rules, list):
        continue
    for rule in rules:
        if not isinstance(rule, dict):
            # !reference entries land here (None) — skip cleanly.
            continue
        changes = rule.get("changes", [])
        if isinstance(changes, dict):
            # GitLab also accepts `changes: {paths: [...], compare_to: ...}`.
            changes = changes.get("paths", [])
        if not isinstance(changes, list):
            continue
        for change in changes:
            if isinstance(change, str):
                paths.add(change)

for p in sorted(paths):
    print(p)
PYEOF
)

# Mapped roles: any 'ansible/roles/<name>' prefix inside a deploy-*
# job's changes: list. Captures both `ansible/roles/<name>/**` and
# any future literal-file forms.
MAPPED_ROLES=$(
    printf '%s\n' "$DEPLOY_PATHS" \
        | grep -oE '^ansible/roles/[A-Za-z0-9_-]+' \
        | sed 's|^ansible/roles/||' \
        | sort -u \
        || true
)

# Mapped playbooks: every 'ansible/playbooks/<path>.yml' that appears
# verbatim in a deploy-* job's changes: list. We do NOT match
# wildcards like `ansible/playbooks/**` here — wildcard catches are
# intentionally not given coverage credit so a single ** doesn't
# silently mask a missing trigger for a newly added playbook.
MAPPED_PLAYBOOKS=$(
    printf '%s\n' "$DEPLOY_PATHS" \
        | grep -oE '^ansible/playbooks/[A-Za-z0-9_./-]+\.ya?ml$' \
        | sed 's|^ansible/playbooks/||' \
        | sort -u \
        || true
)

# Mapped inventory paths: explicit 'ansible/inventories/prod/<path>.yml'
# entries in a deploy-* job's changes: list. Same wildcard caveat as
# playbooks; a `group_vars/**` glob does NOT confer coverage on every
# group_var file.
MAPPED_INVENTORY_PATHS=$(
    printf '%s\n' "$DEPLOY_PATHS" \
        | grep -oE '^ansible/inventories/prod/[A-Za-z0-9_./-]+\.ya?ml$' \
        | sed 's|^ansible/inventories/prod/||' \
        | sort -u \
        || true
)

# Use `grep -Fxq` (fixed-string, whole-line) for membership checks: the
# path/role identifiers contain `.`, `/`, and other regex metachars, so
# a `grep -qx` would silently treat them as regexes and risk false
# matches on e.g. "k3s-srv" vs "k3s.srv".
UNMAPPED_ROLES=()
for role in $CHANGED_ROLES; do
    if printf '%s\n' "${INTENTIONALLY_UNMAPPED_ROLES[@]}" | grep -Fxq "$role"; then
        continue
    fi
    if ! printf '%s\n' "$MAPPED_ROLES" | grep -Fxq "$role"; then
        UNMAPPED_ROLES+=("$role")
    fi
done

UNMAPPED_PLAYBOOKS=()
for pb in $CHANGED_PLAYBOOKS; do
    if printf '%s\n' "${INTENTIONALLY_UNMAPPED_PLAYBOOKS[@]}" | grep -Fxq "$pb"; then
        continue
    fi
    if ! printf '%s\n' "$MAPPED_PLAYBOOKS" | grep -Fxq "$pb"; then
        UNMAPPED_PLAYBOOKS+=("$pb")
    fi
done

UNMAPPED_INVENTORY_PATHS=()
for inv in $CHANGED_INVENTORY_PATHS; do
    if printf '%s\n' "${INTENTIONALLY_UNMAPPED_INVENTORY_PATHS[@]}" | grep -Fxq "$inv"; then
        continue
    fi
    if ! printf '%s\n' "$MAPPED_INVENTORY_PATHS" | grep -Fxq "$inv"; then
        UNMAPPED_INVENTORY_PATHS+=("$inv")
    fi
done

FAILED=0

if [ "${#UNMAPPED_ROLES[@]}" -gt 0 ]; then
    {
        echo "ERROR: The following changed roles are not mapped to any CI deploy job:"
        echo ""
        for role in "${UNMAPPED_ROLES[@]}"; do
            echo "  - ansible/roles/$role/"
        done
        echo ""
        echo "Resolution options:"
        echo "  1. Add the role to the relevant deploy-* job's changes: list in"
        echo "     .gitlab-ci.yml so the change triggers a rollout. This is the"
        echo "     default expectation for any role that does have a CI-driven"
        echo "     deploy path."
        echo "  2. Add the role to INTENTIONALLY_UNMAPPED_ROLES at the top of"
        echo "     scripts/check-deploy-coverage.sh if it is intentionally"
        echo "     deployed manually via a task wrapper (not unattended CI)."
        echo ""
    } >&2
    FAILED=1
fi

if [ "${#UNMAPPED_PLAYBOOKS[@]}" -gt 0 ]; then
    {
        echo "ERROR: The following changed playbooks are not mapped to any CI deploy job:"
        echo ""
        for pb in "${UNMAPPED_PLAYBOOKS[@]}"; do
            echo "  - ansible/playbooks/$pb"
        done
        echo ""
        echo "Resolution options:"
        echo "  1. Add the playbook path to the relevant deploy-* job's changes:"
        echo "     list in .gitlab-ci.yml so the change triggers a rollout."
        echo "  2. Add the playbook (path relative to ansible/playbooks/) to"
        echo "     INTENTIONALLY_UNMAPPED_PLAYBOOKS at the top of"
        echo "     scripts/check-deploy-coverage.sh if it is intentionally run"
        echo "     manually via a task wrapper (not unattended CI)."
        echo ""
    } >&2
    FAILED=1
fi

if [ "${#UNMAPPED_INVENTORY_PATHS[@]}" -gt 0 ]; then
    {
        echo "ERROR: The following changed inventory paths are not mapped to any CI deploy job:"
        echo ""
        for inv in "${UNMAPPED_INVENTORY_PATHS[@]}"; do
            echo "  - ansible/inventories/prod/$inv"
        done
        echo ""
        echo "Resolution options:"
        echo "  1. Add the inventory path to the relevant deploy-* job's changes:"
        echo "     list in .gitlab-ci.yml so the change triggers a rollout."
        echo "  2. Add the path (relative to ansible/inventories/prod/) to"
        echo "     INTENTIONALLY_UNMAPPED_INVENTORY_PATHS at the top of"
        echo "     scripts/check-deploy-coverage.sh if vars changes here are"
        echo "     intentionally deployed manually."
        echo ""
    } >&2
    FAILED=1
fi

if [ "$FAILED" -eq 1 ]; then
    {
        echo "Either option needs to be in the same MR as the change so the"
        echo "deploy-coverage gate stays accurate."
    } >&2
    exit 1
fi

echo "All changed roles/playbooks/inventory paths are covered by at least one deploy-* job rule."
[ -n "$CHANGED_ROLES" ] && echo "Changed roles:           $(echo "$CHANGED_ROLES" | tr '\n' ' ')"
[ -n "$CHANGED_PLAYBOOKS" ] && echo "Changed playbooks:       $(echo "$CHANGED_PLAYBOOKS" | tr '\n' ' ')"
[ -n "$CHANGED_INVENTORY_PATHS" ] && echo "Changed inventory paths: $(echo "$CHANGED_INVENTORY_PATHS" | tr '\n' ' ')"
# The && chains above leave $? = 1 when the last list is empty — don't let
# the success path exit non-zero.
exit 0
