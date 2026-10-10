"""The *arr apps present no login of their own: config.xml says External.

Left on `Forms` they show their own form after Authentik for any request a
proxy forwards. Rendered with kustomize here, then the script is run.
"""
from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import require_tool

REPO = Path(__file__).resolve().parent.parent
OVERLAY = REPO / "kubernetes" / "apps" / "download-clients"
CLUSTER_CONFIG = REPO / "kubernetes" / "infrastructure" / "sources" / "cluster-config.yaml"

# Every *arr app behind the authentik-auth middleware. sonarr/radarr/lidarr come
# from the _arr component; prowlarr carries its own copy of the same container.
ARR_APPS = ("sonarr", "radarr", "lidarr", "prowlarr")

INIT_NAME = "seed-external-auth"
METHOD = "<AuthenticationMethod>External</AuthenticationMethod>"
# The manifest spells the CIDR as the env var the script reads, never a literal.
NETWORKS = "<TrustedNetworks>$TRUSTED_NETWORKS</TrustedNetworks>"
CONFIG_PATH = "/config/config.xml"


@pytest.fixture(scope="module")
def cluster_config() -> dict:
    return yaml.safe_load(CLUSTER_CONFIG.read_text())["data"]


@pytest.fixture(scope="module")
def rendered() -> dict[str, dict]:
    """name -> pod spec, for every Deployment the download-clients overlay builds."""
    require_tool("kustomize", "arr External-auth gate",
                 "python3 scripts/ci-fetch-tools.py kustomize")
    build = subprocess.run(
        ["kustomize", "build", str(OVERLAY)],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, f"kustomize build failed:\n{build.stderr}"
    pods = {}
    for doc in yaml.safe_load_all(build.stdout):
        if isinstance(doc, dict) and doc.get("kind") == "Deployment":
            pods[doc["metadata"]["name"]] = doc["spec"]["template"]["spec"]
    assert set(ARR_APPS) <= set(pods), (
        f"overlay rendered no Deployment for {sorted(set(ARR_APPS) - set(pods))}"
    )
    return pods


def _seeder(pod: dict) -> dict:
    found = [c for c in pod.get("initContainers") or [] if c["name"] == INIT_NAME]
    assert len(found) == 1, f"expected one {INIT_NAME} init container, got {len(found)}"
    return found[0]


@pytest.mark.parametrize("app", ARR_APPS)
def test_every_arr_runs_the_seeder_before_the_app(rendered, app):
    """Without it the app boots on its stored Forms setting and prompts again."""
    script = _seeder(rendered[app])["command"][-1]
    assert METHOD in script, f"{app}'s {INIT_NAME} does not set External"
    assert NETWORKS in script, f"{app}'s {INIT_NAME} does not set TrustedNetworks"
    assert CONFIG_PATH in script


@pytest.mark.parametrize("app", ARR_APPS)
def test_the_trusted_network_is_the_proxys_own(rendered, app):
    """It names the forwarding proxy, so it must be the pod CIDR, by placeholder."""
    env = {e["name"]: e.get("value") for e in _seeder(rendered[app]).get("env") or []}
    assert env.get("TRUSTED_NETWORKS") == "${cluster_pod_cidr}"


@pytest.mark.parametrize("app", ARR_APPS)
def test_the_seeder_writes_the_file_the_app_reads(rendered, app):
    """A seeder on a different volume would rewrite a config nothing reads."""
    pod = rendered[app]
    seeder_mounts = {(m["name"], m["mountPath"]) for m in _seeder(pod)["volumeMounts"]}
    app_container = next(c for c in pod["containers"] if c["name"] == app)
    app_mounts = {(m["name"], m["mountPath"]) for m in app_container["volumeMounts"]}
    assert ("config", "/config") in seeder_mounts & app_mounts


@pytest.mark.parametrize("app", ARR_APPS)
def test_the_seeder_image_is_the_pinned_busybox(rendered, app):
    """`:latest` would silently change the shell this rewrite depends on."""
    assert _seeder(rendered[app])["image"] == "busybox:${busybox_version}"


def test_prowlarr_has_not_drifted_from_the_shared_skeleton(rendered):
    """Prowlarr is standalone, so its copy is where a fix gets forgotten."""
    seeders = {app: _seeder(rendered[app]) for app in ARR_APPS}
    scripts = {app: c["command"][-1] for app, c in seeders.items()}
    assert len(set(scripts.values())) == 1, (
        "the seeder script differs between apps: " + ", ".join(sorted(scripts))
    )
    envs = {app: str(sorted((e["name"], e.get("value")) for e in c.get("env") or []))
            for app, c in seeders.items()}
    assert len(set(envs.values())) == 1, "the seeder env differs between apps"


def test_the_replacement_is_atomic_and_the_temp_name_unguessable(rendered):
    """A truncate-then-copy loses config.xml outright if the write is cut short,
    so the new content is staged beside the target and moved onto it. The staging
    name comes from mktemp, since a predictable one can be pre-planted."""
    script = _seeder(rendered["sonarr"])["command"][-1]
    assert "mv -f" in script
    assert 'cat "$tmp" > "$conf"' not in script
    assert 'mktemp "$conf.seed-tmp.XXXXXX"' in script
    assert "trap 'rm -f \"$tmp\"' EXIT" in script


# --- the script's own behaviour ---------------------------------------------
# POSIX sh + grep/awk/stat only, so the rendered command runs here unmodified
# apart from the config path, which is redirected into tmp_path.

# config.xml shapes seen in the wild: sonarr/radarr/prowlarr ship an EMPTY
# TrustedNetworks element, lidarr's older build ships none at all.
EMPTY_ELEMENT = (
    "<Config>\n  <Port>8989</Port>\n"
    "  <ApiKey>abc</ApiKey>\n"
    "  <AuthenticationMethod>Forms</AuthenticationMethod>\n"
    "  <AuthenticationRequired>DisabledForLocalAddresses</AuthenticationRequired>\n"
    "  <TrustedNetworks></TrustedNetworks>\n"
    "</Config>"
)
NO_ELEMENT = (
    "<Config>\n  <Port>8686</Port>\n"
    "  <AuthenticationMethod>Forms</AuthenticationMethod>\n</Config>"
)
SELF_CLOSING = "<Config>\n  <AuthenticationMethod />\n  <TrustedNetworks />\n</Config>"
COMPACT = "<Config><Port>8989</Port><AuthenticationMethod>Basic</AuthenticationMethod></Config>"
# Several copies of each, which the apps answer from the FIRST one they parse.
DUPLICATES = (
    "<Config>\n  <AuthenticationMethod>Forms</AuthenticationMethod>\n"
    "  <Port>8989</Port>\n"
    "  <AuthenticationMethod>Basic</AuthenticationMethod>\n"
    "  <TrustedNetworks>1.2.3.0/24</TrustedNetworks>\n"
    "  <AuthenticationMethod />\n"
    "  <TrustedNetworks>9.9.9.0/24</TrustedNetworks>\n</Config>"
)
# The nastiest shape: a stale pair SITS BEFORE a correct one, so a seeder that
# only checked "is the wanted text present" would leave the stale pair winning.
STALE_BEFORE_DESIRED = (
    "<Config>\n  <AuthenticationMethod>Forms</AuthenticationMethod>\n"
    "  <TrustedNetworks>1.2.3.0/24</TrustedNetworks>\n"
    "  <AuthenticationMethod>External</AuthenticationMethod>\n"
    "  <TrustedNetworks>10.42.0.0/16</TrustedNetworks>\n</Config>"
)
# Valid XML a pretty-printer or a restore can produce: the element spans lines,
# so a single-line strip pattern leaves the stale value in front of the live one.
MULTILINE = (
    "<Config>\n  <AuthenticationMethod>\n    Forms\n  </AuthenticationMethod>\n"
    "  <Port>8989</Port>\n"
    "  <TrustedNetworks>\n    1.2.3.0/24\n  </TrustedNetworks>\n</Config>\n"
)
WHITESPACE_IN_TAGS = (
    "<Config>\n  <AuthenticationMethod >  Forms  </AuthenticationMethod >\n"
    "  <Port>8989</Port>\n</Config>\n"
)
MIXED = (
    "<Config>\n  <AuthenticationMethod>\n Forms\n</AuthenticationMethod>\n"
    "  <AuthenticationMethod>External</AuthenticationMethod>\n"
    "  <TrustedNetworks />\n  <TrustedNetworks>\n 9.9.9.0/24\n</TrustedNetworks>\n"
    "  <Port>8989</Port>\n</Config>\n"
)
# A commented-out root tag ABOVE the real one, and an element commented out in
# place: a scan blind to comment spans inserts inside the comment.
COMMENTED_ROOT = (
    "<!--\n<Config>\n  <AuthenticationMethod>Forms</AuthenticationMethod>\n"
    "</Config>\n-->\n<Config>\n  <Port>8989</Port>\n"
    "  <AuthenticationMethod>Forms</AuthenticationMethod>\n</Config>\n"
)
COMMENTED_ELEMENT = (
    "<Config>\n  <!-- <AuthenticationMethod>Basic</AuthenticationMethod> -->\n"
    "  <Port>8989</Port>\n  <TrustedNetworks>1.2.3.0/24</TrustedNetworks>\n</Config>\n"
)
NO_ROOT = "<Nope/>"
# No real root tag at all, so there is nothing to patch and nothing to keep.
ONLY_COMMENTS = (
    "<!-- <Config>\n  <AuthenticationMethod>Forms</AuthenticationMethod>\n"
    "</Config> -->\n"
)
# A value holding a bare `<` is not valid XML and defeats the strip, which is
# what the fail-closed count guard is for.
UNSTRIPPABLE = (
    "<Config>\n  <AuthenticationMethod>a<b</AuthenticationMethod>\n"
    "  <TrustedNetworks></TrustedNetworks>\n</Config>\n"
)
SHAPES = [EMPTY_ELEMENT, NO_ELEMENT, SELF_CLOSING, COMPACT, DUPLICATES,
          STALE_BEFORE_DESIRED, MULTILINE, WHITESPACE_IN_TAGS, MIXED,
          COMMENTED_ROOT, COMMENTED_ELEMENT]


@pytest.fixture(scope="module")
def shim_path(tmp_path_factory) -> str:
    """PATH for running the script. The container runs busybox, whose `stat -c`
    a BSD host lacks; on such a host a translating shim goes in front so the rest
    of the script still runs. chown/chmod to their current values work as-is."""
    probe = subprocess.run(["stat", "-c", "%a", str(REPO)], capture_output=True)
    if probe.returncode == 0:
        return os.environ.get("PATH", "/usr/bin:/bin")
    shim_dir = tmp_path_factory.mktemp("statshim")
    stat_shim = shim_dir / "stat"
    stat_shim.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "-c" ]; then\n'
        '  fmt=$(printf "%s" "$2" | sed "s/%a/%Lp/g"); shift 2\n'
        '  exec /usr/bin/stat -f "$fmt" "$@"\n'
        'fi\n'
        'exec /usr/bin/stat "$@"\n'
    )
    stat_shim.chmod(0o755)
    return f"{shim_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"


