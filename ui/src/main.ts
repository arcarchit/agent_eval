import { createApp } from 'vue';
import { createRouter, createWebHashHistory } from 'vue-router';
import App from './App.vue';
import Configs from './pages/Configs.vue';
import Experiments from './pages/Experiments.vue';
import Experiment from './pages/Experiment.vue';
import Trial from './pages/Trial.vue';

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/experiments' },
    { path: '/configs', component: Configs },
    { path: '/experiments', component: Experiments },
    { path: '/experiments/:id', component: Experiment, props: true },
    { path: '/trials/:id', component: Trial, props: true },
  ],
});

createApp(App).use(router).mount('#app');
