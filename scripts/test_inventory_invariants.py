"""Invariants the production inventory has to keep: address and vmid consistency,
VIPs matching cluster-config, and the HA triad (guest host, affinity rule, replication
source) moving as one unit. PyYAML only, so it runs in the `python-tests` CI job.
"""
from __future__ import annotations

import copy
import ipaddress
import re
from pathlib import Path

import inventory_tree
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
INVENTORY = REPO / "ansible/inventories/prod"
HOSTS_FILE = INVENTORY / "hosts.yml"
ALL_VARS_FILE = INVENTORY / "group_vars/all.yml"
CLUSTER_CONFIG = REPO / "kubernetes/infrastructure/sources/cluster-config.yaml"
DNS_VARS_FILE = INVENTORY / "group_vars/dns.yml"

# cluster-config key -> the group_vars/all.yml key holding the same address.
VIP_MIRRORS = {
    "cluster_syslog_vip": "syslog_vip",
    "cluster_wg_easy_vip": "wg_easy_vip",
}

# Every VIP an inventory host must not claim, and how to name it in a failure.
VIP_LABELS = {
    "cluster_api_vip": "the k3s API VIP",
    "cluster_metallb_public_vip": "the public MetalLB VIP",
    "cluster_metallb_internal_vip": "the internal MetalLB VIP",
    "cluster_wg_easy_vip": "the wg-easy VIP",
    "cluster_syslog_vip": "the syslog VIP",
}

RESOURCE_RE = re.compile(r"^(?:ct|vm):(\d+)$")


# Loaders, side-effect free so the mutation tests can feed them copies.


def inventory_hosts(inventory: dict, with_host_vars: bool = True) -> dict[str, dict]:
    """host name -> merged vars, for every host in the YAML inventory.

    The group merge comes from inventory_tree, so gates read inventory shape one
    way; host_vars/<host>.yml folds in on top.
    """
    hosts = inventory_tree.host_vars(inventory or {})
    if with_host_vars:
        for name, merged in hosts.items():
            path = INVENTORY / "host_vars" / f"{name}.yml"
            if path.is_file():
                merged.update(yaml.safe_load(path.read_text()) or {})
    return hosts


def first_host_entry(inventory: dict) -> dict:
    """A reference to one host's vars INSIDE `inventory`, for mutation tests."""
    found: list[dict] = []

    def walk(node) -> None:
        if found or not isinstance(node, dict):
            return
        for key, value in node.items():
            if key == "hosts" and isinstance(value, dict):
                for host_vars in value.values():
                    if isinstance(host_vars, dict) and host_vars.get("ansible_host"):
                        found.append(host_vars)
                        return
            elif key != "vars":
                walk(node[key])

    walk(inventory)
    assert found, "no host entry to mutate"
    return found[0]


def cluster_config(doc: dict) -> dict[str, str]:
    return {k: str(v) for k, v in (doc.get("data") or {}).items()}


@pytest.fixture(scope="module")
def hosts() -> dict:
    return yaml.safe_load(HOSTS_FILE.read_text())


@pytest.fixture(scope="module")
def all_vars() -> dict:
    return yaml.safe_load(ALL_VARS_FILE.read_text())


@pytest.fixture(scope="module")
def config() -> dict[str, str]:
    return cluster_config(yaml.safe_load(CLUSTER_CONFIG.read_text()))


# Checks. Each returns the list of problems it found, so a mutation test can
# assert the check fires rather than only that the live data passes.


def duplicate_fields(inventory: dict, field: str) -> tuple[list[str], int]:
    """Clashes on `field`, and how many hosts declared it at all.

    The count is the floor: a field that moved to host_vars or was renamed
    leaves every uniqueness check below vacuously true.
    """
    owners: dict[str, list[str]] = {}
    for name, host_vars in sorted(inventory_hosts(inventory).items()):
        value = host_vars.get(field)
        if value is not None:
            owners.setdefault(str(value), []).append(name)
    clashes = [
        f"{field} {value} is claimed by {', '.join(names)}"
        for value, names in sorted(owners.items())
        if len(names) > 1
    ]
    return clashes, sum(len(names) for names in owners.values())


