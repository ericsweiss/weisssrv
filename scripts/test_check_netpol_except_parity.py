"""Tests for scripts/check-netpol-except-parity.py.

Canonical suite: a consumer that vendors the script vendors this file too.
"""
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

mod = load_script("check-netpol-except-parity.py")


def _policy(except_list):
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "allow-egress-thing"},
        "spec": {
            "podSelector": {},
            "policyTypes": ["Egress"],
            "egress": [
                {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": except_list}}]}
            ],
        },
    }


def _write(tmp_path, doc):
    path = tmp_path / "networkpolicy.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


@pytest.mark.parametrize("label", sorted(mod.CANONICAL))
def test_each_canonical_set_passes(tmp_path, label):
    path = _write(tmp_path, _policy(list(mod.CANONICAL[label])))
    assert mod.check_paths([path]) == []


def test_a_dropped_cidr_is_a_violation(tmp_path):
    shortened = mod.RESERVED_FULL[:-1]
    path = _write(tmp_path, _policy(shortened))
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "non-canonical except-list" in violations[0]


def test_reordering_is_a_violation(tmp_path):
    reordered = list(reversed(mod.LAN_FENCE))
    path = _write(tmp_path, _policy(reordered))
    assert mod.check_paths([path])


def test_a_deleted_except_list_is_a_violation(tmp_path):
    """The edit that most directly re-opens the LAN: drop the key entirely."""
    doc = _policy(mod.LAN_FENCE)
    doc["spec"]["egress"][0]["to"][0]["ipBlock"].pop("except")
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "no except-list" in violations[0]


def test_an_emptied_except_list_is_a_violation(tmp_path):
    path = _write(tmp_path, _policy([]))
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "no except-list" in violations[0]


def test_a_narrow_egress_ipblock_needs_no_except(tmp_path):
    doc = _policy(mod.LAN_FENCE)
    peer = doc["spec"]["egress"][0]["to"][0]["ipBlock"]
    peer.pop("except")
    peer["cidr"] = "1.1.1.1/32"
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_an_ingress_except_list_is_not_held_to_the_canon(tmp_path):
    """Ingress is exempt whatever it excludes: the lists fence egress only."""
    doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "allow-wan-ingress"},
        "spec": {
            "podSelector": {},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.0.0.0/8"]}}]}],
        },
    }
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_a_narrow_egress_except_list_is_not_held_to_the_canon(tmp_path):
    doc = _policy(["10.0.10.5/32"])
    doc["spec"]["egress"][0]["to"][0]["ipBlock"]["cidr"] = "10.0.10.0/24"
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_a_substitution_placeholder_cidr_is_not_evaluated(tmp_path):
    """A template spells site CIDRs as `${name}`; they are neither bad nor fenced."""
    doc = _policy(mod.LAN_FENCE)
    doc["spec"]["egress"].append(
        {"to": [{"ipBlock": {"cidr": "${cluster_lan_cidr}"}}], "ports": [{"port": 443}]}
    )
    doc["spec"]["egress"].append(
        {"to": [{"ipBlock": {"cidr": "${cluster_metallb_internal_vip}/32"}}]}
    )
    path = _write(tmp_path, doc)
    violations, scanned, errors = mod.scan_paths([path])
    assert violations == [] and errors == [] and scanned == 1


def test_an_unfenced_ingress_default_route_is_allowed(tmp_path):
    """wg-easy's WAN endpoint: ingress from anywhere is the intended shape."""
    doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "allow-wg-easy-ingress"},
        "spec": {
            "podSelector": {},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}],
        },
    }
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_yml_files_are_scanned(tmp_path):
    (tmp_path / "netpol.yml").write_text(yaml.safe_dump(_policy(["10.0.0.0/8"])))
    assert mod.check_paths([tmp_path])


# --- Bypasses the peer-shaped arms cannot see --------------------------------


