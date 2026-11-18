<script setup>
import { computed, ref } from 'vue';
import { useContest } from '../composables/useContest.js';
import { duration } from '../lib/format.js';
import { ccpcDisplay, scoreboardResult } from '../lib/scoreboard.js';
import ProblemLetter from './ProblemLetter.vue';
import ScoreboardTable from './ScoreboardTable.vue';
import SubmissionTable from './SubmissionTable.vue';

const c = useContest();
const refreshingBoard = ref(false);
const refreshingSubmissions = ref(false);
const refreshingContest = ref(false);
const result = computed(() => scoreboardResult(c));
const errorTitle = computed(() => `无法读取 ${c.state?.demo ? '演示榜单' : 'QOJ 榜单'}`);
const teamRow = computed(() => {
  if (!c.canRead || result.value.error) return null;
  const row = result.value.rows.find((row) => row.is_self === true)
    || (c.state?.demo && c.state.run ? { ...c.state.run, rank: '—',
      team_name: c.state.run.team_name || c.state.team?.name || 'My Team', is_self: true } : null);
  if (!row) return row;
  if (ccpcDisplay(c) || c.part?.scoreboard_source === 'xcpcio') {
    return { ...row, problem_stats: c.state?.run?.problem_stats || row.problem_stats || {} };
  }
  return row;
});
const submissions = computed(() => [...(c.canRead ? c.state?.submissions || [] : [])]
  .sort((a, b) => Number(b.submitted_at) - Number(a.submitted_at) || Number(b.id) - Number(a.id)));
const solved = computed(() => !c.canRead ? '—' : `${result.value.error ? '—' : c.state?.run?.solved ?? '—'} / ${c.problems.length}`);
const penalty = computed(() => c.canRead && !result.value.error ? c.state?.run?.penalty ?? '—' : '—');
const problemRows = computed(() => c.problems.map((problem) => {
  const stat = c.statFor(problem.id);
  return { problem, stat, status: stat.solved ? 'accepted' : stat.pending ? 'pending'
    : Number(stat.wrong) > 0 ? 'wrong' : Number(stat.attempts) > 0 ? 'neutral' : 'empty' };
}));
const problemHref = (problem) => `#problems/${encodeURIComponent(problem.id)}`;

async function refreshBoard() {
  if (!c.canRead || refreshingBoard.value) return;
  refreshingBoard.value = true;
  try { await c.refreshState(true); }
  finally { refreshingBoard.value = false; }
}

async function refreshSubmissions() {
  if (!c.canRead || refreshingSubmissions.value) return;
  refreshingSubmissions.value = true;
  try { await c.refreshState(true); }
  finally { refreshingSubmissions.value = false; }
}

async function refreshContest() {
  if (!c.can('manage') || refreshingContest.value) return;
  refreshingContest.value = true;
  try {
    await c.refreshContest();
    c.notify('比赛信息已刷新。', 'success');
  } catch (error) { c.notify(error.message, 'error'); }
  finally { refreshingContest.value = false; }
}

function finishDemo() {
  if (!c.state?.demo || !c.can('manage') || !c.running) return;
  c.confirm('结束本次离线演示？', '结束后将停止接受本次演示的新提交。', async () => {
    if (!c.state?.demo || !c.can('manage') || !c.running) return;
    await c.api('/api/finish', {});
    await c.refreshState(true);
    c.notify('本次离线演示已结束。', 'success');
  });
}
</script>

<template>
  <section id="view-overview" class="view" aria-label="比赛概览" :hidden="c.tab !== 'overview'">
    <section class="team-summary" aria-label="本队成绩">
      <div id="team-scoreboard" class="table-scroll">
        <template v-if="c.canRead">
          <div v-if="result.error" class="scoreboard-unavailable" role="status"><strong>{{ errorTitle }}</strong><p>{{ result.error }}</p></div>
          <ScoreboardTable v-else-if="teamRow" :rows="[teamRow]" :overview="true" />
          <p v-else class="empty-state">QOJ 榜单暂无本队记录。</p>
        </template>
      </div>
    </section>
    <div class="team-columns">
      <section class="card table-card submissions-panel">
        <div class="section-heading">
          <h2>Submissions</h2>
          <button type="button" class="button button-outline button-small" :disabled="refreshingSubmissions" :aria-busy="refreshingSubmissions" @click="refreshSubmissions">{{ refreshingSubmissions ? 'Refreshing…' : 'Refresh' }}</button>
        </div>
        <div id="overview-submissions" class="table-scroll"><SubmissionTable :submissions="submissions" /></div>
      </section>
      <aside class="team-sidebar" aria-label="澄清">
        <section class="card info-panel clarification-panel"><div class="section-heading"><h2>Clarifications</h2></div><p><em>No clarifications.</em></p></section>
        <section class="card info-panel clarification-panel"><div class="section-heading"><h2>Clarification Requests</h2></div><p><em>No clarification requests.</em></p></section>
      </aside>
    </div>
  </section>
</template>
