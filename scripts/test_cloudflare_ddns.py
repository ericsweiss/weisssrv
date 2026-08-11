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
    names = {name for name, _ in ddns.RECORDS}
    assert names == {
        "ericsweiss.com",
        "direct.ericsweiss.com",
        "git.ericsweiss.com",
        "vpn.ericsweiss.com",
    }
    # Only the apex is proxied on create; the rest need a direct origin.
    assert dict(ddns.RECORDS)["ericsweiss.com"] is True
    assert not any(proxied for name, proxied in ddns.RECORDS if name != "ericsweiss.com")


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


def test_build_record_body_on_create_uses_the_literal_proxied(ddns):
    body = ddns.build_record_body("ericsweiss.com", "198.51.100.7", True, None)
    assert body["proxied"] is True
    assert body["ttl"] == 1
    assert "comment" not in body


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


def test_main_fails_without_a_token(ddns, monkeypatch):
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    assert ddns.main() == 1
