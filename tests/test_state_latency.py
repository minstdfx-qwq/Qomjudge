"""Slow upstream reads must not serialize browser state requests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest

from app import create_app
from test_native_api import NativeClient


class StateLatencyTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.upstream = NativeClient(start_time=time.time() - 100)
        self.upstream.set_status("vp", "running")
        self.app = create_app(data_dir=Path(self.directory.name), client_factory=lambda: self.upstream)
        self.client = self.app.test_client()
        self.release = Event()
        self.entered = Event()
        self.original = self.upstream.participation
        self.calls = 0
        self.job = None
        self.addCleanup(self.finish)

    def finish(self):
        self.release.set()
        if self.job:
            self.assertTrue(self.job.wait(5))

    def slow(self, *args):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise AssertionError("test worker was not released")
        return self.original(*args)

    def read(self):
        start = time.monotonic()
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        self.assertLess(time.monotonic() - start, 1.0)
        return response.get_json()

    def test_cold_reads_return_promptly_and_share_one_upstream_job(self):
        self.upstream.participation = self.slow
        first = self.read()
        self.assertTrue(self.entered.wait(1))
        rt = next(iter(self.app.extensions["clients"].values()))
        self.job = rt["state_job"]
        self.assertTrue(first["state_refreshing"])
        self.assertFalse(first["permissions"]["view_content"])
        for _ in range(3):
            self.assertTrue(self.read()["state_refreshing"])
        self.assertEqual(self.calls, 1)
        self.finish()
        self.upstream.participation = self.original
        self.assertTrue(self.read()["permissions"]["view_content"])

    def test_warm_reads_keep_results_but_expired_snapshot_is_hidden(self):
        self.assertTrue(self.read()["permissions"]["view_content"])
        self.upstream.participation = self.slow
        state = self.read()
        rt = next(iter(self.app.extensions["clients"].values()))
        self.job = rt["state_job"]
        self.assertTrue(state["state_refreshing"])
        self.assertTrue(state["permissions"]["view_content"])
        with rt["state_lock"]:
            cached = rt["state_snapshot"]
            rt["state_snapshot"] = (*cached[:3], time.monotonic() - 61, cached[4])
        self.assertFalse(self.read()["permissions"]["view_content"])

    def test_cached_state_is_not_reused_after_account_changes(self):
        self.assertTrue(self.read()["permissions"]["view_content"])
        self.upstream.participation = self.slow
        self.upstream.username = "different-account"
        self.assertFalse(self.read()["permissions"]["view_content"])
        self.job = next(iter(self.app.extensions["clients"].values()))["state_job"]


if __name__ == "__main__":
    unittest.main()