def hosts_on_a_vip(inventory: dict, config: dict[str, str]) -> list[str]:
    claimed = {
        config[key]: label for key, label in VIP_LABELS.items() if config.get(key)
    }
    if not claimed:
        raise AssertionError("cluster-config declares no VIPs — nothing examined")
    return [
        f"{name} is on {addr}, {claimed[addr]}"
        for name, host_vars in sorted(inventory_hosts(inventory).items())
        if (addr := str(host_vars.get("ansible_host") or "")) in claimed
    ]


def hosts_outside_the_lan(
    inventory: dict, config: dict[str, str]
) -> tuple[list[str], int]:
    """Hosts addressed outside the LAN, and how many IP literals were compared.

    The count is the floor: a rename that turned every ansible_host into a name
    leaves the comparison below vacuously clean.
    """
    network = ipaddress.ip_network(config["cluster_lan_cidr"], strict=False)
    outside = []
    compared = 0
    for name, host_vars in sorted(inventory_hosts(inventory).items()):
        addr = host_vars.get("ansible_host")
        if addr is None:
            continue
        try:
            address = ipaddress.ip_address(str(addr))
        except ValueError:
            continue  # a name rather than an address; DNS resolves it
        compared += 1
        if address not in network:
            outside.append(f"{name}: {addr}")
    return outside, compared


def vip_mirror_mismatches(all_vars: dict, config: dict[str, str]) -> list[str]:
    problems = []
    for config_key, inventory_key in VIP_MIRRORS.items():
        expected = config.get(config_key)
        actual = all_vars.get(inventory_key)
        if expected is None:
            problems.append(f"cluster-config has no {config_key}")
        elif actual is None:
            problems.append(f"group_vars/all.yml has no {inventory_key}")
        elif str(actual) != expected:
            problems.append(
                f"{inventory_key} is {actual}, cluster-config {config_key} is {expected}"
            )
    return problems


def ha_home_node(rule: dict) -> str | None:
    """The single node a node-affinity rule gives priority 2."""
    homes = [
        node.split(":", 1)[0]
        for node in rule.get("nodes") or []
        if isinstance(node, str) and node.split(":", 1)[-1] == "2"
    ]
    return homes[0] if len(homes) == 1 else None


def ha_triad_problems(inventory: dict, all_vars: dict) -> list[str]:
    """proxmox_host == affinity home == replication source_node, per guest."""
    rules = all_vars.get("proxmox_ha_rules") or []
    jobs = all_vars.get("proxmox_ha_replication_jobs") or []
    if not rules or not jobs:
        raise AssertionError(
            "proxmox_ha_rules or proxmox_ha_replication_jobs is empty — "
            "this gate is examining nothing"
        )

    by_vmid = {
        str(host_vars["vmid"]): (name, host_vars)
        for name, host_vars in inventory_hosts(inventory).items()
        if host_vars.get("vmid") is not None
    }
    problems: list[str] = []
    checked = 0

    for rule in rules:
        name = rule.get("name", "<unnamed>")
        resources = rule.get("resources") or []
        match = RESOURCE_RE.match(str(resources[0])) if resources else None
        if not match:
            problems.append(f"{name}: resources[0] is not ct:<id> / vm:<id>")
            continue
        vmid = match.group(1)
        if vmid not in by_vmid:
            problems.append(f"{name}: vmid {vmid} is in no inventory host")
            continue
        host_name, host_vars = by_vmid[vmid]

        home = ha_home_node(rule)
        if home is None:
            problems.append(f"{name}: needs exactly one node at priority 2")
            continue
        if host_vars.get("proxmox_host") != home:
            problems.append(
                f"{name}: home is {home} but {host_name}'s proxmox_host is "
                f"{host_vars.get('proxmox_host')}"
            )

        for job in jobs:
            if str(job.get("id", "")).split("-", 1)[0] != vmid:
                continue
            checked += 1
            if job.get("source_node") != home:
                problems.append(
                    f"replication {job.get('id')}: source_node "
                    f"{job.get('source_node')} is not {name}'s home {home}"
                )
            if job.get("target_node") == job.get("source_node"):
                problems.append(
                    f"replication {job.get('id')}: target_node equals source_node"
                )

    if not checked:
        raise AssertionError("no replication job matched an HA rule — nothing examined")
    return problems