def _egress_policy(egress_rules, name="allow-egress-thing", namespace=None):
    meta = {"name": name}
    if namespace:
        meta["namespace"] = namespace
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": meta,
        "spec": {"podSelector": {}, "policyTypes": ["Egress"], "egress": egress_rules},
    }


def test_an_empty_egress_rule_is_a_violation(tmp_path):
    """`egress: [- {}]` allows egress everywhere and leaves no ipBlock behind."""
    path = _write(tmp_path, _egress_policy([{}]))
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "no `to:` peers" in violations[0]


def test_a_ports_only_egress_rule_is_a_violation(tmp_path):
    """Same hole, dressed as a port restriction: `to:` is still absent, so the
    rule allows :443 to every destination including the LAN."""
    doc = _egress_policy([{"ports": [{"protocol": "TCP", "port": 443}]}])
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "no `to:` peers" in violations[0]


def test_a_zero_route_split_into_halves_is_a_violation(tmp_path):
    """0.0.0.0/1 + 128.0.0.0/1 is 0.0.0.0/0 written so no peer ends in `/0`."""
    doc = _egress_policy(
        [
            {
                "to": [
                    {"ipBlock": {"cidr": "0.0.0.0/1"}},
                    {"ipBlock": {"cidr": "128.0.0.0/1"}},
                ]
            }
        ]
    )
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "reaches all of" in violations[0]
    assert "192.168.0.0/16" in violations[0]


def test_a_lone_half_route_is_a_violation(tmp_path):
    """The case an exact-coverage test cannot see: one `0.0.0.0/1` peer is not
    the whole address space, but every fenced range fits inside it, so the pod
    still has the LAN, Tailscale and the metadata address."""
    doc = _egress_policy([{"to": [{"ipBlock": {"cidr": "0.0.0.0/1"}}]}])
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "10.0.0.0/8" in violations[0]
    assert "100.64.0.0/10" in violations[0]
    # ...and the fences that live in the OTHER half are not claimed to be
    # reached: this peer stops at 127.255.255.255.
    assert "192.168.0.0/16" not in violations[0]
    assert "172.16.0.0/12" not in violations[0]


def test_a_lone_block_covering_the_whole_lan_is_a_violation(tmp_path):
    """The narrowest spelling of the same escape: a single `192.168.0.0/16`
    peer never approaches a /0 and hands the pod the entire LAN."""
    doc = _egress_policy(
        [
            {
                "to": [{"ipBlock": {"cidr": "192.168.0.0/16"}}],
                "ports": [{"protocol": "TCP", "port": 22}],
            }
        ]
    )
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "reaches all of 192.168.0.0/16" in violations[0]


def test_split_halves_carrying_the_fence_between_them_clear_the_coverage_arm():
    """The coverage arm asks what is reachable, not how it is spelled: halves
    that between them fence the whole LAN reach none of it.
    """
    lower = [c for c in mod.LAN_FENCE if c.startswith(("10.", "100."))]
    upper = [c for c in mod.LAN_FENCE if c.startswith(("172.", "192.", "169."))]
    assert sorted(lower + upper) == sorted(mod.LAN_FENCE), "the split must be total"
    assert mod.unfenced_reach([("0.0.0.0/1", lower), ("128.0.0.0/1", upper)]) == []


def test_the_coverage_arm_keeps_each_except_with_its_own_peer():
    """A NetworkPolicy `except:` narrows only the ipBlock it is written in.
    Pooling the excepts across a rule would report this leak as fenced."""
    leaky = [("0.0.0.0/1", list(mod.LAN_FENCE[:2])), ("128.0.0.0/1", [])]
    assert "192.168.0.0/16" in mod.unfenced_reach(leaky)


