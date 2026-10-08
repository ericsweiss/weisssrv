"""scripts/flux-corpus-gates.sh is the single gate list `task flux:lint` and the
CI flux-lint job both run, so a finding must fail it and no gate may be skipped
because an earlier one failed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
GATES = REPO / "scripts" / "flux-corpus-gates.sh"
CALL = 'scripts/flux-corpus-gates.sh "$RENDER_ALL" "$VERSIONS_CONFIGMAP"'

# One subject per gate that refuses to pass on nothing, so a thin fixture corpus
# is a usable corpus rather than an operator error.
GATE_SUBJECTS = """\
apiVersion: v1
kind: Namespace
metadata:
  name: demo
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: demo
  namespace: demo
spec:
  selector:
    matchLabels:
      app: demo
  endpoints:
    - port: metrics
---
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: demo-store
spec:
  conditions:
    - namespaces: [demo]
  provider:
    fake:
      data: []
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: demo-nfs
spec:
  capacity:
    storage: 1Gi
  accessModes: [ReadWriteMany]
  storageClassName: ""
  mountOptions: [nfsvers=4.2, xprtsec=tls]
  nfs:
    server: pve-nas-01.esweiss.com
    path: /nas_storage/demo
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pinned
  namespace: demo
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: ""
  volumeName: demo-nfs
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Secret
metadata:
  name: demo-pull
  namespace: demo
type: kubernetes.io/dockerconfigjson
stringData:
  .dockerconfigjson: |
    {"auths":{"registry.example.com":{"username":"u","password":"p","auth":"dXA="}}}
"""

# One PVC with no storageClassName: the finding check-pvc-storageclass.py exists
# to catch, and late in the list, so reaching it proves the earlier failures did
# not stop the run.
CORPUS_WITH_A_FINDING = GATE_SUBJECTS + """\
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: data
  namespace: demo
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
"""

GATE_HEADERS = (
    "Checking HPA/VPA invariant",
    "Checking scrape/NetworkPolicy invariant",
    "Checking ingress default-deny coverage",
    "Checking ClusterSecretStore scoping",
    "Checking PVC storageClassName",
    "Checking NFS PersistentVolume TLS",
)


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GATES), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})} if env else None,
    )


def _stub_gate(tmp_path: Path, name: str, exit_code: int) -> dict[str, str]:
    """Shadow one gate with a stub exiting `exit_code`, via a PATH python3.

    The driver calls `python3 scripts/<gate>`, so the stub intercepts by name
    and defers every other gate to the real interpreter.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "python3"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do\n'
        f'  if [ "$arg" = "scripts/{name}" ]; then\n'
        f'    echo "stubbed {name}" >&2\n'
        f"    exit {exit_code}\n"
        "  fi\n"
        "done\n"
        f'exec {sys.executable} "$@"\n'
    )
    stub.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    path = tmp_path / "render-all.yaml"
    path.write_text(CORPUS_WITH_A_FINDING)
    return path


def test_the_wrapper_ships_executable() -> None:
    """Every test here runs it as `bash <path>`, so a lost mode bit passes them
    all and breaks the `./scripts/flux-corpus-gates.sh` callers."""
    assert GATES.is_file(), "the corpus-gate wrapper is missing"
    assert os.access(GATES, os.X_OK), f"{GATES.name} is not executable"


def test_a_corpus_with_a_finding_fails(corpus: Path) -> None:
    result = run(str(corpus))
    assert result.returncode == 1, f"{result.stdout}{result.stderr}"
    assert "no storageClassName" in result.stdout + result.stderr


def test_every_gate_still_runs_after_one_fails(corpus: Path) -> None:
    result = run(str(corpus))
    missing = [header for header in GATE_HEADERS if header not in result.stdout]
    assert not missing, f"gates skipped after an earlier failure: {missing}"


def test_the_appended_tenants_tree_cannot_swallow_the_last_document(corpus: Path) -> None:
    """The tenants build is appended, so it needs its own separator or the
    corpus's final document merges into the first tenant one and vanishes."""
    result = run(str(corpus))
    assert "demo/PersistentVolumeClaim/data" in result.stdout + result.stderr


def _flux_controller_deployments(text: str) -> dict[str, dict]:
    """Every flux-system Deployment in a rendered corpus, by name."""
    return {
        doc["metadata"]["name"]: doc
        for doc in yaml.safe_load_all(text)
        if isinstance(doc, dict)
        and doc.get("kind") == "Deployment"
        and (doc.get("metadata") or {}).get("namespace") == "flux-system"
    }


def test_the_cluster_root_render_is_in_the_corpus(corpus: Path) -> None:
    """The bootstrap flux-system Kustomization's path is the cluster root, which
    scripts/flux-child-kustomizations.py never enumerates, so without the root
    append the Flux controllers' own pod specs reach no gate."""
    run(str(corpus))
    names = set(_flux_controller_deployments(corpus.read_text()))
    assert {
        "source-controller",
        "kustomize-controller",
        "helm-controller",
        "notification-controller",
    } <= names, f"cluster-root render missing from the corpus: {sorted(names)}"


