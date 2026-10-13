// Records the DeepSeek demo: one Chinese instruction → plan → confirmation → run → result.
//
//   node docs/demos/deepseek-chinese-instruction/record.mjs \
//     --app http://127.0.0.1:18972 --endpoint http://127.0.0.1:8788/v1 --model deepseek-v4-pro \
//     --out /tmp/demo-output
//
// The AeroAgentSim server must start without an operator model connection, so the
// recording shows the user connecting DeepSeek in the assistant panel. Set
// DEEPSEEK_API_KEY to type a key into the form (it is never written to the logs).
import { createRequire } from 'node:module';
import { mkdirSync, appendFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(resolve(here, '../../../frontend/package.json'));
const { chromium } = require('@playwright/test');

const arg = (name, fallback) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : fallback; };
const app = arg('app', 'http://127.0.0.1:18972');
const endpoint = arg('endpoint', 'https://api.deepseek.com/v1');
const model = arg('model', 'deepseek-chat');
const out = resolve(arg('out', join(here, 'output')));
const instruction = arg('instruction', '帮我搭一个外卖无人机配送仿真：3架无人机给6个订单送餐，中途刮一阵大风，仿真10分钟，看看能否全部送达。');
const confirmation = '订单固定为 6 单，其余问题都用默认值。';
const executablePath = process.env.AEROAGENTSIM_CHROMIUM || undefined;

for (const dir of ['screenshots', 'video']) mkdirSync(join(out, dir), { recursive: true });
const started = Date.now();
const log = (step, detail = {}) => {
  const line = { t_s: Math.round((Date.now() - started) / 100) / 10, at: new Date().toISOString(), step, ...detail };
  appendFileSync(join(out, 'steps.jsonl'), JSON.stringify(line) + '\n');
  console.log(`[${line.t_s.toFixed(1).padStart(6)}s] ${step}`, Object.keys(detail).length ? JSON.stringify(detail) : '');
};

const browser = await chromium.launch({ executablePath, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1, locale: 'zh-CN',
  recordVideo: { dir: join(out, 'video'), size: { width: 1600, height: 1000 } } });
await context.addInitScript(() => { try { localStorage.setItem('aeroagentsim.locale', 'zh-CN'); } catch { /* storage unavailable */ } });
const page = await context.newPage();
page.setDefaultTimeout(30_000);
let shot = 0;
const capture = async name => { const file = `${String(++shot).padStart(2, '0')}-${name}.png`; await page.screenshot({ path: join(out, 'screenshots', file) }); return file; };
// A caption pill (recording overlay only, not part of the product) says what the viewer is watching.
const caption = async text => {
  await page.evaluate(value => {
    let node = document.getElementById('demo-caption');
    if (!node) {
      node = document.createElement('div'); node.id = 'demo-caption';
      Object.assign(node.style, { position: 'fixed', left: '50%', transform: 'translateX(-50%)', bottom: '118px', whiteSpace: 'nowrap', zIndex: 2147483647, pointerEvents: 'none', 
        padding: '8px 14px', borderRadius: '999px', background: 'rgba(12,22,36,.86)', color: '#fff', font: '600 14px/1.4 system-ui, "PingFang SC", sans-serif',
        boxShadow: '0 6px 18px rgba(0,0,0,.3)' });
      document.body.appendChild(node);
    }
    node.textContent = value;
  }, text).catch(() => {});
};
const pause = ms => page.waitForTimeout(ms);
const waitPlan = async (label, { required = true, timeout = 600_000 } = {}) => {
  const board = page.getByTestId('as-plan-board');
  await board.waitFor({ state: 'visible', timeout });
  await page.getByTestId('as-thinking').waitFor({ state: 'detached', timeout });
  await page.locator('[data-testid="as-validation"][data-state="valid"], [data-testid="as-validation"][data-state="invalid"]').first().waitFor({ timeout: 300_000 });
  const state = await page.getByTestId('as-validation').getAttribute('data-state');
  const version = (await page.getByTestId('as-plan-state').innerText()).replace(/\s+/g, ' ');
  log(label, { validation: state, plan: version });
  if (required && state !== 'valid') throw new Error(`Plan did not validate: ${version}`);
  return state;
};

