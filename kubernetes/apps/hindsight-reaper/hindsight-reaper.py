#!/usr/bin/env python3
"""Delete admission-rejected (Failed-phase) Hindsight pods left by GPU-node reboots.

Hindsight's llama sidecar requests nvidia.com/gpu, satisfiable only on the single
GPU agent (pve-prec-01). When that node reboots — kured coordinated reboots,
driver reloads — the kubelet starts admitting pods before the NVIDIA device
plugin has re-registered healthy GPUs, so a replacement pod scheduled in that
window is rejected at admission ("no healthy devices present; cannot allocate
unhealthy devices nvidia.com/gpu"). The Deployment recovers correctly once the
GPU is healthy, but each rejected pod lingers in phase=Failed: the ReplicaSet
controller never deletes pods it owns, and cluster pod-GC only fires past a high
cluster-wide threshold, so they accumulate across reboots until swept by hand.

This is that sweep. It lists Failed-phase pods carrying the Hindsight app label in
the hindsight namespace and deletes those older than MIN_AGE_MINUTES (a margin so
a just-failed pod the controllers are still reconciling is never touched). It
NEVER touches a Running/Pending pod, so the live replica is safe, and it KEEPS
Failed pods whose reason is Evicted or OOMKilled — a resource-pressure eviction
on this memory-constrained GPU node is evidence to investigate, not a corpse to
sweep. Root cause and why prevention would need the full GPU Operator:
docs/43-gpu-passthrough.md § Reaping admission-rejected pods.

Mounted into the hindsight-reaper CronJob from the configMapGenerator in this
directory (kustomize refuses generator sources outside the kustomization root,
which is why this is not in scripts/; its tests are
scripts/test_hindsight_reaper.py). Stdlib only — the job runs a bare
python:3-slim image with no pip step.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import NamedTuple
from urllib.parse import urlencode

# Hindsight's Deployment pod-template label; a static app label, so unlike the
# runner reaper's cluster-config placeholder it needs no Flux substitution and
# pytest imports it as-is. Restricting the sweep to labelled pods means an
# unrelated pod that someone `kubectl run`s into the namespace is never reaped.
LABEL = "app.kubernetes.io/name=hindsight"

# Failed pods carrying one of these reasons are diagnostically valuable — a
# node-pressure eviction or OOM on the memory-constrained GPU node is a signal to
# investigate, not a corpse to sweep — so they are KEPT even past MIN_AGE.
# Everything else in phase=Failed on a Deployment-managed pod is a benign
# admission-reject / generic terminal corpse the live replica has superseded.
PRESERVE_REASONS = frozenset({"Evicted", "OOMKilled"})

API = "https://kubernetes.default.svc"
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


class Config(NamedTuple):
    namespace: str
    # A pod is reaped only once it has been Failed longer than this, so a pod the
    # ReplicaSet controller is still reconciling in the current instant is left
    # alone. Admission-rejected pods are terminal the moment they appear, so the
    # margin is pure conservatism, not correctness.
    min_age_minutes: int
    # Soft cap < the Job's activeDeadlineSeconds: stop cleanly and let the next
    # run continue instead of a hard deadline-kill -> failed Job.
    budget_seconds: int
    # Short API timeout so a urlopen started near the budget cannot overrun the
    # hard deadline and defeat the soft stop.
    api_timeout_seconds: int
    # Small page keeps a full page of Pod objects well under the memory limit.
    page: int


def load_config(env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    namespace = env.get("NAMESPACE", "").strip()
    # Fail closed: with no namespace the run would sweep nothing and exit 0,
    # indistinguishable from a clean sweep in the Job history.
    if not namespace:
        raise SystemExit("NAMESPACE is empty — nothing to reap; refusing to run")
    cfg = Config(
        namespace=namespace,
        min_age_minutes=int(env.get("MIN_AGE_MINUTES", "30")),
        budget_seconds=int(env.get("BUDGET_SECONDS", "60")),
        api_timeout_seconds=int(env.get("API_TIMEOUT_SECONDS", "10")),
        page=int(env.get("PAGE_LIMIT", "50")),
    )
    # Fail closed on nonsense knobs: a negative age makes every Failed pod
    # eligible immediately; a zero page or budget disables bounds.
    invalid = {
        name: value
        for name, value in (
            ("MIN_AGE_MINUTES", cfg.min_age_minutes),
            ("BUDGET_SECONDS", cfg.budget_seconds),
            ("API_TIMEOUT_SECONDS", cfg.api_timeout_seconds),
            ("PAGE_LIMIT", cfg.page),
        )
        if value <= 0
    }
    if invalid:
        details = ", ".join(f"{k}={v}" for k, v in sorted(invalid.items()))
        raise SystemExit(f"invalid reaper configuration; values must be positive: {details}")
    return cfg


class KubeApi:
    """kube-apiserver client on the pod's ServiceAccount credentials."""

    def __init__(self, token: str, ca_file: str, timeout: int) -> None:
        self._headers = {"Authorization": f"Bearer {token}"}
        self._ctx = ssl.create_default_context(cafile=ca_file)
        self._timeout = timeout

    @classmethod
    def from_service_account(cls, timeout: int, sa_dir: str = SA_DIR) -> "KubeApi":
        with open(f"{sa_dir}/token") as fh:
            token = fh.read().strip()
        return cls(token, f"{sa_dir}/ca.crt", timeout)

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(self._headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(API + path, method=method, headers=headers, data=data)
        with urllib.request.urlopen(req, timeout=self._timeout, context=self._ctx) as r:
            resp = r.read().decode()
        return json.loads(resp) if resp else {}


def _paged(api, path: str, params: dict, page: int):
    """Yield each list response, following the continue token.

    Paged rather than listed whole so a large backlog cannot exhaust the
    container's memory limit before anything is deleted. urlencode because the
    label key's '/' and the field selector's '=' must be encoded.
    """
    params = dict(params, limit=str(page))
    while True:
        resp = api.request("GET", f"{path}?{urlencode(params)}")
        yield resp
        cont = resp.get("metadata", {}).get("continue")
        if not cont:
            return
        params["continue"] = cont


def iter_failed_pods(api, ns: str, page: int):
    # Server-side filter: only the Hindsight app's Failed-phase pods are returned,
    # so a Running/Pending replica is structurally never a candidate.
    params = {"labelSelector": LABEL, "fieldSelector": "status.phase=Failed"}
    for resp in _paged(api, f"/api/v1/namespaces/{ns}/pods", params, page):
        yield from resp.get("items", [])


def parse_ts(ts: str) -> datetime:
    # K8s RFC3339 ends in 'Z'; fromisoformat needs +00:00.
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def is_replicaset_owned(pod: dict) -> bool:
    """True if a ReplicaSet owns the pod — i.e. it is a Deployment replica, the
    only kind this reaper is scoped to.

    The label + phase selectors already exclude everything but Failed Hindsight
    pods, but a hand-created/bare pod could carry the app label too; requiring a
    ReplicaSet owner keeps the sweep to genuine Deployment replicas (which every
    admission-rejected pod is) and never a standalone pod someone left behind.
    """
    for owner in pod.get("metadata", {}).get("ownerReferences") or []:
        if owner.get("kind") == "ReplicaSet":
            return True
    return False


def creation_age_minutes(pod: dict, now: datetime) -> int | None:
    """Age from creationTimestamp; unparseable/absent -> None (KEEP).

    An admission-rejected pod never starts a container, so it has no
    terminated.finishedAt to age from — creationTimestamp is the only clock, and
    for a pod that fails at admission it is effectively the failure time.
    """
    ts = pod.get("metadata", {}).get("creationTimestamp")
    if not ts:
        return None
    try:
        return int((now - parse_ts(ts)).total_seconds() // 60)
    except (TypeError, ValueError):
        return None


def delete_pod(api, ns: str, name: str, uid: str) -> None:
    # uid precondition: the API rejects (409) the delete if this name now refers
    # to a different, newer pod after a stale list.
    api.request("DELETE", f"/api/v1/namespaces/{ns}/pods/{name}",
                {"apiVersion": "v1", "kind": "DeleteOptions",
                 "gracePeriodSeconds": 0,
                 "preconditions": {"uid": uid}})


class Outcome(NamedTuple):
    deleted: int
    had_errors: bool
    budget_hit: bool


def reap(api, cfg: Config, now: datetime, over_budget, log=print) -> Outcome:
    deleted = 0
    had_errors = False
    ns = cfg.namespace
    try:
        for pod in iter_failed_pods(api, ns, cfg.page):
            if over_budget():
                log(f"BUDGET {cfg.budget_seconds}s reached; "
                    "leaving remaining pods for the next run", flush=True)
                return Outcome(deleted, had_errors, True)
            name = pod["metadata"]["name"]
            if not is_replicaset_owned(pod):
                log(f"KEEP   {ns}/{name} (not ReplicaSet-owned)", flush=True)
                continue
            reason = pod.get("status", {}).get("reason", "")
            if reason in PRESERVE_REASONS:
                log(f"KEEP   {ns}/{name} (reason={reason} preserved for investigation)", flush=True)
                continue
            age = creation_age_minutes(pod, now)
            if age is None:
                log(f"KEEP   {ns}/{name} (age=unknown)", flush=True)
                continue
            if age < cfg.min_age_minutes:
                log(f"KEEP   {ns}/{name} (Failed age={age}m < {cfg.min_age_minutes}m)", flush=True)
                continue
            try:
                delete_pod(api, ns, name, pod["metadata"]["uid"])
                log(f"DELETE {ns}/{name} (Failed reason={reason or '-'} age={age}m)", flush=True)
                deleted += 1
            except urllib.error.HTTPError as e:
                # 404 = already gone; 409 = uid precondition lost. Both no-ops.
                if e.code in (404, 409):
                    log(f"GONE   {ns}/{name} (already deleted or replaced)", flush=True)
                else:
                    log(f"ERROR delete {ns}/{name}: HTTP {e.code}", flush=True)
                    had_errors = True
            except urllib.error.URLError as e:
                log(f"ERROR delete {ns}/{name}: {e}", flush=True)
                had_errors = True
    except urllib.error.URLError as e:
        # A list/paging failure (broken RBAC, API outage) must FAIL the Job, not
        # silently no-op past backoffLimit.
        log(f"ERROR list {ns}: {e}", flush=True)
        had_errors = True
    return Outcome(deleted, had_errors, False)


def run(api, cfg: Config, now: datetime, over_budget, log=print) -> int:
    """Reap the namespace; -> process exit code."""
    outcome = reap(api, cfg, now, over_budget, log)
    log(f"reaper done: deleted={outcome.deleted} "
        f"budget_stop={'yes' if outcome.budget_hit else 'no'}", flush=True)
    # Non-zero so the Job fails, backoffLimit retries, and a persistent failure
    # surfaces (failed Job history + KubeJobFailed) instead of a silent no-op. A
    # clean budget stop exits 0 — that is partial progress, not a fault.
    return 1 if outcome.had_errors else 0


def main() -> int:
    cfg = load_config()
    api = KubeApi.from_service_account(cfg.api_timeout_seconds)
    start = time.monotonic()
    return run(
        api,
        cfg,
        datetime.now(timezone.utc),
        lambda: time.monotonic() - start > cfg.budget_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
