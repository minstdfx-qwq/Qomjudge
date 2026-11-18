"""Replay XCPCIO's public config/team/run feed using the native board rules."""
from datetime import datetime
import math
import re
import threading
import time
from urllib.parse import urlsplit

import requests


class XCPCIOError(ValueError):
    pass


def board_url(value):
    if not value:
        return None
    parts = urlsplit(value.strip())
    if (parts.scheme != 'https' or parts.netloc != 'board.xcpcio.com'
            or not re.fullmatch(r'(?:/[A-Za-z0-9_-]+){2,8}/?', parts.path)):
        raise XCPCIOError('榜单地址须为 https://board.xcpcio.com/ 下的比赛网址。')
    return 'https://board.xcpcio.com' + parts.path.rstrip('/')


def _items(data):
    if isinstance(data, list):
        return [(str(i), row) for i, row in enumerate(data)]
    if isinstance(data, dict):
        return list(data.items())
    raise XCPCIOError('XCPCIO 队伍或提交数据格式无法识别。')


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # XCPCIO localized values are commonly wrapped as {texts: {lang: text}}.
        texts = value.get('texts')
        if isinstance(texts, dict):
            preferred = value.get('fallback_lang')
            if isinstance(preferred, str) and isinstance(texts.get(preferred), str):
                return texts[preferred]
            return next((texts[k] for k in ('zh-CN', 'zh', 'en', 'en-US', 'fallback')
                         if isinstance(texts.get(k), str)), next((v for v in texts.values() if isinstance(v, str)), ''))
        return next((value[k] for k in ('zh-CN', 'zh', 'en', 'en-US', 'fallback')
                     if isinstance(value.get(k), str)), next((v for v in value.values() if isinstance(v, str)), ''))
    return ''


def _epoch(value):
    if isinstance(value, (int, float)) and math.isfinite(value):
        # XCPCIO dayjs treats numeric dates as milliseconds.
        return value / 1000
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


AC = {'AC', 'OK', 'ACCEPTED', 'CORRECT'}
WRONG = {'WA', 'WRONG_ANSWER', 'REJECTED', 'RJ', 'INCORRECT', 'NO', 'NO_OUTPUT',
         'RE', 'RT', 'RTE', 'RUNTIME_ERROR', 'TL', 'TLE', 'TIME_LIMIT_EXCEEDED',
         'ML', 'MLE', 'MEMORY_LIMIT_EXCEEDED', 'OL', 'OLE', 'OUTPUT_LIMIT_EXCEEDED',
         'IL', 'ILE', 'IDLENESS_LIMIT_EXCEEDED', 'JE', 'JUDGEMENT_FAILED', 'HACKED'}
PENDING = {'PD', 'PENDING', 'WAITING', 'COMPILING', 'JUDGING', 'FROZEN', 'SUBMITTING', 'UNKNOWN'}


