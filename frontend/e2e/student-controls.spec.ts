import { expect, test } from '@playwright/test';
import { fixtureHeader, fixtureCommits, at } from '../src/behaviours/extension-fixture';

// A clearly labelled recorded fixture makes all operational states available
// deterministically; the other console specs exercise the actual runtime.
test('student run controls, replay, viewer choices, graph search and cause walk', async ({ page }) => {
  const errors: string[] = [], bad: string[] = [], actions: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('response', response => { if (response.status() >= 400) bad.push(`${response.status()} ${response.url()}`); });
  await page.addInitScript(() => localStorage.setItem('aas:welcome-dismissed', '1'));
  const id = 'student-fixture';
  const presets = ['vehicle.reporter', 'edge.coordinator', 'uav.alpha', 'uav.bravo'];
  const extras = Array.from({ length: 44 }, (_, i) => ({ id: `extra-${i}`, generation: 1, typeId: 'vehicle' }));
  const times = ['0', '1000000000', '1000000000', '3000000000'];
  const normalized = JSON.parse(JSON.stringify(fixtureCommits), (key, value) => key === 'ns' ? value === '10' ? '1000000000' : value === '9007199254740993' ? '3000000000' : value : value) as typeof fixtureCommits;
  const commits = normalized.map((commit, i) => ({ ...commit, at: at(times[i], commit.at.microstep),
    kpis: { tasks_completed: { value: i, unit: 'count', label: 'Tasks completed' }, energy_used_kj: { value: i * 4, unit: 'kJ', label: 'Energy used' }, separation_conflicts: { value: i, unit: 'count', label: 'Separation conflicts' } },
    events: i ? [{ at_ns: times[i], category: i === 3 ? 'energy' : 'logistics', severity: i === 3 ? 'warning' : 'info', text: 'Task ready for vehicle-1', text_zh: 'vehicle-1 的任务已就绪', entities: ['task-1', 'vehicle-1'] }] : [],
  }));
  commits[0].created = [...commits[0].created, ...extras, ...presets.map(id => ({ id, generation: 1, typeId: 'vehicle' }))];
  commits[0].facts = [...commits[0].facts, ...[...extras, ...presets.map(id => ({ id, generation: 1 }))].map((entity, i) => ({ entity, fieldId: 'position', value: [i % 8 * 5, Math.floor(i / 8) * 5, 10], producer: 'motion', validFrom: at('0') }))];
  const injection = { id: 'alarm', name: 'Alarm', stream_id: 'operator', command: 'aas.runtime.inject_event', target: 'behaviour', emits: 'fixture.alarm' };
  const header = { ...fixtureHeader, runId: id, kernelRunId: 'fixture-kernel-run', end: at('3000000000'),
    messages: [
      { id: 'aas.runtime.inject_event', kind: 'command', schema: { type: 'record', members: {} } },
      { id: 'fixture.alarm', kind: 'event', schema: { type: 'record', members: { reason: { type: 'string', default: 'Student alarm' } } } },
    ], behaviour: { ...fixtureHeader.behaviour, injectionPoints: [injection] },
  };
  let status = 'running';
  let release!: () => void;
  const tail = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/v1/runs', route => route.fulfill({ json: [
    { id, scenario: 'Student control fixture', status, until_ns: '100000000000', template_id: 'parcel-delivery', template_title: 'Student control fixture' },
    { id: 'comparison-fixture', scenario: 'Comparison fixture', status: 'completed', until_ns: '3000000000' },
  ] }));
  await page.route('**/v1/runs/compare?*', route => route.fulfill({ json: { runs: [
    { id, title: 'Student control fixture', kpis: commits[3].kpis, knobs: { fleet_size: 6 }, algorithms: {} },
    { id: 'comparison-fixture', title: 'Comparison fixture', kpis: { ...commits[3].kpis, tasks_completed: { value: 2, unit: 'count', label: 'Tasks completed' } }, knobs: { fleet_size: 3 }, algorithms: {} },
  ] } }));
  await page.route(`**/v1/runs/${id}/header`, route => route.fulfill({ json: header }));
  await page.route(`**/v1/runs/${id}/commits?*`, route => {
    const start = Number(new URL(route.request().url()).searchParams.get('from'));
    return route.fulfill({ json: { commits: start <= 1 ? commits : [], next: 5, status, ...(['stopped', 'completed'].includes(status) ? { finalCursor: 5 } : {}) } });
  });
  await page.route(`**/v1/runs/${id}/stream?*`, async route => {
    await tail;
    await route.fulfill({ contentType: 'text/event-stream', body: 'event: end\ndata: {"status":"stopped","finalCursor":5}\n\n' });
  });
  await page.route(`**/v1/studio/runs/${id}/configuration`, route => route.fulfill({ json: {
    service_run_id: id, kernel_run_id: 'fixture-kernel-run', epoch: 'fixture-epoch',
    scenario: { id: 'traffic-accident', engines: {}, run: { advance_ns: 1000000000, until_ns: 100000000000 }, ingress_streams: [{ id: 'operator', initial_watermark_ns: 0 }], behaviours: [{ injection_points: [injection] }] },
  } }));
  await page.route(`**/v1/runs/${id}/artifacts`, route => route.fulfill({ json: [] }));
  await page.route(`**/v1/runs/${id}/kpis`, route => route.fulfill({ json: { run_id: id, final: commits[3].kpis, series: commits.map(row => ({ commitIndex: row.commitIndex, at_ns: row.at.ns, kpis: row.kpis })) } }));
  for (const action of ['pause', 'resume', 'stop']) await page.route(`**/v1/runs/${id}/${action}`, route => {
    actions.push(action); status = action === 'pause' ? 'paused' : action === 'resume' ? 'running' : 'stopped';
    return route.fulfill({ json: { requested: action, status } });
  });
  await page.route(`**/v1/runs/${id}/watermark`, route => {
    const value = route.request().postDataJSON().watermark_ns;
    return route.fulfill({ json: { watermark_ns: value.$integer ?? String(value) } });
  });
  await page.route(`**/v1/runs/${id}/ingress`, route => { actions.push('inject'); return route.fulfill({ json: { disposition: 'accepted', status: 'accepted' } }); });
  try {
    await page.goto(`/runs/${id}?mode=live&view=simple&template=parcel-delivery`);
    await expect(page.getByTestId('simple-run-view')).toHaveAttribute('data-cut', '4');
    await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered', 'true');
    for (const label of ['All events', 'Logistics', 'Energy', 'Any importance', 'info', 'notice', 'warning', 'critical']) {
      await page.getByRole('button', { name: label, exact: true }).click();
    }
    await page.getByRole('button', { name: 'All events', exact: true }).click();
    await page.getByRole('button', { name: 'Any importance', exact: true }).click();
    await page.getByRole('checkbox', { name: 'Show all labels', exact: true }).check();
    await page.getByRole('checkbox', { name: 'Show all labels', exact: true }).uncheck();
    await page.getByRole('tab', { name: 'Results', exact: true }).click();
    await expect(page.locator('.newc-kpi-card').first()).toBeVisible();
    await page.locator('.newc-kpi-help').first().click();
    await page.locator('.newc-kpi-more > summary').click();
    await page.getByRole('tab', { name: /^Events/ }).click();
    await page.getByRole('slider', { name: 'Scenario timeline', exact: true }).press('ArrowLeft');
    const speed = page.getByRole('combobox', { name: 'Playback speed', exact: true });
    await speed.press('Enter'); await speed.press('ArrowDown'); await speed.press('Enter');
    await page.getByRole('button', { name: 'Play', exact: true }).click();
    await page.getByRole('button', { name: 'Pause playback', exact: true }).click();
    await page.getByRole('button', { name: 'Follow live', exact: true }).click();
    await page.getByRole('button', { name: 'Run controls', exact: true }).click();
    const controls = page.getByRole('dialog');
    for (const action of ['pause', 'resume']) {
      await expect(controls.getByRole('button', { name: action === 'pause' ? 'Pause simulation' : 'Resume simulation', exact: true })).toBeEnabled();
      await controls.getByRole('button', { name: action === 'pause' ? 'Pause simulation' : 'Resume simulation', exact: true }).click();
    }
    await controls.getByTestId('inject-alarm').click();
    await expect(controls).toContainText('Event accepted for processing');
    await controls.getByRole('button', { name: 'Close', exact: true }).click();
    await page.getByTestId('expert-toggle').click();
    for (const preset of ['Reporter', 'Edge overview', 'Alpha', 'Bravo']) await page.getByRole('button', { name: preset, exact: true }).click();
    for (const mode of ['orbit', 'follow', 'chase', 'cinematic']) await page.getByLabel('View camera', { exact: true }).selectOption(mode);
    for (const quality of ['low', 'med', 'high']) await page.getByLabel('View quality', { exact: true }).selectOption(quality);
    for (const lighting of ['dusk', 'day']) await page.getByLabel('Display lighting', { exact: true }).selectOption(lighting);
    await page.getByRole('button', { name: 'Toggle inspector', exact: true }).click();
    await page.getByRole('button', { name: 'Toggle inspector', exact: true }).click();
    for (const view of ['Relations / topology', 'Queue / state', '3D only']) await page.getByRole('button', { name: view, exact: true }).click();
    await page.getByLabel('Graph directory', { exact: true }).selectOption('tasks');
    await page.getByLabel('Graph directory', { exact: true }).selectOption('');
    await page.getByLabel('Graph type', { exact: true }).selectOption('vehicle');
    await page.getByLabel('Graph type', { exact: true }).selectOption('');
    for (const label of ['Focus + neighbors', 'Hide background actors', 'Show runtime entities']) {
      const toggle = page.getByRole('checkbox', { name: label, exact: true }); await toggle.check(); await toggle.uncheck();
    }
    for (const label of ['Fit graph', 'Zoom in graph', 'Zoom out graph']) await page.getByRole('button', { name: label, exact: true }).click();
    await page.getByRole('button', { name: 'Expand all groups', exact: true }).click();
    await page.getByRole('button', { name: 'Collapse big groups', exact: true }).click();
    await page.getByLabel('Search graph entities', { exact: true }).fill('extra-12');
    await expect(page.getByTestId('entity-graph')).toContainText('matching entities');
    await page.getByLabel('Search graph entities', { exact: true }).fill('');
    await page.getByLabel('Graph entity', { exact: true }).selectOption('task-1:1');
    await expect(page.getByTestId('selected-entity-panel')).toContainText('Task 1');
    await page.getByText(/^Conditions and event chains ·/).click();
    await page.getByRole('button', { name: /^Seek launch → moving/ }).click();
    await page.locator('.ct-moment').first().click();
    const cause = page.getByTestId('cause-trace'); await expect(cause).toBeVisible();
    const links = cause.locator('.ct-follow-btn');
    await expect(links.first()).toBeVisible();
    await links.first().click();
    await expect(cause.getByRole('button', { name: '← Back', exact: true })).toBeEnabled();
    await cause.getByRole('button', { name: '← Back', exact: true }).click();
    await cause.getByRole('button', { name: 'Close explanation', exact: true }).click();
    await page.getByRole('button', { name: 'Photos', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Refresh stored artifacts', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
    await page.getByRole('button', { name: 'External events & controls', exact: true }).click();
    await page.getByRole('button', { name: 'Stop simulation', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Pause simulation', exact: true })).toBeDisabled();
    expect(actions).toEqual(['pause', 'resume', 'inject', 'stop']);
    await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
    release();
    await page.getByTestId('nav-runs').click();
    await expect(page.getByTestId('runs-page')).toBeVisible();
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await page.getByRole('checkbox', { name: 'Compare Student control fixture', exact: true }).check();
    await page.getByRole('checkbox', { name: 'Compare Comparison fixture', exact: true }).check();
    await page.getByTestId('compare-runs').click();
    await expect(page.getByRole('heading', { name: 'Comparing 2 runs', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Go back', exact: true }).click();
    await page.getByRole('link', { name: 'Student control fixture', exact: true }).click();
    await expect(page.getByTestId('simple-run-view')).toBeVisible();
    expect(errors).toEqual([]); expect(bad).toEqual([]);
  } finally { release(); }
});
