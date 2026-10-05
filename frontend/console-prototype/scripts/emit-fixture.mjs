import { DEFAULT_CONFIG } from '../src/config.js';
import { canonicalJSON, createFixtureRun, exportObservations } from '../src/runtime.js';

// Public, independently authored fixture only. No native runtime or server calls.
const run = await createFixtureRun(DEFAULT_CONFIG);
process.stdout.write(canonicalJSON(exportObservations(run)));
