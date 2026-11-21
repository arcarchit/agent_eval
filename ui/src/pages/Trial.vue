<!-- One trial: the task, the config, the grade with its reasoning, and the full trajectory
     (messages, tool calls and results, memory reads/writes, skills used). -->
<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { api, secs, usd } from '../api';

const props = defineProps<{ id: string }>();
const t = ref<any>(null);
onMounted(async () => (t.value = await api('/trials/' + props.id)));

// Duration of each step = time since the previous event. For an ASSISTANT event that is the model call's
// latency; for a TOOL RESULT it is how long the tool ran.
function duration(idx: number | string): string {
  const i = Number(idx);
  const ev = t.value.trace.events;
  if (i === 0 || !ev[i].ts || !ev[i - 1].ts) return '';
  const d = ev[i].ts - ev[i - 1].ts;
  return d >= 0.05 ? d.toFixed(1) + 's' : '';
}
const stepKind: Record<string, string> = { assistant: 'model call', tool_result: 'tool', user: 'harness setup' };

const label: Record<string, string> = {
  session_start: 'NEW SESSION', user: 'USER', assistant: 'ASSISTANT', tool_call: 'TOOL CALL',
  tool_result: 'TOOL RESULT', memory_read: 'MEMORY READ', memory_write: 'MEMORY WRITE', skill_used: 'SKILL USED',
  session_end: 'SESSION END',
};
const color: Record<string, string> = {
  user: 'var(--accent)', assistant: 'var(--fg)', tool_call: 'var(--warn)', tool_result: 'var(--muted)',
  memory_read: '#a855f7', memory_write: '#a855f7', skill_used: '#0891b2', session_start: 'var(--muted)',
  session_end: 'var(--muted)',
};
</script>

<template>
  <div v-if="t">
    <p><router-link :to="'/experiments/' + t.experiment_id">← experiment</router-link></p>
    <h2>
      {{ t.task_id }} · {{ t.config_id }} · trial #{{ t.trial_index }}
      <span :class="t.passed ? 'pass' : 'fail'">{{ t.status === 'done' ? (t.passed ? 'PASSED' : 'FAILED') : t.status.toUpperCase() }}</span>
    </h2>

    <div class="card">
      <div><strong>Task prompt:</strong> {{ t.task.prompt }}</div>
      <div v-if="t.task.memory_setup"><strong>Earlier sessions:</strong> {{ t.task.memory_setup.join(' | ') }}</div>
      <div><strong>Grader:</strong> {{ t.task.grader.type }}
        <span class="muted" v-if="t.task.grader.expected">expected "{{ t.task.grader.expected }}"</span></div>
      <div v-if="t.grader"><strong>Grader reasoning:</strong> <pre>{{ t.grader.reasoning }}</pre></div>
      <div v-if="t.failure_category"><strong>Failure category:</strong> <span class="fail">{{ t.failure_category }}</span></div>
      <div v-if="t.error" class="error"><strong>Error:</strong> <pre>{{ t.error }}</pre></div>
      <div class="muted">
        Config: {{ t.config.harness }} / {{ t.config.model }} · skills: {{ t.config.skills.join(', ') || 'none' }} ·
        memory: {{ t.config.memory ? 'on' : 'off' }} · tokens {{ t.input_tokens }} in / {{ t.output_tokens }} out ·
        cost {{ usd(t.cost_usd) }} · latency {{ secs(t.latency_ms) }} · attempts {{ t.attempts }}
      </div>
    </div>

    <template v-if="t.trace">
      <h3>Trajectory <span class="muted">({{ t.llm_calls ?? '?' }} LLM calls, {{ t.turns ?? '?' }} user turn(s), stop reason: {{ t.trace.stop_reason }};
        times on the right = duration of each step)</span></h3>
      <div v-for="(e, i) in t.trace.events" :key="i" class="card" :style="{ borderLeft: '4px solid ' + color[e.type] }">
        <strong :style="{ color: color[e.type] }">{{ label[e.type] ?? e.type }}</strong>
        <span v-if="e.name"> · {{ e.name }}</span>
        <span v-if="e.error" class="error"> · error</span>
        <span v-if="e.stop_reason" :class="e.stop_reason === 'completed' ? 'muted' : 'warn'"> · {{ e.stop_reason }}</span>
        <span v-if="duration(i)" class="muted" style="float: right">{{ stepKind[e.type] ?? 'after' }} {{ duration(i) }}</span>
        <pre v-if="e.content">{{ e.content }}</pre>
        <pre v-if="e.arguments" class="muted">{{ e.arguments }}</pre>
        <pre v-if="e.output">{{ e.output }}</pre>
      </div>
      <div class="card"><strong>Final answer:</strong> <pre>{{ t.trace.final_answer || '(empty)' }}</pre></div>
    </template>
  </div>
</template>
