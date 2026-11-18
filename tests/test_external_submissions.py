"""Native VP submissions use elapsed times; imported rows have no local source."""
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app import create_app
from external_submissions import merge_submissions, parse_submissions
from qoj import QOJClient, QOJError
from test_native_api import NativeClient
from test_submission_data import HTML


class ExternalSubmissionsTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'mode': 'vp', 'started_at': 1000, 'ends_at': 8200,
                         'contest': {'problems': [{'id': 21701, 'label': 'A'}]}}
        self.html = HTML.replace('2026-09-15 14:18:47', '01:43:12')

    def test_elapsed_native_row_and_verdict(self):
        row, = parse_submissions(self.html, 1042, 'fixture-team', self.snapshot)
        self.assertEqual((row['qoj_id'], row['submitted_at'], row['elapsed_seconds'], row['verdict']),
                         (12345, 7192, 6192, 'AC'))
        self.assertEqual(row['id'], 'external-12345')
        localized = self.html.replace('/problem/21701', '/problem/21701/statement/en')
        self.assertEqual(parse_submissions(localized, 1042, 'fixture-team', self.snapshot), [row])

    def test_other_account_contest_problem_and_time_are_excluded(self):
        for html, cid, user in [(self.html, 1042, 'other'), (self.html, 99, 'fixture-team'),
                                (self.html.replace('/21701', '/999'), 1042, 'fixture-team'),
                                (self.html.replace('01:43:12', '03:00:00'), 1042, 'fixture-team'),
                                (self.html.replace('01:43:12', 'unknown'), 1042, 'fixture-team')]:
            self.assertEqual(parse_submissions(html, cid, user, self.snapshot), [])
        self.snapshot['mode'] = 'official'
        self.assertEqual(parse_submissions(self.html, 1042, 'fixture-team', self.snapshot), [])

    def test_merge_keeps_local_source_and_deduplicates_numeric_strings(self):
        row, = parse_submissions(self.html, 1042, 'fixture-team', self.snapshot)
        local = dict(row, id=7, qoj_id='12345', language='C++17')
        self.assertEqual(merge_submissions([local], [row]), [local])

    def test_all_advertised_pages_are_loaded_and_duplicates_removed(self):
        client = QOJClient()
        self.addCleanup(client.session.close)
        client.username = 'fixture-team'
        def page(path):
            number = int(path.split('=')[-1]) if '=' in path else 1
            rows = ''.join(self.html.split('<tbody>')[1].split('</tbody>')[0]
                           .replace('12345', str(sid)) for sid in range((number-1)*10+1, min(number*10+1, 24)))
            if number == 2:
                rows += self.html.split('<tbody>')[1].split('</tbody>')[0].replace('12345', '10')
            links = ('<ul class="pagination"><li><a href="?page=1">1</a></li>'
                     '<li><a href="/contest/1042/submissions?page=2">2</a></li>')
            if number == 2:
                links += '<li><a href="https://qoj.ac/contest/1042/submissions?page=3">3</a></li>'
            links += ('<li><a href="https://evil.test/contest/1042/submissions?page=4">bad</a></li>'
                      '<li><a href="/contest/99/submissions?page=4">bad</a></li></ul>')
            return SimpleNamespace(text=self.html.split('<tbody>')[0]+'<tbody>'+rows+'</tbody></table>'+links)
        client._page = Mock(side_effect=page)
        rows = client.contest_submissions(1042, self.snapshot)
        self.assertEqual(len(rows), 23)
        self.assertEqual({row['qoj_id'] for row in rows}, set(range(1, 24)))
        self.assertEqual([call.args[0] for call in client._page.call_args_list],
                         ['/contest/1042/submissions', '/contest/1042/submissions?page=2',
                          '/contest/1042/submissions?page=3'])

    def test_background_sync_is_visible_and_not_in_participation_metadata(self):
        with TemporaryDirectory() as directory:
            upstream = NativeClient(time.time()-100)
            upstream.set_status('vp', 'running')
            imported = dict(id='external-12345', qoj_id=12345, problem_id=1, label='A',
                            submitted_at=time.time()-10, elapsed_seconds=90, verdict='AC', language='—')
            upstream.contest_submissions = lambda *args: [imported]
            app = create_app(data_dir=Path(directory), client_factory=lambda: upstream)
            client = app.test_client()
            state = client.get('/api/state').get_json()
            rt = next(iter(app.extensions['clients'].values()))
            job = rt['state_job']
            if job:
                self.assertTrue(job.wait(5))
                state = client.get('/api/state').get_json()
            self.assertEqual(state['submissions'], [imported])
            self.assertNotIn('external_submissions', state['participation'])
            def failed(*args):
                raise QOJError('upstream unavailable', 502)
            upstream.contest_submissions = failed
            state = client.get('/api/state').get_json()
            job = rt['state_job']
            if job:
                self.assertTrue(job.wait(5))
            self.assertTrue(state['permissions']['view_content'])


if __name__ == '__main__':
    unittest.main()
