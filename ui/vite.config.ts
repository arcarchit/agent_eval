import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

// In dev, /api calls go to the NestJS server on :3000.
export default defineConfig({
  plugins: [vue()],
  server: { proxy: { '/api': 'http://localhost:3000' } },
});
