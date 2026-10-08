import { defineConfig, loadEnv, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';

// The existing sources keep the CRA-style convention of JSX inside .js files.
// Vite's esbuild pipeline derives its loader from the file extension and skips
// .js entirely, while @vitejs/plugin-react only applies its babel transform in
// dev mode. This pre-plugin compiles JSX in .js for dev, build and vitest by
// re-running the file through esbuild under a .jsx name (jsx loader, automatic
// runtime). Plain .js without JSX is unaffected.
function jsWithJsx() {
  return {
    name: 'js-with-jsx',
    enforce: 'pre' as const,
    async transform(code: string, id: string) {
      const [path] = id.split('?', 2);
      if (path.endsWith('.js') && !path.includes('/node_modules/')) {
        const result = await transformWithEsbuild(code, `${path}.jsx`, { jsx: 'automatic' });
        return { code: result.code, map: JSON.stringify(result.map) };
      }
      return null;
    },
  };
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
const env = loadEnv(mode, process.cwd(), '');
return {
  define: {
    'process.env.REACT_APP_API_BASE_URL': JSON.stringify(env.REACT_APP_API_BASE_URL ?? '/api'),
    'process.env.REACT_APP_WS_BASE_URL': JSON.stringify(env.REACT_APP_WS_BASE_URL ?? ''),
  },
  optimizeDeps: { entries: ['index.html'], esbuildOptions: { loader: { '.js': 'jsx' as const } } },
  plugins: [
    jsWithJsx(),
    react({
      // Let the react plugin also cover .js so dev fast-refresh works there.
      include: /\.(m?[jt]sx?)$/,
    }),
  ],
  server: {
    port: 3000,
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://localhost:8002',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8002',
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1600,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test-setup.ts',
    css: false,
    // Playwright e2e specs live outside src and must not run under vitest.
    include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}'],
  },
};
});
