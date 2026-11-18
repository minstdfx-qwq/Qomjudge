<script setup>
import { computed } from 'vue';
import { useContest } from '../composables/useContest.js';
import { ccpcDisplay, scoreboardCell, scoreboardProblemTotals, scoreboardMinutes } from '../lib/scoreboard.js';
import ProblemLetter from './ProblemLetter.vue';
import CCPCScoreboardTable from './CCPCScoreboardTable.vue';

const props = defineProps({ rows: { type: Array, default: () => [] }, privateView: { type: Boolean, default: false }, overview: { type: Boolean, default: false } });
const c = useContest();
const ccpc = computed(() => !props.privateView && !!ccpcDisplay(c));
const rankland = computed(() => c.state?.scoreboard_display?.style === 'ccpc26');
const columns = computed(() => rankland.value ? [...c.state.scoreboard_display.revealed_problems, ...Array.from({ length: c.state.scoreboard_display.hidden_count }, (_, i) => ({ id: `hidden-${i}`, label: '?' }))] : c.problems);
const upstreamXCPCIO = computed(() => ['xcpcio', 'rankland'].includes(c.part?.scoreboard_source));
const xcpcio = computed(() => !c.state?.demo && (props.overview || !props.privateView));
const nativeMedals = computed(() => {
  const eligible = (c.state?.scoreboard || []).filter(row => Number(row.solved) > 0);
  const cutoffs = [.1, .3, .6].map(ratio => Math.ceil(eligible.length * ratio));
  return new Map(eligible.map((row, index) => [row, index < cutoffs[0] ? 'gold' : index < cutoffs[1] ? 'silver' : index < cutoffs[2] ? 'bronze' : '']));
});
const totals = computed(() => scoreboardProblemTotals(c));
const tableRows = computed(() => !c.canRead ? [] : props.rows.map((row) => ({
  row: upstreamXCPCIO.value ? row : { ...row, penalty: scoreboardMinutes(row.penalty), organization: '', unofficial: false, medal: nativeMedals.value.get(row) || nativeMedals.value.get((c.state?.scoreboard || []).find(original => original.is_self && row.is_self)) },
  cells: rankland.value ? (row.rankland_cells || []).map((cell, i) => ({ ...cell, id: i, firstBlood: cell.first_blood, hasResult: cell.attempts > 0 })) : c.problems.map((problem) => ({
    id: problem.id,
    ...scoreboardCell(c, c.statFor(problem.id, row)),
  })),
})));
const totalSolved = computed(() => tableRows.value.reduce((sum, { row }) => sum + (Number(row.solved) || 0), 0));
const problemHref = (problem) => `#problems/${encodeURIComponent(problem.id)}`;
</script>

