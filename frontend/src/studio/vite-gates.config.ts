import { defineConfig, mergeConfig } from 'vite';
import shared from '../../vite.config';

// Build/test scratch stays outside the shared worktree.
const scratch = process.env.AAS_STUDIO_SCRATCH ?? '/tmp/aas-q/s2/studio';
export default defineConfig(env => mergeConfig(shared(env), {
  cacheDir: `${scratch}/vite-cache`,
  build: { outDir: `${scratch}/build`, emptyOutDir: true },
  test: { cache: { dir: `${scratch}/vitest-cache` } },
}));
