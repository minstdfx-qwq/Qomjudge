"""Import native contest-list rows without downloading or inventing source code."""
import re

from qoj import parse_verdict
from submission_data import records


def parse_submissions(source, contest_id, username, snapshot):
    problems = {p['id']: p for p in snapshot['contest']['problems']}
    start, end = snapshot.get('started_at'), snapshot.get('ends_at')
    if not username or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return []
    result = []
    for sid, row in records(source, contest_id).items():
        if row['username'] != username or row['problem_id'] not in problems or row['contest_id'] != contest_id:
            continue
        submitted = row['submitted_at']
        # Native VP contest lists use elapsed H+:MM:SS, not a calendar date.
        elapsed = re.fullmatch(r'(\d+):([0-5]\d):([0-5]\d)', row.get('submitted_text') or '')
        if submitted is None and elapsed and snapshot.get('mode') == 'vp':
            submitted = start + int(elapsed[1]) * 3600 + int(elapsed[2]) * 60 + int(elapsed[3])
        if submitted is None or not start <= submitted <= end:
            continue
        problem = problems[row['problem_id']]
        result.append(dict(id=f'external-{sid}', qoj_id=sid, qoj_url=f'https://qoj.ac/submission/{sid}',
                           problem_id=row['problem_id'], label=problem['label'], language=row['language'] or '—',
                           submitted_at=submitted, elapsed_seconds=submitted-start,
                           **parse_verdict(source, sid)))
    return result


def merge_submissions(local, external):
    known = {str(s['qoj_id']) for s in local if s.get('qoj_id')}
    return sorted([*local, *(s for s in external if str(s['qoj_id']) not in known)],
                  key=lambda s: (s['submitted_at'], str(s['id'])), reverse=True)
