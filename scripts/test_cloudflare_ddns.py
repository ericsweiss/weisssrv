"""Unit tests for the cloudflare-ddns CronJob program.

The module lives next to its manifests
(kubernetes/infrastructure/configs/cloudflare-ddns/cloudflare-ddns.py) because
kustomize only accepts configMapGenerator sources inside the kustomization
root, so it is loaded by path here.
"""
import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "kubernetes/infrastructure/configs/cloudflare-ddns/cloudflare-ddns.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("cloudflare_ddns", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ddns():
    return _load()


def test_module_exists():
    assert MODULE_PATH.is_file(), f"{MODULE_PATH} missing — the CronJob mounts it"


def test_records_cover_the_four_wan_names(ddns):
    # ZONE is the ${cluster_external_domain} placeholder in the raw file (Flux
    # substitutes it at reconcile), so assert the record SHAPE around it rather
    # than a hard-coded zone — which is also what keeps this test honest if the
    # site's external domain changes.
    zone = ddns.ZONE
    assert zone == "${cluster_external_domain}"
    names = {name for name, _ in ddns.RECORDS}
    assert names == {zone, f"direct.{zone}", f"git.{zone}", f"vpn.{zone}"}
    # Only the apex is proxied on create; the rest need a direct origin.
    assert dict(ddns.RECORDS)[zone] is True
    assert not any(proxied for name, proxied in ddns.RECORDS if name != zone)


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
    """The PUT replaces the WHOLE record, so a decoration this script omits is
    erased on the next address change — and reads afterwards as Terraform drift
    rather than as a DDNS bug. `comment` was already carried; `tags` and
    `settings` are the two that were being silently dropped."""
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
    """The RECORDS analogue of ct's blank `DDNS_RECORDS` / stray `:false`."""
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
    monkeypatch.setattr(ddns, "zone_is_substituted", lambda *a, **k: True)
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


def test_zone_guard_rejects_the_raw_placeholder(ddns):
    # The file on disk carries the placeholder, so this is the exact value a
    # failed Flux substitution would leave behind.
    assert ddns.zone_is_substituted(ddns.ZONE) is False
    assert ddns.zone_is_substituted("") is False
    assert ddns.zone_is_substituted("nodots") is False
    assert ddns.zone_is_substituted("ericsweiss.com") is True


def test_main_refuses_to_touch_dns_with_an_unsubstituted_zone(ddns, monkeypatch):
    """Empty/placeholder zone -> `GET /zones?name=` is unfiltered; fail before that."""
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    called = []
    monkeypatch.setattr(ddns, "get_public_ip", lambda *a, **k: called.append("ip") or "1.2.3.4")
    monkeypatch.setattr(ddns, "api_request", lambda *a, **k: called.append("api"))
    assert ddns.main() == 1
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
