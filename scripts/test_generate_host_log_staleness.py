"""Coverage for generate-host-log-staleness.py.

The live tree is in step, so it proves nothing about failure: the derivation and
the --check arm run against fixture inventories, and each drift case must FAIL.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
gen = load_script("generate-host-log-staleness.py")


SITE = textwrap.dedent(
    """\
    - name: Base
      hosts: base_managed:extra_servers:!deploy_skipped
      roles:
        - weisssrv.infra.base
        - weisssrv.infra.alloy_host
    """
)

HOSTS = textwrap.dedent(
    """\
    all:
      children:
        base_managed:
          children:
            proxmox:
              hosts:
                pve-one:
                pve-two:
        extra_servers:
          hosts:
            app-one:
    """
)


def write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path, gen.SITE_YML, SITE)
    write(tmp_path, gen.HOSTS_YML, HOSTS)
    write(tmp_path, gen.OUTPUT, gen.render(["pve-one", "pve-two", "app-one"]))
    return tmp_path


def test_hosts_come_out_in_inventory_order(repo: Path) -> None:
    assert gen.alloy_hosts(repo) == ["pve-one", "pve-two", "app-one"]


def test_a_host_in_two_groups_is_rendered_once(repo: Path) -> None:
    write(repo, gen.HOSTS_YML, HOSTS.replace("        app-one:", "        pve-one:"))
    assert gen.alloy_hosts(repo) == ["pve-one", "pve-two"]


def test_check_passes_on_a_generated_file(repo: Path) -> None:
    assert gen.main(["--repo-root", str(repo), "--check"]) == 0


def test_check_fails_when_the_inventory_gains_a_host(repo: Path) -> None:
    write(repo, gen.HOSTS_YML, HOSTS + "        app-two:\n")
    assert gen.main(["--repo-root", str(repo), "--check"]) == 1


def test_check_fails_when_a_rule_is_hand_deleted(repo: Path) -> None:
    body = (repo / gen.OUTPUT).read_text()
    write(repo, gen.OUTPUT, body.replace('host="app-one"', 'host="app-typo"'))
    assert gen.main(["--repo-root", str(repo), "--check"]) == 1


def test_a_hand_edited_header_is_drift(repo: Path) -> None:
    body = (repo / gen.OUTPUT).read_text()
    write(repo, gen.OUTPUT, "# a local note\n" + body[body.index("groups:") :])
    assert gen.main(["--repo-root", str(repo), "--check"]) == 1


def test_writing_makes_check_pass(repo: Path) -> None:
    write(repo, gen.OUTPUT, "groups: []\n")
    assert gen.main(["--repo-root", str(repo)]) == 0
    assert gen.main(["--repo-root", str(repo), "--check"]) == 0


def test_count_is_the_host_count(repo: Path, capsys) -> None:
    assert gen.main(["--repo-root", str(repo), "--count"]) == 0
    assert capsys.readouterr().out.strip() == "3"


def test_a_group_resolving_to_nothing_is_vacuous(repo: Path) -> None:
    write(
        repo,
        gen.HOSTS_YML,
        HOSTS.replace("    extra_servers:\n      hosts:\n        app-one:\n", ""),
    )
    with pytest.raises(gen.Vacuous):
        gen.alloy_hosts(repo)
    assert gen.main(["--repo-root", str(repo), "--check"]) == 2


def test_a_declared_but_empty_group_contributes_no_hosts(repo: Path) -> None:
    """A group declared with no hosts yet is real; only an undeclared one is a fault."""
    write(
        repo,
        gen.HOSTS_YML,
        HOSTS.replace("      hosts:\n        app-one:\n", "      hosts: {}\n"),
    )
    assert gen.alloy_hosts(repo) == ["pve-one", "pve-two"]


def test_an_excluded_host_gets_no_rule(repo: Path) -> None:
    """A host the play excludes never runs alloy_host, so it must not be alerted on."""
    write(
        repo,
        gen.HOSTS_YML,
        HOSTS + "    deploy_skipped:\n      hosts:\n        app-one:\n",
    )
    assert gen.alloy_hosts(repo) == ["pve-one", "pve-two"]
    assert "app-one" not in gen.render(gen.alloy_hosts(repo))


def test_exclusions_are_reported_separately_from_inclusions(repo: Path) -> None:
    assert gen.target_groups(repo / gen.SITE_YML) == (
        ["base_managed", "extra_servers"],
        ["deploy_skipped"],
    )


def test_a_group_the_inventory_never_declares_is_vacuous(repo: Path) -> None:
    write(repo, gen.SITE_YML, SITE.replace("extra_servers", "no_such_group"))
    with pytest.raises(gen.Vacuous):
        gen.alloy_hosts(repo)


def test_two_alloy_host_plays_are_vacuous(repo: Path) -> None:
    write(repo, gen.SITE_YML, SITE + SITE)
    with pytest.raises(gen.Vacuous):
        gen.alloy_hosts(repo)


def test_the_live_file_matches_the_live_inventory() -> None:
    assert gen.main(["--repo-root", str(REPO), "--check"]) == 0
