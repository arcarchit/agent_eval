<!-- One experiment: per-config quality/cost/latency, A-vs-B comparison with CI, per-task matrix,
     and a drill-down into individual trials. Refreshes every 3s while running. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { api, pct, secs, usd } from '../api';

const props = defineProps<{ id: string }>();
const exp = ref<any>(null);
const cmp = ref<any>(null);
const a = ref('');
const b = ref('');
const selected = ref<{ task: string; config: string } | null>(null);
const trials = ref<any[]>([]);
let timer: number | undefined;

async function load() {
  exp.value = await api('/experiments/' + props.id);
  if (!a.value && exp.value.configs.length >= 2) {
    a.value = exp.value.configs[0].id;
    b.value = exp.value.configs[1].id;
  }
  if (a.value && b.value) cmp.value = await api(`/experiments/${props.id}/compare?a=${a.value}&b=${b.value}`);
  if (exp.value.status === 'done') clearInterval(timer);
}

onMounted(() => {
  load();
  timer = window.setInterval(load, 3000);
});
onUnmounted(() => clearInterval(timer));

async function openCell(task: string, config: string) {
  selected.value = { task, config };
  trials.value = await api(`/experiments/${props.id}/trials?task=${task}&config=${encodeURIComponent(config)}`);
}

const progress = computed(() => {
  if (!exp.value) return '';
  const s = exp.value.summaries;
  const done = s.reduce((n: number, x: any) => n + x.trials_done + x.trials_error, 0);
  const all = done + s.reduce((n: number, x: any) => n + x.trials_pending, 0);
  return `${done} / ${all} trials finished`;
});

const ci = (c: number[]) => `[${pct(c[0])}, ${pct(c[1])}]`;
const signedPct = (x: number) => (x > 0 ? '+' : '') + pct(x);
const cellClass = (cell: any) => (!cell?.done ? 'muted' : cell.passed === cell.done ? 'pass' : cell.passed === 0 ? 'fail' : 'warn');
</script>

