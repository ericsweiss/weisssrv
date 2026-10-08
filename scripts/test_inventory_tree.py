"""Cover scripts/inventory_tree.py against fixtures and the live inventory.

The shapes that differ between callers are pinned here: an empty group is real,
a null reference never shadows one, and a group cycle raises.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from inventory_tree import (
    InventoryCycle,
    addresses_by_host,
    all_hosts,
    declared_groups,
    group_index,
    host_vars,
    hosts_by_address,
    load_inventory,
    resolve_hosts,
)

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "scripts" / "inventory_tree.py"
HOSTS_YML = REPO / "ansible/inventories/prod/hosts.yml"

FIXTURE = textwrap.dedent(
    """\
    all:
      children:
        core:
          children:
            pve:
              hosts:
                pve-nas-01:
                  ansible_host: 10.0.10.102
                pve-opt-01:
                  ansible_host: 10.0.10.103
            empty_tier:
              hosts: {}
        guests:
          hosts:
            plex:
              ansible_host: 10.0.10.152
        k3s:
          children:
            pve:
        ledger:
    """
)


@pytest.fixture
def inventory() -> dict:
    return yaml.safe_load(FIXTURE)


def test_resolve_expands_children_in_inventory_order(inventory):
    index = group_index(inventory)
    assert resolve_hosts("core", index) == ["pve-nas-01", "pve-opt-01"]
    assert all_hosts(inventory) == ["pve-nas-01", "pve-opt-01", "plex"]


def test_an_empty_group_is_declared_and_resolves_to_nothing(inventory):
    index = group_index(inventory)
    assert "empty_tier" in index
    assert resolve_hosts("empty_tier", index) == []


def test_a_null_reference_does_not_shadow_the_real_group(inventory):
    """`k3s: children: pve:` reuses pve, whose hosts are declared elsewhere."""
    index = group_index(inventory)
    assert resolve_hosts("k3s", index) == ["pve-nas-01", "pve-opt-01"]


def test_an_undeclared_group_is_distinguishable_from_an_empty_one(inventory):
    index = group_index(inventory)
    assert "no_such_group" not in index
    assert resolve_hosts("no_such_group", index) == []


CYCLIC = """\
all:
  children:
    left:
      hosts:
        a:
      children:
        right:
          hosts:
            b:
          children:
            left:
"""


def test_a_group_cycle_raises_under_strict():
    """Mutation case: two groups naming each other must not resolve silently."""
    index = group_index(yaml.safe_load(CYCLIC))
    with pytest.raises(InventoryCycle) as caught:
        resolve_hosts("left", index, strict=True)
    assert "left" in str(caught.value)


def test_a_group_cycle_terminates_without_strict():
    """The default form stops at the repeat rather than recursing forever."""
    index = group_index(yaml.safe_load(CYCLIC))
    assert resolve_hosts("left", index) == ["a", "b"]


def test_a_null_bodied_group_is_declared_but_carries_nothing(inventory):
    """The deploy ledger groups exist only as names; gates still expand them."""
    assert "ledger" in declared_groups(inventory)
    assert "ledger" not in group_index(inventory)
    assert resolve_hosts("ledger", group_index(inventory)) == []


def test_addresses_map_both_ways(inventory):
    assert hosts_by_address(inventory)["10.0.10.152"] == "plex"
    assert addresses_by_host(inventory)["plex"] == "10.0.10.152"


def test_host_vars_merge_across_groups():
    merged = host_vars(
        yaml.safe_load(
            textwrap.dedent(
                """\
                all:
                  children:
                    one:
                      hosts:
                        plex:
                          ansible_host: 10.0.10.152
                    two:
                      hosts:
                        plex:
                          vm_id: 152
                """
            )
        )
    )
    assert merged["plex"] == {"ansible_host": "10.0.10.152", "vm_id": 152}


def test_the_live_inventory_resolves():
    assert MODULE.is_file() and HOSTS_YML.is_file()
    inventory = load_inventory(HOSTS_YML)
    hosts = all_hosts(inventory)
    assert len(hosts) == len(set(hosts)), "a host resolved twice"
    addresses = hosts_by_address(inventory)
    assert addresses, "the prod inventory declares no ansible_host values"
    assert set(addresses.values()) <= set(hosts)
