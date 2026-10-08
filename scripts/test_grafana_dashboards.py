"""Static checks for the Grafana dashboards under kubernetes/infrastructure/observability.

Dashboards reach the cluster as opaque strings in a configMapGenerator, so
`task flux:lint` never parses the JSON; these checks read the files.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
OBSERVABILITY = REPO / "kubernetes" / "infrastructure" / "observability"
DASHBOARDS = OBSERVABILITY / "dashboards"
KUSTOMIZATION = DASHBOARDS / "kustomization.yaml"
RELEASE = OBSERVABILITY / "kube-prometheus-stack" / "release.yaml"

# Grafana's own datasource spellings, which no chart provisions.
BUILTIN_DATASOURCE_UIDS = {"grafana", "-- Grafana --", "-- Mixed --", "-- Dashboard --"}

# Folders the Grafana sidecar files dashboards into.
ALLOWED_FOLDERS = {"Infrastructure", "Networking", "Applications"}

# $__rate_interval and friends come from Grafana; ${cluster_*} is substituted by
# Flux from the cluster-config ConfigMap before the manifest reaches the cluster.
VARIABLE_PREFIX_ALLOWLIST = ("__", "cluster_")

_VARIABLE_RE = re.compile(r"\$(?:\{(\w+)[^}]*\}|(\w+))")
_QUERY_FIELDS = ("expr", "query", "legendFormat", "title", "expression")

# A whole-value `$name` / `${name}` datasource: a template variable, not a uid.
_TEMPLATE_VAR_RE = re.compile(r"^\$\{?[A-Za-z_]\w*\}?$")


class OperatorError(Exception):
    """A file the suite cannot read or parse: a broken input, not a finding."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OperatorError(f"{path}: unreadable as UTF-8 text ({exc})") from exc


def _load_json(path: Path) -> dict:
    try:
        return json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise OperatorError(f"{path}: not valid JSON ({exc})") from exc


def _load_yaml(path: Path) -> dict:
    try:
        doc = yaml.safe_load(_read_text(path))
    except yaml.YAMLError as exc:
        raise OperatorError(f"{path}: not valid YAML ({exc})") from exc
    if not isinstance(doc, dict):
        raise OperatorError(f"{path}: not a YAML mapping")
    return doc


def dashboard_paths() -> list[Path]:
    """Top-level dashboards plus every registered one that exists on disk.

    A dashboard registered from a subdirectory ships to Grafana, so it is
    content-checked too rather than reported as shipped OK.
    """
    on_disk = {path.relative_to(DASHBOARDS).as_posix() for path in DASHBOARDS.glob("*.json")}
    registered = {
        source for source in registered_sources()
        if (DASHBOARDS / source).is_file()
    }
    return sorted(DASHBOARDS / name for name in on_disk | registered)


def load_dashboards() -> dict[Path, dict]:
    return {path: _load_json(path) for path in dashboard_paths()}


def provisioned_datasource_uids(release: Path) -> set[str]:
    """Datasource uids the kube-prometheus-stack release actually provisions."""
    doc = _load_yaml(release)
    try:
        grafana = doc["spec"]["values"]["grafana"]
        uids = {grafana["sidecar"]["datasources"]["uid"]}
    except (KeyError, TypeError) as exc:
        raise OperatorError(
            f"{release}: no grafana sidecar datasource uid to compare against ({exc})"
        ) from exc
    uids.update(source["uid"] for source in grafana.get("additionalDataSources", []))
    return uids | BUILTIN_DATASOURCE_UIDS


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def datasource_refs(dashboard: dict) -> set[tuple[str, str]]:
    """Every datasource reference, tagged by shape.

    `uid` a dict carrying a string uid, `name` the legacy string form, `nouid` a
    dict without one. A null datasource is skipped: row panels carry one.
    """
    refs: set[tuple[str, str]] = set()
    for node in _walk(dashboard):
        if "datasource" not in node:
            continue
        source = node["datasource"]
        if isinstance(source, dict):
            refs.add(("uid", source["uid"]) if isinstance(source.get("uid"), str)
                     else ("nouid", ""))
        elif isinstance(source, str):
            refs.add(("name", source))
    return refs