def test_a_fenced_zero_route_plus_a_bare_half_is_a_violation(tmp_path):
    """The mixed shape neither arm owns alone: the /0 peer is canonically
    fenced (so the per-peer arm is happy) and the extra half re-opens the LAN
    the /0's except-list just closed."""
    doc = _egress_policy(
        [
            {
                "to": [
                    {"ipBlock": {"cidr": "0.0.0.0/0", "except": list(mod.LAN_FENCE)}},
                    {"ipBlock": {"cidr": "128.0.0.0/1"}},
                ]
            }
        ]
    )
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "reaches all of" in violations[0]


def test_a_fenced_zero_route_is_reported_once(tmp_path):
    """The per-peer arm owns the plain shape; the coverage arm must not
    double-report the same edit."""
    doc = _egress_policy([{"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}])
    path = _write(tmp_path, doc)
    assert len(mod.check_paths([path])) == 1


def test_narrow_lan_egress_rules_are_untouched(tmp_path):
    """A rule allowing one LAN /32 never covers the space, so the coverage arm
    must not look at it — this is the shape most policies in the repo use."""
    doc = _egress_policy(
        [{"to": [{"ipBlock": {"cidr": "192.168.0.102/32"}}], "ports": [{"port": 2049}]}]
    )
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_selector_only_egress_rules_are_untouched(tmp_path):
    """In-cluster peers are not the LAN-escape shape this gate owns."""
    doc = _egress_policy(
        [{"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}}]}]
    )
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_an_ingress_only_policy_is_not_read_as_egress(tmp_path):
    doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "default-deny-ingress"},
        "spec": {"podSelector": {}, "policyTypes": ["Ingress"]},
    }
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_omitted_policy_types_still_counts_as_egress(tmp_path):
    """The API derives policyTypes from the rules present, so a peer-less
    egress rule opens just as much without the literal declaration."""
    doc = _egress_policy([{}])
    doc["spec"].pop("policyTypes")
    path = _write(tmp_path, doc)
    assert mod.check_paths([path])


def _config(tmp_path, doc) -> Path:
    path = tmp_path / "netpol-except.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def test_an_exemption_is_keyed_on_namespace_and_name(tmp_path):
    """An allowlisted peer-less rule passes, and only under its own key."""
    key = "ci/job-egress"
    ns, name = key.split("/")
    policy = mod.load_config(
        _config(tmp_path, {"unrestricted_egress_ok": {key: "job pods deploy"}})
    )
    ok = _write(tmp_path, _egress_policy([{}], name=name, namespace=ns))
    assert mod.check_paths([ok], policy) == []
    other = tmp_path / "other"
    other.mkdir()
    moved = _write(other, _egress_policy([{}], name=name, namespace="downloads"))
    assert mod.check_paths([moved], policy)


def test_a_namespaceless_policy_is_allowlisted_as_default(tmp_path):
    """An omitted namespace reads as `default`, matching every sibling gate."""
    policy = mod.load_config(
        _config(
            tmp_path,
            {"unrestricted_egress_ok": {"default/job-egress": "job pods deploy"}},
        )
    )
    ok = _write(tmp_path, _egress_policy([{}], name="job-egress"))
    assert mod.check_paths([ok], policy) == []
    other = tmp_path / "other"
    other.mkdir()
    fenced = _write(other, _egress_policy([{}], name="job-egress", namespace="ci"))
    assert mod.check_paths([fenced], policy)


def test_a_loaded_config_does_not_leak_into_the_next_caller(tmp_path):
    """The Policy is a value: loading one config must not change what a caller
    holding the default policy sees."""
    policy = mod.load_config(
        _config(tmp_path, {"canonical_except_lists": {"site": ["10.0.0.0/8"]}})
    )
    assert policy.canonical == {"site": ["10.0.0.0/8"]}
    assert mod.Policy().canonical == mod.CANONICAL


def test_the_allowlist_is_empty_without_a_config(tmp_path):
    """Fail-closed: an undeclared peer-less egress rule is a violation."""
    path = _write(tmp_path, _egress_policy([{}], name="job-egress", namespace="ci"))
    assert mod.check_paths([path])


def test_a_reasonless_exemption_is_rejected(tmp_path):
    with pytest.raises(mod.ConfigError, match="has no reason"):
        mod.load_config(_config(tmp_path, {"unrestricted_egress_ok": {"ci/x": ""}}))


def test_a_config_replaces_the_canonical_sets(tmp_path):
    policy = mod.load_config(
        _config(tmp_path, {"canonical_except_lists": {"site": ["10.0.0.0/8"]}})
    )
    assert policy.canonical == {"site": ["10.0.0.0/8"]}
    assert mod.classify(["10.0.0.0/8"], policy) == "site"
    assert mod.classify(mod.RESERVED_FULL, policy) is None


def test_a_config_replaces_the_fence_networks(tmp_path):
    policy = mod.load_config(_config(tmp_path, {"fence_networks": ["172.16.0.0/12"]}))
    assert mod.unfenced_reach([("192.168.0.0/16", [])], policy) == []
    assert mod.unfenced_reach([("172.16.0.0/12", [])], policy) == ["172.16.0.0/12"]


def test_containment_math():
    """A block inside a fence range stays silent; one that contains a fence
    range is reported, whatever its prefix length.
    """
    assert mod.unfenced_reach([("0.0.0.0/0", [])]) == sorted(
        str(f) for f in mod.FENCE_NETS if f.version == 4
    )
    assert mod.unfenced_reach([("192.168.0.0/16", [])]) == ["192.168.0.0/16"]
    assert mod.unfenced_reach([("192.168.0.0/15", [])]) == ["192.168.0.0/16"]
    assert mod.unfenced_reach([("192.168.0.0/24", [])]) == []
    assert mod.unfenced_reach([("192.168.0.102/32", [])]) == []
    assert mod.unfenced_reach([("1.1.1.1/32", [])]) == []
    assert mod.unfenced_reach([]) == []
    # v6 is fenced by its own analogues, and the two families never mix.
    assert mod.unfenced_reach([("::/0", [])]) == ["fc00::/7", "fe80::/10"]


def test_classify_returns_the_matching_label():
    assert mod.classify(mod.RESERVED_FULL) == "reserved-full"
    assert mod.classify(mod.LAN_FENCE) == "lan-fence"
    assert mod.classify(["10.0.0.0/8"]) is None


class TestTheGateRefusesToBeVacuous:
    """A run that inspects nothing must not report green."""

    def test_a_directory_with_no_policies_exits_two(self, tmp_path, capsys):
        (tmp_path / "notes.yaml").write_text("kind: ConfigMap\n")
        assert mod.main([str(tmp_path)]) == 2
        assert "0 NetworkPolicy manifests" in capsys.readouterr().err

    def test_an_empty_directory_exits_two(self, tmp_path, capsys):
        """A renamed manifest subtree leaves the gate pointed at nothing."""
        assert mod.main([str(tmp_path)]) == 2
        assert "is not a gate" in capsys.readouterr().err

    def test_a_nonexistent_path_is_an_operator_error_not_a_traceback(self, tmp_path, capsys):
        assert mod.main([str(tmp_path / "gone")]) == 2
        assert "no such file or directory" in capsys.readouterr().err

    def test_a_scanned_corpus_reports_its_count(self, tmp_path, capsys):
        _write(tmp_path, _policy(list(mod.LAN_FENCE)))
        assert mod.main([str(tmp_path)]) == 0
        assert "across 1 policy/policies" in capsys.readouterr().out

    def test_an_unparseable_manifest_is_an_operator_error_not_a_drift_finding(
        self, tmp_path, capsys
    ):
        """Exit 1 means the fence drifted; a file that does not parse says
        nothing about any except-list, and the headline must not send the reader
        looking for a CIDR."""
        _write(tmp_path, _policy(list(mod.LAN_FENCE)))
        (tmp_path / "broken.yaml").write_text("egress: [oops\n")
        rc = mod.main([str(tmp_path)])
        err = capsys.readouterr().err
        assert rc == 2
        assert "unparseable YAML" in err
        assert "drifted from the canonical sets" not in err
        # PyYAML's mark names the file, not "<unicode string>".
        assert "broken.yaml" in err and "<unicode string>" not in err


def test_the_violation_message_names_the_config_key(tmp_path):
    """It must point at `unrestricted_egress_ok` in --config, not at a module
    constant — the script is a vendored copy, so editing it is what the
    vendored-copy gate fails on."""
    path = _write(tmp_path, _egress_policy([{}], name="job-egress", namespace="ci"))
    violations = mod.check_paths([path])
    assert violations
    assert "unrestricted_egress_ok" in violations[0]
    assert "UNRESTRICTED_EGRESS_OK" not in violations[0]


def test_a_missing_config_file_is_an_operator_error(tmp_path, capsys):
    """Same rule as a missing scan path — reported, not a traceback."""
    assert mod.main(["--config", str(tmp_path / "gone.yaml"), str(tmp_path)]) == 2
    assert "--config" in capsys.readouterr().err


class TestAMalformedConfigIsAnOperatorError:
    """Every broken-config shape exits 2; exit 1 means the LAN fence drifted."""

    def _run(self, tmp_path, text, capsys):
        config = tmp_path / "netpol.yaml"
        config.write_text(text)
        rc = mod.main(["--config", str(config), str(tmp_path)])
        return rc, capsys.readouterr().err

    def test_a_non_mapping_top_level(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "- a\n- b\n", capsys)
        assert rc == 2
        assert "must be a mapping" in err

    def test_a_malformed_canonical_list(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, 'canonical_except_lists: "oops"\n', capsys)
        assert rc == 2
        assert "canonical_except_lists" in err

    def test_an_unparseable_cidr_in_fence_networks(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "fence_networks: [not-a-cidr]\n", capsys)
        assert rc == 2
        assert "fence_networks" in err

    def test_a_reasonless_exemption(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "unrestricted_egress_ok:\n  ci/x: ''\n", capsys)
        assert rc == 2
        assert "has no reason" in err

    def test_unparseable_yaml(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "a: [1\n", capsys)
        assert rc == 2
        assert "--config" in err


def test_a_fence_assembled_from_smaller_peers_is_still_reached():
    """Two /17s covering a fenced /16 must not slip past a per-block subnet
    test — reachability is judged on the collapsed union."""
    reached = mod.unfenced_reach(
        [("192.168.0.0/17", []), ("192.168.128.0/17", [])]
    )
    assert "192.168.0.0/16" in reached


def test_a_deficient_configured_canonical_list_still_trips_the_fence(tmp_path):
    """canonical_except_lists is site data: a configured list that omits a
    fence network satisfies the per-peer equality arm, so containment must
    run on literal /0 rules too."""
    deficient = [c for c in mod.LAN_FENCE if not c.startswith("192.168.")]
    policy = mod.Policy(canonical={"lan-fence": deficient})
    doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "egress-deficient", "namespace": "ns"},
        "spec": {
            "podSelector": {},
            "policyTypes": ["Egress"],
            "egress": [
                {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": deficient}}]}
            ],
        },
    }
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path], policy)
    assert any("192.168.0.0/16" in v for v in violations), violations