# The live inventory keeps every invariant.


def test_inventory_declares_hosts(hosts):
    """Every check below is vacuously true on an empty inventory."""
    assert inventory_hosts(hosts), f"{HOSTS_FILE} declares no hosts"


def test_every_vmid_is_unique(hosts):
    """`pct` and `qm` share ONE vmid namespace, so a collision means the second
    create fails against an id Proxmox already knows — or the reconcile adopts
    the wrong guest, several phases after the edit."""
    clashes, declared = duplicate_fields(hosts, "vmid")
    assert declared, f"no host in {HOSTS_FILE} declares a vmid — nothing examined"
    assert not clashes, "duplicate vmids:\n  " + "\n  ".join(clashes)


def test_every_ansible_host_is_unique(hosts):
    clashes, declared = duplicate_fields(hosts, "ansible_host")
    assert declared, (
        f"no host in {HOSTS_FILE} declares an ansible_host — nothing examined"
    )
    assert not clashes, (
        "two hosts on one address (whichever is provisioned second takes it):\n  "
        + "\n  ".join(clashes)
    )


def test_no_host_claims_a_cluster_vip(hosts, config):
    """kube-vip and MetalLB answer ARP for these, so a guest on the same address
    is an ARP fight that surfaces as an intermittently working endpoint."""
    collisions = hosts_on_a_vip(hosts, config)
    assert not collisions, "hosts configured on a VIP:\n  " + "\n  ".join(collisions)


def test_every_ansible_host_is_inside_the_lan(hosts, config):
    outside, compared = hosts_outside_the_lan(hosts, config)
    assert compared, (
        f"no host in {HOSTS_FILE} declares an ansible_host as an IP literal — "
        "the CIDR comparison inspected nothing"
    )
    assert not outside, (
        f"hosts addressed outside cluster_lan_cidr ({config['cluster_lan_cidr']}):\n  "
        + "\n  ".join(outside)
    )


def test_the_firewall_vips_match_cluster_config(all_vars, config):
    """The firewall rules dereference syslog_vip / wg_easy_vip. A VIP moved in
    cluster-config and not here leaves the rule pointing at the old address —
    and a frame to a VIP is filtered by the GUEST firewall, so it just drops."""
    problems = vip_mirror_mismatches(all_vars, config)
    assert not problems, "VIP mirror drift:\n  " + "\n  ".join(problems)


def test_the_ha_triad_is_one_unit(hosts, all_vars):
    problems = ha_triad_problems(hosts, all_vars)
    assert not problems, (
        "HA affinity / replication / proxmox_host disagree:\n  " + "\n  ".join(problems)
    )


# Mutation cases: each check fires on the breakage it exists to catch.


def test_a_duplicate_vmid_is_caught(hosts):
    broken = copy.deepcopy(hosts)
    taken = sorted(
        host_vars["vmid"]
        for host_vars in inventory_hosts(broken).values()
        if host_vars.get("vmid") is not None
    )
    first_host_entry(broken)["vmid"] = taken[-1]
    assert duplicate_fields(broken, "vmid")[0]


def test_an_inventory_declaring_no_vmid_is_not_a_pass():
    """Mutation proof for the floor: with the field gone everywhere, the
    uniqueness check above has nothing left to compare."""
    synthetic = {
        "all": {
            "hosts": {
                "fixture-a": {"ansible_host": "10.0.10.211"},
                "fixture-b": {"ansible_host": "10.0.10.212"},
            }
        }
    }
    clashes, declared = duplicate_fields(synthetic, "vmid")
    assert clashes == []
    assert declared == 0

    synthetic["all"]["hosts"]["fixture-a"]["vmid"] = 900
    assert duplicate_fields(synthetic, "vmid") == ([], 1)


