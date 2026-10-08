import { defineConfig, mergeConfig } from 'vite';
import shared from '../../vite.config';

// Keep all P7b build/test caches within the assigned Studio directory.
export default defineConfig(env => mergeConfig(shared(env), {
  cacheDir: 'src/studio/.vite',
  build: { outDir: 'src/studio/.build', emptyOutDir: true },
}));