# --- malformed manifests are operator errors, never fence findings -----------


def test_a_null_egress_rule_is_an_operator_error(tmp_path, capsys):
    """A `- ` with nothing after it deserializes to None. Exit 1 means the fence
    drifted, so a shape the API rejects must exit 2 and name the file."""
    (tmp_path / "netpol.yaml").write_text(
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata: {name: broken, namespace: ns}\n"
        "spec:\n  podSelector: {}\n  policyTypes: [Egress]\n  egress:\n    -\n"
    )
    rc = mod.main([str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "netpol.yaml" in err
    assert "not a mapping" in err


def test_a_non_list_peer_key_is_an_operator_error(tmp_path, capsys):
    (tmp_path / "netpol.yaml").write_text(
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata: {name: broken, namespace: ns}\n"
        "spec:\n  podSelector: {}\n  policyTypes: [Egress]\n"
        "  egress:\n    - to: oops\n"
    )
    assert mod.main([str(tmp_path)]) == 2
    assert "not a list" in capsys.readouterr().err


def test_an_unparseable_cidr_is_an_operator_error(tmp_path, capsys):
    """A typo'd cidr contributes nothing to the reachability analysis, so it
    must be reported rather than silently dropped."""
    path = _write(tmp_path, _policy(["10.0.0/8"] + mod.LAN_FENCE[1:]))
    assert mod.main([str(path)]) == 2
    assert "unparseable CIDR" in capsys.readouterr().err


def test_a_zero_prefix_written_as_double_zero_still_needs_a_fence(tmp_path):
    """`/00` is a zero-length prefix, judged numerically like `/0`."""
    doc = _policy(mod.LAN_FENCE)
    peer = doc["spec"]["egress"][0]["to"][0]["ipBlock"]
    peer.pop("except")
    peer["cidr"] = "0.0.0.0/00"
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "no except-list" in violations[0]


def test_an_empty_policytypes_list_takes_the_inferred_default(tmp_path):
    """`policyTypes` is omitempty, so an empty list reaches the API as an absent
    field: egress rules are present, so the policy IS an Egress policy."""
    doc = _egress_policy([{}])
    doc["spec"]["policyTypes"] = []
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) != []


# --- the corpora the gate judges, and the peer with no constraint ------------


def test_an_empty_to_peer_allows_every_destination(tmp_path):
    """`to: [- {}]` is a non-empty peer list carrying no constraint at all, so
    Kubernetes reads it as every destination."""
    path = _write(tmp_path, _egress_policy([{"to": [{}]}]))
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "EMPTY `to:` peer" in violations[0]
    assert "EVERY destination" in violations[0]


def test_a_ports_only_to_peer_allows_every_destination(tmp_path):
    """The same hole dressed as a port peer: `ports:` lives on the rule, so a
    peer spelling it carries none of the three constraints."""
    doc = _egress_policy([{"to": [{"ports": [{"port": 443}]}]}])
    path = _write(tmp_path, doc)
    assert "EMPTY `to:` peer" in mod.check_paths([path])[0]


def test_a_selector_only_to_peer_is_not_the_empty_peer(tmp_path):
    """`podSelector: {}` selects every pod in the namespace — a real peer."""
    doc = _egress_policy([{"to": [{"podSelector": {}}]}])
    assert mod.check_paths([_write(tmp_path, doc)]) == []


def test_an_empty_peer_beside_a_fenced_block_is_still_reported(tmp_path):
    doc = _egress_policy(
        [{"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": list(mod.LAN_FENCE)}}, {}]}]
    )
    assert "EMPTY `to:` peer" in mod.check_paths([_write(tmp_path, doc)])[0]


