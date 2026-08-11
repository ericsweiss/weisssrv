#!/usr/bin/env bash
# Pins that an `include:` input must repeat.
#
# GitLab resolves `include:` at pipeline-CREATION time, before this file's
# `variables:` block exists, so an input cannot read $KUSTOMIZE_VERSION and
# friends — the literal has to be written out again next to every include that
# needs it. This is what keeps those copies equal to their single source.
#
# Each `inp` result is `sort -u`'d, so it equals the `variables:` value only
# when EVERY copy matches.
#
# Run from the repo root. Exit 0 clean, 1 on drift.
set -uo pipefail

CI_FILE="${1:-.gitlab-ci.yml}"
rc=0

# <label> <variables: value> <every include-input value>
cmp_pin() {
    echo "$1: variables=${2:-<none>} include-input(s)=$(echo "$3" | tr '\n' ' ')"
    if [ -z "$2" ] || [ -z "$3" ]; then
        echo "  could not extract both sides"
        rc=1
        return
    fi
    if [ "$2" != "$3" ]; then
        echo "  DRIFT — bump both, they are one pin"
        rc=1
    fi
}

var() { sed -n "s/^  $1: \"\(.*\)\"\$/\1/p" "$CI_FILE" | head -1; }
inp() { sed -n "s/^      $1: \"\(.*\)\"\$/\1/p" "$CI_FILE" | sort -u; }

cmp_pin kustomize_version "$(var KUSTOMIZE_VERSION)" "$(inp kustomize_version)"
cmp_pin kustomize_sha256 "$(var KUSTOMIZE_SHA256)" "$(inp kustomize_sha256)"
cmp_pin pyyaml_version "$(var PYYAML_VERSION)" "$(inp pyyaml_version)"
cmp_pin pytest_version "$(var PYTEST_VERSION)" "$(inp pytest_version)"

exit "$rc"
