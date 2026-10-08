"""Every IngressRoute's TLS secret is issued by a Certificate for its hostname.

Traefik serves whatever the route's own namespace holds, so a cert for another
hostname is rejected by the browser; a commonName also has a 64-byte ceiling.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
KUBERNETES = REPO / "kubernetes"

MANIFEST_GLOBS = ("*.yaml", "*.yml")
# Hostnames compare on the `${cluster_*}` placeholder spelling: Flux substitutes
# them at reconcile time, so that is how both sides are written here.
HOST_MATCH = re.compile(r"Host\(`([^`]+)`\)")

# "<namespace>/<route>": reason — a route deliberately served by another name's
# certificate. Each entry claims a redirect-only or passthrough route.
EXEMPT: dict[str, str] = {}


def _manifests(root: Path) -> list[Path]:
    return sorted(p for glob in MANIFEST_GLOBS for p in root.rglob(glob))


def _documents(path: Path) -> list[dict]:
    try:
        return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    except (OSError, yaml.YAMLError) as error:
        raise AssertionError(f"{path}: {error}") from error


def effective_namespace(path: Path, doc: dict) -> str | None:
    """`metadata.namespace`, else the `namespace:` a parent kustomization injects."""
    explicit = (doc.get("metadata") or {}).get("namespace")
    if explicit:
        return str(explicit)
    for directory in [path.parent, *path.parent.parents]:
        kustomization = directory / "kustomization.yaml"
        if kustomization.is_file():
            injected = (yaml.safe_load(kustomization.read_text()) or {}).get("namespace")
            if injected:
                return str(injected)
        if directory == KUBERNETES:
            break
    return None


CLUSTER_CONFIG = KUBERNETES / "infrastructure/sources/cluster-config.yaml"
# X.509 upper bound on a Common Name. cert-manager rejects the CSR above it, so
# a hostname that fits dnsNames can still fail to issue.
CN_LIMIT = 64


def cluster_identity() -> dict[str, str]:
    """The `${cluster_*}` values Flux substitutes, so lengths measure the real
    hostname rather than the placeholder spelling."""
    data = (yaml.safe_load(CLUSTER_CONFIG.read_text()) or {}).get("data") or {}
    return {str(k): str(v) for k, v in data.items()}


def common_names(root: Path) -> list[tuple[str, str]]:
    """(document label, substituted commonName) for every Certificate carrying one."""
    identity = cluster_identity()
    out = []
    for path in _manifests(root):
        for doc in _documents(path):
            if doc.get("kind") != "Certificate":
                continue
            raw = (doc.get("spec") or {}).get("commonName")
            if not raw:
                continue
            resolved = str(raw)
            for key, value in identity.items():
                resolved = resolved.replace("${" + key + "}", value)
            name = (doc.get("metadata") or {}).get("name") or "<unnamed>"
            out.append((f"{path.relative_to(root.parent)}:{name}", resolved))
    return out


def over_long_common_names(root: Path) -> list[str]:
    return [
        f"{label}: commonName {name!r} is {len(name.encode())} bytes (limit {CN_LIMIT})"
        for label, name in common_names(root)
        if len(name.encode()) > CN_LIMIT
    ]


def certificate_hostnames(root: Path) -> dict[tuple[str | None, str], set[str]]:
    """(namespace, secretName) -> the dnsNames the Certificate issues it for."""
    issued: dict[tuple[str | None, str], set[str]] = {}
    for path in _manifests(root):
        for doc in _documents(path):
            if doc.get("kind") != "Certificate":
                continue
            spec = doc.get("spec") or {}
            secret = spec.get("secretName")
            if not secret:
                continue
            names = {str(n) for n in (spec.get("dnsNames") or [])}
            issued.setdefault((effective_namespace(path, doc), str(secret)), set()).update(names)
    return issued


def route_hostnames(root: Path) -> list[tuple[str, str | None, str | None, set[str]]]:
    """(route id, namespace, tls secretName, Host literals) for every IngressRoute."""
    routes = []
    for path in _manifests(root):
        for doc in _documents(path):
            if doc.get("kind") != "IngressRoute":
                continue
            spec = doc.get("spec") or {}
            name = (doc.get("metadata") or {}).get("name") or "<unnamed>"
            namespace = effective_namespace(path, doc)
            hosts: set[str] = set()
            for rule in spec.get("routes") or []:
                hosts |= set(HOST_MATCH.findall(str((rule or {}).get("match") or "")))
            secret = (spec.get("tls") or {}).get("secretName")
            routes.append((f"{namespace}/{name}", namespace, secret, hosts))
    return routes


def covers(host: str, dns_name: str) -> bool:
    """One cert dnsName against one route hostname, wildcards included.

    A `*.x` leg covers exactly one label above `x`, so `*.esweiss.com` serves
    `app.esweiss.com` and not `a.b.esweiss.com`.
    """
    if host == dns_name:
        return True
    if not dns_name.startswith("*."):
        return False
    suffix = dns_name[2:]
    if not host.endswith("." + suffix):
        return False
    return "." not in host[: -len(suffix) - 1]


def mismatches(root: Path) -> list[str]:
    """Routes whose TLS secret is missing, or issued for another hostname."""
    issued = certificate_hostnames(root)
    problems = []
    for route, namespace, secret, hosts in route_hostnames(root):
        if route in EXEMPT:
            continue
        if not hosts:
            continue
        if not secret:
            problems.append(f"{route}: serves {sorted(hosts)} with no spec.tls.secretName")
            continue
        names = issued.get((namespace, secret))
        if names is None:
            problems.append(
                f"{route}: spec.tls.secretName {secret} has no Certificate in "
                f"namespace {namespace} — Traefik serves its default self-signed cert"
            )
            continue
        uncovered = sorted(h for h in hosts if not any(covers(h, n) for n in names))
        if uncovered:
            problems.append(
                f"{route}: {secret} is issued for {sorted(names)}, which does not "
                f"cover {uncovered}"
            )
    return problems


@pytest.fixture(scope="module")
def corpus() -> tuple[dict, list]:
    issued = certificate_hostnames(KUBERNETES)
    routes = route_hostnames(KUBERNETES)
    assert len(issued) > 5, f"only {len(issued)} Certificate secrets found — the walk broke"
    assert len(routes) > 20, f"only {len(routes)} IngressRoutes found — the walk broke"
    return issued, routes


def test_every_route_is_served_by_a_certificate_for_its_hostname(corpus):
    problems = mismatches(KUBERNETES)
    assert not problems, "IngressRoutes served by the wrong certificate:\n  " + "\n  ".join(
        problems
    )


def test_no_common_name_exceeds_the_x509_ceiling():
    """A commonName is capped at 64 bytes while dnsNames is not, so a long
    hostname that validates here fails at issuance with no manifest diff."""
    names = common_names(KUBERNETES)
    assert names, "no Certificate carries a commonName — the walk broke"
    problems = over_long_common_names(KUBERNETES)
    assert not problems, (
        "Certificates whose commonName cannot be issued (drop commonName and let "
        "dnsNames be the single source):\n  " + "\n  ".join(problems)
    )


def test_an_over_long_common_name_is_reported(tmp_path: Path):
    """Mutation case: the ceiling is measured, not assumed."""
    long_host = "a" * 60 + ".${cluster_external_domain}"
    write(tmp_path, "kubernetes/apps/demo/certificate.yaml",
          CERT.replace("spec:\n", f"spec:\n  commonName: {long_host}\n"))
    assert over_long_common_names(tmp_path / "kubernetes")


def test_exemptions_name_a_live_route(corpus):
    _issued, routes = corpus
    live = {route for route, _ns, _secret, _hosts in routes}
    stale = sorted(set(EXEMPT) - live)
    assert not stale, f"EXEMPT names routes that are gone: {stale}"


def test_a_wildcard_covers_one_label_only():
    assert covers("app.${cluster_internal_domain}", "*.${cluster_internal_domain}")
    assert not covers("a.b.${cluster_internal_domain}", "*.${cluster_internal_domain}")
    assert covers("${cluster_internal_domain}", "${cluster_internal_domain}")
    assert not covers("${cluster_internal_domain}", "*.${cluster_internal_domain}")


def write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


CERT = """\
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: demo-tls
  namespace: demo
