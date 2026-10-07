"""Tests for scripts/unifi-settings-drift.py, the console-owned UniFi posture gate."""

from __future__ import annotations

import ast
import json
import ssl
from pathlib import Path

import pytest
from script_loader import load_path, load_script

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "unifi-settings-drift.py"
CONFIG = SCRIPTS / "unifi-settings.json"


def _load():
    return load_path(SCRIPT)


@pytest.fixture(scope="module")
def gate():
    return _load()


@pytest.fixture(scope="module")
def parity():
    return load_script("check-unifi-doc-parity.py")


# Every key of the live IPS section the expectation pins. The volatile ones
# (_id, key, site_id, utm_token, last_alert_id) are deliberately absent.
PINNED_KEYS = {
    "advanced_filtering_preference",
    "enabled_categories",
    "enabled_networks",
    "endpoint_scanning",
    "honeypot_enabled",
    "ips_mode",
    "memory_optimized",
    "restrict_torrents",
}
VOLATILE_KEYS = {"_id", "key", "site_id", "utm_token", "last_alert_id"}


class TestShippedConfig:
    def test_it_loads_and_pins_inline_ips(self, gate, parity):
        cfg = gate.load_config(CONFIG)
        assert cfg["site"]
        declared = parity.terraform_settings(
            (SCRIPTS.parent / parity.MAIN_TF).read_text()
        )
        assert cfg["desired"]["ips_mode"] == declared["ips_mode"], (
            f"scripts/unifi-settings.json must follow {parity.MAIN_TF}, which "
            "declares the standing IPS posture (docs/46 § Site settings)"
        )

    def test_it_pins_the_whole_ips_posture(self, gate):
        """ips_mode alone passes a console that has lost every category."""
        desired = gate.load_config(CONFIG)["desired"]
        assert set(desired) == PINNED_KEYS
        assert len(desired["enabled_categories"]) == 34
        assert desired["enabled_networks"]

    def test_it_pins_no_volatile_key(self, gate):
        """A per-console id or the utm token would red the gate on any rebuild."""
        assert not VOLATILE_KEYS & set(gate.load_config(CONFIG)["desired"])

    def test_the_pinned_lists_are_stored_sorted(self, gate):
        """Comparison is order-insensitive; storing them sorted keeps diffs readable."""
        desired = gate.load_config(CONFIG)["desired"]
        for key, want in desired.items():
            if isinstance(want, list):
                assert want == sorted(want), f"{key} is not stored sorted"

    def test_a_removed_category_is_drift(self, gate):
        """The mutation case: a category dropped in the console must fail."""
        desired = gate.load_config(CONFIG)["desired"]
        live = dict(desired)
        live["enabled_categories"] = desired["enabled_categories"][1:]
        drift = gate.diff_settings(live, desired)
        assert drift and "enabled_categories" in drift[0]

    def test_the_shipped_expectation_matches_itself(self, gate):
        """The live section read back unchanged must be clean, whatever the order."""
        desired = gate.load_config(CONFIG)["desired"]
        live = {
            key: list(reversed(value)) if isinstance(value, list) else value
            for key, value in desired.items()
        }
        live.update({"utm_token": "x" * 64, "site_id": "0" * 24})
        assert gate.diff_settings(live, desired) == []

    def test_a_config_without_desired_is_rejected(self, gate, tmp_path):
        path = tmp_path / "unifi-settings.json"
        path.write_text(json.dumps({"site": "default"}))
        with pytest.raises(ValueError):
            gate.load_config(path)

    def test_an_empty_desired_is_rejected(self, gate, tmp_path):
        """An empty expectation would report OK against any live console."""
        path = tmp_path / "unifi-settings.json"
        path.write_text(json.dumps({"site": "default", "desired": {}}))
        with pytest.raises(ValueError):
            gate.load_config(path)


class TestEndpoint:
    def test_it_reads_the_ips_section_only(self, gate):
        url = gate.settings_url("https://10.0.1.1/", "default")
        assert url == "https://10.0.1.1/proxy/network/api/s/default/get/setting/ips"

    def test_it_never_addresses_rest_setting(self, gate):
        """/rest/setting returns the device-SSH password and the site API token."""
        assert "rest/setting" not in gate.settings_url("https://10.0.1.1", "default")
        tree = ast.parse(SCRIPT.read_text())
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        urls = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and "rest/setting" in node.value
        ]
        assert not urls, f"the gate builds an unsectioned settings URL: {urls}"


