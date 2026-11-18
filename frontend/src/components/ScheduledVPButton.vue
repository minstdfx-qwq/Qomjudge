<script setup>
import { computed, ref } from 'vue';
import { useContest } from '../composables/useContest.js';
const props = defineProps({ disabled: Boolean });
const c = useContest();
const open = ref(false), busy = ref(false), error = ref(''), plan = ref(null), values = ref({}), consent = ref(false);
const target = ref('');
const fields = computed(() => plan.value?.action?.fields || []);
async function prepare() {
  if (props.disabled || busy.value) return;
  open.value = true; busy.value = true; error.value = ''; plan.value = null; consent.value = false;
  const date = new Date(Date.now() + 5 * 60 * 1000);
  target.value = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
  try {
    plan.value = (await c.api('/api/schedule/prepare', {})).plan;
    values.value = Object.fromEntries(fields.value.map(field => [field.name,
      field.type === 'checkbox' ? false : String(field.default ?? field.value ?? '')]));
  } catch (failure) { error.value = failure.message; }
  finally { busy.value = false; }
}
async function schedule() {
  if (busy.value || !plan.value) return;
  busy.value = true; error.value = '';
  try {
    const result = await c.api('/api/schedule', { target: new Date(target.value).getTime() / 1000,
      fingerprint: plan.value.fingerprint, action: plan.value.action.id, values: values.value, consent: consent.value });
    c.state.scheduled_vp = result.scheduled_vp;
    open.value = false;
  } catch (failure) { error.value = failure.message; }
  finally { busy.value = false; }
}
</script>

<template>
  <button type="button" class="button button-outline schedule-vp-button" :disabled="disabled || busy" @click="prepare">定时 VP</button>
  <Teleport to="body">
    <div v-if="open" class="schedule-dialog-backdrop">
      <section role="dialog" aria-modal="true" aria-labelledby="schedule-title" class="schedule-dialog">
        <h2 id="schedule-title">指定时间开始 VP</h2>
        <p>请确认 QOJ 报名选项，并授权在指定时间自动开赛。等待期间请保持后端运行。</p>
        <form @submit.prevent="schedule">
          <label class="schedule-field">开赛时间（此电脑本地时间）<input v-model="target" type="datetime-local" step="1" required :disabled="busy"></label>
          <p v-if="plan" class="schedule-terms">{{ plan.message }}</p>
          <label v-for="field in fields" :key="field.name" class="schedule-field">
            <template v-if="field.type === 'checkbox'"><span><input v-model="values[field.name]" type="checkbox" :required="!!field.required" :disabled="busy"> {{ field.label || field.name }}</span></template>
            <template v-else>
              {{ field.label || field.name }}
              <select v-if="['select', 'radio'].includes(field.type)" v-model="values[field.name]" :required="!!field.required" :disabled="busy">
                <option value="">请选择</option><option v-for="option in field.options || []" :key="String(option.value)" :value="String(option.value)">{{ option.label ?? option.value }}</option>
              </select>
              <input v-else v-model="values[field.name]" :type="['text', 'number', 'date', 'time', 'datetime-local'].includes(field.type) ? field.type : 'text'" :required="!!field.required" :min="field.min" :max="field.max" :step="field.step" :disabled="busy">
            </template>
          </label>
          <label v-if="plan" class="schedule-field"><span><input v-model="consent" type="checkbox" required :disabled="busy"> 我已阅读报名选项，确认授权到时自动开始本队 VP。</span></label>
          <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
          <div class="schedule-actions"><button type="button" class="button button-outline" :disabled="busy" @click="open = false">取消</button>
            <button type="submit" class="button button-primary" :disabled="busy || !plan || !consent">{{ busy ? '正在处理…' : '确认时间，进入倒计时' }}</button></div>
        </form>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.schedule-vp-button { margin-left: 10px; }
.schedule-dialog-backdrop { position: fixed; inset: 0; z-index: 1000; background: #0009; display: grid; place-items: center; padding: 20px; }
.schedule-dialog { background: white; width: min(580px, 100%); max-height: 90vh; overflow-y: auto; padding: 28px; border-radius: 4px; color: #222; }
.schedule-dialog h2 { margin: 0 0 16px; }
.schedule-dialog p { line-height: 1.6; }
.schedule-field { display: flex; flex-direction: column; gap: 8px; margin: 18px 0; }
.schedule-field input:not([type=checkbox]), .schedule-field select { padding: 8px; border: 1px solid #aaa; border-radius: 3px; }
.schedule-terms { white-space: pre-line; padding: 12px; border: 1px solid #ddd; }
.schedule-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
</style>