def datasource_findings(dashboard: dict, allowed: set[str]) -> list[str]:
    """Datasource references provisioned Grafana will not resolve."""
    declared = declared_variables(dashboard)
    findings = []
    for kind, value in sorted(datasource_refs(dashboard)):
        if kind == "nouid":
            findings.append(
                "a datasource dict with no string `uid`; it resolves to whichever "
                "datasource is default"
            )
        elif _TEMPLATE_VAR_RE.match(value):
            if value.strip("${}") not in declared:
                findings.append(
                    f"datasource variable {value} is declared in no templating.list "
                    f"entry; declared: {sorted(declared)}"
                )
        elif value in BUILTIN_DATASOURCE_UIDS:
            continue
        elif kind == "name":
            findings.append(
                f"legacy string datasource {value!r}; provisioned Grafana matches by "
                "uid, so the panel resolves to the default datasource or none"
            )
        elif value not in allowed:
            findings.append(
                f"datasource uid {value!r} is not provisioned; the cluster "
                f"provisions {sorted(allowed)}"
            )
    return findings


def declared_variables(dashboard: dict) -> set[str]:
    return {entry["name"] for entry in dashboard.get("templating", {}).get("list", [])}


def referenced_variables(dashboard: dict) -> set[str]:
    names = set()
    for node in _walk(dashboard):
        for field in _QUERY_FIELDS:
            value = node.get(field)
            if isinstance(value, str):
                names.update(braced or bare for braced, bare in _VARIABLE_RE.findall(value))
    return names


def undeclared_variables(dashboard: dict) -> set[str]:
    return {
        name
        for name in referenced_variables(dashboard) - declared_variables(dashboard)
        # $1 and friends are label_replace capture groups, not dashboard variables.
        if not name.startswith(VARIABLE_PREFIX_ALLOWLIST) and not name.isdigit()
    }


def load_kustomization() -> dict:
    return _load_yaml(KUSTOMIZATION)


def generator_entries(kustomization: dict) -> list[dict]:
    entries = kustomization.get("configMapGenerator")
    if not isinstance(entries, list):
        raise OperatorError(f"{KUSTOMIZATION}: no configMapGenerator list to check")
    return entries


def generated_files(entries: list[dict]) -> list[str]:
    """Every `files:` entry verbatim, for the failure messages."""
    return [str(name) for entry in entries for name in entry.get("files", [])]


def generator_source(entry: str) -> str:
    """The file a `files:` entry reads. kustomize allows `key=path`, where the
    key names the ConfigMap entry and the path may sit in a subdirectory."""
    return os.path.normpath(entry.split("=")[-1])


def registered_sources() -> set[str]:
    """The source file of every `files:` entry, normalised to a relative path."""
    return {generator_source(name) for name in generated_files(generator_entries(load_kustomization()))}


def _effective(kustomization: dict, entry: dict, slot: str) -> dict:
    """kustomize merges file-level generatorOptions into every entry, entry wins."""
    merged = dict((kustomization.get("generatorOptions") or {}).get(slot) or {})
    merged.update((entry.get("options") or {}).get(slot) or {})
    return merged


def sidecar_label(kustomization: dict, entry: dict) -> str | None:
    """The discovery label, wherever it is set: per entry or hoisted."""
    return _effective(kustomization, entry, "labels").get("grafana_dashboard")


def dashboard_folder(kustomization: dict, entry: dict) -> str | None:
    """The Grafana folder, wherever it is set: per entry or hoisted."""
    return _effective(kustomization, entry, "annotations").get("grafana_folder")


@pytest.fixture(scope="module")
def dashboards() -> dict[Path, dict]:
    return load_dashboards()


def test_dashboards_directory_is_not_empty() -> None:
    assert dashboard_paths(), f"no dashboards found under {DASHBOARDS}"