spec:
  secretName: demo-tls
  dnsNames:
    - demo.${cluster_external_domain}
"""

ROUTE = """\
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: demo
  namespace: demo
spec:
  routes:
    - match: Host(`demo.${cluster_external_domain}`)
      kind: Rule
  tls:
    secretName: demo-tls
"""


def test_a_matching_pair_is_not_reported(tmp_path: Path):
    write(tmp_path, "kubernetes/apps/demo/certificate.yaml", CERT)
    write(tmp_path, "kubernetes/apps/demo/ingressroute.yaml", ROUTE)
    assert mismatches(tmp_path / "kubernetes") == []


def test_a_hostname_the_certificate_does_not_carry_is_reported(tmp_path: Path):
    """Mutation case: the route is renamed and the Certificate is not."""
    write(tmp_path, "kubernetes/apps/demo/certificate.yaml", CERT)
    write(
        tmp_path,
        "kubernetes/apps/demo/ingressroute.yaml",
        ROUTE.replace("Host(`demo.", "Host(`renamed."),
    )
    problems = mismatches(tmp_path / "kubernetes")
    assert len(problems) == 1
    assert "does not cover ['renamed.${cluster_external_domain}']" in problems[0]


def test_a_secret_with_no_certificate_is_reported(tmp_path: Path):
    write(tmp_path, "kubernetes/apps/demo/ingressroute.yaml", ROUTE)
    problems = mismatches(tmp_path / "kubernetes")
    assert len(problems) == 1
    assert "has no Certificate in namespace demo" in problems[0]


def test_a_certificate_in_another_namespace_does_not_count(tmp_path: Path):
    """Traefik reads the Secret from the route's own namespace, nowhere else."""
    write(
        tmp_path,
        "kubernetes/apps/demo/certificate.yaml",
        CERT.replace("namespace: demo", "namespace: other"),
    )
    write(tmp_path, "kubernetes/apps/demo/ingressroute.yaml", ROUTE)
    assert "has no Certificate in namespace demo" in mismatches(tmp_path / "kubernetes")[0]