def test_json_manifests_are_scanned(tmp_path):
    """A JSON manifest is a manifest; globbing YAML alone scanned nothing."""
    import json as _json

    (tmp_path / "netpol.json").write_text(_json.dumps(_policy(["10.0.0.0/8"])))
    violations, scanned, errors = mod.scan_paths([tmp_path])
    assert scanned == 1 and errors == [] and len(violations) == 1


def test_a_json_list_manifest_is_scanned(tmp_path):
    import json as _json

    (tmp_path / "netpol.json").write_text(_json.dumps([_policy(["10.0.0.0/8"])]))
    assert mod.scan_paths([tmp_path])[1] == 1


def test_an_unparseable_json_manifest_is_an_operator_error(tmp_path, capsys):
    (tmp_path / "netpol.json").write_text(
        '{"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",\n'
    )
    assert mod.main([str(tmp_path)]) == 2
    assert "unparseable JSON" in capsys.readouterr().err


def test_an_unparseable_json_file_named_outright_is_still_an_error(tmp_path, capsys):
    """Whatever it holds, a file the gate was pointed at is its subject."""
    path = tmp_path / "dashboard.json"
    path.write_text("{\n")
    assert mod.main([str(path)]) == 2
    assert "unparseable JSON" in capsys.readouterr().err


