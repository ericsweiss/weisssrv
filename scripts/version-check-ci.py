#!/usr/bin/env python3
"""CI wrapper for check-versions.py — runs version check, posts MR comment if updates available."""

import json
import os
import subprocess
import sys
import urllib.request


def post_mr_comment(body):
    """Post a comment on the current merge request via GitLab API."""
    api_url = os.environ.get("CI_API_V4_URL", "")
    project_id = os.environ.get("CI_PROJECT_ID", "")
    mr_iid = os.environ.get("CI_MERGE_REQUEST_IID", "")
    token = os.environ.get("GITLAB_API_TOKEN", "")

    if not all([api_url, project_id, mr_iid, token]):
        return

    url = f"{api_url}/projects/{project_id}/merge_requests/{mr_iid}/notes"
    data = json.dumps({"body": body}).encode()
    headers = {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print("MR comment posted")
    except Exception as e:
        print(f"Warning: could not post MR comment: {e}")


def main():
    # Generate JSON report (for artifacts)
    try:
        r1 = subprocess.run(
            ["./scripts/check-versions.py", "--json"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("Error: version check (JSON) timed out")
        sys.exit(2)
    except OSError as e:
        print(f"Error: failed to execute version check: {e}")
        sys.exit(2)
    with open("version-report.json", "w") as f:
        f.write(r1.stdout)

    # Generate human-readable report
    try:
        r2 = subprocess.run(
            ["./scripts/check-versions.py"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("Error: version check (text) timed out")
        sys.exit(2)
    except OSError as e:
        print(f"Error: failed to execute version check: {e}")
        sys.exit(2)
    with open("version-report.txt", "w") as f:
        f.write(r2.stdout)

    # Print human-readable output to job log
    print(r2.stdout)

    # Exit codes: 0 = all up to date, 1 = updates available, 2 = errors
    # Use the worst exit code from both runs
    if 2 in (r1.returncode, r2.returncode):
        rc = 2
    elif 1 in (r1.returncode, r2.returncode):
        rc = 1
    else:
        rc = 0

    if os.environ.get("CI_MERGE_REQUEST_IID"):
        if rc == 1:
            body = (
                "## Version Check\n\n"
                "The following updates are available:\n\n"
                f"```\n{r2.stdout}\n```\n\n"
                "Run `task maintenance:check-versions` locally for details."
            )
            post_mr_comment(body)
        elif rc == 2:
            error_output = (r1.stderr or r2.stderr or "No error output").strip()
            body = (
                "## Version Check Failed\n\n"
                "The version check encountered errors:\n\n"
                f"```\n{r2.stdout}\n```\n\n"
                f"Stderr:\n```\n{error_output}\n```"
            )
            post_mr_comment(body)

    sys.exit(rc)


if __name__ == "__main__":
    main()
