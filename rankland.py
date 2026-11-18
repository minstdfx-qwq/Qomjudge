"""RankLand SRK replay adapter. No upstream JavaScript is executed."""
import json
import math
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit

import requests
from xcpcio import XCPCIOError, _epoch, _text, replay


def rankland_url(value):
    parts = urlsplit((value or '').strip())
    if parts.scheme != 'https' or parts.username or parts.password:
        raise XCPCIOError('请填写 HTTPS RankLand 或 vp.xcpc.link 比赛链接。')
    if parts.netloc in {'rl.algoux.cn', 'rl.algoux.org'}:
        match = re.fullmatch(r'/ranklist/([A-Za-z0-9][A-Za-z0-9._-]{0,127})/?', parts.path)
        key = match[1] if match else None
    elif parts.netloc == 'vp.xcpc.link':
        key = parse_qs(parts.query).get('contest', [None])[0]
    else:
        key = None
    if not key or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', key):
        raise XCPCIOError('链接须包含 RankLand 比赛 ID，例如 ccpc2026ladies。')
    return 'https://rl.algoux.cn/ranklist/' + key


def seconds(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise XCPCIOError('SRK 时间格式无效。')
    factors = {'ms': .001, 's': 1, 'min': 60, 'h': 3600, 'd': 86400}
    if value[1] not in factors or not isinstance(value[0], (int, float)):
        raise XCPCIOError('SRK 时间单位无效。')
    result = value[0] * factors[value[1]]
    if not math.isfinite(result) or result < 0:
        raise XCPCIOError('SRK 时间值无效。')
    return result


def convert(srk):
    if srk.get('type') != 'general' or srk.get('sorter', {}).get('algorithm') != 'ICPC':
        raise XCPCIOError('目前仅支持 SRK ICPC / CCPC26 榜单。')
    if not re.fullmatch(r'0\.3\.\d+', srk.get('version', '')):
        raise XCPCIOError('目前仅支持 SRK 0.3.x。')
    contest, options = srk['contest'], srk['sorter']['config']
    duration = seconds(contest['duration'])
    start = _epoch(contest['startAt'])
    config = dict(start_time=start * 1000, end_time=(start + duration) * 1000,
                  frozen_time=seconds(contest.get('frozenDuration', [0, 's'])),
                  penalty=seconds(options.get('penalty', [20, 'min'])), medal='ccpc',
                  options={'calculation_of_penalty': 'accumulate_in_seconds_and_finally_to_the_minute'},
                  problems=[{'id': str(i), 'label': p.get('alias', chr(65 + i))}
                            for i, p in enumerate(srk['problems'])])
    teams, runs = [], []
    seen = set()
    for row in srk['rows']:
        user = row['user']
        tid = str(user['id'])
        if tid in seen or len(row['statuses']) != len(srk['problems']):
            raise XCPCIOError('SRK 队伍编号或题目数量不一致。')
        seen.add(tid)
        teams.append(dict(id=tid, name=user['name'], organization=user.get('organization', ''),
                          unofficial=user.get('official') is False))
        for i, stat in enumerate(row['statuses']):
            if stat.get('result') and not stat.get('solutions'):
                raise XCPCIOError('此榜单缺少逐次提交记录，不能可靠回放。')
            for solution in stat.get('solutions', []):
                result = solution['result']
                result = 'AC' if result == 'FB' else 'PENDING' if result == '?' else result
                # SRK excludes these from penalty; CE is retained for freeze counts.
                if result in {'NOUT', 'UKE', None}:
                    result = 'CE'
                runs.append(dict(id=str(len(runs)), team_id=tid, problem_id=str(i),
                                 timestamp=seconds(solution['time']), status=result))
    return config, teams, runs


class RankLandBoard:
    def __init__(self, url, session=None):
        query = parse_qs(urlsplit(url).query)
        try:
            self.reveal_count = int(query.get('count', ['50'])[0])
            self.reveal_ratio = float(query.get('ratio', ['20'])[0]) / 100
        except (ValueError, TypeError):
            raise XCPCIOError('CCPC26 揭题阈值无效。') from None
        if (not 0 <= self.reveal_count <= 100000 or not 0 <= self.reveal_ratio <= 1
                or not (self.reveal_count or self.reveal_ratio)):
            raise XCPCIOError('CCPC26 至少需要一个非零揭题阈值。')
        self.url = rankland_url(url)
        self.session = session or requests.Session()
        self.lock = threading.Lock()
        self.cached = None
        self.loaded_at = 0

    def data(self):
        with self.lock:
            if self.cached is not None and time.monotonic() - self.loaded_at < 300:
                return self.cached
            try:
                response = self.session.get(self.url, timeout=(5, 25), allow_redirects=False)
                response.raise_for_status()
                response.encoding = 'utf-8'
                match = re.search(r"window\.__INITIAL_STATE__='(.*?)'</script>", response.text, re.S)
                if not match:
                    raise XCPCIOError('RankLand 页面未提供 SRK 数据。')
                # The SSR state is JSON wrapped in a JS string, with apostrophes escaped.
                state = json.loads(match[1].replace("\\'", "'"))
                srk = state['ranklistData']['srk']
                converted = convert(srk)
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                raise XCPCIOError('无法读取 RankLand 回放数据：' + str(exc)) from None
            self.cached = (srk, converted)
            self.loaded_at = time.monotonic()
            return self.cached

    def project(self, snapshot, submissions, team_name):
        srk, (config, teams, runs) = self.data()
        elapsed = (snapshot.get('server_time') or time.time()) - snapshot['started_at']
        problems = snapshot['contest']['problems']
        local_by_label = {p['label']: p for p in problems}
        for i, upstream in enumerate(srk['problems']):
            local = local_by_label.get(upstream.get('alias', chr(65 + i)))
            if local is None:
                raise XCPCIOError('RankLand 与 QOJ 题号不一致。')
            link = upstream.get('link', '')
            if link:
                target = urlsplit(link)
                match = re.fullmatch(r'(?:/contest/[0-9]+)?/problem/([0-9]+)/?', target.path)
                if (target.scheme != 'https' or target.netloc != 'qoj.ac'
                        or target.query or target.fragment or not match
                        or int(match[1]) != int(local['id'])):
                    raise XCPCIOError('RankLand 与 QOJ 题目 ID 不一致，拒绝合并。')
        rows, ratio, totals = replay(config, teams, runs, problems, elapsed, submissions, team_name, True, True)
        # SRK ranks compare accumulated penalty rounded to minutes, without a last-solve tiebreak.
        rows.sort(key=lambda r: (-r['solved'], r['penalty'], r['team_name']))
        previous, rank = None, 0
        for i, row in enumerate(rows, 1):
            key = row['solved'], row['penalty']
            if key != previous:
                rank = i
            row['rank'] = rank if row['solved'] else None
            previous = key
            row.pop('medal', None)
        eligible = [r for r in rows if not r['unofficial'] and any(s.get('attempts') for s in r['problem_stats'].values())]
        for i, row in enumerate(eligible, 1):
            if row['solved']:
                row['medal'] = next((name for fraction, name in ((.1, 'gold'), (.3, 'silver'), (.6, 'bronze'))
                                     if i <= math.ceil(len(eligible) * fraction)), '')
        duration = seconds(srk['contest']['duration'])
        freeze_at = duration - config['frozen_time']
        # VP CCPC26 defaults match vp.xcpc.link: 50 teams OR floor(20% of all teams).
        thresholds = ([self.reveal_count] if self.reveal_count else [])
        if self.reveal_ratio:
            thresholds.append(math.floor(len(rows) * self.reveal_ratio))
        threshold = min(thresholds)
        revealed = [p for p in problems if totals[str(p['id'])]['solved'] >= threshold
                    or elapsed >= freeze_at]
        revealed_ids = {str(p['id']) for p in revealed}
        hidden = [p for p in problems if str(p['id']) not in revealed_ids]
        for row in rows:
            stats = row.pop('problem_stats')
            def ordering(p):
                s = stats.get(str(p['id']), {})
                return (0 if s.get('solved') else 1 if s.get('attempts') else 2,
                        s.get('sort_time', float('inf')), p['label'])
            cells = []
            for p in revealed + sorted(hidden, key=ordering):
                stat = stats.get(str(p['id']), {})
                visible = str(p['id']) in revealed_ids
                cells.append(dict(status='accepted' if stat.get('solved') else 'pending' if stat.get('pending')
                                  else 'wrong' if stat.get('attempts') else 'empty',
                                  time=stat.get('time'), attempts=stat.get('attempts', 0),
                                  first_blood=bool(stat.get('first_blood')) if visible else False,
                                  label=p['label'] if visible else None,
                                  problem_id=p['id'] if visible else None))
            row['rankland_cells'] = cells
        return dict(scoreboard=rows, scoreboard_source='rankland', scoreboard_available=True,
                    scoreboard_status='available', scoreboard_message='', ruleset='ICPC',
                    scoreboard_url=self.url, scoreboard_problem_totals={str(p['id']): totals[str(p['id'])] for p in revealed},
                    scoreboard_frozen=freeze_at <= elapsed < duration,
                    scoreboard_display={'style': 'ccpc26', 'revealed_problems': revealed,
                                        'hidden_count': len(hidden), 'threshold': threshold},
                    scoreboard_freeze_at=snapshot['started_at'] + freeze_at)