def test_a_host_on_a_vip_is_caught(hosts, config):
    broken = copy.deepcopy(hosts)
    first_host_entry(broken)["ansible_host"] = config["cluster_metallb_internal_vip"]
    assert hosts_on_a_vip(broken, config)


def test_a_host_outside_the_lan_is_caught(hosts, config):
    broken = copy.deepcopy(hosts)
    first_host_entry(broken)["ansible_host"] = "192.0.2.7"
    assert hosts_outside_the_lan(broken, config)[0]


def test_an_all_hostname_inventory_is_vacuous_not_green(config):
    """Mutation proof for the floor: names rather than literals compare nothing,
    so the CIDR check must report zero inspected rather than a clean pass."""
    synthetic = {
        "all": {
            "hosts": {
                "fixture-a": {"ansible_host": "fixture-a.example.com"},
                "fixture-b": {"ansible_host": "fixture-b.example.com"},
            }
        }
    }
    assert hosts_outside_the_lan(synthetic, config) == ([], 0)

    synthetic["all"]["hosts"]["fixture-a"]["ansible_host"] = "192.0.2.7"
    outside, compared = hosts_outside_the_lan(synthetic, config)
    assert outside and compared == 1


def test_a_moved_vip_is_caught(all_vars, config):
    assert vip_mirror_mismatches(all_vars, {**config, "cluster_syslog_vip": "10.0.10.99"})
    assert vip_mirror_mismatches({**all_vars, "wg_easy_vip": "10.0.10.98"}, config)


def test_a_moved_ha_home_is_caught(hosts, all_vars):
    broken = copy.deepcopy(all_vars)
    rule = broken["proxmox_ha_rules"][0]
    rule["nodes"] = [
        node.replace(":2", ":1") if node.endswith(":2") else node
        for node in rule["nodes"]
    ]
    rule["nodes"][-1] = rule["nodes"][-1].replace(":1", ":2")
    assert ha_triad_problems(hosts, broken)


def replication_target_set_problems(all_vars: dict) -> list[str]:
    """Each vmid's replication targets, which is the job set's only identity.

    Proxmox permutes the job-id to target pairing on migration, so the drift that
    matters is a duplicate target: `pvesr` refuses the second job to that node.
    """
    jobs = all_vars.get("proxmox_ha_replication_jobs") or []
    if not jobs:
        raise AssertionError(
            "proxmox_ha_replication_jobs is empty — this gate is examining nothing"
        )
    by_vmid: dict[str, list[str]] = {}
    for job in jobs:
        vmid = str(job.get("id", "")).split("-", 1)[0]
        by_vmid.setdefault(vmid, []).append(str(job.get("target_node")))
    problems = []
    for vmid, targets in sorted(by_vmid.items()):
        duplicated = sorted({t for t in targets if targets.count(t) > 1})
        if duplicated:
            problems.append(
                f"vmid {vmid}: {', '.join(duplicated)} appears twice in its "
                "target set — pvesr allows one job per target node"
            )
    return problems


def test_each_vmid_replicates_to_each_target_once(all_vars):
    assert not replication_target_set_problems(all_vars), (
        "replication target sets disagree:\n  "
        + "\n  ".join(replication_target_set_problems(all_vars))
    )


def test_a_duplicated_replication_target_is_caught(all_vars):
    """Mutation case: the set comparison must fire, or it is decorative."""
    broken = copy.deepcopy(all_vars)
    jobs = broken["proxmox_ha_replication_jobs"]
    first = jobs[0]
    twin = next(
        job for job in jobs[1:]
        if str(job["id"]).split("-", 1)[0] == str(first["id"]).split("-", 1)[0]
    )
    twin["target_node"] = first["target_node"]
    assert replication_target_set_problems(broken)


