"""Coverage for check-secret-rotation-coverage.py.

The live tree is clean, so it proves nothing about failure: every arm is driven
against a fixture repo, and each has a mutation case that must FAIL.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
gate = load_script("check-secret-rotation-coverage.py")


EXTERNAL_SECRET = textwrap.dedent(
    """\
    apiVersion: external-secrets.io/v1beta1
    kind: ExternalSecret
    metadata:
      name: demo-secrets
      namespace: demo
    spec:
      data:
        - secretKey: token
          remoteRef:
            key: Demo Item
            property: token
    """
)


CLUSTER_EXTERNAL_SECRET = textwrap.dedent(
    """\
    apiVersion: external-secrets.io/v1
    kind: ClusterExternalSecret
    metadata:
      name: fanned-out
    spec:
      externalSecretName: fanned-secrets
      externalSecretSpec:
        data:
          - secretKey: token
            remoteRef:
              key: Fanned Item
              property: credential
    """
)


def write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def doc(items: str = "", workloads: str = "", other: str = "") -> str:
    """A docs/15 shaped page: the two sections the gate reads, plus a third it
    must never accept a credential from."""
    return textwrap.dedent(
        f"""\
        # Credential rotation

        ## {gate.ITEM_SECTION}

        {items}

        ## {gate.WORKLOAD_SECTION}

        {workloads}

        ## Troubleshooting

        {other}
        """
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(gate, "DECLARED_MANUAL", {})
    write(tmp_path, "kubernetes/apps/demo/externalsecret.yaml", EXTERNAL_SECRET)
    write(
        tmp_path,
        gate.DOC,
        doc(items="Demo Item rotates by hand", workloads="demo/demo-secrets refreshes"),
    )
    write(tmp_path, gate.ROTATE_SCRIPT, "# no cases\n")
    return tmp_path


def test_a_documented_secret_passes(repo: Path) -> None:
    assert gate.check(repo) == []
    assert gate.main(["--repo-root", str(repo)]) == 0


def test_an_undocumented_vault_item_fails(repo: Path) -> None:
    write(repo, gate.DOC, doc(workloads="demo/demo-secrets is refreshed by hand"))
    problems = gate.check(repo)
    assert any("Demo Item" in p for p in problems)
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_vault_item_documented_only_in_a_foreign_section_fails(repo: Path) -> None:
    """A name mentioned in an unrelated section documents no rotation."""
    write(
        repo,
        gate.DOC,
        doc(workloads="demo/demo-secrets refreshes", other="Demo Item broke once"),
    )
    problems = gate.check(repo)
    assert any("Demo Item" in p and gate.ITEM_SECTION in p for p in problems)
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_workload_documented_only_in_a_foreign_section_fails(repo: Path) -> None:
    write(
        repo,
        gate.DOC,
        doc(items="Demo Item rotates by hand", other="demo/demo-secrets was resynced"),
    )
    problems = gate.check(repo)
    assert any("reached by no rotation path" in p for p in problems)


def test_a_doc_without_the_owning_sections_is_vacuous(repo: Path) -> None:
    """A re-organised docs/15 must stop the gate, not pass it by substring."""
    write(repo, gate.DOC, "Demo Item rotates by hand: demo/demo-secrets\n")
    with pytest.raises(gate.Vacuous):
        gate.check(repo)
    assert gate.main(["--repo-root", str(repo)]) == 2


def test_a_secret_no_rotation_path_reaches_fails(repo: Path) -> None:
    write(repo, gate.DOC, doc(items="Demo Item lives in the Homelab vault"))
    problems = gate.check(repo)
    assert any("reached by no rotation path" in p for p in problems)


def test_a_rotate_script_case_is_enough(repo: Path) -> None:
    write(repo, gate.DOC, doc(items="Demo Item lives in the Homelab vault"))
    write(repo, gate.ROTATE_SCRIPT, "task flux:refresh-secret -- demo/demo-secrets\n")
    assert gate.check(repo) == []


def test_a_declared_manual_entry_is_enough(repo: Path, monkeypatch) -> None:
    write(repo, gate.DOC, doc(items="Demo Item lives in the Homelab vault"))
    monkeypatch.setattr(gate, "DECLARED_MANUAL", {"demo/demo-secrets": "no procedure yet"})
    assert gate.check(repo) == []


def test_a_stale_declared_manual_entry_fails(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(gate, "DECLARED_MANUAL", {"gone/gone-secrets": "stale"})
    assert any("drop the stale entry" in p for p in gate.check(repo))


def test_an_unparseable_manifest_is_reported_not_skipped(repo: Path) -> None:
    """A file this gate cannot read is a hole in its subject, not a pass."""
    write(repo, "kubernetes/apps/broken/externalsecret.yaml", "a: [1,\n  b: {\n")
    problems = gate.check(repo)
    assert any("kubernetes/apps/broken/externalsecret.yaml" in p for p in problems)
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_no_external_secrets_is_vacuous(tmp_path: Path) -> None:
    (tmp_path / "kubernetes").mkdir()
    write(tmp_path, gate.DOC, "nothing\n")
    write(tmp_path, gate.ROTATE_SCRIPT, "nothing\n")
    with pytest.raises(gate.Vacuous):
        gate.check(tmp_path)
    assert gate.main(["--repo-root", str(tmp_path)]) == 2


def test_a_longer_item_name_does_not_cover_the_shorter_one(repo: Path) -> None:
    """Mutation case: a prefix name is not documented by its longer sibling."""
    write(
        repo,
        gate.DOC,
        doc(items="Demo Item-Archive rotates by hand", workloads="demo/demo-secrets refreshes"),
    )
    problems = gate.check(repo)
    assert any("Demo Item" in p and gate.ITEM_SECTION in p for p in problems)
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_longer_workload_name_does_not_cover_the_shorter_one(repo: Path) -> None:
    write(
        repo,
        gate.DOC,
        doc(items="Demo Item rotates by hand", workloads="demo/demo-secrets-old refreshes"),
    )
    problems = gate.check(repo)
    assert any("demo/demo-secrets is reached by no rotation path" in p for p in problems)
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_an_undocumented_cluster_external_secret_fails(repo: Path) -> None:
    """Mutation case: a cluster-scoped fan-out is a credential like any other."""
    write(repo, "kubernetes/infrastructure/configs/shared/ces.yaml", CLUSTER_EXTERNAL_SECRET)
    problems = gate.check(repo)
    assert any("fanned-secrets is reached by no rotation path" in p for p in problems)
    assert any("Fanned Item" in p and gate.ITEM_SECTION in p for p in problems)
    assert gate.main(["--repo-root", str(repo)]) == 1


def test_a_cluster_external_secret_is_keyed_without_a_namespace(repo: Path) -> None:
    write(repo, "kubernetes/infrastructure/configs/shared/ces.yaml", CLUSTER_EXTERNAL_SECRET)
    names, _, _ = gate.external_secrets(repo)
    assert "fanned-secrets" in names
    assert not any(name.endswith("/fanned-out") for name in names)


def test_the_live_tree_is_clean() -> None:
    assert gate.check(REPO) == []