def test_a_json_file_of_another_shape_is_not_a_manifest(tmp_path):
    """The default scan root holds the Grafana dashboards, and a netpol-fence
    gate reporting a dashboard is a wrong attribution."""
    import json as _json

    (tmp_path / "dashboard.json").write_text(
        _json.dumps({"title": "Cluster", "panels": [{"type": "timeseries"}]})
    )
    (tmp_path / "netpol.json").write_text(_json.dumps(_policy(["10.0.0.0/8"])))
    violations, scanned, errors = mod.scan_paths([tmp_path])
    assert scanned == 1 and errors == [] and len(violations) == 1


def test_a_broken_dashboard_does_not_red_the_gate(tmp_path):
    """A dashboard is hand-edited; a stray comma in one must not read as the
    NetworkPolicy corpus being unreadable."""
    (tmp_path / "dashboard.json").write_text('{"title": "Cluster",,}')
    (tmp_path / "netpol.yaml").write_text(yaml.safe_dump(_policy(["10.0.0.0/8"])))
    violations, scanned, errors = mod.scan_paths([tmp_path])
    assert errors == [] and scanned == 1 and len(violations) == 1


def test_a_placeholder_tree_passes_but_its_render_is_judged(tmp_path, capsys):
    """The LAN-escape arm examines nothing while the CIDR is a placeholder; the
    rendered corpus is where the escape becomes visible."""
    doc = _egress_policy([{"to": [{"ipBlock": {"cidr": "${cluster_lan_cidr}"}}]}])
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []

    corpus = tmp_path / "rendered.yaml"
    corpus.write_text(yaml.safe_dump(doc).replace("${cluster_lan_cidr}", "10.0.0.0/8"))
    assert mod.main(["--corpus", str(corpus)]) == 1
    assert "reaches all of 10.0.0.0/8" in capsys.readouterr().out