def _run(script: str, conf: Path, cidr: str, path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", "-ec", script.replace(CONFIG_PATH, str(conf))],
        capture_output=True, text=True, check=False,
        env={"TRUSTED_NETWORKS": cidr, "PATH": path},
    )


@pytest.fixture(scope="module")
def script(rendered) -> str:
    return _seeder(rendered["sonarr"])["command"][-1]


@pytest.fixture(scope="module")
def pod_cidr(cluster_config) -> str:
    return cluster_config["cluster_pod_cidr"]


def _elements(conf: Path) -> tuple[list[str], list[str]]:
    """(AuthenticationMethod values, TrustedNetworks values) as written.

    Read with a regex rather than an XML parser: the subject is exactly the text
    the script emits, and counting occurrences is what catches a duplicate.
    """
    text = conf.read_text()
    return tuple(
        re.findall(rf"<{tag}>([^<]*)</{tag}>", text)
        for tag in ("AuthenticationMethod", "TrustedNetworks")
    )


def _any_spelling(conf: Path) -> tuple[int, int]:
    """How many of each element the file holds, self-closing ones included."""
    text = conf.read_text()
    return (text.count("<AuthenticationMethod"), text.count("<TrustedNetworks"))


@pytest.mark.parametrize("before", SHAPES)
def test_both_elements_land_exactly_once(script, tmp_path, pod_cidr, shim_path, before):
    """A duplicate is read from the first copy, so a stale one would still win."""
    conf = tmp_path / "config.xml"
    conf.write_text(before)
    result = _run(script, conf, pod_cidr, shim_path)
    assert result.returncode == 0, result.stderr
    assert _elements(conf) == (["External"], [pod_cidr])
    assert _any_spelling(conf) == (1, 1)


