"""Unit tests for the hindsight-reaper CronJob program.

The module lives next to its manifests
(kubernetes/apps/hindsight-reaper/hindsight-reaper.py) because kustomize only
accepts configMapGenerator sources inside the kustomization root, so it is loaded
by path here.

What is under test is the guard set between the CronJob and deleting a pod every
6 hours: only Failed-phase, only old enough, only via a uid-preconditioned
delete, and never the live Running replica (which the server-side phase selector
excludes before the script sees it).
"""
import importlib.util
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "kubernetes/apps/hindsight-reaper/hindsight-reaper.py"
)
CRONJOB_PATH = MODULE_PATH.parent / "cronjob.yaml"

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("hindsight_reaper", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reaper():
    return _load()


def _ts(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pod(name, uid="pod-uid", *, created_minutes_ago=90, reason="UnexpectedAdmissionError",
        bad_timestamp=False, no_timestamp=False, owner="ReplicaSet"):
    meta = {"name": name, "uid": uid}
    if not no_timestamp:
        meta["creationTimestamp"] = "not-a-date" if bad_timestamp else _ts(created_minutes_ago)
    if owner is not None:
        meta["ownerReferences"] = [{"kind": owner, "name": name.rsplit("-", 1)[0]}]
    return {"metadata": meta, "status": {"phase": "Failed", "reason": reason}}


class FakeApi:
    """Records deletes and serves programmed list pages.

    `pages` is a list of {"items": [...], "continue": "tok"|None} served in order
    to successive GET list calls. `delete_errors` maps a pod name to an exception
    the DELETE for it should raise. `list_error` raises on the first GET.
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


@pytest.mark.parametrize("reason", ["Evicted", "OOMKilled"])
def test_preserves_resource_pressure_pods(reaper, reason):
    # A node-pressure eviction / OOM is evidence to investigate, so an old
    # Failed pod carrying such a reason is KEPT despite being past MIN_AGE.
    api = FakeApi([{"items": [pod("hindsight-evicted", reason=reason, created_minutes_ago=300)]}])
    out = reaper.reap(api, _cfg(reaper), NOW, _never_over)
    assert out.deleted == 0
    assert api.deleted == []


def test_preserve_reasons_are_kept_out_of_the_sweep(reaper):
    assert reaper.PRESERVE_REASONS == frozenset({"Evicted", "OOMKilled"})


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
