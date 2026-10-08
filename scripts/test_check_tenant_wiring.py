"""Coverage for check-tenant-wiring.py.

No tenant is onboarded, so the live tree exercises only the empty case. Every
arm runs against a fixture tenant file, and each mutation case must FAIL.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
gate = load_script("check-tenant-wiring.py")

TENANT = textwrap.dedent(
    """\
    ---
    apiVersion: v1
    kind: Namespace
    metadata:
      name: demo-app
      labels:
        fluxcd.io/tenant: demo-app
        pod-security.kubernetes.io/enforce: baseline
        pod-security.kubernetes.io/warn: restricted
        pod-security.kubernetes.io/audit: restricted
    ---
    apiVersion: v1
    kind: ResourceQuota
    metadata:
      name: demo-app-quota
      namespace: demo-app
    spec:
      hard:
        pods: "10"
        requests.cpu: "2"
        requests.memory: 4Gi
        limits.memory: 8Gi
    ---
    apiVersion: v1
    kind: LimitRange
    metadata:
      name: demo-app-limits
      namespace: demo-app
    spec:
      limits:
        - type: Container
          default:
            memory: 512Mi
          defaultRequest:
            cpu: 50m
            memory: 128Mi
          max:
            memory: 2Gi
    ---
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: demo-app-flux
      namespace: flux-system
    ---
    apiVersion: external-secrets.io/v1
    kind: ClusterSecretStore
    metadata:
      name: onepassword-demo-app
    spec:
      conditions:
        - namespaces:
            - demo-app
      provider:
        onepassword:
          connectHost: http://onepassword-connect.external-secrets.svc.cluster.local:8080
          vaults:
            Homelab: 1
    ---
    apiVersion: rbac.authorization.k8s.io/v1
    kind: RoleBinding
    metadata:
      name: demo-app-flux-admin
      namespace: demo-app
    subjects:
      - kind: ServiceAccount
        name: demo-app-flux
        namespace: flux-system
    roleRef:
      kind: ClusterRole
      name: admin
      apiGroup: rbac.authorization.k8s.io
    ---
    apiVersion: rbac.authorization.k8s.io/v1
    kind: RoleBinding
    metadata:
      name: demo-app-flux-crd-editor
      namespace: demo-app
    subjects:
      - kind: ServiceAccount
        name: demo-app-flux
        namespace: flux-system
    roleRef:
      kind: ClusterRole
      name: tenant-crd-editor
      apiGroup: rbac.authorization.k8s.io
    ---
    apiVersion: source.toolkit.fluxcd.io/v1
    kind: GitRepository
    metadata:
      name: demo-app
      namespace: flux-system
    spec:
      interval: 1m
      url: https://git.example.com/eric/demo-app
      ref:
        branch: main
    ---
    apiVersion: kustomize.toolkit.fluxcd.io/v1
    kind: Kustomization
    metadata:
      name: demo-app
      namespace: flux-system
    spec:
      dependsOn:
        - name: infrastructure-configs
      serviceAccountName: demo-app-flux
      sourceRef:
        kind: GitRepository
        name: demo-app
      path: ./kubernetes/flux
      prune: true
      targetNamespace: demo-app
    """
)

QUOTA = textwrap.dedent(
    """\
    ---
    apiVersion: v1
    kind: ResourceQuota
    metadata:
      name: demo-app-quota
      namespace: demo-app
    spec:
      hard:
        pods: "10"
        requests.cpu: "2"
        requests.memory: 4Gi
        limits.memory: 8Gi
    """
)

LIMITS = textwrap.dedent(
    """\
    ---
    apiVersion: v1
    kind: LimitRange
    metadata:
      name: demo-app-limits
      namespace: demo-app
    spec:
      limits:
        - type: Container
          default:
            memory: 512Mi
          defaultRequest:
            cpu: 50m
            memory: 128Mi
          max:
            memory: 2Gi
    """
)

AGGREGATOR = textwrap.dedent(
    """\
    apiVersion: kustomize.config.k8s.io/v1beta1
    kind: Kustomization
    resources:
      - tenant-crd-editor.yaml
      - demo-app.yaml
    """
)


def write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path, f"{gate.TENANTS_DIR}/{gate.AGGREGATOR}", AGGREGATOR)
    write(tmp_path, f"{gate.TENANTS_DIR}/tenant-crd-editor.yaml", "kind: ClusterRole\n")
    write(tmp_path, f"{gate.TENANTS_DIR}/demo-app.yaml", TENANT)
    return tmp_path


def drop(body: str, needle: str) -> str:
    return "\n".join(line for line in body.splitlines() if needle not in line) + "\n"


def test_a_complete_tenant_file_passes(repo: Path) -> None:
    assert gate.check(repo) == []
    assert gate.main(["--repo-root", str(repo)]) == 0


def test_a_yml_tenant_is_checked_like_a_yaml_one(repo: Path) -> None:
    """Flux applies a listed `*.yml`, so the gate must read it too."""
    (repo / gate.TENANTS_DIR / "demo-app.yaml").unlink()
    write(
        repo, f"{gate.TENANTS_DIR}/demo-app.yml",
        drop(TENANT, "pod-security.kubernetes.io/enforce"),
    )
    write(
        repo, f"{gate.TENANTS_DIR}/{gate.AGGREGATOR}",
        AGGREGATOR.replace("demo-app.yaml", "demo-app.yml"),
    )
    problems = gate.check(repo)
    assert any("pod-security.kubernetes.io/enforce" in p for p in problems), problems
    assert gate.main(["--repo-root", str(repo)]) == 1


@pytest.mark.parametrize(
    ("needle", "expected"),
    [
        ("serviceAccountName", "serviceAccountName"),
        ("prune: true", "prune: true"),
        ("targetNamespace", "targetNamespace"),
        ("infrastructure-configs", "dependOn"),
        ("path: ./kubernetes/flux", "spec.path"),
        ("pod-security.kubernetes.io/enforce", "pod-security.kubernetes.io/enforce"),
        ("fluxcd.io/tenant", "fluxcd.io/tenant"),
    ],
)
def test_a_missing_line_fails(repo: Path, needle: str, expected: str) -> None:
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", drop(TENANT, needle))
    problems = gate.check(repo)
    assert any(expected in p for p in problems), problems
    assert gate.main(["--repo-root", str(repo)]) == 1


@pytest.mark.parametrize("role", ["admin", "tenant-crd-editor"])
def test_a_missing_rolebinding_fails(repo: Path, role: str) -> None:
    body = TENANT.replace(f"  name: {role}\n  apiGroup:", "  name: view\n  apiGroup:")
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any(f"grants {role}" in p for p in gate.check(repo))


def test_a_kustomization_without_a_source_ref_fails(repo: Path) -> None:
    body = TENANT.replace(
        "  sourceRef:\n    kind: GitRepository\n    name: demo-app\n", ""
    )
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any("sourceRef.name" in p for p in gate.check(repo))
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_source_ref_naming_no_declared_repository_fails(repo: Path) -> None:
    """The tenant's GitRepository ships in its own wiring file, so a reference
    to any other name reconciles another tenant's repo, or nothing."""
    body = TENANT.replace("    name: demo-app\n", "    name: other-app\n")
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any("GitRepository other-app" in p for p in gate.check(repo))
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_source_ref_of_another_kind_fails(repo: Path) -> None:
    body = TENANT.replace("    kind: GitRepository\n", "    kind: OCIRepository\n")
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any("sourceRef.kind" in p for p in gate.check(repo))