def test_every_dashboard_parses(dashboards) -> None:
    for path, dashboard in dashboards.items():
        assert isinstance(dashboard, dict), f"{path.name} is not a JSON object"
        assert dashboard.get("uid"), f"{path.name} has no uid"
        assert dashboard.get("title"), f"{path.name} has no title"


def test_uid_and_title_are_unique(dashboards) -> None:
    for field in ("uid", "title"):
        seen: dict[str, str] = {}
        for path, dashboard in dashboards.items():
            value = dashboard[field]
            assert value not in seen, (
                f"{path.name} reuses {field} {value!r}, already used by {seen[value]}"
            )
            seen[value] = path.name


def test_datasource_uids_are_provisioned(dashboards) -> None:
    allowed = provisioned_datasource_uids(RELEASE)
    for path, dashboard in dashboards.items():
        findings = datasource_findings(dashboard, allowed)
        assert not findings, f"{path.name}: " + "; ".join(findings)


def test_queries_only_use_declared_variables(dashboards) -> None:
    for path, dashboard in dashboards.items():
        undeclared = undeclared_variables(dashboard)
        assert not undeclared, (
            f"{path.name} references {sorted(undeclared)}, which templating.list does not declare"
        )


def test_generator_entries_and_files_are_one_to_one() -> None:
    entries = generator_entries(load_kustomization())
    listed = generated_files(entries)
    sources = [generator_source(name) for name in listed]
    assert len(sources) == len(set(sources)), "a dashboard is generated twice"
    absent = sorted(
        name for name, source in zip(listed, sources)
        if not (DASHBOARDS / source).is_file()
    )
    assert not absent, f"only in kustomization.yaml: {absent}"
    registered = set(sources)
    # Compared on the top-level glob, so a file registered under a subdirectory
    # key while sitting at the top level is still reported.
    orphans = sorted(
        path.relative_to(DASHBOARDS).as_posix()
        for path in DASHBOARDS.glob("*.json")
        if path.relative_to(DASHBOARDS).as_posix() not in registered
    )
    assert not orphans, f"only on disk: {orphans}"


def test_generator_entries_carry_a_known_folder() -> None:
    kustomization = load_kustomization()
    for entry in generator_entries(kustomization):
        folder = dashboard_folder(kustomization, entry)
        assert folder in ALLOWED_FOLDERS, (
            f"{entry['name']} files into {folder!r}; allowed folders are {sorted(ALLOWED_FOLDERS)}"
        )


def test_every_generated_configmap_carries_the_sidecar_label() -> None:
    """The sidecar discovers dashboards by label; losing it hides all of them."""
    kustomization = load_kustomization()
    for entry in generator_entries(kustomization):
        assert sidecar_label(kustomization, entry) == "1", (
            f'{entry["name"]} sets no grafana_dashboard: "1" label, per entry or '
            "hoisted, so the Grafana sidecar ignores the generated ConfigMap"
        )


def test_no_grafana_com_import_placeholders() -> None:
    for path in dashboard_paths():
        text = _read_text(path)
        for placeholder in ('"__inputs"', '"__requires"', "${DS_"):
            assert placeholder not in text, (
                f"{path.name} still carries the grafana.com import placeholder {placeholder}; "
                "export the dashboard with external sharing OFF and pin the datasource uid"
            )


# --- the checks above must be able to fail ----------------------------------

def test_duplicate_uid_is_detected() -> None:
    dashboards = {Path("a.json"): {"uid": "same", "title": "A"},
                  Path("b.json"): {"uid": "same", "title": "B"}}
    with pytest.raises(AssertionError, match="reuses uid"):
        test_uid_and_title_are_unique(dashboards)


def test_unprovisioned_datasource_is_detected() -> None:
    dashboards = {Path("a.json"): {"panels": [{"datasource": {"type": "prometheus",
                                                              "uid": "thanos"}}]}}
    with pytest.raises(AssertionError, match="thanos"):
        test_datasource_uids_are_provisioned(dashboards)


def test_legacy_string_datasource_is_detected() -> None:
    dashboard = {"panels": [{"datasource": "Prometheus"}]}
    assert datasource_findings(dashboard, {"prometheus"}) == [
        "legacy string datasource 'Prometheus'; provisioned Grafana matches by uid, "
        "so the panel resolves to the default datasource or none"
    ]