def test_a_stale_pair_before_a_correct_one_is_removed(script, tmp_path, pod_cidr, shim_path):
    """The mutation this guards: a seeder that only asks 'is the wanted text
    present' takes the fast path here and leaves the stale pair in front."""
    conf = tmp_path / "config.xml"
    conf.write_text(STALE_BEFORE_DESIRED)
    assert _run(script, conf, pod_cidr, shim_path).returncode == 0
    assert "1.2.3.0/24" not in conf.read_text()
    assert "Forms" not in conf.read_text()


@pytest.mark.parametrize("before", SHAPES)
def test_a_second_run_changes_nothing(script, tmp_path, pod_cidr, shim_path, before):
    conf = tmp_path / "config.xml"
    conf.write_text(before)
    assert _run(script, conf, pod_cidr, shim_path).returncode == 0
    after_first = conf.read_text()
    second = _run(script, conf, pod_cidr, shim_path)
    assert second.returncode == 0, second.stderr
    assert conf.read_text() == after_first
    assert "no change" in second.stdout


def test_the_trusted_network_is_the_proxy_not_the_clients(
    script, tmp_path, cluster_config, shim_path
):
    """Naming a client range instead would leave the proxy untrusted and the
    forwarded address still unread."""
    conf = tmp_path / "config.xml"
    conf.write_text(EMPTY_ELEMENT)
    pod_cidr = cluster_config["cluster_pod_cidr"]
    assert _run(script, conf, pod_cidr, shim_path).returncode == 0
    written = _elements(conf)[1]
    assert written == [pod_cidr]
    for key in ("cluster_lan_cidr", "cluster_tailnet_cidr", "cluster_home_cidr"):
        assert cluster_config[key] not in written
    # Traefik forwards from a pod address, so that address must fall inside it.
    assert ipaddress.ip_address("10.42.3.70") in ipaddress.ip_network(pod_cidr)