class TestDiff:
    def test_a_matching_section_is_clean(self, gate):
        assert gate.diff_settings({"ips_mode": "ips", "other": 1}, {"ips_mode": "ips"}) == []

    def test_detection_mode_is_drift(self, gate):
        """The mutation case: the console silently back on IDS must fail."""
        drift = gate.diff_settings({"ips_mode": "ids"}, {"ips_mode": "ips"})
        assert drift and "ips_mode" in drift[0]

    def test_a_missing_key_is_drift(self, gate):
        drift = gate.diff_settings({}, {"ips_mode": "ips"})
        assert drift and "absent" in drift[0]

    def test_lists_compare_unordered(self, gate):
        assert gate.diff_settings({"cats": ["b", "a"]}, {"cats": ["a", "b"]}) == []
        assert gate.diff_settings({"cats": ["a"]}, {"cats": ["a", "b"]})

    def test_a_reported_value_is_truncated(self, gate):
        drift = gate.diff_settings({"cats": ["x" * 200]}, {"cats": ["a"]})
        assert drift and len(drift[0]) < 300


class TestCredentialHandling:
    def test_it_refuses_to_run_without_credentials(self, gate, monkeypatch, capsys):
        for var in ("UNIFI_API_URL", "UNIFI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert gate.main([]) == 2
        assert "UNIFI_API_URL" in capsys.readouterr().out

    def test_an_api_failure_reports_the_type_not_the_body(self, gate, monkeypatch, capsys):
        monkeypatch.setenv("UNIFI_API_URL", "https://10.0.1.1")
        monkeypatch.setenv("UNIFI_API_KEY", "x" * 20)

        def boom(*_args, **_kwargs):
            raise RuntimeError("secret-bearing body")

        monkeypatch.setattr(gate, "fetch_section", boom)
        assert gate.main([]) == 2
        assert "secret-bearing" not in capsys.readouterr().out


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture()
def urlopen_spy(gate, monkeypatch):
    """Capture the ssl context fetch_section builds and serve a canned body."""
    seen: dict = {}

    def fake(_req, timeout=None, context=None):
        seen["context"] = context
        return _FakeResponse(seen.get("payload", {"data": [{"ips_mode": "ips"}]}))

    monkeypatch.setattr(gate.urllib.request, "urlopen", fake)
    return seen


def test_tls_verification_is_on_unless_opted_out(gate, monkeypatch, urlopen_spy):
    """The insecure path is an explicit opt-in, never the default."""
    monkeypatch.delenv("UNIFI_ALLOW_INSECURE", raising=False)
    monkeypatch.setenv("UNIFI_API_URL", "https://unifi.invalid")
    monkeypatch.setenv("UNIFI_API_KEY", "k")
    gate.main([])
    assert urlopen_spy["context"].verify_mode == ssl.CERT_REQUIRED
    assert urlopen_spy["context"].check_hostname is True

    monkeypatch.setenv("UNIFI_ALLOW_INSECURE", "1")
    gate.main([])
    assert urlopen_spy["context"].verify_mode == ssl.CERT_NONE
    assert urlopen_spy["context"].check_hostname is False


class TestFetchSection:
    """The shape guard: several sections in one body must not be compared."""

    def test_a_well_formed_body_returns_the_section(self, gate, urlopen_spy):
        urlopen_spy["payload"] = {"data": [{"ips_mode": "ips"}]}
        assert gate.fetch_section("https://unifi.invalid", "k") == {"ips_mode": "ips"}

    @pytest.mark.parametrize(
        "payload",
        [
            {"data": [{"ips_mode": "ips"}, {"ips_mode": "ids"}]},
            {"data": []},
            {"data": {"ips_mode": "ips"}},
        ],
    )
    def test_an_unexpected_shape_is_refused(self, gate, urlopen_spy, payload):
        urlopen_spy["payload"] = payload
        with pytest.raises(RuntimeError, match="unexpected response shape"):
            gate.fetch_section("https://unifi.invalid", "k")


class TestMain:
    """The 0/1 wiring between diff_settings() and the gate's verdict."""

    def _env(self, monkeypatch):
        monkeypatch.setenv("UNIFI_API_URL", "https://unifi.invalid")
        monkeypatch.setenv("UNIFI_API_KEY", "k")

    def test_a_matching_console_is_clean(self, gate, monkeypatch, capsys):
        self._env(monkeypatch)
        desired = dict(gate.load_config(gate.DEFAULT_CONFIG)["desired"])
        desired["_volatile"] = "ignored"
        monkeypatch.setattr(gate, "fetch_section", lambda *a, **k: desired)
        assert gate.main([]) == 0
        assert "OK: site" in capsys.readouterr().out

    def test_a_flipped_ips_mode_is_drift(self, gate, monkeypatch, capsys):
        """Mutation case: fails if the verdict is inverted or the diff
        arguments are swapped."""
        self._env(monkeypatch)
        live = dict(gate.load_config(gate.DEFAULT_CONFIG)["desired"], ips_mode="ids")
        monkeypatch.setattr(gate, "fetch_section", lambda *a, **k: live)
        assert gate.main([]) == 1
        out = capsys.readouterr().out
        assert "DRIFT" in out and "ips_mode" in out
