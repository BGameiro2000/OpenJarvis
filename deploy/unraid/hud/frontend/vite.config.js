import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// The backend serves dist/ at /. In dev, /api goes to a local backend.
export default defineConfig({
  plugins: [svelte()],
  build: { outDir: 'dist', assetsInlineLimit: 0, sourcemap: false, modulePreload: { polyfill: false } },
  server: { proxy: { '/api': 'http://127.0.0.1:8080' } },
});
