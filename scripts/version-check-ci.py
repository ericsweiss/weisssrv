#!/usr/bin/env python3
"""CI wrapper for check-versions.py — runs version check, posts MR comment if updates available."""
import json
import os
import subprocess
import sys
from urllib.request import Request, urlopen


def post_mr_comment(body: str) -> None:
    """Post a comment to the current MR via GitLab API."""
    api_url = os.environ.get("CI_API_V4_URL", "")
    project_id = os.environ.get("CI_PROJECT_ID", "")
    mr_iid = os.environ.get("CI_MERGE_REQUEST_IID", "")
    token = os.environ.get("GITLAB_API_TOKEN", "")

    if not all([api_url, project_id, mr_iid, token]):
        if mr_iid:
            # In an MR pipeline but a credential/URL is missing — surface it
            # so a revoked/absent GITLAB_API_TOKEN doesn't silently swallow
            # the version comment.
            print(
                "Warning: in an MR pipeline but GitLab API URL/project/token is "
                "incomplete; skipping MR comment (check GITLAB_API_TOKEN).",
                file=sys.stderr,
            )
        return

    url = f"{api_url}/projects/{project_id}/merge_requests/{mr_iid}/notes"
    data = json.dumps({"body": body}).encode()
    req = Request(url, data=data, headers={
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=30) as resp:
            resp.read()
        print("MR comment posted")
    except Exception as e:
        print(f"Warning: could not post MR comment: {e}")


def main():
    # Run version check once with --json
    try:
        result = subprocess.run(
            ["./scripts/check-versions.py", "--json"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("Error: version check timed out")
        sys.exit(2)
    except OSError as e:
        print(f"Error: failed to execute version check: {e}")
        sys.exit(2)

    rc = result.returncode

    # Parse JSON for human-readable output
    updates = 0
    errors = 0
    data = {}
    try:
        data = json.loads(result.stdout)
        # Persist the validated JSON as the artifact.
        with open("version-report.json", "w") as f:
            f.write(result.stdout)
        summary = data.get("summary", {})
        # Fall back to "total_services" for older check-versions.py
        # payloads still in any cache the CI job consumes. The key was
        # renamed in a prior refactor; both shapes may appear in flight.
        total = summary.get("total", summary.get("total_services", 0))
        up_to_date = summary.get("up_to_date", 0)
        updates = summary.get("updates_available", 0)
        held = summary.get("updates_held", 0)
        errors = summary.get("errors", 0)

        print(f"Version check: {total} services, {up_to_date} up to date, {updates} updates, {held} held, {errors} errors")

        # Use .get() for field access below: a malformed service entry must not
        # raise KeyError here, since the surrounding except would then overwrite
        # the already-written valid artifact with an error stub.
        if updates > 0:
            print("\nUpdates available:")
            for svc in data.get("services", []):
                if svc.get("update_available") and not svc.get("held"):
                    print(f"  {svc.get('name', '?')}: {svc.get('current_version', '?')} -> {svc.get('latest_version', '?')}")

        if errors > 0:
            print("\nErrors:")
            for svc in data.get("services", []):
                if svc.get("error"):
                    print(f"  {svc.get('name', '?')}: {svc.get('error')}")

        # Reconcile rc with parsed summary in case they diverge
        if errors > 0:
            rc = 2
        elif updates > 0:
            rc = 1
        else:
            rc = 0
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        print("Warning: could not parse version check output")
        print(result.stdout)
        rc = 2
        # Write a self-describing stub so the artifact isn't a 0-byte or
        # raw-text file that reads like a successful empty report.
        with open("version-report.json", "w") as f:
            json.dump(
                {
                    "error": f"version-check output not parseable: {type(e).__name__}: {e}",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                f,
                indent=2,
            )

    # Post MR comment when there are actionable updates and/or errors.
    # Report BOTH together — a transient single-service error must not
    # suppress the actionable update table (or vice versa).
    if os.environ.get("CI_MERGE_REQUEST_IID"):
        sections = []
        if updates > 0:
            update_lines = []
            for svc in data.get("services", []):
                # Held updates are documented non-actionable holds; they'd
                # otherwise re-post the same comment on every pipeline.
                if svc.get("update_available") and not svc.get("held"):
                    # Registry notes carry intent (e.g. "intentionally held
                    # back: open upstream regression").
                    notes = svc.get("notes", "")
                    update_lines.append(
                        f"| {svc.get('name', '?')} | {svc.get('current_version', '?')} | "
                        f"{svc.get('latest_version', '?')} | {notes} |"
                    )
            sections.append(
                "### Updates available\n\n"
                "| Service | Current | Latest | Notes |\n"
                "|---------|---------|--------|-------|\n"
                + "\n".join(update_lines)
            )
        if errors > 0 and data:
            err_lines = [
                f"- {svc.get('name', '?')}: {svc.get('error', 'unknown error')}"
                for svc in data.get("services", [])
                if svc.get("error")
            ]
            sections.append("### Errors\n\n" + "\n".join(err_lines))
        elif rc == 2 and not data:
            # Parse failure — no structured services to itemize.
            error_output = (result.stderr or result.stdout or "No error output").strip()
            sections.append("### Version check failed\n\n```\n" + error_output + "\n```")

        if sections:
            body = (
                "## Version Check\n\n"
                + "\n\n".join(sections)
                + "\n\nRun `task maintenance:check-versions` locally for details."
            )
            post_mr_comment(body)

    sys.exit(rc)


if __name__ == "__main__":
    main()