def test_everything_else_in_the_file_survives(script, tmp_path, pod_cidr, shim_path):
    """The ApiKey in this file is what every API client authenticates with."""
    conf = tmp_path / "config.xml"
    conf.write_text(EMPTY_ELEMENT)
    assert _run(script, conf, pod_cidr, shim_path).returncode == 0
    kept = conf.read_text()
    for line in ("<Port>8989</Port>", "<ApiKey>abc</ApiKey>",
                 "<AuthenticationRequired>DisabledForLocalAddresses"):
        assert line in kept
    assert "Forms" not in kept


def test_the_file_mode_survives_the_rewrite(script, tmp_path, pod_cidr, shim_path):
    """The replacement is a fresh file moved into place, so it has to carry the
    original's mode or the app loses access to its own config."""
    conf = tmp_path / "config.xml"
    conf.write_text(EMPTY_ELEMENT)
    conf.chmod(0o640)
    assert _run(script, conf, pod_cidr, shim_path).returncode == 0
    assert conf.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("before", [*SHAPES, NO_ROOT, UNSTRIPPABLE, ONLY_COMMENTS])
def test_no_leftover_temp_file(script, tmp_path, pod_cidr, shim_path, before):
    """A stray staging file beside the real one confuses a restore. The trap has
    to clear it on the error paths too, which is where one would survive."""
    conf = tmp_path / "config.xml"
    conf.write_text(before)
    _run(script, conf, pod_cidr, shim_path)
    assert [p.name for p in tmp_path.iterdir()] == ["config.xml"]


