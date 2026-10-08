"""Unit tests for the cloudflare-ddns CronJob program, loaded by path."""
import os
from pathlib import Path

import pytest
from script_loader import load_path

REPO = Path(__file__).resolve().parent.parent
DDNS_DIR = REPO / "kubernetes/infrastructure/configs/cloudflare-ddns"
MODULE_PATH = DDNS_DIR / "cloudflare-ddns.py"
CRONJOB_PATH = DDNS_DIR / "cronjob.yaml"

# The program is configured only by env, so the suite supplies the env the
# CronJob supplies. Any zone with a dot in it exercises the same code paths.
TEST_ZONE = "example.test"
TEST_RECORDS = "@, direct:false, git:false, vpn:false"


def _load(zone=TEST_ZONE, records=TEST_RECORDS):
    previous = {key: os.environ.get(key) for key in ("DDNS_ZONE", "DDNS_RECORDS")}
    os.environ["DDNS_ZONE"] = zone
    os.environ["DDNS_RECORDS"] = records
    try:
        return load_path(MODULE_PATH)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="module")
def ddns():
    return _load()


def test_module_exists():
    assert MODULE_PATH.is_file(), f"{MODULE_PATH} missing — the CronJob mounts it"


def test_the_program_carries_no_site_data(ddns):
    """Zone and record list come from env only. A built-in list would keep
    working after the CronJob stopped passing one, hiding the breakage."""
    source = MODULE_PATH.read_text()
    assert "DEFAULT_RECORDS" not in source
    assert "esweiss" not in source
    assert "cluster_external_domain" not in source
    assert ddns.ZONE == TEST_ZONE
    assert ddns.RECORDS == (
        (TEST_ZONE, True),
        (f"direct.{TEST_ZONE}", False),
        (f"git.{TEST_ZONE}", False),
        (f"vpn.{TEST_ZONE}", False),
    )


def test_an_unset_record_list_leaves_nothing_to_manage(capsys):
    """RECORDS parses to empty, which records_are_valid then refuses."""
    unset = _load(records="")
    assert unset.RECORDS == ()
    assert unset.records_are_valid(unset.RECORDS) is False
    assert "RECORDS is empty" in capsys.readouterr().err


def test_parse_records_reads_the_cronjob_spec(ddns):
    """The CronJob supplies the record list, so the parser is the contract."""
    zone = ddns.ZONE
    parsed = ddns.parse_records("@, direct:false, git:false, vpn:false")
    assert parsed == (
        (zone, True),
        (f"direct.{zone}", False),
        (f"git.{zone}", False),
        (f"vpn.{zone}", False),
    )
    # A bare label is proxied by default; ':false' turns it off.
    assert ddns.parse_records("www") == ((f"www.{zone}", True),)
    assert ddns.parse_records("www:false") == ((f"www.{zone}", False),)
    # Whitespace and empty entries between commas are ignored.
    assert ddns.parse_records(" www , ,") == ((f"www.{zone}", True),)


def test_parse_records_keeps_a_malformed_entry_for_the_validator(ddns):
    """Dropping one would silently stop updating a WAN name."""
    for spec in ("a:bogus", "b:false:oops"):
        parsed = ddns.parse_records(spec)
        assert parsed == (spec,), parsed
        assert ddns.records_are_valid(parsed) is False


def test_parse_records_rejects_an_empty_or_dot_edged_label(ddns):
    """A label like '' or '.' would build `.zone` and POST it as a new record."""
    for spec in (":false", " :false", ".:false", "www.:false", "a..b:false"):
        parsed = ddns.parse_records(spec)
        assert ddns.records_are_valid(parsed) is False, parsed
    # A mixed spec still fails closed, so no run manages a partial list.
    assert ddns.records_are_valid(ddns.parse_records(" :false, www")) is False


