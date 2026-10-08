"""Unit tests for the hindsight-reaper CronJob program, loaded by path from
kubernetes/apps/hindsight-reaper/. The guards under test: Failed phase only, old
enough only, uid-preconditioned delete only, never the live Running replica.
"""
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from script_loader import load_path

MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "kubernetes/apps/hindsight-reaper/hindsight-reaper.py"
)
CRONJOB_PATH = MODULE_PATH.parent / "cronjob.yaml"

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def _load():
    return load_path(MODULE_PATH)


@pytest.fixture(scope="module")
def reaper():
    return _load()


def _ts(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pod(name, uid="pod-uid", *, created_minutes_ago=90, reason="UnexpectedAdmissionError",
        bad_timestamp=False, no_timestamp=False, owner="ReplicaSet",
        terminated_reason=None, status_list="containerStatuses"):
    """A Failed pod. `terminated_reason` sets the terminated reason on
    `status_list`, the container-level list that is the only place Kubernetes
    ever writes OOMKilled.
    """
    meta = {"name": name, "uid": uid}
    if not no_timestamp:
        meta["creationTimestamp"] = "not-a-date" if bad_timestamp else _ts(created_minutes_ago)
    if owner is not None:
        meta["ownerReferences"] = [{"kind": owner, "name": name.rsplit("-", 1)[0]}]
    status = {"phase": "Failed", "reason": reason}
    if terminated_reason is not None:
        status[status_list] = [
            {"name": "app", "state": {"terminated": {"reason": terminated_reason}}}
        ]
    return {"metadata": meta, "status": status}


class FakeApi:
    """Records deletes and serves programmed list pages.

    `pages` are served in order to successive GETs, `delete_errors` maps a pod
    name to the exception its DELETE raises, `list_error` raises on the first GET.
    """

    def __init__(self, pages, delete_errors=None, list_error=None):
        self._pages = list(pages)
        self._delete_errors = delete_errors or {}
        self._list_error = list_error
        self.deleted = []            # (name, uid) in delete order
        self.delete_bodies = []      # request bodies, to assert the uid precondition

    def request(self, method, path, body=None):
        if method == "GET":
            if self._list_error is not None:
                raise self._list_error
            page = self._pages.pop(0)
            return {"items": page.get("items", []),
                    "metadata": {"continue": page.get("continue")}}
        if method == "DELETE":
            name = path.rsplit("/", 1)[-1]
            self.delete_bodies.append(body)
            if name in self._delete_errors:
                raise self._delete_errors[name]
            self.deleted.append((name, (body or {}).get("preconditions", {}).get("uid")))
            return {}
        raise AssertionError(f"unexpected {method} {path}")


def _cfg(reaper, **over):
    base = dict(namespace="hindsight", min_age_minutes=30, budget_seconds=60,
                api_timeout_seconds=10, page=50)
    base.update(over)
    return reaper.Config(**base)


def _never_over():
    return False


# --------------------------------------------------------------------------- #
# load_config

def test_load_config_defaults(reaper):
    cfg = reaper.load_config({"NAMESPACE": "hindsight"})
    assert cfg.namespace == "hindsight"
    assert cfg.min_age_minutes == 30
    assert cfg.budget_seconds == 60


def test_load_config_missing_namespace_refuses(reaper):
    with pytest.raises(SystemExit):
        reaper.load_config({})


@pytest.mark.parametrize("bad", [
    {"NAMESPACE": "hindsight", "MIN_AGE_MINUTES": "0"},
    {"NAMESPACE": "hindsight", "MIN_AGE_MINUTES": "-5"},
    {"NAMESPACE": "hindsight", "BUDGET_SECONDS": "0"},
    {"NAMESPACE": "hindsight", "PAGE_LIMIT": "0"},
])
def test_load_config_rejects_nonpositive(reaper, bad):
    with pytest.raises(SystemExit):
        reaper.load_config(bad)


# --------------------------------------------------------------------------- #
# creation_age_minutes

def test_creation_age_absent_is_none(reaper):
    assert reaper.creation_age_minutes({"metadata": {}}, NOW) is None


def test_creation_age_unparseable_is_none(reaper):
    assert reaper.creation_age_minutes(pod("p", bad_timestamp=True), NOW) is None


def test_creation_age_value(reaper):
    assert reaper.creation_age_minutes(pod("p", created_minutes_ago=45), NOW) == 45


# --------------------------------------------------------------------------- #
# reap

def test_deletes_old_failed_pod_with_uid_precondition(reaper):
    api = FakeApi([{"items": [pod("hindsight-abc-1", uid="u1", created_minutes_ago=90)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 1
    assert out.had_errors is False
    assert api.deleted == [("hindsight-abc-1", "u1")]
    # every delete carries the uid precondition + gracePeriodSeconds 0
    assert api.delete_bodies[0]["preconditions"]["uid"] == "u1"
    assert api.delete_bodies[0]["gracePeriodSeconds"] == 0


def test_keeps_young_failed_pod(reaper):
    api = FakeApi([{"items": [pod("hindsight-young", created_minutes_ago=5)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 0
    assert api.deleted == []


def test_keeps_unknown_age_pod(reaper):
    api = FakeApi([{"items": [pod("hindsight-noage", no_timestamp=True)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 0
    assert api.deleted == []


def test_preserves_evicted_pod(reaper):
    # A node-pressure eviction is evidence to investigate, so an old Failed pod
    # carrying it is KEPT despite being past MIN_AGE.
    api = FakeApi([{"items": [pod("hindsight-evicted", reason="Evicted",
                                  created_minutes_ago=300)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 0
    assert api.deleted == []


def test_preserves_oomkilled_pod(reaper):
    # OOMKilled lands on the container status, never on status.reason, so the
    # preserve check has to read both levels.
    api = FakeApi([{"items": [pod("hindsight-oom", reason="Error",
                                  terminated_reason="OOMKilled",
                                  created_minutes_ago=300)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 0
    assert api.deleted == []


@pytest.mark.parametrize("status_list", ["initContainerStatuses", "ephemeralContainerStatuses"])
def test_preserves_oomkilled_init_or_ephemeral_container(reaper, status_list):
    # An init container and a debug ephemeral container get their own status
    # list, so the preserve check reads all three.
    api = FakeApi([{"items": [pod("hindsight-oom", reason="Error",
                                  terminated_reason="OOMKilled",
                                  status_list=status_list,
                                  created_minutes_ago=300)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 0
    assert api.deleted == []


def test_sweeps_a_pod_whose_init_container_reason_is_not_preserved(reaper):
    # Proves the init-container read is a filter, not a blanket keep.
    api = FakeApi([{"items": [pod("hindsight-initerr", reason="Error",
                                  terminated_reason="Error",
                                  status_list="initContainerStatuses",
                                  created_minutes_ago=300)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 1


def test_sweeps_a_pod_whose_container_reason_is_not_preserved(reaper):
    # Proves the container-level read is a filter, not a blanket keep.
    api = FakeApi([{"items": [pod("hindsight-err", reason="Error",
                                  terminated_reason="Error",
                                  created_minutes_ago=300)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 1


def test_keeps_non_replicaset_owned_pod(reaper):
    # A bare/hand-created Failed pod that merely carries the app label is not a
    # Deployment replica, so it is never reaped even when old.
    api = FakeApi([{"items": [pod("hindsight-bare", owner=None, created_minutes_ago=300)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 0
    assert api.deleted == []


def test_is_replicaset_owned(reaper):
    assert reaper.is_replicaset_owned(pod("x")) is True
    assert reaper.is_replicaset_owned(pod("x", owner=None)) is False
    assert reaper.is_replicaset_owned(pod("x", owner="DaemonSet")) is False


def test_budget_stop_leaves_remaining(reaper):
    api = FakeApi([{"items": [pod("hindsight-old", created_minutes_ago=90)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, lambda: True)
    assert out.budget_hit is True
    assert out.deleted == 0
    assert api.deleted == []


@pytest.mark.parametrize("code", [404, 409])
def test_delete_race_is_noop(reaper, code):
    err = urllib.error.HTTPError("u", code, "gone", {}, None)
    api = FakeApi([{"items": [pod("hindsight-raced", created_minutes_ago=90)]}],
                  delete_errors={"hindsight-raced": err})
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 0
    assert out.had_errors is False


def test_delete_hard_error_sets_had_errors(reaper):
    err = urllib.error.HTTPError("u", 500, "boom", {}, None)
    api = FakeApi([{"items": [pod("hindsight-err", created_minutes_ago=90)]}],
                  delete_errors={"hindsight-err": err})
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.had_errors is True


def test_list_failure_sets_had_errors(reaper):
    api = FakeApi([], list_error=urllib.error.URLError("apiserver down"))
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.had_errors is True
    assert out.deleted == 0


def test_run_exit_code(reaper):
    ok = FakeApi([{"items": [pod("hindsight-old", created_minutes_ago=90)]}])
    assert reaper.run(ok, _cfg(reaper), NOW, _never_over) == 0
    bad = FakeApi([], list_error=urllib.error.URLError("down"))
    assert reaper.run(bad, _cfg(reaper), NOW, _never_over) == 1


# --------------------------------------------------------------------------- #
# manifest ⇄ program consistency

def _cronjob() -> dict:
    return yaml.safe_load(CRONJOB_PATH.read_text())


def _container_env() -> dict:
    spec = _cronjob()["spec"]["jobTemplate"]["spec"]
    container = spec["template"]["spec"]["containers"][0]
    return {var["name"]: var["value"] for var in container["env"]}


def test_cronjob_targets_hindsight_namespace(reaper):
    assert _container_env()["NAMESPACE"] == "hindsight"


def test_cronjob_min_age_is_positive_int(reaper):
    # The manifest value must survive load_config's positive-int guard.
    env = {"NAMESPACE": "hindsight", "MIN_AGE_MINUTES": _container_env()["MIN_AGE_MINUTES"]}
    assert reaper.load_config(env).min_age_minutes > 0


def test_active_deadline_covers_retries(reaper):
    """activeDeadlineSeconds must hold backoffLimit+1 attempts of the budget."""
    spec = _cronjob()["spec"]["jobTemplate"]["spec"]
    attempts = spec["backoffLimit"] + 1
    # Program default budget (the manifest sets no BUDGET_SECONDS override).
    budget = reaper.load_config({"NAMESPACE": "hindsight"}).budget_seconds
    assert spec["activeDeadlineSeconds"] >= attempts * budget


def test_image_is_digest_pinned(reaper):
    spec = _cronjob()["spec"]["jobTemplate"]["spec"]
    image = spec["template"]["spec"]["containers"][0]["image"]
    assert "@sha256:" in image


def test_label_selector_targets_hindsight(reaper):
    assert reaper.LABEL == "app.kubernetes.io/name=hindsight"


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class TestKubeApi:
    """The only place the HTTP method, body and headers reach the wire."""

    def _capture(self, reaper, monkeypatch, body: bytes = b'{"items": []}'):
        seen = {}

        def fake_urlopen(req, timeout=None, context=None):
            seen["req"] = req
            seen["timeout"] = timeout
            return _FakeResponse(body)

        monkeypatch.setattr(reaper.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(reaper.ssl, "create_default_context", lambda cafile=None: "ctx")
        return seen

    def test_a_get_carries_the_bearer_token_and_no_body(self, reaper, monkeypatch):
        seen = self._capture(reaper, monkeypatch)
        api = reaper.KubeApi("tok", "/dev/null", 5)
        assert api.request("GET", "/api/v1/pods") == {"items": []}
        req = seen["req"]
        assert req.get_method() == "GET"
        assert req.full_url == reaper.API + "/api/v1/pods"
        assert req.headers["Authorization"] == "Bearer tok"
        assert "Content-type" not in req.headers
        assert req.data is None
        assert seen["timeout"] == 5

    def test_a_body_is_sent_as_json(self, reaper, monkeypatch):
        seen = self._capture(reaper, monkeypatch)
        api = reaper.KubeApi("tok", "/dev/null", 5)
        api.request("DELETE", "/api/v1/namespaces/ns/pods/p", {"preconditions": {"uid": "u"}})
        req = seen["req"]
        assert req.get_method() == "DELETE"
        assert req.headers["Content-type"] == "application/json"
        assert json.loads(req.data.decode()) == {"preconditions": {"uid": "u"}}

    def test_an_empty_response_body_is_an_empty_mapping(self, reaper, monkeypatch):
        self._capture(reaper, monkeypatch, body=b"")
        api = reaper.KubeApi("tok", "/dev/null", 5)
        assert api.request("DELETE", "/api/v1/namespaces/ns/pods/p") == {}

    def test_from_service_account_reads_the_mounted_token(self, reaper, monkeypatch, tmp_path):
        (tmp_path / "token").write_text("mounted-token\n", encoding="utf-8")
        (tmp_path / "ca.crt").write_text("", encoding="utf-8")
        seen = self._capture(reaper, monkeypatch)
        api = reaper.KubeApi.from_service_account(5, sa_dir=str(tmp_path))
        api.request("GET", "/api/v1/pods")
        assert seen["req"].headers["Authorization"] == "Bearer mounted-token"

    def test_an_unreachable_apiserver_propagates(self, reaper, monkeypatch):
        def boom(req, timeout=None, context=None):
            raise urllib.error.URLError("down")

        monkeypatch.setattr(reaper.urllib.request, "urlopen", boom)
        monkeypatch.setattr(reaper.ssl, "create_default_context", lambda cafile=None: "ctx")
        api = reaper.KubeApi("tok", "/dev/null", 5)
        with pytest.raises(urllib.error.URLError):
            api.request("GET", "/api/v1/pods")