def test_datasource_dict_without_a_uid_is_detected() -> None:
    dashboard = {"panels": [{"datasource": {"type": "prometheus"}}]}
    assert datasource_findings(dashboard, {"prometheus"}) == [
        "a datasource dict with no string `uid`; it resolves to whichever "
        "datasource is default"
    ]


def test_undeclared_datasource_variable_is_detected() -> None:
    dashboard = {
        "templating": {"list": [{"name": "ds"}]},
        "panels": [{"datasource": {"type": "prometheus", "uid": "${source}"}}],
    }
    findings = datasource_findings(dashboard, {"prometheus"})
    assert findings == [
        "datasource variable ${source} is declared in no templating.list entry; "
        "declared: ['ds']"
    ]


def test_a_declared_datasource_variable_and_a_null_datasource_pass() -> None:
    dashboard = {
        "templating": {"list": [{"name": "ds"}]},
        "panels": [
            {"datasource": {"type": "prometheus", "uid": "${ds}"}},
            {"type": "row", "datasource": None},
            {"datasource": "-- Grafana --"},
        ],
    }
    assert datasource_findings(dashboard, {"prometheus"}) == []


def test_undeclared_variable_is_detected() -> None:
    dashboard = {
        "templating": {"list": [{"name": "pod"}]},
        "panels": [{"targets": [{"expr": 'up{pod=~"$pod", server="$server"}'}]}],
    }
    assert undeclared_variables(dashboard) == {"server"}


def test_grafana_and_flux_variables_are_not_flagged() -> None:
    dashboard = {
        "templating": {"list": []},
        "panels": [{"targets": [
            {"expr": 'rate(up{job="x"}[$__rate_interval])'},
            {"expr": 'probe_success{instance="https://git.${cluster_internal_domain}"}'},
        ]}],
    }
    assert undeclared_variables(dashboard) == set()


def test_a_key_equals_path_entry_is_not_a_double_finding(tmp_path, monkeypatch) -> None:
    """`key=path` names the ConfigMap key, not a second file."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "kept.json").write_text("{}")
    monkeypatch.setattr(
        "test_grafana_dashboards.KUSTOMIZATION",
        _kustomization(tmp_path, ["kept.json=sub/kept.json"]),
    )
    monkeypatch.setattr("test_grafana_dashboards.DASHBOARDS", tmp_path)
    test_generator_entries_and_files_are_one_to_one()


def test_a_dashboard_registered_from_a_subdirectory_is_content_checked(tmp_path, monkeypatch) -> None:
    """Registration, not only the top-level glob, decides what gets parsed."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "dash.json").write_text(json.dumps({
        "uid": "sub-dash", "title": "Sub Dash",
        "panels": [{"datasource": {"uid": "mystery"}}],
    }))
    monkeypatch.setattr(
        "test_grafana_dashboards.KUSTOMIZATION",
        _kustomization(tmp_path, ["dash.json=sub/dash.json"]),
    )
    monkeypatch.setattr("test_grafana_dashboards.DASHBOARDS", tmp_path)
    assert dashboard_paths() == [tmp_path / "sub" / "dash.json"]
    with pytest.raises(AssertionError, match="mystery"):
        test_datasource_uids_are_provisioned(load_dashboards())


def test_a_registered_file_that_does_not_exist_is_detected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "test_grafana_dashboards.KUSTOMIZATION",
        _kustomization(tmp_path, ["kept.json=sub/kept.json"]),
    )
    monkeypatch.setattr("test_grafana_dashboards.DASHBOARDS", tmp_path)
    with pytest.raises(AssertionError, match="sub/kept.json"):
        test_generator_entries_and_files_are_one_to_one()


