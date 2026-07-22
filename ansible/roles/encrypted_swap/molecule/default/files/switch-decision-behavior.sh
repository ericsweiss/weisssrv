#!/usr/bin/env bash
# Molecule behavior test for encrypted_swap's live-switch decision. Sources the
# rendered encrypted-swap-switch.sh and drives encrypted_swap_switch against
# synthetic /proc/meminfo + /proc/swaps, asserting the three arms AND that the
# already-active / defer arms NEVER call swapoff (the memory-safety invariant).
# Runs on the target via ansible.builtin.script after converge deploys the script.
set -euo pipefail

s="${1:-/usr/local/sbin/encrypted-swap-switch.sh}"
[ -f "$s" ] || { echo >&2 "encrypted-swap-switch.sh not rendered at $s"; exit 1; }
# The REAL render must be valid bash (CI shellcheck only lints the neutralized template).
bash -n "$s"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo >&2 "FAIL: $*"; exit 1; }

# Run one arm in an isolated subshell: source the script, stub swapoff/systemctl/
# swapon to record their invocations, then call the decision with synthetic /proc
# files. Prints the decision on stdout; the recorded calls land in $2. The
# success-path swapon stub APPENDS the mapper to the synthetic swaps file so the
# script's post-switch /proc/swaps verification sees an activated mapper.
# FAIL_CMD names one stub that returns 1 (failure-injection arm); empty = all
# stubs succeed.
run_arm() {
  local meminfo="$1" calls="$2"
  : > "$calls"
  env -i CALLS="$calls" MEMINFO="$meminfo" SWAPS="$3" FAIL_CMD="${4:-}" bash -c '
    # shellcheck disable=SC1091
    source "'"$s"'"
    swapoff()   { echo "swapoff $*"   >> "$CALLS"; [ "$FAIL_CMD" != swapoff ]; }
    systemctl() { echo "systemctl $*" >> "$CALLS"; [ "$FAIL_CMD" != systemctl ]; }
    swapon()    {
      echo "swapon $*" >> "$CALLS"
      [ "$FAIL_CMD" != swapon ] || return 1
      # FAIL_CMD=verify: swapon "succeeds" but the mapper never appears in
      # SWAPS — drives the mapper-not-in-swaps verification failure arm.
      [ "$FAIL_CMD" = verify ] && return 0
      case "$1" in /dev/mapper/*) printf "%s partition 100 0 100\n" "$1" >> "$SWAPS" ;; esac
    }
    encrypted_swap_switch cryptswap 2048
  '
}

# --- Arm 1: already-active (SWAPS lists the mapper) -> no swapoff --------------
cat > "$WORK/mem1" <<'EOF'
MemAvailable:    8000000 kB
SwapTotal:       8000000 kB
SwapFree:        4000000 kB
EOF
printf '/dev/mapper/cryptswap partition 8000000 4000000 100\n' > "$WORK/swaps1"
out="$(run_arm "$WORK/mem1" "$WORK/calls1" "$WORK/swaps1")"
[ "$out" = "already-active" ] || fail "already-active arm printed '$out'"
[ ! -s "$WORK/calls1" ] || fail "already-active arm called $(tr '\n' ',' < "$WORK/calls1")"

# --- Arm 2: defer (mapper absent, MemAvailable < swap+margin) -> no swapoff ----
# used = 8000000-1000000 = 7000000 kB; margin = 2048*1024 = 2097152 kB;
# need = 9097152 kB > MemAvailable 1000000 kB -> defer.
cat > "$WORK/mem2" <<'EOF'
MemAvailable:    1000000 kB
SwapTotal:       8000000 kB
SwapFree:        1000000 kB
EOF
: > "$WORK/swaps2"
out="$(run_arm "$WORK/mem2" "$WORK/calls2" "$WORK/swaps2")"
case "$out" in defer-reboot:*) ;; *) fail "defer arm printed '$out'" ;; esac
[ ! -s "$WORK/calls2" ] || fail "defer arm called swapoff/cryptsetup ($(tr '\n' ',' < "$WORK/calls2")) — memory-safety invariant broken"

# --- Arm 3: live-switch (mapper absent, MemAvailable covers swap+margin) -------
# used = 8000000-7000000 = 1000000 kB; need = 3097152 kB <= MemAvailable 8000000.
cat > "$WORK/mem3" <<'EOF'
MemAvailable:    8000000 kB
SwapTotal:       8000000 kB
SwapFree:        7000000 kB
EOF
: > "$WORK/swaps3"
out="$(run_arm "$WORK/mem3" "$WORK/calls3" "$WORK/swaps3")"
[ "$out" = "switched-live" ] || fail "live-switch arm printed '$out'"
grep -q '^swapoff -a$'   "$WORK/calls3" || fail "live-switch arm did not swapoff -a"
grep -q '^systemctl start systemd-cryptsetup@cryptswap.service$' "$WORK/calls3" \
  || fail "live-switch arm did not start the cryptsetup unit"
grep -q '^swapon /dev/mapper/cryptswap$' "$WORK/calls3" || fail "live-switch arm did not swapon the mapper"

# --- Arm 4: failure injection — a failed switch must NOT report switched-live --
# The caller deletes the plaintext fstab fallback on the switched-live token, so
# a false success here is the swapless-host failure mode; assert every injected
# failure yields a distinct switch-failed token AND a non-zero exit, and that
# the cryptsetup/swapon failure arms restore plaintext swap (swapon -a).
for failing in swapoff systemctl swapon verify; do
  : > "$WORK/swaps4"
  rc=0
  out="$(run_arm "$WORK/mem3" "$WORK/calls4" "$WORK/swaps4" "$failing")" || rc=$?
  [ "$rc" -ne 0 ] || fail "live-switch with failing $failing exited 0"
  case "$out" in switch-failed:*) ;; *) fail "failing $failing printed '$out' (expected switch-failed:*)" ;; esac
  if [ "$failing" != "swapoff" ]; then
    grep -q '^swapon -a$' "$WORK/calls4" || fail "failing $failing did not attempt plaintext restore (swapon -a)"
    # Post-open failures (swapon-mapper / mapper-not-in-swaps) must RELEASE the
    # backing LV before restoring: the mapper holds it, so a bare `swapon -a`
    # would EBUSY. Assert the cryptsetup unit stop precedes swapon -a.
    if [ "$failing" = "swapon" ] || [ "$failing" = "verify" ]; then
      grep -q '^systemctl stop systemd-cryptsetup@cryptswap.service$' "$WORK/calls4" \
        || fail "failing $failing did not stop the cryptsetup unit before restore"
      stop_line=$(grep -n '^systemctl stop' "$WORK/calls4" | head -1 | cut -d: -f1)
      restore_line=$(grep -n '^swapon -a$' "$WORK/calls4" | head -1 | cut -d: -f1)
      [ "$stop_line" -lt "$restore_line" ] \
        || fail "failing $failing restored swap BEFORE releasing the mapper (EBUSY hazard)"
    fi
  fi
done

echo "encrypted-swap switch-decision behavior OK"
