#!/usr/bin/env bash
# Run `molecule test` with an in-job destroy + jitter retry, shared by the
# molecule-tests and integration-tests CI jobs so the retry policy (max attempts
# + jitter window) lives in ONE place instead of two hand-synced copies.
#
# Why the retry exists: under a full pipeline fan-out, simultaneous systemd-
# container starts on a shared node race cgroup setup and die at prepare
# ("Container X is not running"). Job-level retry re-rolls the same dice (all
# attempts land in the same storm); destroy + a randomized sleep de-conflicts
# the restart instead. The durable fix is right-sizing the privileged runner
# quota (kubernetes/apps/gitlab-runner-privileged) so the storm is rare — this
# loop is the documented complementary stopgap (see .molecule-base in
# .gitlab-ci.yml).
#
# The caller cd's into the role / integration-test directory first; molecule
# runs in $PWD. Base opts (before the subcommand, e.g. `-c <base.yml>`) and
# scenario opts (after it, e.g. `-s default`) are passed via env so both apply
# to the `test` AND the `destroy` invocations:
#   MOL_MAX  = max attempts            (default 4)
#   MOL_BASE = args before subcommand  (default empty)
#   MOL_SCEN = args after subcommand   (default empty)
#
# Usage:
#   MOL_BASE="-c $CI_PROJECT_DIR/ansible/molecule/base.yml" MOL_SCEN="-s default" \
#     bash "$CI_PROJECT_DIR/scripts/molecule-retry.sh"
#   bash "$CI_PROJECT_DIR/scripts/molecule-retry.sh"   # integration-tests: no base/scen
set -uo pipefail

MOL_MAX="${MOL_MAX:-4}"
MOL_BASE="${MOL_BASE:-}"
MOL_SCEN="${MOL_SCEN:-}"

attempt=1
# shellcheck disable=SC2086  # intentional word-split of MOL_BASE/MOL_SCEN
until molecule $MOL_BASE test $MOL_SCEN; do
    rc=$?
    attempt=$((attempt + 1))
    if [ "$attempt" -gt "$MOL_MAX" ]; then
        exit "$rc"
    fi
    echo "molecule attempt $((attempt - 1)) failed (rc=$rc); destroying + retrying ($attempt/$MOL_MAX)"
    # shellcheck disable=SC2086  # intentional word-split of MOL_BASE/MOL_SCEN
    molecule $MOL_BASE destroy $MOL_SCEN || true
    # $RANDOM is per-shell and runner-pod start times differ, so 20-65s of
    # jitter genuinely spreads simultaneous restarts across the fan-out.
    sleep $(((RANDOM % 46) + 20))
done