def test_a_cpu_limit_on_a_flux_controller_fails(corpus: Path, tmp_path: Path) -> None:
    """The kustomization.yaml patch that strips the bootstrap CPU limits is only
    gated while the root render is in the corpus, so mutate it back and prove the
    CPU-limit policy objects."""
    run(str(corpus))
    victim = _flux_controller_deployments(corpus.read_text())["source-controller"]
    container = victim["spec"]["template"]["spec"]["containers"][0]
    container.setdefault("resources", {}).setdefault("limits", {})["cpu"] = "1000m"
    mutated = tmp_path / "mutated.yaml"
    mutated.write_text(GATE_SUBJECTS + "---\n" + yaml.safe_dump(victim))

    result = run(str(mutated))
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "flux-system/Deployment/source-controller" in output, output
    assert "limits.cpu=1000m" in output, output


def test_a_missing_corpus_is_an_operator_error(tmp_path: Path) -> None:
    assert run().returncode == 2
    assert run(str(tmp_path / "absent.yaml")).returncode == 2


def test_an_empty_corpus_is_an_operator_error(tmp_path: Path) -> None:
    """Zero rendered documents would make every gate below pass vacuously."""
    empty = tmp_path / "render-all.yaml"
    empty.write_text("")
    result = run(str(empty))
    assert result.returncode == 2, f"{result.stdout}{result.stderr}"
    assert "is empty" in result.stderr


def _object_floor() -> str:
    """The `^kind:` floor block, which sits after the corpus appends."""
    lines = GATES.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("if ! grep -qE "))
    end = next(i for i in range(start, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start:end + 1])


def test_the_object_floor_runs_after_the_corpus_appends() -> None:
    """The appends write `---` separators, so the size check up front cannot see
    a corpus holding no object; the floor has to sit below them."""
    body = GATES.read_text()
    assert body.index("Adding the cluster root") < body.index("if ! grep -qE ")


def test_a_corpus_of_only_separators_is_an_operator_error(tmp_path: Path) -> None:
    """Mutation case: without the floor every gate reports clean over nothing."""
    separators = tmp_path / "render-all.yaml"
    separators.write_text("---\n---\n")
    result = subprocess.run(
        ["bash", "-c", f'RENDER_ALL={separators}\n{_object_floor()}'],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2, f"{result.stdout}{result.stderr}"
    assert "holds no Kubernetes object" in result.stderr


def test_a_corpus_holding_an_object_clears_the_floor(tmp_path: Path) -> None:
    held = tmp_path / "render-all.yaml"
    held.write_text("---\nkind: Namespace\n")
    result = subprocess.run(
        ["bash", "-c", f'RENDER_ALL={held}\n{_object_floor()}'],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"{result.stdout}{result.stderr}"


def test_both_callers_pass_the_versions_configmap() -> None:
    """Without the second argument the HelmRelease values validation is skipped,
    so the two real call sites are asserted to supply it."""
    for name in ("taskfiles/flux.yml", ".gitlab-ci.yml"):
        body = (REPO / name).read_text()
        assert CALL in body, (
            f"{name} no longer calls the corpus-gate wrapper with both arguments, "
            f"which silently skips the HelmRelease values validation"
        )


def test_a_gate_exiting_2_is_an_operator_error_not_a_finding(corpus: Path, tmp_path: Path):
    """exit 1 means a NetworkPolicy-style finding; a gate that could not run at
    all must not be reported as one."""
    env = _stub_gate(tmp_path, "check-scrape-netpol.py", 2)
    result = run(str(corpus), env=env)
    assert result.returncode == 2, f"{result.stdout}{result.stderr}"
    assert "operator error, not a finding" in result.stderr


def test_a_gate_exiting_1_still_exits_1(corpus: Path, tmp_path: Path):
    """Mutation counterpart: the worst-status tracking must not promote a real
    finding to an operator error."""
    env = _stub_gate(tmp_path, "check-scrape-netpol.py", 1)
    result = run(str(corpus), env=env)
    assert result.returncode == 1, f"{result.stdout}{result.stderr}"
    assert "operator error" not in result.stderr


def test_an_empty_merged_configmap_is_an_operator_error(tmp_path: Path) -> None:
    """A merge that succeeds while writing nothing would validate every values
    block with its ${...} placeholders intact."""
    clean = tmp_path / "render-all.yaml"
    clean.write_text(GATE_SUBJECTS)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    # flux-env.sh merges via `python3 - <files>`; silencing only that call makes
    # the merge exit 0 with an empty file and leaves every gate on the real
    # interpreter.
    stub = bin_dir / "python3"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "-" ]; then exit 0; fi\n'
        f'exec {sys.executable} "$@"\n'
    )
    stub.chmod(0o755)

    versions = "kubernetes/infrastructure/sources/versions-configmap.yaml"
    result = run(str(clean), versions, env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    assert result.returncode == 2, f"{result.stdout}{result.stderr}"
    assert "empty merged ConfigMap" in result.stderr, result.stderr