def test_a_source_ref_outside_flux_system_fails(repo: Path) -> None:
    body = TENANT.replace(
        "  sourceRef:\n", "  sourceRef:\n    namespace: demo-app\n"
    )
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any("sourceRef.namespace" in p for p in gate.check(repo))


def test_a_path_aimed_at_the_cluster_tree_fails(repo: Path) -> None:
    """Mutation case: the tenant ServiceAccount would reconcile this repo's own
    manifests into the tenant namespace."""
    body = TENANT.replace(
        "  path: ./kubernetes/flux\n", "  path: ./kubernetes/clusters/weisssrv\n"
    )
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any("directory of this cluster repo" in p for p in gate.check(repo))
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_path_climbing_out_of_the_tenant_repo_fails(repo: Path) -> None:
    body = TENANT.replace(
        "  path: ./kubernetes/flux\n", "  path: ../other-repo/kubernetes\n"
    )
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any("climbs above the tenant repository root" in p for p in gate.check(repo))


CONDITIONS = "  conditions:\n    - namespaces:\n        - demo-app\n"


def test_a_store_without_conditions_fails(repo: Path) -> None:
    """Mutation case: an unscoped ClusterSecretStore is readable from every
    namespace, and no corpus gate renders this tree."""
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", TENANT.replace(CONDITIONS, ""))
    assert any("no spec.conditions" in p for p in gate.check(repo))
    assert gate.main(["--repo-root", str(repo)]) == 1