def test_an_emptied_replication_list_is_vacuous_not_green(all_vars):
    with pytest.raises(AssertionError):
        replication_target_set_problems({**all_vars, "proxmox_ha_replication_jobs": []})


def test_a_replication_job_targeting_its_own_source_is_caught(hosts, all_vars):
    broken = copy.deepcopy(all_vars)
    job = broken["proxmox_ha_replication_jobs"][0]
    job["target_node"] = job["source_node"]
    assert ha_triad_problems(hosts, broken)


def test_an_emptied_ha_list_is_vacuous_not_green(hosts, all_vars):
    """A renamed key must fail loudly rather than turn the gate into a pass."""
    with pytest.raises(AssertionError):
        ha_triad_problems(hosts, {**all_vars, "proxmox_ha_rules": []})
    with pytest.raises(AssertionError):
        ha_triad_problems(hosts, {**all_vars, "proxmox_ha_replication_jobs": []})


# AdGuard's per-client rate limiter drops the excess, so a managed host missing
# from the whitelist waits out its resolver timeout. The list is derived from the
# roster, so the check is that the derivation is intact rather than a set compare.

# Every term the derived whitelist expression must still contain. `sort` is what
# holds the order the AdGuard console returns, so losing it is drift too.
_WHITELIST_TERMS = (
    "_dns_addressed_hosts",
    "ansible_host",
    "unique",
    "sort",
)


def ratelimit_whitelist_gaps(dns_vars: dict) -> list[str]:
    roster = str(dns_vars.get("_dns_addressed_hosts") or "")
    whitelist = str(dns_vars.get("adguard_home_ratelimit_whitelist") or "")
    assert whitelist, "adguard_home_ratelimit_whitelist is empty or renamed"
    gaps = [f"the whitelist expression lost `{term}`" for term in _WHITELIST_TERMS
            if term not in whitelist]
    if "groups['all']" not in roster or "ansible_host" not in roster:
        gaps.append("_dns_addressed_hosts no longer selects every addressed host")
    return sorted(gaps)


@pytest.fixture(scope="module")
def dns_vars() -> dict:
    return yaml.safe_load(DNS_VARS_FILE.read_text())


def test_every_managed_host_is_rate_limit_whitelisted(dns_vars):
    assert not ratelimit_whitelist_gaps(dns_vars)


def test_a_hand_written_whitelist_is_caught(dns_vars):
    """Mutation case: going back to a literal list drops a new host silently."""
    literal = dict(dns_vars)
    literal["adguard_home_ratelimit_whitelist"] = ["10.0.0.1", "10.0.0.2"]
    assert ratelimit_whitelist_gaps(literal)


def test_a_narrowed_roster_is_caught(dns_vars):
    """Mutation case: a roster that stops covering every host must fail."""
    narrowed = dict(dns_vars)
    narrowed["_dns_addressed_hosts"] = "{{ groups['proxmox'] | list }}"
    assert ratelimit_whitelist_gaps(narrowed)


# Traefik and the MetalLB speaker both select on the ingress label with a HARD
# nodeSelector. Zero labelled nodes leaves every LoadBalancer Service holding its
# EXTERNAL-IP with nothing announcing it (MetalLBSpeakerNotScheduled, docs/25).
INGRESS_LABEL_FLOOR = 2


def ingress_label(config: dict[str, str]) -> str:
    return f"{config['cluster_node_label_domain']}/ingress"


def ingress_labelled_nodes(inventory: dict, label: str) -> list[str]:
    """k3s nodes carrying the ingress label, by inventory host name."""
    return sorted(
        name
        for name, host in inventory_hosts(inventory).items()
        if str((host.get("k3s_labels") or {}).get(label, "")).lower() == "true"
    )


def _strip_label(node, label: str) -> None:
    if isinstance(node, dict):
        (node.get("k3s_labels") or {}).pop(label, None)
        for value in node.values():
            _strip_label(value, label)
    elif isinstance(node, list):
        for value in node:
            _strip_label(value, label)