def replay(config, teams, runs, problems, elapsed, own_submissions, team_name, include_totals=False, include_sort_times=False):
    """No final totals are used: filter submissions before scoring every team."""
    upstream = config.get('problems')
    if upstream is None:
        upstream = [{'id': str(i), 'label': label} for i, label in enumerate(config.get('problem_id', []))]
    labels = [str(p['label']) for p in upstream]
    local = {str(p['label']): p for p in problems}
    if len(set(labels)) != len(labels) or len(local) != len(problems) or set(labels) != set(local):
        raise XCPCIOError('XCPCIO 与 QOJ 题号不一致，不能合并榜单；请检查所选比赛。')
    mapping = {str(p['id']): local[str(p['label'])]['id'] for p in upstream}
    duration = _epoch(config['end_time']) - _epoch(config['start_time'])
    if not math.isfinite(duration) or duration <= 0:
        raise XCPCIOError('XCPCIO 比赛时长无效。')
    elapsed = max(0, min(float(elapsed), duration))
    ratio = max(0, min(10000, math.floor(elapsed / duration * 10000)))
    cutoff = math.floor(duration * ratio / 10000)
    options = config.get('options', {})
    unit = options.get('submission_timestamp_unit', 'second')
    divisor = {'second': 1, 'millisecond': 1000, 'microsecond': 1000000, 'nanosecond': 1000000000}.get(unit)
    calculation = options.get('calculation_of_penalty', 'in_minutes')
    if divisor is None or calculation not in {'in_minutes', 'in_seconds', 'accumulate_in_seconds_and_finally_to_the_minute'}:
        raise XCPCIOError('XCPCIO 时间或计罚规则暂不支持。')
    penalty = float(config.get('penalty', 1200))
    if not math.isfinite(penalty) or penalty < 0:
        raise XCPCIOError('XCPCIO 错误提交罚时无效。')
    rows, events, counted = {}, [], []
    for key, team in _items(teams):
        tid = str(team.get('id', team.get('team_id', key)))
        if tid in rows:
            raise XCPCIOError('XCPCIO 队伍编号重复。')
        groups = team.get('group', [])
        if isinstance(groups, (list, tuple, set)):
            group_names = {str(value).strip().lower() for value in groups}
        else:
            group_names = {str(groups).strip().lower()}
        rows[tid] = dict(team_name=_text(team.get('name', team.get('team_name', tid))),
                         organization=_text(team.get('organization')),
                         unofficial=bool(team.get('unofficial') or team.get('is_unofficial'))
                         or bool(group_names & {'unofficial', 'unofficial_team', 'star', '打星'}),
                         is_self=False, problem_stats={}, solved=0, penalty=0, _last=-1)
    for key, run in _items(runs):
        stamp = float(run['timestamp']) / divisor
        if not math.isfinite(stamp) or stamp < 0 or math.floor(stamp) > cutoff or stamp >= duration:
            continue
        tid, pid = str(run['team_id']), mapping.get(str(run['problem_id']))
        if tid not in rows or pid is None or run.get('is_ignore'):
            continue
        status = str(run['status']).upper().replace(' ', '_')
        event = (stamp, tid, str(run.get('id', run.get('submission_id', key))), pid, status)
        events.append(event)
        counted.append(event)
    own_id = '\0qomjudge-self'
    rows[own_id] = dict(team_name=team_name, organization='虚拟参赛', unofficial=False,
                        is_self=True, problem_stats={}, solved=0, penalty=0, _last=-1)
    ids = local_ids(problems)
    freeze_length = float(config.get('frozen_time') or 0)
    freeze_start = max(0, duration - freeze_length) if math.isfinite(freeze_length) else duration
    freeze_active = 0 < freeze_length and freeze_start <= elapsed < duration
    totals = {str(problem_id): dict(solved=0, submissions=0, accepted=0, incorrect=0,
                                   frozen=0, first_blood=None) for problem_id in mapping.values()}
    for sub in own_submissions:
        stamp = float(sub['elapsed_seconds'])
        if sub['problem_id'] in ids and 0 <= stamp <= elapsed and stamp < duration:
            event = (stamp, own_id, str(sub.get('qoj_id') or sub['id']), sub['problem_id'], sub['verdict'])
            events.append(event)
            counted.append(event)
    for stamp, _, _, pid, status in counted:
        total = totals[str(pid)]
        if freeze_active and stamp >= freeze_start:
            total['frozen'] += 1
            continue
        total['submissions'] += 1
        if status in AC:
            total['accepted'] += 1
            if total['first_blood'] is None or stamp < total['first_blood']:
                total['first_blood'] = stamp
        elif status not in {'CE', 'COMPILATION_ERROR'}:
            total['incorrect'] += 1
    pre_freeze_attempts, frozen_attempts = {}, {}
    for stamp, tid, _, pid, status in counted:
        key = (tid, str(pid))
        if stamp < freeze_start and status not in {'CE', 'COMPILATION_ERROR'}:
            pre_freeze_attempts[key] = pre_freeze_attempts.get(key, 0) + 1
        if freeze_active and stamp >= freeze_start:
            frozen_attempts[key] = frozen_attempts.get(key, 0) + 1
    # Native board: same timestamp/team -> AC first, then lexical submission ID.
    events.sort(key=lambda e: (e[0], e[1], 0 if e[4] in AC else 1, e[2]))
    first = {}
    for stamp, tid, sid, pid, status in events:
        if freeze_active and stamp >= freeze_start:
            continue
        precise_stamp = stamp
        stamp = math.floor(stamp)
        row = rows[tid]
        stat = row['problem_stats'].setdefault(str(pid), dict(solved=False, wrong=0, pending=0, attempts=0, time=None))
        if stat['solved'] or status not in AC | WRONG | PENDING:
            continue
        if include_sort_times:
            stat['sort_time'] = precise_stamp
        stat['attempts'] += 1
        if status in AC:
            stat.update(solved=True, time=stamp // 60, _seconds=precise_stamp)
            first[pid] = min(first.get(pid, precise_stamp), precise_stamp)
            totals[str(pid)]['solved'] += 1
            row['solved'] += 1
            row['_last'] = stamp // 60
            row['penalty'] += (stamp // 60 * 60 if calculation == 'in_minutes' else stamp) + stat['wrong'] * penalty
        elif status in WRONG:
            stat['wrong'] += 1
        else:
            stat['pending'] += 1
    for row in rows.values():
        if calculation == 'accumulate_in_seconds_and_finally_to_the_minute':
            row['penalty'] = row['penalty'] // 60 * 60
        row['penalty_seconds'] = row['penalty']
        row['penalty'] = math.floor(row['penalty'] / 60)
        for pid, stat in row['problem_stats'].items():
            seconds = stat.pop('_seconds', None)
            stat['first_blood'] = stat['solved'] and seconds == first.get(int(pid))
            stat['failed'] = not stat['solved'] and stat['wrong'] > 0
            if stat['solved']:
                stat['display'] = ('+' + (str(stat['wrong']) if stat['wrong'] else '') + '\n' + str(stat['time']))
                stat['pending'] = False
            elif stat['pending']:
                stat['display'] = '?' + str(stat['attempts'])
            else:
                stat['display'] = '-' + str(stat['wrong']) if stat['wrong'] else ''
            stat['text'] = stat['display']
    if freeze_active:
        for (tid, problem_id), count in frozen_attempts.items():
            row = rows[tid]
            stat = row['problem_stats'].setdefault(problem_id, dict(solved=False, wrong=0, pending=0,
                                                                    attempts=0, time=None))
            if stat['solved']:
                continue
            stat['pending'] = count
            stat['frozen_attempts'] = count
            stat['attempts'] = pre_freeze_attempts.get((tid, problem_id), 0) + count
            stat['display'] = '?' + str(stat['attempts'])
            stat['text'] = stat['display']
    ordered = sorted(rows.values(), key=lambda row: (-row['solved'], row['penalty_seconds'], row['_last'], row['team_name']))
    previous, rank = None, 0
    for index, row in enumerate(ordered, 1):
        key = (row['solved'], row['penalty_seconds'], row.pop('_last'))
        if key != previous:
            rank = index
        # Domjudge leaves teams with no solved problems unranked.
        row['rank'], previous = (rank if row['solved'] > 0 else None), key
    if str(config.get('medal', '')).lower() == 'ccpc':
        eligible = [row for row in ordered if not row['unofficial'] and row['solved'] > 0]
        count = len(eligible)
        cutoffs = [math.ceil(count * fraction) for fraction in (.10, .30, .60)]
        for position, row in enumerate(eligible, 1):
            if position <= cutoffs[0]:
                row['medal'] = 'gold'
            elif position <= cutoffs[1]:
                row['medal'] = 'silver'
            elif position <= cutoffs[2]:
                row['medal'] = 'bronze'
    # Distinct accepted teams are counted after replay, including the inserted VP team.
    for problem_id, total in totals.items():
        total['solved'] = sum(bool(row['problem_stats'].get(problem_id, {}).get('solved')) for row in ordered)
        first_stamp = total['first_blood']
        total['first_blood'] = math.floor(first_stamp / 60) if first_stamp is not None else None
    for row in ordered:
        for stat in row['problem_stats'].values():
            stat['time_display'] = str(stat['time']) if stat['solved'] else None
            stat['display'] = ('+' + (str(stat['wrong']) if stat['wrong'] else '') + '\n' + str(stat['time'])) if stat['solved'] else stat['display']
    return (ordered, ratio, totals) if include_totals else (ordered, ratio)


def local_ids(problems):
    return {p['id'] for p in problems}


class XCPCIOBoard:
    def __init__(self, url, session=None):
        self.url = board_url(url)
        self.session = session or requests.Session()
        self.lock = threading.Lock()
        self.cached = None
        self.loaded_at = 0

    def data(self):
        with self.lock:
            if self.cached is not None and time.monotonic() - self.loaded_at < 300:
                return self.cached
            base = 'https://board-data.xcpcio.com/data' + urlsplit(self.url).path
            values = []
            for filename in ('config', 'team', 'run'):
                try:
                    response = self.session.get(base + '/' + filename + '.json', timeout=(5, 15), allow_redirects=False)
                    # Older deployments may still publish only on the board host.
                    # Select the source using config, then keep all three files together.
                    if filename == 'config' and response.status_code == 404:
                        base = 'https://board.xcpcio.com/data' + urlsplit(self.url).path
                        response = self.session.get(base + '/config.json', timeout=(5, 15), allow_redirects=False)
                    if response.status_code != 200:
                        raise XCPCIOError(f'XCPCIO {filename} 数据返回 HTTP {response.status_code}。')
                    values.append(response.json())
                except (requests.RequestException, ValueError) as exc:
                    raise XCPCIOError(f'无法读取 XCPCIO {filename} 数据：{exc}') from None
            self.cached, self.loaded_at = values, time.monotonic()
            return self.cached

    def project(self, snapshot, submissions, team_name):
        config, teams, runs = self.data()
        elapsed = (snapshot.get('server_time') or time.time()) - snapshot['started_at']
        rows, ratio, totals = replay(config, teams, runs, snapshot['contest']['problems'], elapsed, submissions, team_name,
                                     include_totals=True)
        contest = {**snapshot['contest'], 'problems': [dict(p) for p in snapshot['contest']['problems']]}
        upstream = config.get('problems')
        if upstream is None:
            colors = config.get('balloon_color', [])
            upstream = [dict(label=label, balloon_color=colors[i] if i < len(colors) else {})
                        for i, label in enumerate(config.get('problem_id', []))]
        colors = {str(p['label']): (p.get('balloon_color') or {}).get('background_color') for p in upstream}
        for problem in contest['problems']:
            color = colors.get(str(problem['label']))
            if isinstance(color, str) and re.fullmatch(r'#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?', color):
                problem['color'] = color
        return dict(scoreboard=rows, scoreboard_source='xcpcio', scoreboard_available=True,
                    contest=contest,
                    scoreboard_status='available', ruleset='ICPC', scoreboard_problem_totals=totals,
                    scoreboard_url=self.url + f'?progress-ratio={ratio}', scoreboard_progress_ratio=ratio,
                    scoreboard_message='', scoreboard_frozen=False, scoreboard_freeze_at=None)