def test_a_leftover_placeholder_in_a_corpus_is_an_operator_error(tmp_path, capsys):
    """In a rendered stream a `${...}` CIDR is a substitution the render missed."""
    doc = _egress_policy([{"to": [{"ipBlock": {"cidr": "${cluster_lan_cidr}"}}]}])
    corpus = tmp_path / "rendered.yaml"
    corpus.write_text(yaml.safe_dump(doc))
    assert mod.main(["--corpus", str(corpus)]) == 2
    assert "unparseable CIDR" in capsys.readouterr().err


def test_a_corpus_on_stdin_is_scanned(monkeypatch, capsys):
    import io

    doc = _egress_policy([{"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}])
    monkeypatch.setattr("sys.stdin", io.StringIO(yaml.safe_dump(doc)))
    assert mod.main(["--corpus", "-"]) == 1
    assert "<stdin>" in capsys.readouterr().out


def test_a_policy_less_corpus_is_an_operator_error(tmp_path, capsys):
    """A render that never reached the policies must not read as a pass."""
    corpus = tmp_path / "rendered.yaml"
    corpus.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata: {name: settings}\n")
    assert mod.main(["--corpus", str(corpus)]) == 2
    assert "scanned 0 NetworkPolicy manifests" in capsys.readouterr().err


def test_a_missing_corpus_file_is_an_operator_error(tmp_path, capsys):
    assert mod.main(["--corpus", str(tmp_path / "gone.yaml")]) == 2
    assert "--corpus" in capsys.readouterr().err
