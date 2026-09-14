"""Require successful main-branch CI for the exact commit being delivered.

Uses gh's existing authentication locally and GH_TOKEN in Actions. Missing,
cancelled, skipped and failed checks never count as success.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from urllib.parse import urlencode

WORKFLOWS = ("tests.yaml", "hacs.yaml", "hassfest.yaml")


def api(endpoint):
    result = subprocess.run(
        ["gh", "api", endpoint], check=True, capture_output=True, text=True, timeout=30
    )
    return json.loads(result.stdout)


def check(repo, sha):
    """Return evidence when all checks pass, None while waiting; fail closed."""
    evidence = []
    pending = False
    query = urlencode({"head_sha": sha, "branch": "main", "event": "push"})
    for workflow in WORKFLOWS:
        runs = api(f"repos/{repo}/actions/workflows/{workflow}/runs?{query}")[
            "workflow_runs"
        ]
        # Defend against wrong-SHA/branch evidence, even if the API filters drift.
        runs = [
            run
            for run in runs
            if run["head_sha"] == sha
            and run["head_branch"] == "main"
            and run["event"] == "push"
        ]
        if not runs:
            print(f"{workflow}: waiting for main push CI at {sha}", flush=True)
            pending = True
            continue
        run = max(runs, key=lambda item: item["id"])
        if run["status"] != "completed":
            print(f"{workflow}: {run['status']} — {run['html_url']}", flush=True)
            pending = True
            continue
        if run["conclusion"] != "success":
            raise RuntimeError(f"{workflow}: {run['conclusion']} — {run['html_url']}")
        jobs = api(
            f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100"
        )
        if (
            not jobs["jobs"]
            or jobs["total_count"] != len(jobs["jobs"])
            or any(job["conclusion"] != "success" for job in jobs["jobs"])
        ):
            raise RuntimeError(f"{workflow}: not every job passed — {run['html_url']}")
        evidence.append(
            f"- [{workflow}]({run['html_url']}) (attempt {run['run_attempt']})"
        )
    return None if pending else evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="christianreiss/ha-silverline")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    deadline = time.monotonic() + args.timeout
    while True:
        evidence = check(args.repo, sha)
        if evidence is not None:
            report = f"Release CI passed for `{sha}`\n\n" + "\n".join(evidence) + "\n"
            print(report)
            if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
                with pathlib.Path(summary).open("a") as output:
                    output.write(report)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for successful CI at {sha}")
        time.sleep(min(15, max(0, deadline - time.monotonic())))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError) as error:
        print(f"RELEASE BLOCKED: {error}", file=sys.stderr)
        sys.exit(1)
