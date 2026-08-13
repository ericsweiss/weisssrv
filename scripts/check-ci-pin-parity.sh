#!/usr/bin/env bash
# Pins that an `include:` input must repeat.
#
# GitLab resolves `include:` at pipeline-CREATION time, before this file's
# `variables:` block exists, so an input cannot read $KUSTOMIZE_VERSION and
# friends — the literal has to be written out again next to every include that
# needs it. This is what keeps those copies equal to their single source.
#
# The pin list is DERIVED, not written here: every `name: "value"` under an
# `inputs:` block whose upper-cased name also exists as a `variables:` key is a
# copy of that variable and is compared. A new pin duplicated into an include is
# therefore covered the moment it is added. FLOOR_PINS is a floor assertion on
# the derivation itself — if a parser regression reduced the derived set to
# nothing, the gate would otherwise pass by inspecting zero pins.
#
# Each input's values are `sort -u`'d, so a pin equals its variable only when
# EVERY copy matches.
#
# Run from the repo root. Exit 0 clean, 1 on drift.
set -uo pipefail

CI_FILE="${1:-.gitlab-ci.yml}"
rc=0

# Pins that must always be derivable. Not the list being checked — the proof
# that the derivation still works.
FLOOR_PINS="kustomize_version kustomize_sha256 pyyaml_version pytest_version"

var() { sed -n "s/^  $1: \"\(.*\)\"\$/\1/p" "$CI_FILE" | head -1; }
inp() { sed -n "s/^      $1: \"\(.*\)\"\$/\1/p" "$CI_FILE" | sort -u; }

# Every input key that also names a `variables:` key. `tr` handles the
# lower_snake -> UPPER_SNAKE convention the include inputs follow.
derive_pins() {
    sed -n 's/^      \([a-z0-9_]*\): ".*"$/\1/p' "$CI_FILE" | sort -u | while read -r key; do
        [ -n "$key" ] || continue
        upper=$(echo "$key" | tr '[:lower:]' '[:upper:]')
        [ -n "$(var "$upper")" ] && echo "$key"
    done
}

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

pins=$(derive_pins)

# The floor: a derivation that stopped seeing a known pin is a broken gate, not
# a clean pipeline.
for floor in $FLOOR_PINS; do
    if ! echo "$pins" | grep -qx "$floor"; then
        echo "$floor: derived from neither side — the include-input parser no longer sees it"
        rc=1
    fi
done

for key in $pins; do
    upper=$(echo "$key" | tr '[:lower:]' '[:upper:]')
    cmp_pin "$key" "$(var "$upper")" "$(inp "$key")"
done

exit "$rc"