try {
  log('open', { app });
  await page.goto(app);
  await caption('AeroAgentSim · 用一句中文指令让 DeepSeek 搭建并运行仿真');
  await pause(2500);
  await capture('gallery');

  await page.getByTestId('wb-describe').click();
  await page.getByTestId('wb-assistant').waitFor();
  await caption('① 在助手面板连接 DeepSeek（OpenAI 兼容接口）');
  if (!await page.getByTestId('as-connect').isVisible()) await page.getByTestId('as-model-chip').click();
  if (!await page.getByTestId('as-connect-url').isVisible()) await page.getByTestId('as-connect').getByRole('button').filter({ hasText: /Change|更换/ }).click();
  await page.getByTestId('as-connect-url').fill('');
  await page.getByTestId('as-connect-url').pressSequentially(endpoint, { delay: 25 });
  await page.getByTestId('as-connect-model').fill('');
  await page.getByTestId('as-connect-model').pressSequentially(model, { delay: 40 });
  if (process.env.DEEPSEEK_API_KEY) await page.getByTestId('as-connect-key').fill(process.env.DEEPSEEK_API_KEY);
  await pause(800);
  await capture('connect-deepseek');
  await page.getByTestId('as-connect-save').click();
  await page.getByTestId('as-input').waitFor({ timeout: 120_000 });
  log('connected', { endpoint, model, api_key: process.env.DEEPSEEK_API_KEY ? 'provided (not logged)' : 'none' });
  await pause(1500);
  await capture('connected');

  await caption('② 用户输入一句中文指令');
  await page.getByTestId('as-input').click();
  await page.getByTestId('as-input').pressSequentially(instruction, { delay: 70 });
  await pause(800);
  await capture('instruction');
  await page.getByTestId('as-send').click();
  log('instruction_sent', { text: instruction });
  await caption('DeepSeek 正在读取平台能力、起草并编译方案（真实耗时）');
  const firstState = await waitPlan('plan_ready', { required: false });
  await caption('③ 方案板：场景设定与行为层（尚未改动项目）');
  await pause(4000);
  await capture('plan-scene');
  await page.getByTestId('as-tab-interactions').click();
  await caption('交互时序：谁在什么时候做什么');
  const play = page.getByTestId('as-seq-play');
  if (await play.isVisible()) await play.click();
  await pause(6000);
  await capture('plan-interactions');
  await page.getByTestId('as-tab-changes').click();
  await caption('变更：相对起点模板的类型化操作');
  await pause(4000);
  await capture('plan-changes');
  await page.getByTestId('as-tab-scene').click();

  const questions = await page.getByTestId('as-question').count();
  log('open_questions', { count: questions });
  if (questions > 0 || firstState !== 'valid') {
    await caption('④ 方案列出待确认问题：用户回复“都用默认值”');
    await pause(2500);
    await capture('open-questions');
    const before = (await page.getByTestId('as-plan-state').innerText()).replace(/\s+/g, ' ');
    await page.getByTestId('as-input').click();
    await page.getByTestId('as-input').pressSequentially(confirmation, { delay: 90 });
    await page.getByTestId('as-send').click();
    log('confirmation_sent', { text: confirmation });
    await page.getByTestId('as-thinking').waitFor({ timeout: 30_000 }).catch(() => {});
    await waitPlan('plan_after_confirmation');
    log('plan_versions', { before, after: (await page.getByTestId('as-plan-state').innerText()).replace(/\s+/g, ' ') });
    await pause(3000);
    await capture('plan-defaults');
  }

  await caption('⑤ 用户确认：点击“确认并应用”，方案保存为项目版本');
  await pause(2000);
  await page.getByTestId('as-confirm').click();
  await page.locator('[data-testid="as-plan-board"][data-stage="configured"]').waitFor({ timeout: 120_000 });
  await page.getByTestId('as-apply-report').waitFor();
  log('applied', { url: page.url() });
  await pause(4000);
  await capture('applied');

  await caption('⑥ 初始化到 t = 0，然后启动仿真（10× 速度）');
  await page.getByTestId('as-initialize').click();
  await page.getByTestId('wb-start').waitFor({ timeout: 240_000 });
  log('initialized', { url: page.url() });
  await pause(2500);
  await capture('initialized');
  await page.getByTestId('wb-start').click();
  await page.getByTestId('wb-speed').waitFor({ timeout: 60_000 });
  await page.getByTestId('wb-speed').selectOption('10');
  log('started', { speed: '10x' });
  await caption('仿真运行中：送餐无人机取餐、飞行，阵风期间停飞等待');
  let last = '';
  const deadline = Date.now() + 30 * 60_000;
  for (let i = 0; Date.now() < deadline; i++) {
    const status = await page.getByTestId('workbench').getAttribute('data-status');
    if (status !== last) { log('run_status', { status }); last = status; }
    if (['completed', 'stopped', 'faulted', 'interrupted', 'input_timeout'].includes(status)) break;
    if (i % 15 === 7) await capture(`running-${String(i).padStart(3, '0')}`);
    await pause(2000);
  }
  if (last !== 'completed') throw new Error(`Run ended as ${last}`);

  await caption('⑦ 结果：经过（预期与实际记录对照）与关键指标');
  await page.getByTestId('as-tab-story').click();
  await page.locator('.as-story:not(.is-loading)').waitFor({ timeout: 60_000 });
  await pause(4000);
  const story = await page.getByTestId('as-story').innerText();
  const kpis = await page.getByTestId('wb-kpis').innerText().catch(() => '');
  log('result', { story: story.slice(0, 2000), kpis });
  await capture('result-story');
  await page.getByTestId('as-story').evaluate(node => node.scrollIntoView({ block: 'end' })).catch(() => {});
  await pause(3000);
  await capture('result-story-end');
  await caption('完成：一句中文指令 → DeepSeek 配置 → 用户确认 → 仿真运行成功');
  await pause(5000);
  await capture('done');
  log('done', { url: page.url() });
} catch (error) {
  log('error', { message: String(error?.stack ?? error) });
  await capture('error').catch(() => {});
  process.exitCode = 1;
} finally {
  const video = page.video();
  await context.close();
  await browser.close();
  if (video) writeFileSync(join(out, 'video', 'path.txt'), await video.path() + '\n');
}