def test_an_injected_namespace_pairs_the_two(tmp_path: Path):
    """An app whose kustomization carries `namespace:` omits it on the objects."""
    write(tmp_path, "kubernetes/apps/demo/kustomization.yaml", "namespace: demo\n")
    write(
        tmp_path,
        "kubernetes/apps/demo/certificate.yaml",
        CERT.replace("  namespace: demo\n", ""),
    )
    write(
        tmp_path,
        "kubernetes/apps/demo/ingressroute.yaml",
        ROUTE.replace("  namespace: demo\n", ""),
    )
    assert mismatches(tmp_path / "kubernetes") == []


def test_a_route_without_tls_is_reported(tmp_path: Path):
    write(tmp_path, "kubernetes/apps/demo/certificate.yaml", CERT)
    write(
        tmp_path,
        "kubernetes/apps/demo/ingressroute.yaml",
        ROUTE.replace("  tls:\n    secretName: demo-tls\n", ""),
    )
    assert "no spec.tls.secretName" in mismatches(tmp_path / "kubernetes")[0]


def test_an_unparseable_manifest_names_itself(tmp_path: Path):
    broken = "kubernetes/apps/demo/ingressroute.yaml"
    write(tmp_path, broken, "kind: [unclosed\n")
    with pytest.raises(AssertionError, match="ingressroute.yaml"):
        mismatches(tmp_path / "kubernetes")
