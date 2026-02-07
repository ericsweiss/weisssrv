#!/usr/bin/env bash
# Run Molecule tests for all roles that have test scenarios.
#
# Usage:
#   ./test-all-roles.sh              # Test all roles
#   ./test-all-roles.sh k3s unbound  # Test specific roles
#   MOLECULE_OPTS="--destroy=never" ./test-all-roles.sh  # Keep containers
#
# Prerequisites:
#   - Docker running
#   - molecule, molecule-docker installed (pip)
#   - ansible-galaxy collections: community.docker, ansible.posix, community.general
#
# IMPORTANT: ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1 is required because
# molecule-docker's create.yml has a broken conditional that Ansible 2.20+
# rejects. This is a molecule-docker upstream issue, not a problem with our
# roles. See: https://github.com/ansible-community/molecule-plugins/issues/254

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROLES_DIR="${SCRIPT_DIR}/roles"

# Required environment variable for molecule-docker compatibility
export ANSIBLE_ALLOW_BROKEN_CONDITIONALS=1

# Additional molecule options (e.g., --destroy=never)
MOLECULE_OPTS="${MOLECULE_OPTS:-}"

# Discover all roles with molecule scenarios
discover_testable_roles() {
    local roles=()
    for role_dir in "${ROLES_DIR}"/*/; do
        if [[ -d "${role_dir}molecule" ]]; then
            roles+=("$(basename "${role_dir}")")
        fi
    done
    echo "${roles[@]}"
}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Track results
PASSED=()
FAILED=()
SKIPPED=()

echo "============================================"
echo "  Molecule Test Runner for weisssrv roles"
echo "============================================"
echo ""

# Determine which roles to test
if [[ $# -gt 0 ]]; then
    ROLES_TO_TEST=("$@")
else
    # Bash 3.2 compatible (no mapfile)
    ROLES_TO_TEST=()
    while IFS= read -r role; do
        ROLES_TO_TEST+=("$role")
    done < <(discover_testable_roles | tr ' ' '\n')
fi

echo "Roles to test: ${ROLES_TO_TEST[*]}"
echo "Molecule options: ${MOLECULE_OPTS:-none}"
echo ""

# Check prerequisites
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}ERROR: python3 not found${NC}"
    exit 1
fi

if ! ~/.pyenv/versions/3.13.5/bin/molecule --version &>/dev/null && ! python3 -m molecule --version &>/dev/null; then
    echo -e "${RED}ERROR: molecule not found (pip install molecule molecule-docker)${NC}"
    exit 1
fi

# Use molecule from pyenv if available, otherwise try system
if ~/.pyenv/versions/3.13.5/bin/molecule --version &>/dev/null; then
    MOLECULE_CMD=~/.pyenv/versions/3.13.5/bin/molecule
else
    MOLECULE_CMD="python3 -m molecule"
fi

if ! docker info &>/dev/null 2>&1; then
    echo -e "${RED}ERROR: Docker is not running${NC}"
    exit 1
fi

# Run tests
for role in "${ROLES_TO_TEST[@]}"; do
    role_path="${ROLES_DIR}/${role}"

    if [[ ! -d "${role_path}/molecule" ]]; then
        echo -e "${YELLOW}SKIP${NC} ${role} (no molecule scenario)"
        SKIPPED+=("${role}")
        continue
    fi

    echo "--------------------------------------------"
    echo -e "Testing: ${YELLOW}${role}${NC}"
    echo "--------------------------------------------"

    pushd "${role_path}" >/dev/null

    # shellcheck disable=SC2086
    if ${MOLECULE_CMD} test ${MOLECULE_OPTS} 2>&1; then
        echo -e "${GREEN}PASS${NC} ${role}"
        PASSED+=("${role}")
    else
        echo -e "${RED}FAIL${NC} ${role}"
        FAILED+=("${role}")
    fi

    popd >/dev/null
    echo ""
done

# Summary
echo "============================================"
echo "  Test Summary"
echo "============================================"

if [[ ${#PASSED[@]} -gt 0 ]]; then
    echo -e "${GREEN}PASSED (${#PASSED[@]}):${NC} ${PASSED[*]}"
fi

if [[ ${#SKIPPED[@]} -gt 0 ]]; then
    echo -e "${YELLOW}SKIPPED (${#SKIPPED[@]}):${NC} ${SKIPPED[*]}"
fi

if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo -e "${RED}FAILED (${#FAILED[@]}):${NC} ${FAILED[*]}"
    exit 1
fi

echo ""
echo -e "${GREEN}All tests passed!${NC}"
exit 0
