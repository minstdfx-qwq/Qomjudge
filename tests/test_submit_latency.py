"""Keep scoreboard downloads out of the CLI submission path."""
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from app import create_app
from qoj import QOJClient
from test_native_adapter_api import BoundFixtureClient
from test_native_api import NativeClient


class SubmissionClient(NativeClient):
    def participation_for_submission(self, contest_id, mode="vp"):
        self.calls.append(("validation", contest_id, mode))
        return dict(self.snapshots[mode])


class SubmitLatencyTests(unittest.TestCase):
    def test_real_validation_adapter_skips_standings_but_checks_registration(self):
        client = BoundFixtureClient(mode="vp", entered=True)
        snapshot = QOJClient.participation_for_submission(client, 9, "vp")
        self.assertTrue(snapshot["can_submit"])
        self.assertIn("/contest/9/registrants", client.gets)
        self.assertNotIn("/contest/9/standings", client.gets)
        self.assertEqual(snapshot["scoreboard"], [])

    def test_cli_sequence_reuses_recent_reads_but_revalidates_before_post(self):
        with TemporaryDirectory() as directory:
            upstream = SubmissionClient(time.time() - 60)
            upstream.set_status("vp", "running")
            app = create_app(data_dir=Path(directory), client_factory=lambda: upstream)
            browser = app.test_client()
            state = browser.get("/api/state").get_json()
            csrf = {"X-CSRF-Token": state["csrf_token"]}
            self.assertIn(browser.post("/api/team/create", json={"name": "Test"}, headers=csrf).status_code, (200, 201))
            issued = browser.post("/api/cli/token", json={}, headers=csrf)
            self.assertIn(issued.status_code, (200, 201))
            config = issued.get_json()["config"]
            headers = {"Authorization": "Bearer " + config["token"]}
            terminal = app.test_client()
            upstream.calls.clear()
            self.assertEqual(terminal.get("/api/state", headers=headers).status_code, 200)
            self.assertEqual(terminal.get("/api/problems/1", headers=headers).status_code, 200)
            result = terminal.post("/api/submit", headers=headers, json={
                "problem_id": 1, "language": "C++17", "source": "int main() {}", "filename": "A.cpp"})
            self.assertEqual(result.status_code, 201, result.get_data(as_text=True))
            self.assertEqual(sum(call[0] == "validation" for call in upstream.calls), 1)
            self.assertEqual(sum(call[0] == "participation" for call in upstream.calls), 0)
            self.assertEqual(sum(call[0] == "submit" for call in upstream.calls), 1)
            # A previously valid cache cannot authorize a new submission after
            # the upstream says the contest is no longer open.
            upstream.set_status("vp", "finished")
            rejected = terminal.post("/api/submit", headers=headers, json={
                "problem_id": 1, "language": "C++17", "source": "int main() {}", "filename": "A.cpp"})
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(sum(call[0] == "submit" for call in upstream.calls), 1)


if __name__ == "__main__":
    unittest.main()