def test_enough_nodes_carry_the_ingress_label(hosts, config):
    label = ingress_label(config)
    labelled = ingress_labelled_nodes(hosts, label)
    assert len(labelled) >= INGRESS_LABEL_FLOOR, (
        f"{len(labelled)} node(s) carry {label} ({labelled}); Traefik and the MetalLB "
        f"speaker both require it, so the floor is {INGRESS_LABEL_FLOOR}"
    )


def test_an_unlabelled_fleet_is_caught(hosts, config):
    label = ingress_label(config)
    stripped = copy.deepcopy(hosts)
    _strip_label(stripped, label)
    assert not ingress_labelled_nodes(stripped, label)


# Guest vmid <-> address, and the docs/01 allocation table.

OVERVIEW_DOC = REPO / "docs/01-overview.md"

# Rows of the docs/01 allocation table that hold no inventory host.
HOST_FREE_BANDS = {
    "10.0.10.1-98": "DHCP and workstations, outside this inventory",
    "10.0.10.99-101": "MetalLB VIPs, claimed by no host",
}

_BAND_ROW = re.compile(
    r"^\|\s*(?P<net>\d+\.\d+\.\d+)\.(?P<lo>\d+)-(?P<hi>\d+)\s*\|", re.MULTILINE
)


def vmid_octet_mismatches(inventory: dict) -> tuple[list[str], int]:
    """Guests whose vmid is not the last octet of their address, and the count
    compared. Every guest here is numbered after its address, so a vmid that
    drifts points `qm`/`pct` at another guest than the inventory names."""
    problems = []
    compared = 0
    for name, host_vars in sorted(inventory_hosts(inventory).items()):
        vmid = host_vars.get("vmid")
        addr = host_vars.get("ansible_host")
        if vmid is None or addr is None:
            continue
        try:
            octet = int(str(addr).rsplit(".", 1)[1])
        except (IndexError, ValueError):
            continue  # a name rather than an address
        compared += 1
        if int(vmid) != octet:
            problems.append(f"{name}: {addr} has vmid {vmid}, expected {octet}")
    return problems, compared


def allocation_bands(doc_text: str) -> dict[str, tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]]:
    """Row label -> (first, last) address, from the docs/01 allocation table."""
    bands = {}
    for match in _BAND_ROW.finditer(doc_text):
        net, lo, hi = match["net"], match["lo"], match["hi"]
        bands[f"{net}.{lo}-{hi}"] = (
            ipaddress.ip_address(f"{net}.{lo}"),
            ipaddress.ip_address(f"{net}.{hi}"),
        )
    return bands


def addresses_outside_the_bands(inventory: dict, bands: dict) -> tuple[list[str], int]:
    """Hosts addressed outside every documented band, and the count compared."""
    problems = []
    compared = 0
    for name, host_vars in sorted(inventory_hosts(inventory).items()):
        try:
            address = ipaddress.ip_address(str(host_vars.get("ansible_host")))
        except ValueError:
            continue  # a name rather than an address
        compared += 1
        if not any(lo <= address <= hi for lo, hi in bands.values()):
            problems.append(f"{name}: {address} is in no docs/01 allocation band")
    return problems, compared


def unclaimed_bands(inventory: dict, bands: dict) -> list[str]:
    """Documented bands no host sits in, bar the ones declared host-free."""
    addresses = []
    for host_vars in inventory_hosts(inventory).values():
        try:
            addresses.append(ipaddress.ip_address(str(host_vars.get("ansible_host"))))
        except ValueError:
            continue
    return [
        f"{label} is reserved in docs/01 but holds no inventory host"
        for label, (lo, hi) in bands.items()
        if label not in HOST_FREE_BANDS
        and not any(lo <= address <= hi for address in addresses)
    ]


@pytest.fixture(scope="module")
def bands() -> dict:
    return allocation_bands(OVERVIEW_DOC.read_text(encoding="utf-8"))


