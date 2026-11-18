export function scoreboardMinutes(value) {
  const match = /^(\d+):([0-5]\d)(?::([0-5]\d))?$/.exec(String(value ?? '').trim());
  return match ? Number(match[1]) * 60 + Number(match[2]) : value;
}

export function ccpcDisplay(c) {
  return c.canRead && c.state?.scoreboard_display?.style === 'ccpc' ? c.state.scoreboard_display : null;
}

// Keep QOJ's published values intact: the display format also carries frozen results.
export function scoreboardResult(c) {
  if (!c.canRead) return { rows: [], message: '' };
  const part = c.part || {};
  const sourceMessage = typeof part.scoreboard_message === 'string' ? part.scoreboard_message
    : typeof c.state?.scoreboard_message === 'string' ? c.state.scoreboard_message : '';
  const ccpc = ccpcDisplay(c);
  const message = [...new Set([sourceMessage, ccpc?.message].filter((item) => typeof item === 'string' && item))].join('\n');
  if (c.scoreboardError) return { error: c.scoreboardError };
  if (ccpc?.error) return { error: typeof ccpc.error === 'string' ? ccpc.error : '无法生成 CCPC 榜单，请刷新重试。' };
  if (part.scoreboard_available === false || ['unavailable', 'error'].includes(part.scoreboard_status)) {
    return { error: message || 'QOJ 暂未返回可读取的榜单，请刷新重试。' };
  }
  const record = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  const scalar = (value) => value == null || typeof value === 'string'
    || typeof value === 'number' && Number.isFinite(value);
  const count = (value) => Number.isSafeInteger(value) && value >= 0;
  const validCCPCCell = (cell) => record(cell) && typeof cell.key === 'string'
    && ['accepted', 'wrong', 'pending', 'neutral'].includes(cell.status)
    && typeof cell.display === 'string' && typeof cell.revealed === 'boolean'
    && (cell.first_blood == null || typeof cell.first_blood === 'boolean')
    && (cell.revealed ? scalar(cell.problem_id) && cell.problem_id != null && typeof cell.label === 'string'
      : cell.problem_id == null && cell.label == null);
  const validCCPC = !ccpc || Array.isArray(ccpc.revealed_problems)
    && ccpc.revealed_problems.every((problem) => record(problem) && scalar(problem.id) && problem.id != null
      && typeof problem.label === 'string' && scalar(problem.title) && count(problem.threshold)
      && (problem.solved == null || count(problem.solved))
      && (problem.submissions == null || count(problem.submissions)))
    && count(ccpc.hidden_count) && typeof ccpc.all_revealed === 'boolean';
  const validRow = (row) => record(row)
    && ['rank', 'team_name', 'username', 'solved', 'penalty'].every((key) => scalar(row[key]))
    && (ccpc ? row.problem_stats == null && Array.isArray(row.ccpc_cells) && row.ccpc_cells.every(validCCPCCell)
      : row.problem_stats == null || record(row.problem_stats)
        && Object.values(row.problem_stats).every((cell) => record(cell) && scalar(cell.display) && scalar(cell.text)));
  const rows = c.state?.scoreboard;
  if (!validCCPC || !Array.isArray(rows) || !rows.every(validRow)) {
    return { error: '服务器返回的榜单格式不正确，请刷新重试。' };
  }
  return { rows, message };
}

export function scoreboardSourceURL(c) {
  if (!c.canRead || c.state?.demo || typeof c.part?.scoreboard_url !== 'string') return undefined;
  try {
    const url = new URL(c.part.scoreboard_url);
    if (['https://qoj.ac', 'https://board.xcpcio.com', 'https://rl.algoux.cn', 'https://rl.algoux.org'].includes(url.origin) && !url.username && !url.password) return url.href;
  } catch {}
  return undefined;
}