@pytest.mark.parametrize(
    "replacement",
    [
        "  conditions:\n    - namespaces:\n        - demo-app\n        - other-tenant\n",
        "  conditions:\n    - namespaceSelector: {}\n    - namespaces:\n        - demo-app\n",
        "  conditions:\n    - namespaceRegexes:\n        - demo-.*\n",
    ],
)
def test_a_store_reaching_past_the_namespace_fails(repo: Path, replacement: str) -> None:
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", TENANT.replace(CONDITIONS, replacement))
    assert any("reaches past the tenant namespace" in p for p in gate.check(repo))
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_store_that_admits_another_namespace_only_fails(repo: Path) -> None:
    body = TENANT.replace(CONDITIONS, CONDITIONS.replace("demo-app", "other-tenant"))
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any("do not admit the tenant namespace" in p for p in gate.check(repo))


def test_an_unlisted_tenant_file_fails(repo: Path) -> None:
    write(
        repo,
        f"{gate.TENANTS_DIR}/{gate.AGGREGATOR}",
        AGGREGATOR.replace("  - demo-app.yaml\n", ""),
    )
    assert any("not listed" in p for p in gate.check(repo))


def test_a_listed_but_absent_file_fails(repo: Path) -> None:
    (repo / gate.TENANTS_DIR / "demo-app.yaml").unlink()
    assert any("does not exist" in p for p in gate.check(repo))


def test_a_service_account_in_the_tenant_namespace_fails(repo: Path) -> None:
    body = TENANT.replace(
        "kind: ServiceAccount\nmetadata:\n  name: demo-app-flux\n  namespace: flux-system",
        "kind: ServiceAccount\nmetadata:\n  name: demo-app-flux\n  namespace: demo-app",
        1,
    )
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any("kustomize-controller impersonates" in p for p in gate.check(repo))


@pytest.mark.parametrize(("block", "kind"), [("QUOTA", "ResourceQuota"), ("LIMITS", "LimitRange")])
def test_a_tenant_without_a_namespace_cap_fails(repo: Path, block: str, kind: str) -> None:
    """Mutation case: `admin` places no ceiling, so the caps are the ceiling."""
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", TENANT.replace(globals()[block], ""))
    assert any(f"declares no {kind}" in p for p in gate.check(repo))
    assert gate.main(["--repo-root", str(repo)]) == 1


@pytest.mark.parametrize(
    "replacement",
    [
        LIMITS.replace("      max:\n        memory: 2Gi\n",
                       '      max:\n        cpu: "1"\n        memory: 2Gi\n'),
        LIMITS.replace("      default:\n        memory: 512Mi\n",
                       '      default:\n        cpu: "1"\n        memory: 512Mi\n'),
    ],
)
def test_a_limit_range_that_caps_cpu_fails(repo: Path, replacement: str) -> None:
    """A `max` with no matching `default` becomes the default limit, so either
    key hands every tenant container a CPU limit."""
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", TENANT.replace(LIMITS, replacement))
    assert any("sets cpu under" in p for p in gate.check(repo))
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_cap_without_a_namespace_fails(repo: Path) -> None:
    """Mutation case: the tenants aggregator sets none, so it lands in `default`."""
    body = TENANT.replace(QUOTA, QUOTA.replace("  namespace: demo-app\n", ""))
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any("has no metadata.namespace" in p for p in gate.check(repo))
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_cap_aimed_at_another_namespace_fails(repo: Path) -> None:
    body = TENANT.replace(QUOTA, QUOTA.replace("  namespace: demo-app", "  namespace: downloads"))
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", body)
    assert any("neither the tenant namespace" in p for p in gate.check(repo))
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_missing_tenants_directory_is_vacuous(tmp_path: Path) -> None:
    with pytest.raises(gate.Vacuous):
        gate.check(tmp_path)
    assert gate.main(["--repo-root", str(tmp_path)]) == 2