def test_orphan_dashboard_file_is_detected(tmp_path, monkeypatch) -> None:
    (tmp_path / "kept.json").write_text("{}")
    (tmp_path / "orphan.json").write_text("{}")
    monkeypatch.setattr(
        "test_grafana_dashboards.KUSTOMIZATION",
        _kustomization(tmp_path, ["kept.json"]),
    )
    monkeypatch.setattr("test_grafana_dashboards.DASHBOARDS", tmp_path)
    with pytest.raises(AssertionError, match="orphan.json"):
        test_generator_entries_and_files_are_one_to_one()


@pytest.mark.parametrize("hoist", [True, False])
def test_unknown_folder_is_detected(tmp_path, monkeypatch, hoist) -> None:
    monkeypatch.setattr(
        "test_grafana_dashboards.KUSTOMIZATION",
        _kustomization(tmp_path, ["kept.json"], folder="Sandbox", hoist=hoist),
    )
    with pytest.raises(AssertionError, match="Sandbox"):
        test_generator_entries_carry_a_known_folder()


def test_folder_set_in_neither_place_is_detected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "test_grafana_dashboards.KUSTOMIZATION",
        _kustomization(tmp_path, ["kept.json"], folder=None),
    )
    with pytest.raises(AssertionError, match="None"):
        test_generator_entries_carry_a_known_folder()


@pytest.mark.parametrize("hoist", [True, False])
def test_missing_sidecar_label_is_detected(tmp_path, monkeypatch, hoist) -> None:
    monkeypatch.setattr(
        "test_grafana_dashboards.KUSTOMIZATION",
        _kustomization(tmp_path, ["kept.json"], label=None, hoist=hoist),
    )
    with pytest.raises(AssertionError, match="grafana_dashboard"):
        test_every_generated_configmap_carries_the_sidecar_label()


def test_a_malformed_dashboard_is_an_operator_error(tmp_path, monkeypatch) -> None:
    """A broken file must not read as a dashboard finding."""
    (tmp_path / "broken.json").write_text("{not json")
    monkeypatch.setattr("test_grafana_dashboards.DASHBOARDS", tmp_path)
    with pytest.raises(OperatorError, match="broken.json: not valid JSON"):
        load_dashboards()


def test_a_non_utf8_dashboard_is_an_operator_error(tmp_path, monkeypatch) -> None:
    (tmp_path / "binary.json").write_bytes(b"\xff\xfe{}")
    monkeypatch.setattr("test_grafana_dashboards.DASHBOARDS", tmp_path)
    with pytest.raises(OperatorError, match="unreadable as UTF-8"):
        load_dashboards()


def test_a_kustomization_without_a_generator_is_an_operator_error(tmp_path, monkeypatch) -> None:
    path = tmp_path / "kustomization.yaml"
    path.write_text("resources: []\n")
    monkeypatch.setattr("test_grafana_dashboards.KUSTOMIZATION", path)
    with pytest.raises(OperatorError, match="no configMapGenerator list"):
        test_generator_entries_carry_a_known_folder()


def test_a_release_without_a_sidecar_datasource_is_an_operator_error(tmp_path) -> None:
    path = tmp_path / "release.yaml"
    path.write_text("spec:\n  values: {}\n")
    with pytest.raises(OperatorError, match="no grafana sidecar datasource uid"):
        provisioned_datasource_uids(path)


def _kustomization(tmp_path: Path, files: list[str], folder: str | None = "Infrastructure",
                   label: str | None = "1", hoist: bool = False) -> Path:
    """A minimal kustomization; hoist moves the per-entry options up a level."""
    generator_options: dict = {"disableNameSuffixHash": True}
    options: dict = {}
    if folder is not None:
        options["annotations"] = {"grafana_folder": folder}
    if label is not None:
        options["labels"] = {"grafana_dashboard": label}
    entry: dict = {}
    if hoist:
        generator_options.update(options)
    elif options:
        entry["options"] = options
    path = tmp_path / "kustomization.yaml"
    path.write_text(yaml.safe_dump({
        "generatorOptions": generator_options,
        "configMapGenerator": [
            {"name": f"grafana-dashboard-{name.removesuffix('.json')}",
             "files": [name],
             **entry}
            for name in files
        ],
    }))
    return path