export function scoreboardSourceLabel(c) {
  if (c.canRead && c.part?.scoreboard_source === 'rankland') return 'CCPC 2026 · RankLand 回放';
  if (c.canRead && c.part?.scoreboard_source === 'xcpcio') return 'XCPCIO 现场榜单';
  if (c.state?.demo) return '离线演示榜单';
  if (c.canRead && c.part?.scoreboard_source === 'qoj-pc2') return 'QOJ PC2 榜单';
  if (c.canRead && c.part?.scoreboard_source === 'qoj-external') return 'QOJ External Standings';
  return 'QOJ 榜单';
}

// Header totals use the whole published board, even above the single-team view.
// Prefer QOJ's own totals: summing displayed cells can miss unlisted submissions.
export function scoreboardProblemTotals(c) {
  const result = scoreboardResult(c);
  if (!c.canRead || result.error) return {};
  const count = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const ccpc = ccpcDisplay(c);
  if (ccpc) {
    // Never reconstruct public problem totals from a team's private results.
    const revealed = new Map(ccpc.revealed_problems.map((problem) => [String(problem.id), problem]));
    return Object.fromEntries(c.problems.map((problem) => {
      const total = revealed.get(String(problem.id));
      return [problem.id, total ? {
        solved: total.solved ?? null, submissions: total.submissions ?? null,
        text: `${total.solved ?? '—'} / ${total.submissions ?? '—'}`,
        title: `通过队伍数 / 提交次数：${total.solved ?? '未知'} / ${total.submissions ?? '未知'}（已揭晓题目）`,
      } : { solved: null, submissions: null, text: '未揭晓', title: '此题尚未揭晓，不展示全场统计。' }];
    }));
  }
  const published = c.state?.scoreboard_problem_totals || c.part?.scoreboard_problem_totals;
  const totals = {};
  for (const problem of c.problems) {
    const upstream = published?.[problem.id];
    let solved = 0;
    let submissions = 0;
    if (upstream && typeof upstream === 'object' && !Array.isArray(upstream)) {
      solved = count(upstream.solved);
      submissions = count(upstream.submissions);
    } else {
      for (const row of result.rows) {
        const stat = row.problem_stats?.[problem.id] || {};
        const display = String(stat.display ?? stat.text ?? '').trim();
        // Pending/frozen markers are never evidence of a solve.
        if (stat.solved === true && (c.state?.demo || !stat.pending && !display.startsWith('?'))) solved++;
        let attempts = count(c.state?.demo ? stat.submissions ?? stat.attempts : stat.attempts);
        if (attempts === null) {
          const marker = /^([+?−-])(\d*)(?:\s|$)/u.exec(display);
          const pc2 = /^(\d+)\/(?:\d+|--|\?+)$/.exec(display);
          if (pc2) attempts = count(Number(pc2[1]));
          else if (marker && marker[1] === '+') attempts = count(Number(marker[2] || 0) + 1);
          else if (marker && marker[2]) attempts = count(Number(marker[2]));
          else if ((!display || display === '-') && !stat.pending && !stat.solved) attempts = 0;
        }
        submissions = submissions === null || attempts === null ? null : count(submissions + attempts);
      }
      if (!c.state?.demo && c.part?.ruleset && c.part.ruleset !== 'ICPC') solved = null;
    }
    const key = String(problem.id);
    const upstreamTotal = upstream || {};
    let nativeIncorrect = 0;
    let nativeFirstBlood = null;
    if (c.part?.scoreboard_source !== 'xcpcio') {
      for (const row of result.rows) {
        const stat = row.problem_stats?.[problem.id] || {};
        const display = String(stat.display ?? stat.text ?? '').trim();
        if (stat.first_blood === true && stat.solved) {
          const value = scoreboardMinutes(stat.time);
          if (Number.isFinite(Number(value)) && String(value).trim() !== '') nativeFirstBlood = Number(value);
        }
        if (stat.pending || display.startsWith('?')) continue;
        let attempts = count(stat.attempts);
        if (attempts === null && (!display || display === '-')) attempts = 0;
        if (attempts === null) continue;
        nativeIncorrect += Math.max(0, attempts - (stat.solved ? 1 : 0));
      }
    }
    totals[problem.id] = {
      solved, submissions,
      accepted: count(upstreamTotal.accepted) ?? solved,
      incorrect: count(upstreamTotal.incorrect) ?? count(upstreamTotal.wrong) ?? nativeIncorrect,
      frozen: count(upstreamTotal.frozen) ?? 0,
      firstBlood: count(upstreamTotal.first_blood) ?? nativeFirstBlood,
      text: `${solved ?? '—'} / ${submissions ?? '—'}`,
      title: `通过队伍数 / 提交次数：${solved ?? '未知'} / ${submissions ?? '未知'}（${upstream ? 'QOJ 公开汇总' : '当前可见榜单'}）`,
    };
  }
  return totals;
}

