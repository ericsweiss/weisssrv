#!/usr/bin/env python3
"""The VPN provider-parity gate fails when the three files it reads disagree."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from script_loader import load_path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
GATE = SCRIPTS / "check-vpn-provider-parity.py"


def _load():
    return load_path(GATE)


gate = _load()

CREDCHECK = '''#!/usr/bin/env bash
case "$provider" in
    privado)
        req_keys="privadovpn-user privadovpn-password"
        ;;
    "vpn unlimited")
        req_keys="vpnunlimited-user vpnunlimited-password"
        ;;
    *)
        exit 2
        ;;
esac
'''

SIDECAR = '''              case "$VPN_PROVIDER" in
                privado)
                  export OPENVPN_USER_SECRETFILE=/vpn-secrets/privadovpn-user
                  export OPENVPN_PASSWORD_SECRETFILE=/vpn-secrets/privadovpn-password
                  ;;
                "vpn unlimited")
                  export OPENVPN_USER_SECRETFILE=/vpn-secrets/vpnunlimited-user
                  export OPENVPN_PASSWORD_SECRETFILE=/vpn-secrets/vpnunlimited-password
                  ;;
                *)
                  exit 1
                  ;;
              esac
'''

WRAPPER = '''#!/usr/bin/env bash
case "$PROVIDER" in
    privado | privadovpn) GLUE="privado" ;;
    vpnunlimited | "vpn unlimited") GLUE="vpn unlimited" ;;
    *) exit 1 ;;
esac
'''


def _tree(tmp_path: Path, credcheck=CREDCHECK, sidecar=SIDECAR, wrapper=WRAPPER) -> Path:
    for rel, body in ((gate.CREDCHECK, credcheck), (gate.SIDECAR, sidecar),
                      (gate.WRAPPER, wrapper)):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return tmp_path


def test_the_live_repo_is_in_step():
    run = subprocess.run([sys.executable, str(GATE)], capture_output=True, text=True,
                         cwd=REPO)
    assert run.returncode == 0, run.stdout + run.stderr


def test_a_matching_tree_passes(tmp_path):
    assert gate.check(_tree(tmp_path)) == []


def test_a_key_added_only_to_the_preflight_fails(tmp_path):
    credcheck = CREDCHECK.replace(
        'req_keys="vpnunlimited-user vpnunlimited-password"',
        'req_keys="vpnunlimited-user vpnunlimited-password vpnunlimited-clientcrt"',
    )
    problems = gate.check(_tree(tmp_path, credcheck=credcheck))
    assert problems and "vpnunlimited-clientcrt" in " ".join(problems)


def test_a_provider_added_only_to_the_sidecar_fails(tmp_path):
    sidecar = SIDECAR.replace(
        "                *)",
        "                mullvad)\n"
        "                  export OPENVPN_USER_SECRETFILE=/vpn-secrets/mullvad-user\n"
        "                  ;;\n"
        "                *)",
    )
    problems = gate.check(_tree(tmp_path, sidecar=sidecar))
    assert problems and "mullvad" in " ".join(problems)


def test_an_alias_pointing_at_an_unknown_provider_fails(tmp_path):
    wrapper = WRAPPER.replace('GLUE="vpn unlimited"', 'GLUE="vpn-unlimited"')
    problems = gate.check(_tree(tmp_path, wrapper=wrapper))
    assert problems and "vpn-unlimited" in " ".join(problems)


def test_a_parser_that_matches_nothing_is_a_failure_not_a_pass(tmp_path):
    # The whole point of the gate is that it cannot pass vacuously.
    problems = gate.check(_tree(tmp_path, credcheck="#!/usr/bin/env bash\n"
                                                    'case "$provider" in\nesac\n'))
    assert problems and "parsed no provider arms" in " ".join(problems)


def test_a_missing_case_block_is_reported_not_ignored(tmp_path):
    with pytest.raises(SystemExit):
        gate.check(_tree(tmp_path, wrapper="#!/usr/bin/env bash\necho hi\n"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
