import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
export default defineConfig({
  root: fileURLToPath(new URL('../../frontend', import.meta.url)),
  plugins: [react()],
  test: { environment: 'jsdom', globals: true, css: false, setupFiles: './src/test-setup.ts',
    include: [fileURLToPath(new URL('./agent-console.test.tsx', import.meta.url))], cache: false },
});