export function scoreboardCell(c, stat = {}) {
  if (!c.state?.demo) {
    const text = String(stat.display ?? stat.text ?? '');
    const source = c.part?.scoreboard_source;
    if (source === 'xcpcio' && stat.solved === true) {
      const attempts = Number(stat.attempts) || 1;
      const time = stat.time_display ?? stat.time ?? '—';
      return { status: 'accepted', firstBlood: stat.first_blood === true, text, time, attempts,
        detail: `${time} 分钟通过；${attempts} 次提交`, demo: false };
    }
    if (source === 'xcpcio') {
      const attempts = Number(stat.attempts) || 0;
      const status = stat.pending ? 'pending' : stat.failed || stat.wrong > 0 ? 'wrong' : 'empty';
      return { status, firstBlood: false, text, attempts, hasResult: attempts > 0,
        detail: attempts ? `${attempts} 次提交${status === 'pending' ? '（封榜后结果隐藏）' : ''}` : '尚未提交',
        demo: false };
    }
    const external = source === 'qoj-external';
    const failed = stat.failed === true || !external && text.trim().startsWith('-');
    const empty = !text || external && text.trim() === '-';
    const status = stat.pending || text.trim().startsWith('?') ? 'pending'
      : stat.solved ? 'accepted' : failed ? 'wrong' : empty ? 'empty' : 'neutral';
    const firstBlood = ['qoj', 'qoj-external', 'xcpcio'].includes(source)
      && status === 'accepted' && stat.first_blood === true;
    const detail = firstBlood ? `${text}\nFirst to solve / 一血` : text;
    return {
      status, firstBlood, text: text || '\u00a0', detail,
      time: scoreboardMinutes(stat.time_display ?? stat.time ?? (text.trim().split(/\s+/).slice(1).join(' ') || '—')),
      attempts: stat.attempts ?? (/^\+(\d*)/.test(text.trim()) ? Number(/^\+(\d*)/.exec(text.trim())[1] || 0) + 1 : Number(/^[?−-](\d+)/.exec(text.trim())?.[1]) || 0),
      hasResult: status !== 'empty',
      ariaLabel: firstBlood ? detail : undefined, demo: false,
    };
  }
  const attempts = Number(stat.attempts) || 0;
  const wrong = Number(stat.wrong) || 0;
  const status = stat.solved ? 'accepted' : stat.pending ? 'pending'
    : wrong ? 'wrong' : attempts ? 'neutral' : 'empty';
  const value = stat.solved ? stat.time ?? '—' : stat.pending ? '?' : wrong ? `−${wrong}` : '—';
  const detail = attempts
    ? `${stat.solved ? `第 ${stat.time} 分钟通过；` : ''}${attempts} 次模拟提交，${wrong} 次模拟错误`
    : '尚未提交';
  return {
    status, firstBlood: false, value, detail, attempts,
    hasResult: !!(stat.solved || stat.pending || wrong || attempts),
    ariaLabel: status === 'empty' ? '尚未提交' : undefined, demo: true,
  };
}