def test_the_cronjob_passes_both_values_from_cluster_config(ddns):
    """The env wiring is the whole configuration, so it is the contract."""
    import yaml

    cronjob = yaml.safe_load(CRONJOB_PATH.read_text())
    containers = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e.get("value") for e in containers[0]["env"]}
    assert env["DDNS_ZONE"] == "${cluster_external_domain}"
    assert env["DDNS_RECORDS"] == "${cluster_ddns_records}"

    config = yaml.safe_load(
        (REPO / "kubernetes/infrastructure/sources/cluster-config.yaml").read_text()
    )
    spec = config["data"]["cluster_ddns_records"]
    assert ddns.records_are_valid(ddns.parse_records(spec, zone=TEST_ZONE)) is True


def test_get_public_ip_skips_non_global_and_non_ipv4(ddns, monkeypatch):
    replies = {"a": "10.1.2.3", "b": "not-an-ip", "c": "203.0.113.9", "d": "8.8.4.4"}
    monkeypatch.setattr(ddns, "_fetch_ip", lambda url, timeout=10: replies[url])
    assert ddns.get_public_ip(services=("a", "b", "c", "d"), attempts=1) == "8.8.4.4"


def test_get_public_ip_retries_then_gives_up(ddns):
    calls = []

    def boom(url, timeout=10):
        calls.append(url)
        raise OSError("no route to host")

    module = _load()
    module._fetch_ip = boom
    slept = []
    assert module.get_public_ip(services=("a",), attempts=3, sleep=slept.append) is None
    assert len(calls) == 3
    assert slept == [module.IP_RETRY_SLEEP, module.IP_RETRY_SLEEP]


def test_build_record_body_preserves_terraform_owned_fields(ddns):
    existing = {"ttl": 60, "proxied": True, "comment": "managed by terraform"}
    body = ddns.build_record_body("git.ericsweiss.com", "198.51.100.7", False, existing)
    assert body == {
        "type": "A",
        "name": "git.ericsweiss.com",
        "content": "198.51.100.7",
        "ttl": 60,
        "proxied": True,
        "comment": "managed by terraform",
    }


def test_build_record_body_preserves_every_terraform_owned_decoration(ddns):
    """The PUT replaces the WHOLE record, so every Terraform-owned decoration must
    be carried forward; one this script omits is erased on the next address
    change and reads afterwards as Terraform drift."""
    existing = {
        "ttl": 60,
        "proxied": True,
        "comment": "Terraform-owned",
        "tags": ["managed"],
        "settings": {"ipv4_only": True},
    }
    body = ddns.build_record_body("git.ericsweiss.com", "198.51.100.7", False, existing)
    assert body == {
        "type": "A",
        "name": "git.ericsweiss.com",
        "content": "198.51.100.7",
        "ttl": 60,
        "proxied": True,
        "comment": "Terraform-owned",
        "tags": ["managed"],
        "settings": {"ipv4_only": True},
    }


def test_build_record_body_drops_null_decorations_but_keeps_empty_ones(ddns):
    """Cloudflare returns an unset decoration as null — sending it back would be
    a type error — while a deliberately-empty one is a value the record has."""
    body = ddns.build_record_body(
        "vpn.ericsweiss.com",
        "198.51.100.7",
        False,
        {"ttl": 1, "proxied": False, "comment": None, "tags": [], "settings": {}},
    )
    assert "comment" not in body
    assert body["tags"] == []
    assert body["settings"] == {}


def test_build_record_body_on_create_uses_the_literal_proxied(ddns):
    body = ddns.build_record_body("ericsweiss.com", "198.51.100.7", True, None)
    assert body["proxied"] is True
    assert body["ttl"] == 1
    assert not any(key in body for key in ddns.PRESERVED_FIELDS)


def test_update_record_is_a_noop_when_the_ip_is_unchanged(ddns, monkeypatch):
    seen = []

    def fake_api(token, url, method="GET", data=None, timeout=30):
        seen.append((method, url))
        return {"success": True, "result": [{"id": "r1", "content": "198.51.100.7"}]}

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.update_record("tok", "z1", "git.ericsweiss.com", "198.51.100.7", False) is True
    assert [m for m, _ in seen] == ["GET"]


