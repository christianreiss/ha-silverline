"""Release safety checks run without Home Assistant or GitHub credentials."""

import importlib.util
import pathlib
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "check_release_ci", pathlib.Path(__file__).parents[1] / "check_release_ci.py"
)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class ReleaseGateTests(unittest.TestCase):
    def run_record(self, **changes):
        return dict(
            {
                "id": 42,
                "head_sha": "release-sha",
                "head_branch": "main",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/example/repo/actions/runs/42",
                "run_attempt": 2,
            },
            **changes,
        )

    def response(self, endpoint):
        if "/jobs?" in endpoint:
            return {"total_count": 1, "jobs": [{"conclusion": "success"}]}
        self.assertIn("head_sha=release-sha", endpoint)
        self.assertIn("branch=main", endpoint)
        self.assertIn("event=push", endpoint)
        return {"workflow_runs": self.runs}

    def setUp(self):
        self.runs = [self.run_record()]
        self.mock_api = patch.object(ci, "api", side_effect=self.response).start()
        self.addCleanup(patch.stopall)

    def test_requires_all_three_workflows_and_records_evidence(self):
        evidence = ci.check("example/repo", "release-sha")
        self.assertEqual(len(evidence), 3)
        self.assertEqual(self.mock_api.call_count, 6)
        for workflow, line in zip(ci.WORKFLOWS, evidence, strict=True):
            self.assertIn(workflow, line)
            self.assertIn("attempt 2", line)

    def test_wrong_commit_branch_or_event_cannot_authorize_release(self):
        for changes in (
            {"head_sha": "different-sha"},
            {"head_branch": "feature"},
            {"event": "pull_request"},
        ):
            with self.subTest(changes=changes):
                self.runs = [self.run_record(**changes)]
                self.assertIsNone(ci.check("example/repo", "release-sha"))

    def test_missing_and_pending_checks_wait(self):
        for runs in ([], [self.run_record(status="in_progress", conclusion=None)]):
            self.runs = runs
            self.assertIsNone(ci.check("example/repo", "release-sha"))

    def test_terminal_non_success_blocks_even_with_older_green_run(self):
        for conclusion in ("failure", "cancelled", "skipped", "neutral", "timed_out"):
            with self.subTest(conclusion=conclusion):
                self.runs = [
                    self.run_record(id=41),
                    self.run_record(conclusion=conclusion),
                ]
                with self.assertRaises(RuntimeError):
                    ci.check("example/repo", "release-sha")

    def test_skipped_empty_or_incomplete_job_list_blocks(self):
        for jobs in (
            {"total_count": 1, "jobs": [{"conclusion": "skipped"}]},
            {"total_count": 0, "jobs": []},
            {"total_count": 2, "jobs": [{"conclusion": "success"}]},
        ):
            with self.subTest(jobs=jobs):
                self.mock_api.side_effect = lambda endpoint: (
                    jobs if "/jobs?" in endpoint else {"workflow_runs": self.runs}
                )
                with self.assertRaises(RuntimeError):
                    ci.check("example/repo", "release-sha")

    def test_api_failure_blocks(self):
        self.mock_api.side_effect = subprocess.CalledProcessError(1, "gh")
        with self.assertRaises(subprocess.CalledProcessError):
            ci.check("example/repo", "release-sha")

    def test_timeout_blocks_missing_ci(self):
        self.runs = []
        with (
            patch("sys.argv", ["check_release_ci.py", "--timeout", "0"]),
            patch.object(ci.subprocess, "check_output", return_value="release-sha"),
            self.assertRaisesRegex(RuntimeError, "Timed out"),
        ):
            ci.main()

    def test_pending_ci_is_rechecked_before_success(self):
        with (
            patch("sys.argv", ["check_release_ci.py"]),
            patch.object(ci.subprocess, "check_output", return_value="release-sha"),
            patch.object(ci, "check", side_effect=[None, ["green evidence"]]) as check,
            patch.object(ci.time, "sleep"),
            patch.dict(ci.os.environ, {"GITHUB_STEP_SUMMARY": ""}),
        ):
            ci.main()
            self.assertEqual(check.call_count, 2)


if __name__ == "__main__":
    unittest.main()
