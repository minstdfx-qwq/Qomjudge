<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useContest } from '../composables/useContest.js';
const c = useContest();
const now = ref(Date.now()), warning = ref(false), cancelling = ref(false);
const job = computed(() => c.state?.scheduled_vp);
const active = computed(() => ['pending', 'starting'].includes(job.value?.status));
const remaining = computed(() => Math.max(0, Math.ceil(Number(job.value?.target) - now.value / 1000 - c.clockOffset)));
const clock = computed(() => [Math.floor(remaining.value / 3600), Math.floor(remaining.value % 3600 / 60), remaining.value % 60]
  .map(value => String(value).padStart(2, '0')).join(':').split(''));
const masks = ['abcdef', 'bc', 'abdeg', 'abcdg', 'bcfg', 'acdfg', 'acdefg', 'abc', 'abcdefg', 'abcdfg'];
const segments = [
  ['a', '12,3 48,3 53,8 47,13 13,13 7,8'], ['b', '49,15 55,9 58,15 55,44 49,49 45,43'],
  ['c', '49,53 55,48 58,55 55,84 49,91 45,85'], ['d', '13,87 47,87 53,93 48,98 12,98 7,93'],
  ['e', '5,55 11,49 15,55 12,85 6,91 2,85'], ['f', '5,15 11,9 15,15 12,44 6,49 2,43'],
  ['g', '12,46 47,46 53,51 47,56 12,56 6,51'],
];
let ticker, warningTimer, statusTimer, armedAt = 0, disposed = false, reading = false;
let waitingJobId = null;
function touch(event) {
  if (!active.value || Date.now() < armedAt || event.target?.closest?.('[data-countdown-cancel]')) return;
  warning.value = true;
  clearTimeout(warningTimer); warningTimer = setTimeout(() => { warning.value = false; }, 2500);
  if (event.type === 'keydown' && !['F5', 'F11'].includes(event.key) && !event.ctrlKey && !event.metaKey) event.preventDefault();
}
async function readStatus() {
  if (!active.value || reading || disposed) return;
  reading = true;
  try {
    const result = await c.api('/api/schedule/status');
    if (disposed || !c.state) return;
    c.state.scheduled_vp = result.scheduled_vp;
    if (result.server_time) c.clockOffset = result.server_time - Date.now() / 1000;
    if (result.scheduled_vp?.status === 'started') {
      await c.refreshState(true);
    } else if (result.scheduled_vp?.status === 'error') {
      await c.refreshState(true);
    }
  } catch { /* Keep the countdown and retry; the backend owns the start. */ }
  finally { reading = false; }
}
async function cancel() {
  if (cancelling.value) return;
  cancelling.value = true;
  try { c.state.scheduled_vp = (await c.api('/api/schedule/cancel', {})).scheduled_vp; }
  catch (failure) { c.notify(failure.message, 'error'); }
  finally { cancelling.value = false; }
}
watch(active, value => {
  document.documentElement.classList.toggle('vp-countdown-active', value);
  if (value) { armedAt = Date.now() + 600; warning.value = false; readStatus(); }
}, { immediate: true });
watch([() => job.value?.id, () => job.value?.status, () => c.canRead], ([id, status, readable]) => {
  if (['pending', 'starting'].includes(status)) { waitingJobId = id; return; }
  // Only finish a countdown observed in this tab. Historical completed jobs
  // and ordinary state polls must never take over the user's navigation.
  if (!id || waitingJobId !== id) return;
  if (status === 'started' && readable) {
    waitingJobId = null;
    if (c.tab === 'participation') c.navigate(c.can('manage') ? 'overview' : 'problems');
  } else if (status === 'error') {
    waitingJobId = null;
    c.participationError = job.value.message; c.navigate('participation');
  } else if (status === 'cancelled') waitingJobId = null;
}, { immediate: true });
onMounted(() => {
  ticker = setInterval(() => { now.value = Date.now(); }, 100);
  statusTimer = setInterval(readStatus, 1000);
  for (const event of ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart']) document.addEventListener(event, touch, { capture: true, passive: false });
});
onUnmounted(() => {
  disposed = true; clearInterval(ticker); clearInterval(statusTimer); clearTimeout(warningTimer);
  document.documentElement.classList.remove('vp-countdown-active');
  for (const event of ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart']) document.removeEventListener(event, touch, true);
});
</script>

<template>
  <Teleport to="body">
    <section v-if="active" class="vp-countdown" role="dialog" aria-modal="true" aria-label="VP 开赛倒计时">
      <div class="digital-clock" role="timer" :aria-label="clock.join('')">
        <svg v-for="(digit, index) in clock" :key="index" :viewBox="digit === ':' ? '0 0 22 102' : '0 0 60 102'" :class="{ colon: digit === ':' }" aria-hidden="true">
          <template v-if="digit === ':'"><circle cx="11" cy="32" r="4" fill="white"/><circle cx="11" cy="72" r="4" fill="white"/></template>
          <polygon v-for="[segment, points] in digit === ':' ? [] : segments" :key="segment" :points="points" :fill="masks[Number(digit)].includes(segment) ? '#fff' : '#090909'"/>
        </svg>
      </div>
      <p class="countdown-warning" :class="{ visible: warning }">Do not touch mouse and keyboard!</p>
      <p v-if="job?.status === 'starting' || !remaining" class="countdown-starting">正在等待 QOJ 确认开赛…</p>
      <button data-countdown-cancel type="button" class="countdown-cancel" :disabled="cancelling || job?.status === 'starting'" @click="cancel">{{ cancelling ? '正在取消…' : '取消定时 VP' }}</button>
    </section>
  </Teleport>
</template>

<style>
html.vp-countdown-active { overflow: hidden; }
html.vp-countdown-active #app { visibility: hidden; }
.vp-countdown { position: fixed; inset: 0; z-index: 10000; background: #000; color: #fff; display: flex; flex-direction: column; align-items: center; justify-content: center; cursor: none; }
.digital-clock { display: flex; align-items: center; justify-content: center; gap: clamp(4px, 1.1vw, 18px); width: min(86vw, 1100px); }
.digital-clock svg { width: 12%; overflow: visible; }
.digital-clock svg.colon { width: 4.4%; }
.countdown-warning { height: 2em; margin: 48px 20px 0; text-align: center; font: 500 clamp(15px, 2vw, 26px)/1.5 monospace; visibility: hidden; }
.countdown-warning.visible { visibility: visible; }
.countdown-starting { position: absolute; bottom: 100px; font: 16px monospace; }
.countdown-cancel { position: absolute; bottom: 28px; right: 28px; padding: 8px 12px; border: 1px solid #444; color: #888; background: #000; cursor: pointer; font: 13px sans-serif; }
.countdown-cancel:hover, .countdown-cancel:focus { color: white; border-color: white; }
</style>
