from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock
from app import create_app
from qoj import QOJClient
from scheduled_vp import ScheduledVP, ScheduleError
from test_joint_entry import JointEntryAdapter


class ScheduledVPTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.launch = Mock(return_value={'status': 'running'})
        self.scheduler = ScheduledVP(self.launch, clock=lambda: self.now)
        self.addCleanup(self.scheduler.close)

    def test_exact_time_once_without_browser_or_retry(self):
        job = self.scheduler.schedule('owner', 1060, {'username': 'fixture'})
        self.scheduler.fire('owner', job['id'])
        self.launch.assert_not_called()
        self.now = 1060
        self.scheduler.fire('owner', job['id'])
        self.scheduler.fire('owner', job['id'])
        self.launch.assert_called_once_with('owner', {'username': 'fixture'})
        self.assertEqual(self.scheduler.view('owner')['status'], 'started')
        self.assertNotIn('binding', self.scheduler.view('owner'))

    def test_cancellation_duplicates_and_invalid_time(self):
        for target in [True, float('nan'), 1001, 1000+8*86400]:
            with self.assertRaises(ScheduleError):
                self.scheduler.schedule('owner', target, {})
        job = self.scheduler.schedule('owner', 1060, {})
        with self.assertRaises(ScheduleError):
            self.scheduler.schedule('owner', 1070, {})
        self.scheduler.cancel('owner')
        self.now = 1061
        self.scheduler.fire('owner', job['id'])
        self.launch.assert_not_called()

    def test_uncertain_post_is_not_retried(self):
        self.launch.side_effect = RuntimeError('uncertain response')
        job = self.scheduler.schedule('owner', 1060, {})
        self.now = 1060
        self.scheduler.fire('owner', job['id'])
        self.scheduler.fire('owner', job['id'])
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(self.scheduler.view('owner')['status'], 'error')

    def test_timer_worker_starts_without_any_browser_requests(self):
        fired = Event()
        self.launch.side_effect = lambda *args: (fired.set() or {'status': 'running'})
        self.scheduler.schedule('owner', 1060, {})
        self.now = 1060
        self.assertTrue(fired.wait(3))
        self.assertEqual(self.launch.call_count, 1)


class ScheduleAPITests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.upstream = QOJClient()
        self.addCleanup(self.upstream.session.close)
        self.adapter = JointEntryAdapter()
        self.upstream.session.adapters.clear()
        self.upstream.session.mount('https://qoj.ac/', self.adapter)
        self.upstream.username = self.adapter.username
        self.upstream.authenticated = True
        self.app = create_app(contest_id=9, data_dir=Path(directory.name), client_factory=lambda: self.upstream)
        self.addCleanup(self.app.extensions['joint_vp'].close)
        self.scheduler = self.app.extensions['scheduled_vp']
        self.addCleanup(self.scheduler.close)
        self.now = 1000
        self.scheduler.clock = lambda: self.now
        self.browser = self.app.test_client()
        self.csrf = self.browser.get('/api/state').get_json()['csrf_token']
        self.assertEqual(self.post('/api/team/create', {'name': 'Fixture VP'}).status_code, 200)

    def post(self, path, body):
        return self.browser.post(path, json=body, headers={'X-CSRF-Token': self.csrf})

    def arrange(self):
        response = self.post('/api/schedule/prepare', {})
        self.assertEqual(response.status_code, 200, response.get_json())
        plan = response.get_json()['plan']
        body = dict(target=1060, fingerprint=plan['fingerprint'], action=plan['action']['id'], values={}, consent=True)
        response = self.post('/api/schedule', body)
        self.assertEqual(response.status_code, 200, response.get_json())
        with self.browser.session_transaction() as session:
            owner = session['sid']
        return owner, response.get_json()['scheduled_vp']

    def test_no_early_post_and_fresh_single_post_at_due_time(self):
        owner, job = self.arrange()
        self.assertEqual(self.adapter.posts, [])
        old_token = self.adapter.latest_confirmation_token
        state = self.browser.get('/api/state').get_json()
        self.assertFalse(state['permissions']['view_content'])
        self.assertEqual(state['scheduled_vp']['status'], 'pending')
        self.assertEqual(self.post('/api/start', {}).status_code, 409)
        self.now = 1060
        self.scheduler.fire(owner, job['id'])
        self.assertEqual(self.scheduler.view(owner)['status'], 'started')
        self.assertEqual(len(self.adapter.posts), 1)
        self.assertNotEqual(self.adapter.body(self.adapter.posts[0])['_token'], [old_token])

    def test_changed_account_or_terms_stop_before_post(self):
        owner, job = self.arrange()
        self.adapter.terms = 'Changed registration terms'
        self.now = 1060
        self.scheduler.fire(owner, job['id'])
        self.assertEqual(self.scheduler.view(owner)['status'], 'error')
        self.assertEqual(self.adapter.posts, [])

    def test_missing_csrf_and_missing_consent_are_rejected(self):
        response = self.browser.post('/api/schedule/prepare', json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post('/api/schedule', {}).status_code, 400)

    def test_changed_account_is_rejected_before_automatic_start(self):
        owner, job = self.arrange()
        self.upstream.username = 'different-account'
        self.now = 1060
        self.scheduler.fire(owner, job['id'])
        self.assertEqual(self.scheduler.view(owner)['status'], 'error')
        self.assertEqual(self.adapter.posts, [])


if __name__ == '__main__':
    unittest.main()
