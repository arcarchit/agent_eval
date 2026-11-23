<!-- Create and list configs. A config = harness × model × settings. Configs are immutable:
     saving changed settings under the same name creates a new version. -->
<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { api } from '../api';

const meta = ref<any>(null);
const configs = ref<any[]>([]);
const error = ref('');
const form = ref({
  name: '', harness: 'hermes', model: '', skills: [] as string[], memory: false, tools: ['file'] as string[],
  temperature: 0, max_steps: 10, max_tokens: 1024, timeout_s: 300,
});

async function load() {
  configs.value = await api('/configs');
}
onMounted(async () => {
  meta.value = await api('/meta');
  form.value.model = meta.value.models[0];
  await load();
});

async function save() {
  error.value = '';
  try {
    await api('/configs', form.value);
    await load();
  } catch (e: any) {
    error.value = e.message;
  }
}
</script>

<template>
  <h2>Configs</h2>
  <div class="card" v-if="meta">
    <div class="row">
      <label>Name <input v-model="form.name" placeholder="hermes-skills-on" /></label>
      <label>Harness <select v-model="form.harness"><option v-for="h in meta.harnesses" :key="h">{{ h }}</option></select></label>
      <label>Model <select v-model="form.model"><option v-for="m in meta.models" :key="m">{{ m }}</option></select></label>
      <label>Temperature <input type="number" step="0.1" v-model.number="form.temperature" style="width: 80px" /></label>
      <label>Max steps <input type="number" v-model.number="form.max_steps" style="width: 70px" /></label>
      <label>Max tokens / reply <input type="number" v-model.number="form.max_tokens" style="width: 90px" /></label>
      <label>Timeout (s) <input type="number" v-model.number="form.timeout_s" style="width: 80px" /></label>
    </div>
    <div class="row" style="margin-top: 10px">
      <span class="muted">Tools:</span>
      <span v-for="t in meta.toolsets" :key="t"><input type="checkbox" :value="t" v-model="form.tools" /> {{ t }}</span>
      <span class="muted">Skills:</span>
      <span v-for="s in meta.skills" :key="s"><input type="checkbox" :value="s" v-model="form.skills" /> {{ s }}</span>
      <span><input type="checkbox" v-model="form.memory" /> memory</span>
      <button @click="save">Save config</button>
    </div>
    <p class="error" v-if="error">{{ error }}</p>
  </div>

  <table>
    <tr><th>ID</th><th>Harness</th><th>Model</th><th>Skills</th><th>Memory</th><th>Tools</th><th>Temp</th><th>Steps</th><th>Created</th></tr>
    <tr v-for="c in configs" :key="c.id">
      <td><strong>{{ c.id }}</strong></td>
      <td>{{ c.spec.harness }}</td><td>{{ c.spec.model }}</td>
      <td>{{ c.spec.skills.join(', ') || '–' }}</td><td>{{ c.spec.memory ? 'on' : 'off' }}</td>
      <td>{{ c.spec.tools.join(', ') }}</td><td>{{ c.spec.temperature }}</td><td>{{ c.spec.max_steps }}</td>
      <td class="muted">{{ c.created_at.slice(0, 16) }}</td>
    </tr>
  </table>
</template>