def test_every_guest_vmid_matches_its_last_octet(hosts):
    problems, compared = vmid_octet_mismatches(hosts)
    assert compared, f"no host in {HOSTS_FILE} declares both a vmid and an address"
    assert not problems, "\n  ".join(["vmid/address mismatches:", *problems])


def test_a_renumbered_guest_keeping_its_vmid_is_caught(hosts):
    broken = copy.deepcopy(hosts)
    entry = first_host_entry(broken)
    entry["vmid"] = 999
    entry["ansible_host"] = "10.0.10.111"
    assert vmid_octet_mismatches(broken)[0]


def test_an_inventory_with_no_vmid_compares_nothing():
    synthetic = {"all": {"hosts": {"fixture": {"ansible_host": "10.0.10.150"}}}}
    assert vmid_octet_mismatches(synthetic) == ([], 0)


def test_the_allocation_table_parses(bands):
    assert bands, f"{OVERVIEW_DOC} declares no `| 10.x.y.A-B |` allocation rows"
    for label in HOST_FREE_BANDS:
        assert label in bands, (
            f"{label} is declared host-free here but is in no docs/01 row; the "
            "table moved and this exemption is stale"
        )


def test_every_inventory_address_sits_in_a_documented_band(hosts, bands):
    problems, compared = addresses_outside_the_bands(hosts, bands)
    assert compared, f"no host in {HOSTS_FILE} carries an IP literal"
    assert not problems, "\n  ".join(["addresses outside docs/01:", *problems])


def test_every_documented_band_is_claimed(hosts, bands):
    """A band nobody uses over-reserves 10.0.10.0/24 silently."""
    unused = unclaimed_bands(hosts, bands)
    assert not unused, "\n  ".join(["unused allocation bands:", *unused])


def test_an_address_outside_every_band_is_caught(hosts, bands):
    broken = copy.deepcopy(hosts)
    first_host_entry(broken)["ansible_host"] = "10.0.10.180"
    assert addresses_outside_the_bands(broken, bands)[0]


def test_an_unclaimed_band_is_caught(hosts):
    narrowed = {"10.0.10.240-249": (
        ipaddress.ip_address("10.0.10.240"),
        ipaddress.ip_address("10.0.10.249"),
    )}
    assert unclaimed_bands(hosts, narrowed)


# The cpu node label and the selectors that must match it.

K8S_ROOT = REPO / "kubernetes"
CPU_LABEL_VALUES = {"modern", "legacy"}


def cpu_label(config: dict[str, str]) -> str:
    return f"{config['cluster_node_label_domain']}/cpu"


def cpu_labelled_nodes(inventory: dict, label: str) -> dict[str, str]:
    """{inventory host: cpu label value} for every node carrying the label."""
    found = {}
    for name, host in inventory_hosts(inventory).items():
        value = (host.get("k3s_labels") or {}).get(label)
        if value is not None:
            found[name] = str(value)
    return found


def _cpu_labelled_entries(node, label: str):
    if isinstance(node, dict):
        if label in (node.get("k3s_labels") or {}):
            yield node
        for value in node.values():
            yield from _cpu_labelled_entries(value, label)
    elif isinstance(node, list):
        for value in node:
            yield from _cpu_labelled_entries(value, label)


def cpu_label_value_problems(labelled: dict[str, str]) -> list[str]:
    return [
        f"{name}: {label_value!r} is no known cpu class"
        for name, label_value in sorted(labelled.items())
        if label_value not in CPU_LABEL_VALUES
    ]


def _cpu_selector_values(node, rel: str, found: list[tuple[str, str]]) -> None:
    """nodeSelector entries and matchExpressions values keyed on `/cpu`."""
    if isinstance(node, dict):
        selector = node.get("nodeSelector")
        if isinstance(selector, dict):
            for key, value in selector.items():
                if str(key).endswith("/cpu"):
                    found.append((rel, str(value)))
        for expression in node.get("matchExpressions") or []:
            if isinstance(expression, dict) and str(expression.get("key", "")).endswith("/cpu"):
                for value in expression.get("values") or []:
                    found.append((rel, str(value)))
        for value in node.values():
            _cpu_selector_values(value, rel, found)
    elif isinstance(node, list):
        for value in node:
            _cpu_selector_values(value, rel, found)


