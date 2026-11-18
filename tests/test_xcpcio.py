from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from app import create_app
from test_native_api import NativeClient
from xcpcio import replay, board_url, XCPCIOBoard, XCPCIOError


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(start_time=1800000000000, end_time=1800018000000, penalty=1200,
                           problems=[dict(id='0', label='A', balloon_color={'background_color': '#ff0000'}),
                                     dict(id='1', label='B', balloon_color={'background_color': '#00ff00'})],
                           options={'submission_timestamp_unit': 'millisecond'})
        self.problems = [dict(id=14, label='A', color='#111111'), dict(id=15, label='B', color='#222222')]
        self.teams = [dict(id='x', name='Onsite X'), dict(id='y', name='Onsite Y')]
        self.runs = [dict(team_id='x', problem_id='0', timestamp=60000, status='WRONG_ANSWER'),
                     dict(team_id='x', problem_id='0', timestamp=600000, status='CORRECT'),
                     dict(team_id='y', problem_id='1', timestamp=1200000, status='CORRECT')]

    def project(self, elapsed=3600, own=()):
        return replay(self.config, self.teams, self.runs, self.problems, elapsed, own, 'Local VP')

    def test_replay_cutoff_and_insertion_do_not_leak_future_results(self):
        own = [dict(id=1, problem_id=14, elapsed_seconds=300, verdict='AC')]
        rows, ratio = self.project(3600, own)
        self.assertEqual(ratio, 2000)
        self.assertEqual([(r['team_name'], r['solved'], r['penalty'], r['rank']) for r in rows],
                         [('Local VP', 1, 5, 1), ('Onsite Y', 1, 20, 2), ('Onsite X', 1, 30, 3)])
        rows, ratio = self.project(0)
        self.assertEqual(ratio, 0)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r['solved'] == 0 for r in rows))
        rows, _ = self.project(900)
        self.assertEqual(next(r for r in rows if r['team_name'] == 'Onsite Y')['solved'], 0)

    def test_subsecond_order_wrong_before_ac_and_post_ac_ignored(self):
        self.runs = [dict(team_id='x', problem_id='0', timestamp=60100, status='WRONG_ANSWER'),
                     dict(team_id='x', problem_id='0', timestamp=60900, status='CORRECT'),
                     dict(team_id='x', problem_id='0', timestamp=61000, status='WRONG_ANSWER')]
        rows, _ = self.project()
        self.assertEqual(rows[0]['penalty'], 21)
        self.assertEqual(rows[0]['problem_stats']['14']['wrong'], 1)

    def test_replay_includes_team_metadata_medals_and_totals_after_acceptance(self):
        self.config['medal'] = 'ccpc'
        self.teams = [dict(id=str(i), name=f'Team {i:02}', organization=f'University {i:02}')
                      for i in range(1, 11)]
        self.runs = []
        for i in range(1, 11):
            self.runs.append(dict(team_id=str(i), problem_id='0', timestamp=i * 60000, status='CORRECT'))
        self.runs.append(dict(team_id='1', problem_id='0', timestamp=700000, status='WRONG_ANSWER'))
        self.runs.extend([
            dict(team_id='2', problem_id='0', timestamp=16200000, status='PENDING'),
            dict(team_id='3', problem_id='0', timestamp=16300000, status='COMPILATION_ERROR'),
        ])
        self.config['frozen_time'] = 3600
        rows, _, totals = replay(self.config, self.teams, self.runs, self.problems, 18000, [], 'Local VP',
                                 include_totals=True)
        first = next(row for row in rows if row['team_name'] == 'Team 01')
        self.assertEqual(first['organization'], 'University 01')
        self.assertEqual(first['medal'], 'gold')
        self.assertEqual(first['problem_stats']['14']['attempts'], 1)
        self.assertEqual(totals['14'], {'solved': 10, 'submissions': 13, 'accepted': 10,
                                        'incorrect': 2, 'frozen': 0, 'first_blood': 1})

    def test_same_minute_ties_and_native_last_solve_tiebreak(self):
        self.runs = [dict(team_id='x', problem_id='0', timestamp=600000, status='CORRECT'),
                     dict(team_id='y', problem_id='0', timestamp=609000, status='CORRECT')]
        rows, _ = self.project()
        self.assertEqual([r['rank'] for r in rows[:2]], [1, 1])
        self.runs[0]['timestamp'] = 600000
        self.runs[1]['timestamp'] = 1800000
        self.runs.insert(0, dict(team_id='x', problem_id='0', timestamp=1, status='WRONG_ANSWER'))
        rows, _ = self.project()
        self.assertEqual([(r['team_name'], r['rank']) for r in rows[:2]], [('Onsite X', 1), ('Onsite Y', 2)])

    def test_penalty_units_and_ignored_compilation_errors(self):
        self.runs = [dict(team_id='x', problem_id='0', timestamp=1000, status='COMPILATION_ERROR'),
                     dict(team_id='x', problem_id='0', timestamp=61900, status='CORRECT')]
        self.config['options']['calculation_of_penalty'] = 'in_seconds'
        rows, _ = self.project()
        self.assertEqual(rows[0]['penalty_seconds'], 61)
        self.assertEqual(rows[0]['problem_stats']['14']['wrong'], 0)

    def test_mapping_mismatch_fails_instead_of_using_wrong_problems(self):
        self.config['problems'][0]['label'] = 'C'
        with self.assertRaises(XCPCIOError):
            self.project()

    def test_board_url_and_colors_do_not_mutate_qoj_snapshot(self):
        for url in ['http://board.xcpcio.com/ccpc/11', 'https://evil.test/ccpc/11',
                    'https://board.xcpcio.com@evil.test/ccpc/11', 'https://board.xcpcio.com/ccpc/../x']:
            with self.assertRaises(XCPCIOError):
                board_url(url)
        board = XCPCIOBoard('https://board.xcpcio.com/ccpc/11/test?progress-ratio=2000')
        self.addCleanup(board.session.close)
        board.data = lambda: (self.config, self.teams, self.runs)
        snapshot = dict(started_at=1000, server_time=4600, contest={'problems': self.problems})
        before = deepcopy(snapshot)
        result = board.project(snapshot, [], '椰子猫物语')
        own = next(row for row in result['scoreboard'] if row['is_self'])
        self.assertEqual(own['team_name'], '椰子猫物语')
        self.assertNotIn('username', own)
        self.assertEqual(result['contest']['problems'][0]['color'], '#ff0000')
        self.assertEqual(result['scoreboard_progress_ratio'], 2000)
        self.assertEqual(snapshot, before)

    def test_api_uses_created_team_name_and_keeps_qoj_account_private(self):
        with TemporaryDirectory() as directory:
            upstream = NativeClient(time.time()-100)
            upstream.contest_data['problems'] = self.problems
            upstream.set_status('vp', 'running')
            upstream.contest_submissions = lambda *args: []
            app = create_app(data_dir=Path(directory), client_factory=lambda: upstream,
                             xcpcio_url='https://board.xcpcio.com/ccpc/11/test')
            self.addCleanup(app.extensions['scheduled_vp'].close)
            self.addCleanup(app.extensions['joint_vp'].close)
            board = app.extensions['xcpcio_board']
            self.addCleanup(board.session.close)
            board.data = lambda: (self.config, self.teams, self.runs)
            browser = app.test_client()
            csrf = browser.get('/api/state').get_json()['csrf_token']
            response = browser.post('/api/team/create', json={'name': '椰子猫物语'}, headers={'X-CSRF-Token': csrf})
            self.assertEqual(response.status_code, 200)
            browser.get('/api/state')
            rt = next(iter(app.extensions['clients'].values()))
            job = rt['state_job']
            if job:
                self.assertTrue(job.wait(5))
            state = browser.get('/api/state').get_json()
            own = next(row for row in state['scoreboard'] if row['is_self'])
            self.assertEqual(own['team_name'], '椰子猫物语')
            self.assertNotIn('username', own)
            self.assertEqual(state['participation']['scoreboard_source'], 'xcpcio')
            self.assertEqual(state['contest']['problems'][0]['color'], '#ff0000')
            job = rt['state_job']
            if job:
                self.assertTrue(job.wait(5))


if __name__ == '__main__':
    unittest.main()
