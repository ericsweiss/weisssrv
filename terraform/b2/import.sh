#!/usr/bin/env bash
# One-time / disaster-recovery state bootstrap for terraform/b2.
#
# Runs `terraform import` for the managed bucket (the same address<->id as
# imports.tf), skipping it if already in state, so it is idempotent and safe to
# re-run. `terraform import` only READS the B2 API and writes Terraform state —
# it never modifies the bucket and never applies configuration. The native
# `import {}` block in imports.tf is the primary path on Terraform >= 1.5; this
# script exists as a fallback for older toolchains / DR runbooks, mirroring
# terraform/authentik/import.sh.
#
# Invoke via `task terraform:b2-import` (wraps this in `op run` with the
# TF_VAR_* credentials and TF_HTTP_* state backend env).
set -euo pipefail
cd "$(dirname "$0")"

# address|id — the bucket imports by its B2 bucket_id (matches imports.tf).
IMPORTS='
b2_bucket.weisssrv_backup|4ef45c874b3188409cf10a11
'

STATE="$(terraform state list 2>/dev/null || true)"
imported=0
skipped=0
while IFS='|' read -r addr id; do
  [ -n "${addr}" ] || continue
  if printf '%s\n' "${STATE}" | grep -Fxq "${addr}"; then
    skipped=$((skipped + 1))
    continue
  fi
  echo "==> terraform import '${addr}' '${id}'"
  terraform import -input=false "${addr}" "${id}"
  imported=$((imported + 1))
done <<EOF
${IMPORTS}
EOF

echo "import.sh done: ${imported} imported, ${skipped} already in state."