def cpu_selector_values(root: Path) -> list[tuple[str, str]]:
    """(relative path, selected cpu class) per manifest selecting on the label."""
    found: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.yaml")):
        rel = str(path.relative_to(root.parent))
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            _cpu_selector_values(doc, rel, found)
    return sorted(found)


def unmatched_cpu_selectors(
    selectors: list[tuple[str, str]], labelled: dict[str, str]
) -> list[str]:
    present = set(labelled.values())
    return [
        f"{rel}: selects {value!r}, which no node in {HOSTS_FILE.name} carries"
        for rel, value in selectors
        if value not in present
    ]


def test_nodes_carry_the_cpu_label_under_the_config_prefix(hosts, config):
    """The manifests spell the key as ${cluster_node_label_domain}/cpu, so a
    hosts.yml prefix that differs labels nothing the selectors can see."""
    labelled = cpu_labelled_nodes(hosts, cpu_label(config))
    assert labelled, (
        f"no node in {HOSTS_FILE} carries {cpu_label(config)}; the k8s nodeSelectors "
        "on that key would then match nothing"
    )


def test_every_cpu_label_value_is_a_known_class(hosts, config):
    labelled = cpu_labelled_nodes(hosts, cpu_label(config))
    problems = cpu_label_value_problems(labelled)
    assert not problems, "\n  ".join(["unknown cpu label values:", *problems])


def test_every_cpu_selector_matches_a_labelled_node(hosts, config):
    selectors = cpu_selector_values(K8S_ROOT)
    assert selectors, "no manifest under kubernetes/ selects on /cpu — nothing compared"
    problems = unmatched_cpu_selectors(selectors, cpu_labelled_nodes(hosts, cpu_label(config)))
    assert not problems, "\n  ".join(
        ["cpu selectors no node satisfies, so the workload stays Pending:", *problems]
    )


def test_a_typo_in_a_cpu_selector_is_caught():
    assert unmatched_cpu_selectors(
        [("kubernetes/apps/x/deployment.yaml", "modren")], {"k3s-agt-01": "modern"}
    )
    assert unmatched_cpu_selectors(
        [("kubernetes/apps/x/deployment.yaml", "modern")], {"k3s-agt-01": "modern"}
    ) == []


def test_an_unknown_cpu_label_value_is_caught(hosts, config):
    broken = copy.deepcopy(hosts)
    label = cpu_label(config)
    for node in _cpu_labelled_entries(broken, label):
        node["k3s_labels"][label] = "turbo"
        break
    assert cpu_label_value_problems(cpu_labelled_nodes(broken, label))


def test_both_cpu_selector_forms_are_read(tmp_path):
    """A nodeSelector map and a nodeAffinity matchExpressions list, or the
    comparison above misses half the consumers."""
    root = tmp_path / "kubernetes"
    root.mkdir()
    (root / "deployment.yaml").write_text(
        "kind: Deployment\nspec:\n  template:\n    spec:\n      nodeSelector:\n"
        "        esweiss.com/cpu: modern\n"
    )
    (root / "release.yaml").write_text(
        "kind: HelmRelease\nspec:\n  values:\n    affinity:\n      nodeAffinity:\n"
        "        requiredDuringSchedulingIgnoredDuringExecution:\n"
        "          nodeSelectorTerms:\n"
        "            - matchExpressions:\n"
        "                - key: esweiss.com/cpu\n"
        "                  operator: NotIn\n"
        "                  values: [\"legacy\"]\n"
    )
    assert cpu_selector_values(root) == [
        ("kubernetes/deployment.yaml", "modern"),
        ("kubernetes/release.yaml", "legacy"),
    ]
