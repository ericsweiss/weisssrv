"""Every Traefik Middleware and ServersTransport reference resolves.

An unresolvable reference fails CLOSED: the router is disabled and the host 404s.
Both shapes are collected: IngressRoute CRs and the Traefik chart's own values.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
K8S_ROOT = REPO / "kubernetes"


def _yaml_files(root: Path) -> list[Path]:
    return sorted({*root.rglob("*.yaml"), *root.rglob("*.yml")})


def _traefik_objects(root: Path) -> tuple[dict, list[str], int]:
    """({defined, refs}, unparsed files, files parsed) under `root`.

    A reference with no namespace resolves in the referring document's own
    namespace, which is how Traefik reads it.
    """
    defined: dict[str, set] = {"Middleware": set(), "ServersTransport": set()}
    refs: dict[str, set] = {"Middleware": set(), "ServersTransport": set()}
    unreadable: list[str] = []
    parsed = 0
    for path in _yaml_files(root):
        try:
            docs = list(yaml.safe_load_all(path.read_text()))
        except (OSError, yaml.YAMLError) as exc:
            unreadable.append(f"{path.name}: {exc}")
            continue
        parsed += 1
        where = str(path.relative_to(root))
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            meta = doc.get("metadata") or {}
            home = meta.get("namespace")
            kind = doc.get("kind")
            if kind in defined:
                defined[kind].add((home, meta.get("name")))
            elif kind == "IngressRoute":
                for route in (doc.get("spec") or {}).get("routes") or []:
                    for entry in route.get("middlewares") or []:
                        refs["Middleware"].add(
                            (entry.get("namespace") or home, entry.get("name"), where)
                        )
                    for service in route.get("services") or []:
                        name = service.get("serversTransport")
                        if name:
                            refs["ServersTransport"].add((home, name, where))
            elif kind == "HelmRelease":
                values = (doc.get("spec") or {}).get("values") or {}
                for block in (values.get("ingressRoute") or {}).values():
                    if not isinstance(block, dict):
                        continue
                    for entry in block.get("middlewares") or []:
                        refs["Middleware"].add(
                            (entry.get("namespace") or home, entry.get("name"), where)
                        )
    return {"defined": defined, "refs": refs}, unreadable, parsed


def _dangling(collected: dict) -> list[str]:
    problems = []
    for kind, found in collected["refs"].items():
        known = collected["defined"][kind]
        for namespace, name, where in sorted(found):
            if (namespace, name) not in known:
                problems.append(f"{where} references {kind} {namespace}/{name}")
    return problems


@pytest.fixture(scope="module")
def shipped() -> tuple[dict, list[str], int]:
    return _traefik_objects(K8S_ROOT)


def test_both_sides_of_the_check_are_populated(shipped):
    """An emptied side would pass the resolution arm comparing nothing."""
    collected, unreadable, parsed = shipped
    assert parsed, "parsed no manifests under kubernetes/"
    assert not unreadable, f"unparsed manifests shrink this check: {unreadable}"
    assert collected["refs"]["Middleware"], "found no middleware references"
    assert collected["defined"]["Middleware"], "found no Middleware definitions"
    assert collected["refs"]["ServersTransport"], "found no serversTransport references"
    assert collected["defined"]["ServersTransport"], "found no ServersTransport definitions"


def test_every_traefik_middleware_reference_resolves(shipped):
    problems = _dangling(shipped[0])
    assert not problems, (
        "Traefik cannot resolve these, so it disables the whole router and the "
        f"front door 404s: {problems}"
    )


def test_a_dangling_traefik_reference_is_reported(tmp_path):
    """Mutation case: the collector, not just the shipped manifests."""
    (tmp_path / "defined.yaml").write_text(
        "kind: Middleware\nmetadata:\n  name: hsts-header\n  namespace: traefik\n"
        "---\nkind: ServersTransport\nmetadata:\n  name: vm-tls\n  namespace: vm\n"
    )
    (tmp_path / "routes.yaml").write_text(
        "kind: IngressRoute\nmetadata:\n  name: app\n  namespace: vm\n"
        "spec:\n  routes:\n    - middlewares:\n"
        "        - name: hsts-header\n          namespace: traefik\n"
        "        - name: typo-header\n          namespace: traefik\n"
        "      services:\n        - name: backend\n          serversTransport: vm-tls\n"
    )
    (tmp_path / "release.yaml").write_text(
        "kind: HelmRelease\nmetadata:\n  name: traefik\n  namespace: traefik\n"
        "spec:\n  values:\n    ingressRoute:\n      dashboard:\n        middlewares:\n"
        "          - name: gone-strict\n            namespace: traefik\n"
    )
    collected, unreadable, parsed = _traefik_objects(tmp_path)
    assert not unreadable and parsed == 3
    problems = _dangling(collected)
    assert any("typo-header" in problem for problem in problems)
    assert any("gone-strict" in problem for problem in problems)
    assert not any("hsts-header" in problem for problem in problems)
    assert not any("vm-tls" in problem for problem in problems)


def test_a_namespaceless_reference_resolves_in_its_own_namespace(tmp_path):
    """Traefik defaults an unqualified reference to the IngressRoute's namespace."""
    (tmp_path / "all.yaml").write_text(
        "kind: Middleware\nmetadata:\n  name: local-auth\n  namespace: apps\n"
        "---\nkind: IngressRoute\nmetadata:\n  name: app\n  namespace: apps\n"
        "spec:\n  routes:\n    - middlewares:\n        - name: local-auth\n"
        "---\nkind: IngressRoute\nmetadata:\n  name: other\n  namespace: elsewhere\n"
        "spec:\n  routes:\n    - middlewares:\n        - name: local-auth\n"
    )
    problems = _dangling(_traefik_objects(tmp_path)[0])
    assert problems == ["all.yaml references Middleware elsewhere/local-auth"]


def test_an_unparseable_manifest_is_reported(tmp_path):
    (tmp_path / "broken.yaml").write_text("kind: Middleware\n  name: [oops\n")
    _, unreadable, parsed = _traefik_objects(tmp_path)
    assert unreadable and parsed == 0
