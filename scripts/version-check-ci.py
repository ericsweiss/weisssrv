#!/usr/bin/env python3
"""CI wrapper for check-versions.py: runs the check, refreshes the MR comment,
and writes the JSON report artifact. Exit 0 up to date, 1 updates, 2 errors.
Environment and inputs: docs/SCRIPTS.md - version-check-ci.py.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from urllib.request import Request, urlopen

CHECK_CMD = os.environ.get("CHECK_VERSIONS_CMD", "./scripts/check-versions.py")
LOCAL_CMD = os.environ.get("CHECK_VERSIONS_LOCAL", CHECK_CMD)
# CWD-relative default: the `artifacts:` path CI collects. Keep it stable —
# a consumer's .gitlab-ci.yml names it.
DEFAULT_OUTPUT = "version-report.json"
DEFAULT_TOKEN_ENV = "GITLAB_API_TOKEN"
# Hidden in the note body so a re-run refreshes that note instead of stacking
# an identical comment on every pipeline.
MARKER = "<!-- weisssrv:version-check -->"


def neutralize_quick_actions(text: str) -> str:
    """Indent lines starting with `/` so GitLab cannot read them as quick actions.

    Second layer behind fence_for, for the upstream error text this note embeds.
    """
    return "\n".join(" " + line if line.startswith("/") else line for line in text.splitlines())


def fence_for(text: str) -> str:
    """A code fence longer than the longest backtick run in `text`.

    A fixed ``` fence is closed early by text that contains one, which puts the
    rest of the note — quick actions included — back into markdown.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _api(url: str, token: str, method: str = "GET", payload=None):
    """One GitLab API call as the token's owner; raises on any failure."""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"PRIVATE-TOKEN": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else {}


_BOT_USER_ID = None


def _bot_user_id(api_url: str, token: str):
    """The token owner's user id, fetched once per run, or None.

    None means "do not edit anything": the marker alone would let the bot
    rewrite a human's comment that happens to quote it.
    """
    global _BOT_USER_ID
    if _BOT_USER_ID is None:
        try:
            _BOT_USER_ID = _api(f"{api_url}/user", token).get("id")
        except Exception as e:
            print(f"Warning: could not identify the API token's user ({e}).")
            _BOT_USER_ID = False
    return _BOT_USER_ID or None


def _marked_note_id(notes_url: str, token: str, api_url: str = ""):
    """id of the note a previous run of this job left, or None."""
    bot_id = _bot_user_id(api_url, token) if api_url else None
    if bot_id is None:
        return None
    try:
        notes = _api(f"{notes_url}?per_page=100", token)
    except Exception as e:
        print(f"Warning: could not list MR notes ({e}); posting a new comment.")
        return None
    if not isinstance(notes, list):
        return None
    for note in notes:
        if not isinstance(note, dict) or note.get("system"):
            continue
        author = note.get("author") if isinstance(note.get("author"), dict) else {}
        if author.get("id") == bot_id and MARKER in str(note.get("body", "")):
            return note.get("id")
    return None


def upsert_mr_comment(body: str, token_env: str = DEFAULT_TOKEN_ENV) -> None:
    """Refresh this job's MR note, or post it the first time."""
    api_url = os.environ.get("CI_API_V4_URL", "")
    project_id = os.environ.get("CI_PROJECT_ID", "")
    mr_iid = os.environ.get("CI_MERGE_REQUEST_IID", "")
    token = os.environ.get(token_env, "")

    if not all([api_url, project_id, mr_iid, token]):
        if mr_iid:
            # Surface it: a revoked or absent token must not silently swallow
            # the version comment.
            print(
                "Warning: in an MR pipeline but GitLab API URL/project/token is "
                f"incomplete; skipping MR comment (check ${token_env}).",
                file=sys.stderr,
            )
        return

    notes_url = f"{api_url}/projects/{project_id}/merge_requests/{mr_iid}/notes"
    note_id = _marked_note_id(notes_url, token, api_url)
    try:
        if note_id:
            _api(f"{notes_url}/{note_id}", token, method="PUT", payload={"body": body})
            print("MR comment updated")
        else:
            _api(notes_url, token, method="POST", payload={"body": body})
            print("MR comment posted")
    except Exception as e:
        print(f"Warning: could not post MR comment: {e}")


def _count(value) -> int:
    """A summary counter, or 0 when the producer emitted a non-number."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _services(data: dict) -> list:
    """Well-formed (dict) service entries; tolerates a missing or mis-shaped
    `services`."""
    services = data.get("services")
    if not isinstance(services, list):
        return []
    return [svc for svc in services if isinstance(svc, dict)]


def _write_report(path: str, text: str) -> None:
    """Write the report artifact, creating a --output parent dir if needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        metavar="PATH",
        help=f"where to write the JSON report artifact (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        metavar="NAME",
        help=f"env var holding the MR-comment token (default: {DEFAULT_TOKEN_ENV})",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)

    # check-versions polls ~50 services sequentially, so a partial outage can
    # take minutes. The generous env-tunable timeout keeps a slow endpoint from
    # SIGKILLing a run that mostly succeeded.
    _timeout_raw = os.environ.get("VERSION_CHECK_TIMEOUT", "600")
    try:
        timeout = int(_timeout_raw)
        if timeout <= 0:
            raise ValueError("must be positive")
    except ValueError:
        print(f"Warning: invalid VERSION_CHECK_TIMEOUT={_timeout_raw!r}; using 600s")
        timeout = 600
    try:
        result = subprocess.run(
            [*shlex.split(CHECK_CMD), "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print("Error: version check timed out")
        sys.exit(2)
    except OSError as e:
        print(f"Error: failed to execute version check: {e}")
        sys.exit(2)

    rc = result.returncode

    updates = 0
    errors = 0
    data = {}
    try:
        data = json.loads(result.stdout)
        # Non-dict payload: treat as a parse failure so the stub artifact is written.
        if not isinstance(data, dict):
            raise ValueError(
                f"version-check output is not a JSON object (got {type(data).__name__})"
            )
        _write_report(args.output, result.stdout)
        summary = data.get("summary")
        if not isinstance(summary, dict):
            # Tolerate a missing or mis-shaped summary; the defaults apply.
            summary = {}
        total = _count(summary.get("total", 0))
        up_to_date = _count(summary.get("up_to_date", 0))
        updates = _count(summary.get("updates_available", 0))
        held = _count(summary.get("updates_held", 0))
        errors = _count(summary.get("errors", 0))

        print(f"Version check: {total} services, {up_to_date} up to date, {updates} updates, {held} held, {errors} errors")

        # .get() only: a KeyError here would overwrite the valid artifact with a stub.
        if updates > 0:
            print("\nUpdates available:")
            for svc in _services(data):
                if svc.get("update_available") and not svc.get("held"):
                    print(f"  {svc.get('name', '?')}: {svc.get('current_version', '?')} -> {svc.get('latest_version', '?')}")

        if errors > 0:
            print("\nErrors:")
            for svc in _services(data):
                if svc.get("error"):
                    print(f"  {svc.get('name', '?')}: {svc.get('error')}")

        if errors > 0:
            rc = 2
        elif updates > 0:
            rc = 1
        else:
            rc = 0
        if result.returncode not in (0, 1, 2):
            print(
                f"Warning: checker exited {result.returncode} (outside the "
                "documented 0/1/2 contract); reporting 2"
            )
            rc = 2
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        print("Warning: could not parse version check output")
        print(result.stdout)
        rc = 2
        # Reset so the MR-comment block takes the parse-failure branch.
        data = {}
        # Write a self-describing stub so the artifact isn't a 0-byte or
        # raw-text file that reads like a successful empty report.
        _write_report(args.output, json.dumps(
            {
                "error": f"version-check output not parseable: {type(e).__name__}: {e}",
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            indent=2,
        ))

    # Post MR comment when there are actionable updates and/or errors.
    # Report BOTH together — a transient single-service error must not
    # suppress the actionable update table (or vice versa).
    if os.environ.get("CI_MERGE_REQUEST_IID"):
        sections = []
        # Gate each section on its row list, not on the summary counters.
        update_lines = []
        for svc in _services(data):
            if svc.get("update_available") and not svc.get("held"):
                notes = svc.get("notes", "")
                update_lines.append(
                    f"| {svc.get('name', '?')} | {svc.get('current_version', '?')} | "
                    f"{svc.get('latest_version', '?')} | {notes} |"
                )
        if update_lines:
            sections.append(
                "### Updates available\n\n"
                "| Service | Current | Latest | Notes |\n"
                "|---------|---------|--------|-------|\n"
                + "\n".join(update_lines)
            )
        err_lines = [
            f"- {svc.get('name', '?')}: {svc.get('error', 'unknown error')}"
            for svc in _services(data)
            if svc.get("error")
        ]
        if err_lines:
            sections.append("### Errors\n\n" + "\n".join(err_lines))
        elif rc == 2 and not data:
            # Parse failure — no structured services to itemize.
            error_output = (result.stderr or result.stdout or "No error output").strip()
            fence = fence_for(error_output)
            sections.append(
                f"### Version check failed\n\n{fence}\n{error_output}\n{fence}"
            )

        if sections:
            body = (
                MARKER
                + "\n## Version Check\n\n"
                + "\n\n".join(sections)
                + f"\n\nRun `{LOCAL_CMD}` locally for details."
            )
            # Upstream error bodies and version names reach this note: a line
            # starting with `/` is a GitLab quick action run as the token owner.
            upsert_mr_comment(neutralize_quick_actions(body), args.token_env)

    sys.exit(rc)


if __name__ == "__main__":
    main()