def test_update_record_posts_when_the_record_is_absent(ddns, monkeypatch):
    seen = []

    def fake_api(token, url, method="GET", data=None, timeout=30):
        seen.append((method, data))
        if method == "GET":
            return {"success": True, "result": []}
        return {"success": True}

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.update_record("tok", "z1", "vpn.ericsweiss.com", "198.51.100.7", False) is True
    assert seen[-1][0] == "POST"


def test_update_record_puts_the_preserved_decorations_end_to_end(ddns, monkeypatch):
    """build_record_body is only half the guarantee — this is the body that
    actually reaches the wire on the PUT path."""
    seen = []

    def fake_api(token, url, method="GET", data=None, timeout=30):
        seen.append((method, url, data))
        if method == "GET":
            return {
                "success": True,
                "result": [
                    {
                        "id": "r1",
                        "content": "198.51.100.1",
                        "ttl": 60,
                        "proxied": True,
                        "comment": "Terraform-owned",
                        "tags": ["managed"],
                        "settings": {"ipv4_only": True},
                    }
                ],
            }
        return {"success": True}

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.update_record("tok", "z1", "git.ericsweiss.com", "198.51.100.7", False) is True
    method, url, data = seen[-1]
    assert method == "PUT"
    assert url.endswith("/dns_records/r1")
    assert data["content"] == "198.51.100.7"
    assert data["comment"] == "Terraform-owned"
    assert data["tags"] == ["managed"]
    assert data["settings"] == {"ipv4_only": True}


def test_update_record_refuses_an_ambiguous_multi_record_name(ddns, monkeypatch, capsys):
    """Two A records for one name is ambiguous ownership: updating just the
    first leaves the stale sibling answering intermittently — a round-robin half
    outage — and picking one at all guesses which record Terraform owns."""
    seen = []

    def fake_api(token, url, method="GET", data=None, timeout=30):
        seen.append((method, url))
        return {
            "success": True,
            "result": [
                {"id": "r1", "content": "198.51.100.1"},
                {"id": "r2", "content": "198.51.100.2"},
            ],
        }

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.update_record("tok", "z1", "vpn.ericsweiss.com", "203.0.113.9", False) is False
    # Only the query happened: nothing was written, not even the first record.
    assert [m for m, _ in seen] == ["GET"]
    captured = capsys.readouterr()
    assert "ambiguous ownership" in captured.err
    # stdout is the per-record report a healthy run also writes; a refusal buried
    # there reads as success.
    assert "ambiguous ownership" not in captured.out


def test_records_are_valid_rejects_empty_and_nameless_lists(ddns, capsys):
    """An empty RECORDS list, or any entry with no name, must be rejected."""
    assert ddns.records_are_valid(ddns.RECORDS) is True
    assert ddns.records_are_valid(()) is False
    assert "RECORDS is empty" in capsys.readouterr().err
    for bad in ((("", False),), (("   ", True),), ((None, False),),
                (("git.ericsweiss.com", False), ("", True))):
        assert ddns.records_are_valid(bad) is False
        assert "no record name" in capsys.readouterr().err


def test_records_are_valid_rejects_malformed_entry_shapes(ddns, capsys):
    """A stray 2-char string unpacks as (name, proxied) and its first character
    would pass the blank-name check — shape is validated before content."""
    for bad in (("ab",), (("git", False, "extra"),), (("git",),), (42,)):
        assert ddns.records_are_valid(bad) is False
        assert "not a (name, proxied) pair" in capsys.readouterr().err
    assert ddns.records_are_valid((("git", "yes"),)) is False
    assert "non-boolean proxied flag" in capsys.readouterr().err
    # Terraform-style list entries stay accepted — the shape check must not
    # tighten pair to tuple-only.
    assert ddns.records_are_valid((["git", True],)) is True


def _main_past_the_zone_guard(ddns, monkeypatch, records):
    """Drive main() past the token and zone guards, making every outbound call
    fatal to the assertion: the point is that nothing is reached."""
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    monkeypatch.setattr(ddns, "RECORDS", records)
    calls = []
    monkeypatch.setattr(ddns, "get_public_ip", lambda *a, **k: calls.append("ip") or "203.0.113.9")
    monkeypatch.setattr(ddns, "api_request", lambda *a, **k: calls.append("api"))
    return calls


