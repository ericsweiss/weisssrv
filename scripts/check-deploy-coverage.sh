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
# ## Policy: trade-off of the intentionally-unmapped lists
#
# 1. Why these lists exist. Some role/playbook/inventory paths are
#    deployed by human-in-the-loop task wrappers (k3s, proxmox_ha,
#    zfs_encryption, proxmox_vm/lxc) or affect every Ansible deploy
#    globally (hosts.yml — group memberships drive role targeting).
#    Mapping any of those to a single CI deploy-* job either
#    mis-fans-out (wrong rollout for the change) or fans the change
#    into every deploy-* rule and produces noisy redeploys for changes
#    the operator should review host-by-host.
#
# 2. What the gate gives up. A vars-only change to a path on one of
#    the INTENTIONALLY_UNMAPPED_* lists will NOT force a
#    deploy-coverage failure. For example, editing
#    host_vars/plex.yml or group_vars/k3s.yml passes this gate
#    silently. The operator is responsible for re-running the right
#    `task` wrapper (e.g. `task plex:deploy`, `task k3s:deploy`)
#    after touching those paths. Don't assume CI will catch you.
#
# 3. Why we accept it. The alternative — fanning every change into
#    every deploy-* job — produces noisier CI than the value gained,
#    and a noisy gate is one operators learn to ignore. The lists are
#    short, scoped, and reviewed-on-add, which keeps them honest.
#
# 4. How to keep the lists honest. Every entry below MUST have an
#    inline rationale comment explaining (a) why it isn't mapped to a
#    CI deploy job and (b) which task wrapper or operator workflow
#    deploys it instead. Reviewers reject any addition without one.
#    If you're tempted to add an entry without a rationale, you
#    probably want to wire the path into a deploy-* job's `changes:`
#    list instead.

set -euo pipefail

# Roles deliberately not mapped to a CI deploy job because deployment
# requires human-in-the-loop work that doesn't belong in unattended CI:
#
#   k3s            — node lifecycle (rolling drain/cordon/upgrade) via
#                    `task k3s:deploy` / `task maintenance:update-k3s-nodes`.
#   proxmox_vm     — VM provisioning, intentionally out of CI; runs via
#                    `task k3s:provision-vms` and similar wrappers.
#   proxmox_lxc    — same reasoning as proxmox_vm.
#   proxmox_ha     — HA rules / replication; sensitive, manual via
#                    `task proxmox:ha`.
#   zfs_encryption — ZFS-native passphrase activation; sensitive cold-boot
#                    operation. Manual via `task zfs:encrypt`.
INTENTIONALLY_UNMAPPED_ROLES=(
    k3s
    proxmox_vm
    proxmox_lxc
    proxmox_ha
    zfs_encryption
)

# Playbooks deliberately not mapped to a CI deploy job. Identified by
# their path relative to ansible/playbooks/ (e.g. "k3s.yml",
# "bootstrap/storage-bootstrap.yml"). Reasons:
#
#   site.yml                     — broad fan-out playbook used by many
#                                  deploy-* jobs via --limit/--tags.
#                                  No 1:1 mapping; each deploy job that
#                                  invokes site.yml lists its own
#                                  role/inventory triggers.
#   k3s.yml                      — k3s node lifecycle (rolling
#                                  drain/cordon/upgrade) via
#                                  `task k3s:deploy`. Never CI-driven.
#   k3s-provision-vms.yml        — VM provisioning on Proxmox hosts
#                                  via `task k3s:provision-vms`. Manual.
#   zfs-encryption.yml           — Sensitive cold-boot ZFS passphrase
#                                  activation. Manual via
#                                  `task zfs:encrypt`.
#   proxmox-ha.yml               — HA rules / replication; manual via
#                                  `task proxmox:ha`.
#   proxmox-enable-autostart.yml — One-shot autostart enablement after
#                                  cluster expansion. Manual.
#   postflight.yml               — Operator-run post-deploy verification
#                                  helper, invoked locally not by CI.
#   show-cert-host-keys.yml      — Operator helper for populating
#                                  host_vars/dns-01.yml host_key fields
#                                  during cert distribution setup.
#   bootstrap/storage-bootstrap.yml — One-shot ZFS pool bootstrap on
#                                  pve-nas-01. Pool creation is too
#                                  destructive to automate via CI.
#   maintenance/_ensure-nfs-server-healthy.yml,
#   maintenance/_reboot-if-needed.yml,
#   maintenance/update-applications.yml,
#   maintenance/update-full.yml,
#   maintenance/update-helm-charts.yml,
#   maintenance/update-k3s-nodes.yml,
#   maintenance/update-packages.yml — Wrapped by the maintenance-* CI
#                                  jobs (manual-trigger), not by the
#                                  deploy stage. The maintenance jobs
#                                  have their own change rules; the
#                                  deploy-coverage gate doesn't apply.
INTENTIONALLY_UNMAPPED_PLAYBOOKS=(
    site.yml
    k3s.yml
    k3s-provision-vms.yml
    zfs-encryption.yml
    proxmox-ha.yml
    proxmox-enable-autostart.yml
    postflight.yml
    show-cert-host-keys.yml
    bootstrap/storage-bootstrap.yml
    maintenance/_ensure-nfs-server-healthy.yml
    maintenance/_reboot-if-needed.yml
    maintenance/update-applications.yml
    maintenance/update-full.yml
    maintenance/update-helm-charts.yml
    maintenance/update-k3s-nodes.yml
    maintenance/update-packages.yml
)

# Inventory paths deliberately not mapped to a CI deploy job.
# Identified by path relative to ansible/inventories/prod/ (e.g.
# "group_vars/k3s.yml", "host_vars/plex.yml", "hosts.yml"). Reasons:
#
#   hosts.yml                — THE inventory file: lists every host
#                              and its group memberships. A change
#                              here affects every Ansible deploy
#                              because group memberships drive role
#                              targeting (which hosts get base, dns,
#                              storage, etc.). Mapping it to a single
#                              deploy-* job would mis-fan-out;
#                              fanning it into every deploy-* rule
#                              would generate noisy redeploys for
#                              changes the operator should review
#                              host-by-host. Operator decides which
#                              deploy-* jobs to re-run manually after
#                              a hosts.yml change.
#   group_vars/k3s.yml       — k3s cluster vars; deploy is manual via
#                              `task k3s:deploy`.
#   host_vars/plex.yml       — Plex LXC vars consumed by the plex.yml
#                              playbook (already mapped via the
#                              playbook entry in deploy-plex). Listed
#                              here so a vars-only change still maps
#                              cleanly without extra CI churn.
#   host_vars/smtp-relay.yml — Mail relay host vars; deploy-ansible-mail
#                              triggers off the role paths. Listed here
#                              because mail role + playbook changes are
#                              the deploy gate, not host vars.
INTENTIONALLY_UNMAPPED_INVENTORY_PATHS=(
    hosts.yml
    group_vars/k3s.yml
    host_vars/plex.yml
    host_vars/smtp-relay.yml
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
