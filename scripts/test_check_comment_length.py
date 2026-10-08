"""Tests for scripts/check-comment-length.py, the comment-block length gate.

Covers every comment shape, the CRITICAL: budget, the glob and config plumbing
and the three exit codes; every rule has a case that makes the gate FAIL.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from script_loader import load_script

ccl = load_script("check-comment-length.py")
SCRIPT = Path(__file__).resolve().parent / "check-comment-length.py"
CONFIG = Path(__file__).resolve().parent / "comment-length.yaml"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def _run_without_pyyaml(path_entry: Path, *args: str) -> subprocess.CompletedProcess:
    """The gate with a PyYAML that raises on import, as a JSON-config job has."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(path_entry), "PATH": "/usr/bin:/bin"},
    )


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestHashRuns:
    def test_three_line_run_passes(self, tmp_path):
        write(tmp_path, "a.sh", "# one\n# two\n# three\necho hi\n")
        assert run(str(tmp_path)).returncode == 0

    def test_four_line_run_fails(self, tmp_path):
        write(tmp_path, "a.sh", "# one\n# two\n# three\n# four\necho hi\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "4 content lines" in proc.stdout

    def test_blank_comment_lines_do_not_count(self, tmp_path):
        write(tmp_path, "a.sh", "# one\n#\n# two\n#\n# three\necho hi\n")
        assert run(str(tmp_path)).returncode == 0

    def test_a_blank_line_ends_the_run(self, tmp_path):
        write(tmp_path, "a.sh", "# one\n# two\n\n# three\n# four\necho hi\n")
        assert run(str(tmp_path)).returncode == 0

    def test_trailing_comments_are_not_a_run(self, tmp_path):
        body = "".join(f"echo {n}  # note {n}\n" for n in range(6))
        write(tmp_path, "a.sh", body)
        assert run(str(tmp_path)).returncode == 0

    def test_shebang_does_not_open_a_run(self, tmp_path):
        write(tmp_path, "a.sh", "#!/usr/bin/env bash\n# one\n# two\n# three\necho hi\n")
        assert run(str(tmp_path)).returncode == 0

    def test_yaml_banner_is_checked(self, tmp_path):
        write(tmp_path, "a.yml", "---\n# a\n# b\n# c\n# d\nkey: value\n")
        assert run(str(tmp_path)).returncode == 1


class TestCriticalBudget:
    def test_critical_block_of_eight_passes(self, tmp_path):
        body = "# CRITICAL: guards an outage\n" + "".join(
            f"# line {n}\n" for n in range(7)
        )
        write(tmp_path, "a.sh", body + "echo hi\n")
        assert run(str(tmp_path)).returncode == 0

    def test_critical_block_of_nine_fails(self, tmp_path):
        body = "# CRITICAL: guards an outage\n" + "".join(
            f"# line {n}\n" for n in range(8)
        )
        write(tmp_path, "a.sh", body + "echo hi\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "limit 8" in proc.stdout

    def test_the_marker_must_open_the_block(self, tmp_path):
        body = "# intro\n# CRITICAL: guards an outage\n" + "".join(
            f"# line {n}\n" for n in range(3)
        )
        write(tmp_path, "a.sh", body + "echo hi\n")
        assert run(str(tmp_path)).returncode == 1


class TestSlashComments:
    def test_long_double_slash_run_fails(self, tmp_path):
        write(tmp_path, "main.tf", "// a\n// b\n// c\n// d\nresource x {}\n")
        assert run(str(tmp_path)).returncode == 1

    def test_long_block_comment_fails(self, tmp_path):
        write(tmp_path, "main.tf", "/* a\n * b\n * c\n * d\n */\nresource x {}\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "main.tf:1" in proc.stdout

    def test_short_block_comment_passes(self, tmp_path):
        write(tmp_path, "main.tf", "/* a\n * b\n */\nresource x {}\n")
        assert run(str(tmp_path)).returncode == 0

    def test_a_glob_in_a_string_literal_is_not_a_block_opener(self, tmp_path):
        write(
            tmp_path,
            "main.tf",
            'variable "arn" {\n  default = "arn:aws:s3:::bucket/*"\n}\n'
            + "".join("resource x%d {}\n" % n for n in range(10)),
        )
        proc = run(str(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_a_string_glob_does_not_hide_a_real_over_long_block(self, tmp_path):
        write(
            tmp_path,
            "main.tf",
            'variable "redirect" {\n  default = "https://example.com/*"\n}\n'
            + "".join("resource x%d {}\n" % n for n in range(10))
            + "/* a\n * b\n * c\n * d\n */\nresource y {}\n",
        )
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "main.tf:14" in proc.stdout, proc.stdout

    def test_an_unterminated_block_opener_reports_nothing(self, tmp_path):
        """An unterminated `/*` is the language's own error, not a long block."""
        write(
            tmp_path,
            "main.tf",
            "/* a\n" + "".join("resource x%d {}\n" % n for n in range(20)),
        )
        proc = run(str(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_two_openers_before_a_closer_are_not_one_block(self, tmp_path):
        write(
            tmp_path,
            "main.tf",
            "/* a\n/* b\n"
            + "".join("resource x%d {}\n" % n for n in range(5))
            + "*/\nresource y {}\n",
        )
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "main.tf:2" in proc.stdout, proc.stdout
        assert "main.tf:1" not in proc.stdout, proc.stdout

    def test_an_escaped_quote_does_not_unbalance_the_string_scan(self, tmp_path):
        line = '  default = "say \\" hi /*"'
        assert ccl._opener_at(line, "/*") == -1
        write(
            tmp_path,
            "main.tf",
            'variable "x" {\n' + line + "\n}\n"
            + "".join("resource x%d {}\n" % n for n in range(10)),
        )
        proc = run(str(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_long_jinja_comment_fails(self, tmp_path):
        write(tmp_path, "t.j2", "{# a\n   b\n   c\n   d #}\nvalue\n")
        assert run(str(tmp_path)).returncode == 1


class TestPythonDocstrings:
    def test_long_module_docstring_fails(self, tmp_path):
        write(tmp_path, "m.py", '"""a\n\nb\nc\nd\n"""\nx = 1\n')
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "m.py:1" in proc.stdout

    def test_summary_blank_and_two_body_lines_pass(self, tmp_path):
        write(tmp_path, "m.py", '"""a\n\nb\nc\n"""\nx = 1\n')
        assert run(str(tmp_path)).returncode == 0

    def test_long_function_docstring_fails(self, tmp_path):
        write(tmp_path, "m.py", 'def f():\n    """a\n\n    b\n    c\n    d\n    """\n')
        assert run(str(tmp_path)).returncode == 1

    def test_a_long_non_docstring_string_is_ignored(self, tmp_path):
        write(tmp_path, "m.py", 'x = 1\nY = """a\nb\nc\nd\ne\n"""\n')
        assert run(str(tmp_path)).returncode == 0

    def test_unparseable_python_is_an_operator_error(self, tmp_path):
        write(tmp_path, "broken.py", "def f(:\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 2
        assert "broken.py" in proc.stderr


class TestSelection:
    def test_unknown_suffix_is_skipped(self, tmp_path):
        write(tmp_path, "notes.md", "# a\n# b\n# c\n# d\n")
        write(tmp_path, "ok.sh", "echo hi\n")
        assert run(str(tmp_path)).returncode == 0

    def test_a_dockerfile_stage_suffix_is_still_a_dockerfile(self, tmp_path):
        """This repo ships docker/<app>/Dockerfile.<stage>; no table has `.codex`."""
        write(tmp_path, "Dockerfile.codex", "# a\n# b\n# c\n# d\nFROM scratch\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "Dockerfile.codex:1:" in proc.stdout

    def test_a_jinja_template_source_resolves_to_the_rendered_markers(self, tmp_path):
        """A copier `.jinja` source is the only copy of the rendered comment."""
        write(tmp_path, "main.tf.jinja", "# a\n# b\n# c\n# d\nlocals {}\n")
        write(tmp_path, "Dockerfile.jinja", "# a\n# b\n# c\n# d\nFROM scratch\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "main.tf.jinja:1:" in proc.stdout
        assert "Dockerfile.jinja:1:" in proc.stdout

    def test_a_jinja_source_of_an_unknown_language_is_still_skipped(self, tmp_path):
        write(tmp_path, "notes.md.jinja", "# a\n# b\n# c\n# d\n")
        write(tmp_path, "ok.sh", "echo hi\n")
        assert run(str(tmp_path)).returncode == 0

    def test_a_conditional_copier_path_resolves_to_the_rendered_suffix(self, tmp_path):
        """Left in place, `{% endif %}` reads as the suffix and skips the file."""
        name = "{% if ci_shape == 'gitlab' %}.gitlab-ci.yml{% endif %}.jinja"
        write(tmp_path, name, "# a\n# b\n# c\n# d\nstages: []\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "%s:1:" % name in proc.stdout

    def test_a_jinja_block_comment_is_checked_whatever_the_file_renders_into(
        self, tmp_path
    ):
        """`.yaml` has no block delimiters, so only the .jinja source adds them."""
        write(tmp_path, "deployment.yaml.jinja", "{#\na\nb\nc\nd\n#}\nkind: X\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "deployment.yaml.jinja:1:" in proc.stdout

    def test_a_short_jinja_block_comment_passes(self, tmp_path):
        write(tmp_path, "deployment.yaml.jinja", "{#\na\nb\n#}\nkind: X\n")
        assert run(str(tmp_path)).returncode == 0

    def test_a_j2_jinja_source_reports_one_block_not_two(self, tmp_path):
        """`.j2` already carries the jinja delimiters, so a `.j2.jinja` source
        must not double-count the same block."""
        write(tmp_path, "unit.service.j2.jinja", "{#\na\nb\nc\nd\n#}\n[Unit]\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert proc.stdout.count("unit.service.j2.jinja:1:") == 1

    def test_a_gitattributes_block_over_the_limit_fails(self, tmp_path):
        write(tmp_path, ".gitattributes", "# a\n# b\n# c\n# d\n* text=auto\n")
        assert run(str(tmp_path)).returncode == 1

    def test_overlapping_roots_report_a_finding_once(self, tmp_path):
        write(tmp_path, "src/a.sh", "# a\n# b\n# c\n# d\necho hi\n")
        proc = run(str(tmp_path), str(tmp_path / "src"))
        assert proc.returncode == 1
        assert proc.stdout.count("a.sh:1:") == 1

    def test_exclude_glob_drops_the_offender(self, tmp_path):
        write(tmp_path, "vendor/a.sh", "# a\n# b\n# c\n# d\necho hi\n")
        write(tmp_path, "ok.sh", "echo hi\n")
        assert run(str(tmp_path), "--exclude", "*/vendor/*").returncode == 0
        assert run(str(tmp_path)).returncode == 1

    def test_include_glob_narrows_the_scan(self, tmp_path):
        write(tmp_path, "a.sh", "# a\n# b\n# c\n# d\necho hi\n")
        write(tmp_path, "b.yml", "key: value\n")
        assert run(str(tmp_path), "--include", "*.yml").returncode == 0

    def test_default_excludes_skip_dot_git(self, tmp_path):
        write(tmp_path, ".git/hooks/a.sh", "# a\n# b\n# c\n# d\n")
        write(tmp_path, "ok.sh", "echo hi\n")
        assert run(str(tmp_path)).returncode == 0

    def test_a_single_file_argument_is_scanned(self, tmp_path):
        bad = write(tmp_path, "a.sh", "# a\n# b\n# c\n# d\necho hi\n")
        assert run(str(bad)).returncode == 1


class TestConfig:
    def test_config_supplies_paths_and_limits(self, tmp_path):
        write(tmp_path, "a.sh", "# a\n# b\n# c\n# d\necho hi\n")
        config = write(
            tmp_path,
            "cl.yml",
            json.dumps(
                {"paths": [str(tmp_path)], "max_lines": 4, "critical_max_lines": 9}
            ),
        )
        assert run("--config", str(config)).returncode == 0

    def test_config_exclude_is_honoured(self, tmp_path):
        write(tmp_path, "vendor/a.sh", "# a\n# b\n# c\n# d\necho hi\n")
        config = write(
            tmp_path,
            "cl.yml",
            json.dumps({"paths": [str(tmp_path)], "exclude": ["*/vendor/*"]}),
        )
        assert run("--config", str(config)).returncode == 0

    def test_a_json_config_applies_without_pyyaml(self, tmp_path):
        """The JSON escape hatch: a config job needs no PyYAML on PATH."""
        (tmp_path / "yaml.py").write_text('raise ImportError("poisoned")\n')
        write(tmp_path, "src/a.sh", "# a\n# b\n# c\n# d\necho hi\n")
        config = write(
            tmp_path,
            "cl.json",
            json.dumps({"paths": [str(tmp_path / "src")], "max_lines": 4}),
        )
        assert _run_without_pyyaml(tmp_path, "--config", str(config)).returncode == 0
        write(tmp_path, "src/b.sh", "# a\n# b\n# c\n# d\n# e\necho hi\n")
        assert _run_without_pyyaml(tmp_path, "--config", str(config)).returncode == 1

    def test_missing_config_is_an_operator_error(self, tmp_path):
        proc = run("--config", str(tmp_path / "absent.yml"))
        assert proc.returncode == 2
        assert "config not found" in proc.stderr

    def test_non_mapping_config_is_an_operator_error(self, tmp_path):
        config = write(tmp_path, "cl.yml", "- a\n- b\n")
        assert run("--config", str(config)).returncode == 2

    def test_inverted_limits_are_an_operator_error(self, tmp_path):
        write(tmp_path, "a.sh", "echo hi\n")
        assert run(str(tmp_path), "--max", "9", "--critical-max", "2").returncode == 2

    def test_a_zero_max_is_an_operator_error(self, tmp_path):
        write(tmp_path, "a.sh", "# one\n")
        assert ccl.main(["--max", "0", str(tmp_path)]) == 2

    def test_a_non_integer_max_in_the_config_is_an_operator_error(self, tmp_path):
        write(tmp_path, "a.sh", "# one\n")
        config = write(tmp_path, "cfg.yml", 'max_lines: "4"\n')
        proc = run("--config", str(config), str(tmp_path))
        assert proc.returncode == 2
        assert "must be integers" in proc.stderr

    def test_a_string_include_in_the_config_is_an_operator_error(self, tmp_path):
        write(tmp_path, "a.sh", "# one\n")
        config = write(tmp_path, "cfg.yml", 'include: "src/**"\n')
        proc = run("--config", str(config), str(tmp_path))
        assert proc.returncode == 2
        assert "list of globs" in proc.stderr


class TestOperatorErrors:
    def test_missing_path_exits_2(self, tmp_path):
        proc = run(str(tmp_path / "absent"))
        assert proc.returncode == 2
        assert "path not found" in proc.stderr

    def test_no_scannable_files_exits_2(self, tmp_path):
        write(tmp_path, "notes.md", "text\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 2
        assert "no scannable files" in proc.stderr

    def test_an_undecodable_file_is_an_operator_error(self, tmp_path, capsys):
        path = tmp_path / "latin.sh"
        path.write_bytes("#!/bin/sh\n# caf\xe9\n".encode("latin-1"))
        assert ccl.main([str(tmp_path)]) == 2
        assert "latin.sh" in capsys.readouterr().err


class TestUnit:
    def test_limit_for_reads_the_critical_marker(self):
        limits = ccl.Limits()
        assert limits.limit_for("CRITICAL: x") == 8
        assert limits.limit_for("anything else") == 3

    def test_check_file_returns_findings_sorted_by_line(self, tmp_path):
        path = write(
            tmp_path,
            "a.sh",
            "# a\n# b\n# c\n# d\n\necho hi\n\n# e\n# f\n# g\n# h\n",
        )
        findings = ccl.check_file(path, ccl.Limits())
        assert [f.line for f in findings] == [1, 8]


class TestBashParameterExpansionIsNotAJinjaComment:
    """`${#arr[@]}` in a .j2 shell template opened a block that ran to EOF."""

    def test_it_opens_no_block(self, tmp_path):
        path = tmp_path / "script.sh.j2"
        path.write_text(
            "#!/bin/bash\n"
            "count() {\n"
            "  [[ ${#EXCLUDE_LIST[@]} -gt 0 ]] || return 0\n"
            "}\n" + "echo line\n" * 20
        )
        assert ccl.check_file(path, ccl.Limits()) == []

    def test_a_real_jinja_block_is_still_caught(self, tmp_path):
        path = tmp_path / "real.sh.j2"
        path.write_text(
            "{# one\n   two\n   three\n   four #}\n"
            "  [[ ${#ARR[@]} -gt 0 ]] || return 0\n"
        )
        findings = ccl.check_file(path, ccl.Limits())
        assert len(findings) == 1
        assert findings[0].length == 4

    def test_a_shell_glob_in_a_template_opens_no_block(self, tmp_path):
        """`/*` at the start of a stripped line is a case pattern, not a comment."""
        write(
            tmp_path,
            "case.sh.j2",
            'case "$x" in\n  "$src"/*)\n    echo hit\n    ;;\nesac\n',
        )
        assert run(str(tmp_path)).returncode == 0


class TestDockerfileParserDirectives:
    """A leading `# syntax=` / `# escape=` line is machine-readable config, so
    it neither counts toward the limit nor joins the header block below it."""

    def test_a_directive_plus_a_three_line_header_passes(self, tmp_path):
        write(
            tmp_path,
            "Dockerfile",
            "# syntax=docker/dockerfile:1\n# one\n# two\n# three\nFROM scratch\n",
        )
        assert run(str(tmp_path)).returncode == 0

    def test_a_genuine_four_line_header_still_fails(self, tmp_path):
        """Mutation guard: the directive is exempt, the prose below it is not."""
        write(
            tmp_path,
            "Dockerfile",
            "# syntax=docker/dockerfile:1\n# one\n# two\n# three\n# four\n"
            "FROM scratch\n",
        )
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "Dockerfile:2:" in proc.stdout

    def test_an_escape_directive_is_exempt_too(self, tmp_path):
        write(
            tmp_path,
            "Dockerfile",
            "# escape=`\n# one\n# two\n# three\nFROM scratch\n",
        )
        assert run(str(tmp_path)).returncode == 0

    def test_a_directive_spelling_later_in_the_file_is_a_comment(self, tmp_path):
        """Only the preamble carries directives, so a later `# syntax=` line is
        prose and counts."""
        write(
            tmp_path,
            "Dockerfile",
            "FROM scratch\n# one\n# two\n# three\n# syntax=docker/dockerfile:1\n",
        )
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "Dockerfile:2:" in proc.stdout

    def test_a_stage_suffixed_dockerfile_gets_the_same_exemption(self, tmp_path):
        write(
            tmp_path,
            "Dockerfile.build",
            "# syntax=docker/dockerfile:1\n# one\n# two\n# three\nFROM scratch\n",
        )
        assert run(str(tmp_path)).returncode == 0

    def test_a_shell_script_directive_spelling_is_still_a_comment(self, tmp_path):
        """The exemption is Dockerfile-only: the same line in a .sh is prose."""
        write(tmp_path, "a.sh", "# syntax=x\n# one\n# two\n# three\necho hi\n")
        proc = run(str(tmp_path))
        assert proc.returncode == 1
        assert "a.sh:1:" in proc.stdout


def test_the_gate_is_clean_on_its_own_source():
    assert run(str(SCRIPT)).returncode == 0


def test_the_repo_config_is_clean():
    """The wired-in run: `task lint:comment-length` and its CI arm pass this."""
    proc = run("--config", str(CONFIG))
    assert proc.returncode == 0, proc.stdout + proc.stderr