def test_a_fresh_install_is_seeded_not_skipped(script, tmp_path, pod_cidr, shim_path):
    """Skipping would let the app write a Forms default and keep its own login
    until the next restart. The apps merge their defaults into this file."""
    conf = tmp_path / "config.xml"
    result = _run(script, conf, pod_cidr, shim_path)
    assert result.returncode == 0, result.stderr
    assert conf.exists(), "a fresh install was left with no config.xml"
    assert _elements(conf) == (["External"], [pod_cidr])
    assert _any_spelling(conf) == (1, 1)
    assert [p.name for p in tmp_path.iterdir()] == ["config.xml"]
    # Only the two elements, so nothing here can contradict an app default.
    assert conf.read_text().count("<") == 6


def test_an_unstrippable_element_is_refused_not_duplicated(
    script, tmp_path, pod_cidr, shim_path
):
    """Fail closed: when the strip cannot remove a stale element the result would
    carry two, and the app answers from the first. Refuse instead of replacing."""
    conf = tmp_path / "config.xml"
    conf.write_text(UNSTRIPPABLE)
    result = _run(script, conf, pod_cidr, shim_path)
    assert result.returncode == 1
    assert "AuthenticationMethod x2" in result.stderr
    assert conf.read_text() == UNSTRIPPABLE


def test_a_planted_symlink_at_the_predictable_path_is_not_followed(
    script, tmp_path, pod_cidr, shim_path
):
    """Anything that can write /config could pre-create the old fixed staging
    path; writing through it would clobber whatever it points at."""
    conf = tmp_path / "config.xml"
    conf.write_text(EMPTY_ELEMENT)
    victim = tmp_path / "victim"
    victim.write_text("do not clobber")
    planted = tmp_path / "config.xml.seed-tmp"
    planted.symlink_to(victim)
    assert _run(script, conf, pod_cidr, shim_path).returncode == 0
    assert victim.read_text() == "do not clobber"
    assert planted.is_symlink()
    assert _elements(conf) == (["External"], [pod_cidr])


@pytest.mark.parametrize("before", [COMMENTED_ROOT, COMMENTED_ELEMENT])
def test_a_commented_span_is_never_the_insertion_point(
    script, tmp_path, pod_cidr, shim_path, before
):
    """The mutation this guards: a scan that does not strip comment spans takes
    a commented-out root tag as the insertion point, so both elements land
    inside the comment and the app boots on its stored Forms setting."""
    conf = tmp_path / "config.xml"
    conf.write_text(before)
    assert _run(script, conf, pod_cidr, shim_path).returncode == 0
    text = conf.read_text()
    assert "<!--" not in text and "-->" not in text
    assert "Forms" not in text and "Basic" not in text
    assert _elements(conf) == (["External"], [pod_cidr])
    assert "<Port>8989</Port>" in text


def test_a_file_of_only_comments_is_refused_not_emptied(
    script, tmp_path, pod_cidr, shim_path
):
    """No real root tag survives the strip, so there is nowhere to insert and
    replacing would hand the app a config with neither element."""
    conf = tmp_path / "config.xml"
    conf.write_text(ONLY_COMMENTS)
    result = _run(script, conf, pod_cidr, shim_path)
    assert result.returncode == 1
    assert "AuthenticationMethod x0" in result.stderr
    assert conf.read_text() == ONLY_COMMENTS


def test_an_unpatchable_config_fails_the_pod(script, tmp_path, pod_cidr, shim_path):
    """Starting the app anyway would serve its own login prompt instead."""
    conf = tmp_path / "config.xml"
    conf.write_text(NO_ROOT)
    result = _run(script, conf, pod_cidr, shim_path)
    assert result.returncode == 1
    assert "ERROR" in result.stderr
    assert conf.read_text() == NO_ROOT