def test_main_fails_closed_on_an_empty_record_list(ddns, monkeypatch, capsys):
    """An empty list would exit 0 having managed no DNS at all — a green CronJob
    that publishes nothing is the failure nobody notices."""
    calls = _main_past_the_zone_guard(ddns, monkeypatch, ())
    assert ddns.main() == 1
    assert calls == []
    assert "RECORDS is empty" in capsys.readouterr().err


def test_main_fails_closed_on_a_nameless_record_entry(ddns, monkeypatch, capsys):
    """A blank name sends `?type=A&name=` — the unfiltered-list shape get_zone_id
    already refuses for zones, which here would rewrite an arbitrary record."""
    calls = _main_past_the_zone_guard(
        ddns, monkeypatch, (("git.ericsweiss.com", False), ("", True))
    )
    assert ddns.main() == 1
    # Rejected before the first API call, and before even detecting the IP.
    assert calls == []
    assert "no record name" in capsys.readouterr().err


def test_main_fails_without_a_token(ddns, monkeypatch):
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    assert ddns.main() == 1


def test_zone_guard_rejects_empty_and_unsubstituted_zones(ddns):
    assert ddns.zone_is_valid("") is False
    assert ddns.zone_is_valid("nodots") is False
    # Exactly what a failed Flux substitution leaves in the env var.
    assert ddns.zone_is_valid("${cluster_external_domain}") is False
    assert ddns.zone_is_valid(TEST_ZONE) is True


def test_main_refuses_to_touch_dns_with_an_unsubstituted_zone(monkeypatch):
    """Empty/placeholder zone -> `GET /zones?name=` is unfiltered; fail before that."""
    unsubstituted = _load(zone="${cluster_external_domain}")
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    called = []
    monkeypatch.setattr(
        unsubstituted, "get_public_ip", lambda *a, **k: called.append("ip") or "1.2.3.4"
    )
    monkeypatch.setattr(unsubstituted, "api_request", lambda *a, **k: called.append("api"))
    assert unsubstituted.main() == 1
    assert called == []


def test_get_zone_id_rejects_a_zone_the_api_did_not_actually_match(ddns, monkeypatch):
    """The unfiltered-list shape: rows come back, none of them ours."""
    monkeypatch.setattr(
        ddns,
        "api_request",
        lambda *a, **k: {"success": True, "result": [{"id": "other", "name": "not-ours.com"}]},
    )
    assert ddns.get_zone_id("tok", "ericsweiss.com") is None


def test_get_zone_id_returns_the_matching_zone(ddns, monkeypatch):
    monkeypatch.setattr(
        ddns,
        "api_request",
        lambda *a, **k: {
            "success": True,
            "result": [{"id": "other", "name": "not-ours.com"}, {"id": "z1", "name": "ericsweiss.com"}],
        },
    )
    assert ddns.get_zone_id("tok", "ericsweiss.com") == "z1"


def test_update_record_percent_encodes_the_record_name_in_the_query(ddns, monkeypatch):
    """A name carrying `&` or `+` must not break out of the `name=` parameter."""
    seen = []

    def fake_api(token, url, method="GET", data=None, timeout=30):
        seen.append(url)
        return {"success": True, "result": [{"id": "r1", "content": "198.51.100.7"}]}

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.update_record("tok", "z1", "a+b&zone_id=evil.ericsweiss.com", "198.51.100.7", False) is True
    assert seen[0].endswith("name=a%2Bb%26zone_id%3Devil.ericsweiss.com")
    assert "&zone_id=" not in seen[0]


def test_get_zone_id_percent_encodes_the_zone_in_the_query(ddns, monkeypatch):
    seen = []

    def fake_api(token, url, method="GET", data=None, timeout=30):
        seen.append(url)
        return {"success": True, "result": [{"id": "z1", "name": "a+b&x=1.com"}]}

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.get_zone_id("tok", "a+b&x=1.com") == "z1"
    assert seen[0].endswith("/zones?name=a%2Bb%26x%3D1.com")
