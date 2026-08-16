#!/usr/bin/env python3
"""Assert every hand-written `molecule-test:<tag>` literal equals WEISSSRV_LIB_REF.

The integration scenarios spell the image as
`${MOLECULE_TEST_IMAGE:-.../molecule-test:vX.Y.Z}`. CI always overrides the
variable (.gitlab/ci/integration-jobs.yml builds it from $WEISSSRV_LIB_REF), so
only a LOCAL `task ansible:test-integration-*` reads the literal — which is
exactly why a stale one is invisible: the pipeline is green while the local run
tests against an old image.

check-lib-pins.py cannot cover these (it reads the `include:` block and
ansible/requirements.yml only) and it is vendored byte-identical from
weisssrv-lib, so this site-local gate carries the same contract for the molecule
literals: they are copies of one pin, `--fix` rewrites them.

.gitlab-ci.yml is READ-ONLY here — it is the single source of the ref.

Usage:
  scripts/check-molecule-image-pin.py         # verify (exit 1 on drift)
  scripts/check-molecule-image-pin.py --fix   # rewrite the literals to the pin
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
REF_VAR = "WEISSSRV_LIB_REF"
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# Files carrying the literal. Globs are resolved relative to the repo root, so a
# scenario added later is covered without touching this list.
SOURCES = (
    "ansible/integration-tests/*/molecule/default/molecule.yml",
    "ansible/TESTING.md",
)

# The tag half of `weisssrv-lib/molecule-test:<tag>` / `molecule-ci:<tag>`.
# Anchored on the image path so an unrelated `:v1.2.3` is never rewritten.
IMAGE_RE = re.compile(r"(weisssrv-lib/molecule-(?:test|ci):)(v[\w.\-]+)")


class _RefTolerantLoader(yaml.SafeLoader):
    """SafeLoader that survives GitLab's `!reference` tag."""


_RefTolerantLoader.add_multi_constructor("!reference", lambda loader, suffix, node: None)


def declared_ref(ci_file: Path) -> str:
    doc = yaml.load(ci_file.read_text(encoding="utf-8"), Loader=_RefTolerantLoader) or {}
    want = (doc.get("variables") or {}).get(REF_VAR)
    if not want:
        raise SystemExit(f"{ci_file}: variables.{REF_VAR} is not set (the single source)")
    if not isinstance(want, str) or TAG_RE.fullmatch(want) is None:
        raise SystemExit(
            f"{ci_file}: {REF_VAR} is {want!r}, which is not a release tag (vX.Y.Z)"
        )
    return want


def sources(root: Path = REPO) -> list[Path]:
    found: list[Path] = []
    for pattern in SOURCES:
        found.extend(sorted(root.glob(pattern)))
    return found


def check(want: str, root: Path = REPO) -> list[str]:
    """Return a list of problems; empty means every literal matches the pin."""
    problems: list[str] = []
    seen = 0
    for path in sources(root):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for _, tag in IMAGE_RE.findall(line):
                seen += 1
                if tag != want:
                    problems.append(
                        f"{path.relative_to(root)}:{line_no}: molecule image pins "
                        f"{tag!r}, but {REF_VAR} is {want!r}"
                    )
    if not seen:
        # Nothing to check is not the same as everything being fine: if the
        # scenarios stop spelling the fallback, say so rather than passing an
        # empty set.
        problems.append("no molecule image literals found — has the fallback moved?")
    return problems


def fix(want: str, root: Path = REPO) -> int:
    changed = 0
    for path in sources(root):
        text = path.read_text(encoding="utf-8")
        updated, n = IMAGE_RE.subn(lambda m: m.group(1) + want, text)
        if n and updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--ci-file", type=Path, default=REPO / ".gitlab-ci.yml")
    ap.add_argument(
        "--fix", action="store_true", help="rewrite the literals to variables." + REF_VAR
    )
    args = ap.parse_args(argv)

    want = declared_ref(args.ci_file)
    if args.fix:
        changed = fix(want)
        problems = check(want)
        if problems:
            print("check-molecule-image-pin: FAILED after rewrite", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print(f"check-molecule-image-pin: rewrote {changed} file(s) to {want}")
        return 0

    problems = check(want)
    if problems:
        print("check-molecule-image-pin: FAILED", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nFix with: scripts/check-molecule-image-pin.py --fix",
            file=sys.stderr,
        )
        return 1
    print(f"check-molecule-image-pin: OK — every molecule image pinned at {want}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