<template>
  <CCPCScoreboardTable v-if="c.canRead && ccpc" :rows="rows" />
  <table v-else-if="c.canRead" class="scoreboard-table" :class="{ 'xcpcio-scoreboard': xcpcio, 'xcpcio-overview': xcpcio && props.overview }">
    <thead>
      <tr>
        <th>Rank</th><th>Team</th>
        <th v-if="xcpcio" class="xcpcio-score-heading">Score</th>
        <th v-else>Solved</th><th v-if="!xcpcio">Penalty</th>
        <th v-for="problem in columns" :key="problem.id" class="scoreboard-problem">
          <span v-if="rankland && problem.label === '?'">?</span>
          <a v-else :href="problemHref(problem)" :title="problem.title" @click.prevent="c.navigate('problems', problem.id)">
            <ProblemLetter :problem="problem" />
          </a>
          <span v-if="!privateView && !xcpcio && totals[problem.id]" class="problem-totals" :data-problem-totals="problem.id"
                :title="totals[problem.id].title" :aria-label="totals[problem.id].title">{{ totals[problem.id].text }}</span>
        </th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="({ row, cells }, index) in tableRows" :key="index" :class="{ 'self-row': row.is_self === true, 'unofficial-row': upstreamXCPCIO && row.unofficial }">
        <td class="rank-cell" :class="row.medal ? `rank-medal-${row.medal}` : ''">{{ Number(row.solved) > 0 ? (xcpcio && props.overview && c.state?.scoreboard_frozen ? '?' : (row.rank ?? '—')) : '' }}</td>
        <td class="team-cell" :class="{ 'team-unofficial': upstreamXCPCIO && row.unofficial }"><span class="team-name">{{ row.team_name ?? row.username ?? '—' }}</span><small v-if="upstreamXCPCIO && row.organization" class="team-organization">{{ row.organization }}</small></td>
        <td class="score-cell">
          <span v-if="xcpcio" class="xcpcio-score-pair"><span :aria-label="`Solved ${row.solved ?? '—'}`">{{ row.solved ?? '—' }}</span><span :aria-label="`Penalty ${row.penalty ?? '—'}`">{{ row.penalty ?? '—' }}</span></span>
          <template v-else>{{ row.solved ?? '—' }}</template>
        </td>
        <td v-if="!xcpcio" class="penalty-cell">{{ row.penalty ?? '—' }}</td>
        <td v-for="cell in cells" :key="cell.id" class="scoreboard-problem"
            :class="[`cell-${cell.status}`, { 'cell-first-blood': cell.firstBlood }]"
            :title="cell.detail" :aria-label="cell.ariaLabel">
          <template v-if="xcpcio">
            <template v-if="cell.status === 'accepted'">
              <strong>{{ cell.time }}</strong><small>{{ cell.attempts }} {{ cell.attempts === 1 ? 'try' : 'tries' }}</small>
            </template>
            <template v-else-if="cell.hasResult">
              <strong class="scoreboard-cell-spacer" aria-hidden="true">&nbsp;</strong>
              <small>{{ cell.attempts }} {{ cell.attempts === 1 ? 'try' : 'tries' }}</small>
            </template>
          </template>
          <span v-else-if="!cell.demo" class="upstream-score">{{ cell.text }}</span>
          <template v-else-if="cell.hasResult">
            <strong>{{ cell.value }}</strong><small>{{ cell.attempts }} {{ cell.attempts === 1 ? 'try' : 'tries' }}</small>
          </template>
          <template v-else-if="!xcpcio">&nbsp;</template>
        </td>
      </tr>
      <tr v-if="!tableRows.length" class="empty-row"><td :colspan="(xcpcio ? 3 : 4) + c.problems.length">暂无参赛队伍</td></tr>
    </tbody>
    <tfoot v-if="xcpcio && !props.overview">
      <tr class="xcpcio-totals-row"><th class="summary-team" colspan="2">SUMMARY</th>
        <td class="summary-score"><span class="xcpcio-score-pair"><span>{{ totalSolved }}</span><span aria-hidden="true"></span></span></td>
        <td v-for="problem in columns" :key="problem.id" class="scoreboard-problem" :data-problem-totals="problem.id">
          <div class="xcpcio-footer-stats" title="AC 次数 / 非 AC 非 CE / 封榜后提交 / 一血时间">
            <span><b aria-hidden="true">👍</b>{{ totals[problem.id]?.accepted ?? '—' }}</span>
            <span><b aria-hidden="true">👎</b>{{ totals[problem.id]?.incorrect ?? '—' }}</span>
            <span><b class="summary-question" aria-hidden="true">?</b>{{ totals[problem.id]?.frozen ?? '—' }}</span>
            <span><b class="summary-clock" aria-hidden="true">◷</b>{{ totals[problem.id]?.firstBlood == null ? '—' : `${totals[problem.id].firstBlood}min` }}</span>
          </div>
        </td>
      </tr>
    </tfoot>
  </table>
</template>

<style scoped>
.problem-totals { display: block; margin-top: 5px; font-size: 11px; line-height: 1.3; font-weight: 500; white-space: nowrap; color: #4f6275; font-variant-numeric: tabular-nums; }
.team-name { display: block; }
</style>
