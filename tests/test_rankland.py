import time
import unittest

from rankland import RankLandBoard, convert, rankland_url
from xcpcio import XCPCIOError


class RankLandTests(unittest.TestCase):
    def board(self):
        srk = dict(type='general', version='0.3.14',
                   contest=dict(startAt='2026-11-01T01:00:00Z', duration=[300, 'min'], frozenDuration=[60, 'min']),
                   sorter=dict(algorithm='ICPC', config=dict(penalty=[20, 'min'])),
                   problems=[dict(alias='A', link='https://qoj.ac/contest/4613/problem/1')],
                   rows=[dict(user=dict(id=str(i), name=f'Team {i}', official=i != 0),
                              statuses=[dict(result='AC', solutions=[dict(result='WA', time=[10, 's']),
                                                                    dict(result='AC', time=[15000 if i == 0 else 60, 's'])])])
                         for i in range(10)])
        board = RankLandBoard('https://vp.xcpc.link/?contest=ccpc2026ladies&rule=CCPC26')
        board.cached, board.loaded_at = (srk, convert(srk)), time.monotonic()
        return board

    def snapshot(self, elapsed):
        return dict(started_at=100, server_time=100 + elapsed,
                    contest=dict(id=4613, problems=[dict(id=1, label='A', title='A')]))

    def test_url_validation(self):
        self.assertEqual(rankland_url('https://vp.xcpc.link/?contest=ccpc2026ladies'),
                         'https://rl.algoux.cn/ranklist/ccpc2026ladies')
        for url in ['http://rl.algoux.cn/ranklist/x', 'https://evil.test/ranklist/x', 'https://vp.xcpc.link/']:
            with self.assertRaises(XCPCIOError):
                rankland_url(url)

    def test_freeze_summary_and_unfreeze(self):
        board = self.board()
        during = board.project(self.snapshot(16000), [], 'Local VP')
        total = during['scoreboard_problem_totals']['1']
        self.assertEqual((total['accepted'], total['incorrect'], total['frozen']), (9, 10, 1))
        final = board.project(self.snapshot(18000), [], 'Local VP')
        total = final['scoreboard_problem_totals']['1']
        self.assertEqual((total['accepted'], total['incorrect'], total['frozen']), (10, 10, 0))
        star = next(r for r in final['scoreboard'] if r['team_name'] == 'Team 0')
        self.assertTrue(star['unofficial'])
        self.assertNotIn('medal', star)

    def test_hidden_cells_do_not_expose_problem_identity(self):
        result = self.board().project(self.snapshot(20), [], 'Local VP')
        self.assertEqual(result['scoreboard_display']['hidden_count'], 1)
        self.assertEqual(result['scoreboard_problem_totals'], {})
        for row in result['scoreboard']:
            self.assertNotIn('problem_stats', row)
            cell = row['rankland_cells'][0]
            self.assertIsNone(cell['label'])
            self.assertIsNone(cell['problem_id'])
            self.assertFalse(cell['first_blood'])

    def test_mismatched_qoj_problem_rejected(self):
        snapshot = self.snapshot(20)
        snapshot['contest']['problems'][0]['id'] = 2
        with self.assertRaises(XCPCIOError):
            self.board().project(snapshot, [], 'Local VP')

    def test_qoj_library_and_other_contest_links_match_same_problem(self):
        for link in ['https://qoj.ac/problem/1', 'https://qoj.ac/contest/1840/problem/1']:
            board = self.board()
            board.cached[0]['problems'][0]['link'] = link
            self.assertTrue(board.project(self.snapshot(100), [], 'Local VP')['scoreboard'])

    def test_api_refresh_completes_and_publishes_ccpc26_metadata(self):
        from tempfile import TemporaryDirectory
        from app import create_app
        from test_native_api import NativeClient
        client = NativeClient(start_time=time.time() - 100)
        client.contest_data['id'] = 4613
        client.set_status('vp', 'running')
        with TemporaryDirectory() as directory:
            app = create_app(contest_id=4613, data_dir=directory, client_factory=lambda: client,
                             vp_rule='ccpc26', rankland_url='https://rl.algoux.cn/ranklist/ccpc2026ladies')
            app.extensions['xcpcio_board'].__dict__.update(self.board().__dict__)
            browser = app.test_client()
            browser.get('/api/state')
            runtime = next(iter(app.extensions['clients'].values()))
            job = runtime.get('state_job')
            if job:
                self.assertTrue(job.wait(5))
            state = browser.get('/api/state').get_json()
            self.assertFalse(state.get('state_refreshing'))
            self.assertEqual(state['participation']['scoreboard_source'], 'rankland')
            self.assertEqual(state['scoreboard_display']['style'], 'ccpc26')
            self.assertTrue(state['scoreboard'])


if __name__ == '__main__':
    unittest.main()
