#!/usr/bin/env python3
"""Assert every hand-written molecule test-image tag equals the library ref.

CI overrides MOLECULE_TEST_IMAGE, so only a local run reads the literal and a
stale one hides behind a green pipeline. `--fix` rewrites them. docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml  # noqa: F401  (ci_yaml needs it; imported here to name it)
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import ci_yaml  # noqa: E402  (resolved from this script's own directory)
except ImportError:
    print(
        "ERROR: ci_yaml.py must sit next to this script — vendor both "
        "(see weisssrv-lib scripts/vendorable-paths.yml).",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

LIB_PROJECT = "eric/weisssrv-lib"
REF_VAR = "WEISSSRV_LIB_REF"
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# Files carrying the literal. Globs resolve against the tree holding the CI
# file, so a scenario added later under ANY name is covered.
DEFAULT_SOURCES = (
    "ansible/integration-tests/*/molecule/*/molecule.yml",
    "ansible/integration-tests/*/molecule/*/molecule.yaml",
    "ansible/TESTING.md",
)

# The image names the library publishes for molecule runs.
IMAGE_NAMES = ("test", "ci")


class OperatorError(Exception):
    """A bad invocation or a scan that inspected nothing: exit 2, not a finding."""


def image_re(project: str = LIB_PROJECT) -> re.Pattern:
    """The tag half of `<project>/molecule-{test,ci}:<tag>`.

    Anchored on the image path, so an unrelated `:v1.2.3` is never rewritten,
    and on a `v`-prefixed tag, so a `:local` sentinel is not a stale pin.
    """
    return re.compile(
        r"(%s/molecule-(?:%s):)(v[\w.\-]+)"
        % (re.escape(project), "|".join(IMAGE_NAMES))
    )


def declared_ref(ci_file: Path, ref_var: str = REF_VAR) -> str:
    """variables.<ref_var> from the pipeline file — the single source for the tag."""
    try:
        doc = ci_yaml.load_ci(ci_file, loader=ci_yaml.NullTagCILoader)
    except OSError as exc:
        raise OperatorError("%s: %s" % (ci_file, exc)) from exc
    variables = doc.get("variables")
    want = variables.get(ref_var) if isinstance(variables, dict) else None
    if not want:
        raise OperatorError(
            "%s: variables.%s is not set (the single source for the pin)"
            % (ci_file, ref_var)
        )
    if not isinstance(want, str) or TAG_RE.fullmatch(want) is None:
        raise OperatorError(
            "%s: %s is %r, which is not a release tag (vX.Y.Z)"
            % (ci_file, ref_var, want)
        )
    return want


def sources(root: Path, patterns=DEFAULT_SOURCES) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        found.extend(sorted(root.glob(pattern)))
    return sorted(set(found))


def check(
    want: str,
    root: Path,
    patterns=DEFAULT_SOURCES,
    project: str = LIB_PROJECT,
    ref_var: str = REF_VAR,
) -> tuple[list[str], int]:
    """-> (drift findings, literals inspected). The count feeds the vacuity guard."""
    pattern = image_re(project)
    problems: list[str] = []
    seen = 0
    for path in sources(root, patterns):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise OperatorError("%s: %s" % (path, exc)) from exc
        for line_no, line in enumerate(text.splitlines(), 1):
            for _, tag in pattern.findall(line):
                seen += 1
                if tag != want:
                    problems.append(
                        "%s:%d: molecule image pins %r, but %s is %r"
                        % (path.relative_to(root), line_no, tag, ref_var, want)
                    )
    return problems, seen


def fix(
    want: str, root: Path, patterns=DEFAULT_SOURCES, project: str = LIB_PROJECT
) -> int:
    """Rewrite every literal to `want`; -> the number of files changed."""
    pattern = image_re(project)
    changed = 0
    for path in sources(root, patterns):
        text = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(lambda m: m.group(1) + want, text)
        if count and updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--ci-file", type=Path, default=Path(".gitlab-ci.yml"),
        help="read the ref from this file and act on the tree containing it",
    )
    parser.add_argument(
        "--project", default=LIB_PROJECT,
        help="library project path in the image reference (default: %(default)s)",
    )
    parser.add_argument(
        "--ref-var", default=REF_VAR,
        help="pipeline variable holding the pin (default: %(default)s)",
    )
    parser.add_argument(
        "--source", action="append", default=None, metavar="GLOB",
        help="file carrying the literal, relative to the tree; repeatable "
             "(default: the molecule scenarios and ansible/TESTING.md)",
    )
    parser.add_argument(
        "--fix", action="store_true", help="rewrite the literals to the declared ref",
    )
    args = parser.parse_args(argv)

    patterns = tuple(args.source) if args.source else DEFAULT_SOURCES
    ci_file = args.ci_file.resolve()
    root = ci_file.parent
    try:
        want = declared_ref(ci_file, args.ref_var)
        if args.fix:
            changed = fix(want, root, patterns, args.project)
        problems, seen = check(want, root, patterns, args.project, args.ref_var)
    except OperatorError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    if problems:
        print("check-molecule-image-pin: FAILED", file=sys.stderr)
        for problem in problems:
            print("  %s" % problem, file=sys.stderr)
        print(
            "\nFix with: scripts/check-molecule-image-pin.py --fix", file=sys.stderr
        )
        return 1
    if not seen:
        # Nothing to check is not the same as everything being fine: once the
        # scenarios stop spelling the fallback, say so rather than pass on an
        # empty set.
        print(
            "ERROR: no molecule image literal found under %s — has the fallback "
            "moved, or is --project/--source wrong?" % root,
            file=sys.stderr,
        )
        return 2
    if args.fix:
        print("check-molecule-image-pin: rewrote %d file(s) to %s" % (changed, want))
        return 0
    print(
        "check-molecule-image-pin: OK — %d literal(s) pinned at %s" % (seen, want)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