def test_an_empty_tenants_dir_passes_when_the_aggregator_lists_no_tenant(
    tmp_path: Path,
) -> None:
    write(tmp_path, f"{gate.TENANTS_DIR}/{gate.AGGREGATOR}", "resources: []\n")
    assert gate.main(["--repo-root", str(tmp_path)]) == 0


def test_an_empty_tenants_dir_listing_only_the_shared_file_passes(tmp_path: Path) -> None:
    write(
        tmp_path,
        f"{gate.TENANTS_DIR}/{gate.AGGREGATOR}",
        "resources:\n  - tenant-crd-editor.yaml\n",
    )
    assert gate.main(["--repo-root", str(tmp_path)]) == 0


def test_an_empty_tenants_dir_listing_a_tenant_is_vacuous(tmp_path: Path) -> None:
    """Mutation case: a listed tenant the scan cannot see means the scan broke,
    so the gate must exit 2 rather than report an empty tree as a pass."""
    write(
        tmp_path,
        f"{gate.TENANTS_DIR}/{gate.AGGREGATOR}",
        "resources:\n  - acme-app.yaml\n",
    )
    assert gate.main(["--repo-root", str(tmp_path)]) == 2


def test_a_tenant_that_is_present_is_still_checked(repo: Path) -> None:
    write(repo, f"{gate.TENANTS_DIR}/demo-app.yaml", drop(TENANT, "prune: true"))
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_the_live_tree_is_clean() -> None:
    assert gate.check(REPO) == []


def readme_example() -> str:
    """The complete tenant wiring fence from the onboarding README.

    The GitLab-provider fence deliberately carries only the store and refers to
    this one for the rest, so the complete example is the one to check.
    """
    readme = (REPO / gate.TENANTS_DIR / "README.md").read_text()
    blocks = re.findall(r"```yaml\n(.*?)```", readme, re.S)
    return next(
        b for b in blocks if "kind: Kustomization" in b and "serviceAccountName" in b
    )


def stage_example(root: Path, example: str) -> None:
    docs = [d for d in yaml.safe_load_all(example) if isinstance(d, dict)]
    namespace = next(d for d in docs if d.get("kind") == "Namespace")["metadata"]["name"]
    write(root, f"{gate.TENANTS_DIR}/{namespace}.yaml", example)
    write(
        root,
        f"{gate.TENANTS_DIR}/{gate.AGGREGATOR}",
        f"resources:\n  - tenant-crd-editor.yaml\n  - {namespace}.yaml\n",
    )


def test_the_readme_example_satisfies_the_gate(tmp_path: Path) -> None:
    """The onboarding example is what a tenant copies, so it must pass the gate."""
    stage_example(tmp_path, readme_example())
    assert gate.check(tmp_path) == []
    assert gate.main(["--repo-root", str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "needle", ["serviceAccountName", "infrastructure-configs", "prune: true"]
)
def test_a_wiring_piece_dropped_from_the_readme_example_fails(
    tmp_path: Path, needle: str
) -> None:
    """Mutation case: the example is checked, not merely parsed."""
    stage_example(tmp_path, drop(readme_example(), needle))
    assert gate.check(tmp_path) != []
    assert gate.main(["--repo-root", str(tmp_path)]) == 1


def test_a_dot_slash_resource_entry_is_the_same_file(repo: Path) -> None:
    """Kustomize accepts `./name.yaml`; the gate must not read it as unlisted."""
    write(
        repo,
        f"{gate.TENANTS_DIR}/{gate.AGGREGATOR}",
        AGGREGATOR.replace("  - demo-app.yaml", "  - ./demo-app.yaml"),
    )
    assert gate.check(repo) == []
    assert gate.main(["--repo-root", str(repo)]) == 0


def test_an_unparseable_aggregator_is_vacuous_not_a_traceback(repo: Path) -> None:
    """The aggregator is the gate's own input: unreadable means it inspected
    nothing, which is exit 2 rather than a finding."""
    write(repo, f"{gate.TENANTS_DIR}/{gate.AGGREGATOR}", "resources: [a,\n  b: {\n")
    with pytest.raises(gate.Vacuous):
        gate.listed_resources(repo)
    assert gate.main(["--repo-root", str(repo)]) == 2
