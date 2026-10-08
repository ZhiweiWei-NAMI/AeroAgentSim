import { resolve } from 'node:path';
import original from '../../frontend/vite.config';

// Keep gate artifacts within the task's owned test tree.
export default (environment: { command: 'build' | 'serve'; mode: string }) => {
  const config = original(environment);
  const artifacts = resolve(process.cwd(), '../tests/platform/.p1f-checks');
  return { ...config, cacheDir: resolve(artifacts, 'vite-cache'),
    build: { ...config.build, outDir: resolve(artifacts, 'frontend-build') },
    test: { ...config.test, cache: false, pool: 'forks', maxWorkers: 1, minWorkers: 1 } };
};