<template>
  <div v-if="exp">
    <h2>{{ exp.name }} <span class="muted">{{ exp.id }} · N={{ exp.trials_per_task }} · {{ exp.status }} · {{ progress }}</span></h2>
    <p>
      Export: <a :href="`/api/experiments/${id}/export?format=json`">JSON</a> ·
      <a :href="`/api/experiments/${id}/export?format=csv`">CSV</a>
    </p>

    <h3>Quality, cost and latency per config</h3>
    <table>
      <tr><th>Config</th><th class="num">Pass rate</th><th>95% CI (bootstrap over tasks)</th><th class="num">Avg cost / trial</th>
        <th class="num">Total cost</th><th class="num">Avg latency</th><th class="num">p90 latency</th><th class="num">Avg tokens</th>
        <th class="num">Done / errors</th><th>Failure categories</th></tr>
      <tr v-for="s in exp.summaries" :key="s.config_id">
        <td><strong>{{ s.config_id }}</strong></td>
        <td class="num">{{ pct(s.pass_rate) }}</td><td>{{ ci(s.pass_rate_ci) }}</td>
        <td class="num">{{ usd(s.avg_cost_usd) }}</td><td class="num">{{ usd(s.total_cost_usd) }}</td>
        <td class="num">{{ secs(s.avg_latency_ms) }}</td><td class="num">{{ secs(s.p90_latency_ms) }}</td>
        <td class="num">{{ Math.round(s.avg_tokens) || '–' }}</td>
        <td class="num">{{ s.trials_done }} / <span :class="{ warn: s.trials_error }">{{ s.trials_error }}</span></td>
        <td><span v-for="(n, k) in s.failures" :key="k" style="margin-right: 8px">{{ k }}: {{ n }}</span></td>
      </tr>
    </table>
    <p class="muted" v-if="exp.summaries.some((s: any) => s.trials_error)">
      Errored trials (provider outages after retries, crashes) are excluded from pass rates, not counted as failures.
    </p>

    <div class="card" v-if="exp.configs.length >= 2">
      <div class="row">
        <label>A <select v-model="a" @change="load"><option v-for="c in exp.configs" :key="c.id">{{ c.id }}</option></select></label>
        <label>B <select v-model="b" @change="load"><option v-for="c in exp.configs" :key="c.id">{{ c.id }}</option></select></label>
      </div>
      <div v-if="cmp" style="margin-top: 10px">
        <div class="verdict" :class="cmp.verdict === 'no significant difference' ? 'muted' : 'pass'">{{ cmp.verdict }}</div>
        <p>
          Mean difference A − B: <strong>{{ signedPct(cmp.mean_diff) }}</strong>,
          95% CI {{ ci(cmp.ci) }}, bootstrap p ≈ {{ cmp.p_value.toFixed(3) }}, paired over {{ cmp.tasks_compared }} tasks.
          <span v-if="cmp.verdict === 'no significant difference'">The CI contains 0, so this difference may be noise.</span>
          <span v-if="cmp.tasks_dropped.length" class="warn">Dropped (no finished trials on one side): {{ cmp.tasks_dropped.join(', ') }}</span>
        </p>
        <table>
          <tr><th>Task</th><th class="num">A</th><th class="num">B</th><th class="num">Δ</th><th>95% CI of Δ</th><th>Reading</th></tr>
          <tr v-for="t in cmp.per_task" :key="t.task_id">
            <td>{{ t.task_id }}</td>
            <td class="num">{{ pct(t.a_rate) }} <span class="muted">n={{ t.n_a }}</span></td>
            <td class="num">{{ pct(t.b_rate) }} <span class="muted">n={{ t.n_b }}</span></td>
            <td class="num">{{ signedPct(t.delta) }}</td><td>{{ ci(t.ci) }}</td>
            <td :class="t.label === 'A better' || t.label === 'B better' ? 'pass' : 'muted'">{{ t.label }}</td>
          </tr>
        </table>
      </div>
    </div>

    <h3>Per-task results (passed / finished) — click a cell to see its trials</h3>
    <table>
      <tr><th>Task</th><th>Grader</th><th v-for="c in exp.configs" :key="c.id">{{ c.id }}</th></tr>
      <tr v-for="t in exp.tasks" :key="t.id">
        <td>{{ t.id }}</td><td class="muted">{{ t.grader }}</td>
        <td v-for="c in exp.configs" :key="c.id">
          <a href="#" :class="cellClass(exp.matrix[t.id]?.[c.id])" @click.prevent="openCell(t.id, c.id)">
            {{ exp.matrix[t.id]?.[c.id]?.passed ?? 0 }} / {{ exp.matrix[t.id]?.[c.id]?.done ?? 0 }}
          </a>
          <span v-if="exp.matrix[t.id]?.[c.id]?.error" class="warn"> ({{ exp.matrix[t.id][c.id].error }} err)</span>
        </td>
      </tr>
    </table>

    <div class="card" v-if="selected">
      <strong>{{ selected.task }} · {{ selected.config }}</strong>
      <table>
        <tr><th>Trial</th><th>Status</th><th>Result</th><th>Failure category</th><th class="num">Cost</th><th class="num">Latency</th><th>Attempts</th></tr>
        <tr v-for="t in trials" :key="t.id">
          <td><router-link :to="'/trials/' + t.id">#{{ t.trial_index }}</router-link></td>
          <td>{{ t.status }}</td>
          <td :class="t.passed ? 'pass' : 'fail'">{{ t.status === 'done' ? (t.passed ? 'pass' : 'fail') : '–' }}</td>
          <td>{{ t.failure_category ?? '' }}<span class="error" v-if="t.error"> {{ t.error.slice(0, 120) }}</span></td>
          <td class="num">{{ usd(t.cost_usd) }}</td><td class="num">{{ secs(t.latency_ms) }}</td><td>{{ t.attempts }}</td>
        </tr>
      </table>
    </div>
  </div>
</template>
