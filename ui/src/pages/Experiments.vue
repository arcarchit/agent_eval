<!-- Start an experiment (configs × tasks × N trials) and list past experiments. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { api } from '../api';

const router = useRouter();
const meta = ref<any>(null);
const configs = ref<any[]>([]);
const experiments = ref<any[]>([]);
const error = ref('');
const form = ref({ name: '', config_ids: [] as string[], trials_per_task: 5, task_ids: [] as string[] });

const taskCount = computed(() => form.value.task_ids.length || meta.value?.tasks.length || 0);
const trialCount = computed(() => taskCount.value * form.value.config_ids.length * form.value.trials_per_task);

onMounted(async () => {
  [meta.value, configs.value, experiments.value] = await Promise.all([api('/meta'), api('/configs'), api('/experiments')]);
});

async function start() {
  error.value = '';
  try {
    const { id } = await api('/experiments', form.value);
    router.push('/experiments/' + id);
  } catch (e: any) {
    error.value = e.message;
  }
}
</script>

<template>
  <h2>Experiments</h2>
  <div class="card" v-if="meta">
    <div class="row">
      <label>Name <input v-model="form.name" placeholder="skills on vs off" /></label>
      <label>Trials per task (N) <input type="number" v-model.number="form.trials_per_task" style="width: 80px" /></label>
      <label>Tasks (none selected = all {{ meta.tasks.length }})
        <select multiple v-model="form.task_ids" size="4" style="min-width: 220px">
          <option v-for="t in meta.tasks" :key="t.id" :value="t.id">{{ t.id }} · {{ t.grader }}{{ t.needs ? ' · needs ' + t.needs : '' }}</option>
        </select>
      </label>
    </div>
    <p class="muted" style="margin: 10px 0 4px">Configs to compare:</p>
    <div class="row">
      <span v-for="c in configs" :key="c.id"><input type="checkbox" :value="c.id" v-model="form.config_ids" /> {{ c.id }}</span>
      <router-link v-if="!configs.length" to="/configs">Create a config first</router-link>
    </div>
    <div class="row" style="margin-top: 10px">
      <button @click="start" :disabled="!form.config_ids.length">Run {{ trialCount }} trials</button>
    </div>
    <p class="error" v-if="error">{{ error }}</p>
  </div>

  <table>
    <tr><th>Experiment</th><th>Configs</th><th>N</th><th>Progress</th><th>Status</th><th>Created</th></tr>
    <tr v-for="e in experiments" :key="e.id">
      <td><router-link :to="'/experiments/' + e.id">{{ e.name }}</router-link> <span class="muted">{{ e.id }}</span></td>
      <td>{{ e.config_ids.join(' vs ') }}</td>
      <td>{{ e.trials_per_task }}</td>
      <td>{{ e.done + e.errors }} / {{ e.total }} <span v-if="e.errors" class="warn">({{ e.errors }} errors)</span></td>
      <td>{{ e.status }}</td>
      <td class="muted">{{ e.created_at.slice(0, 16) }}</td>
    </tr>
  </table>
</template>
