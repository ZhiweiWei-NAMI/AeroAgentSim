import { createRequire } from 'node:module';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
const require = createRequire(new URL('../package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const out = resolve(process.argv[2] ?? 'test-results/gpu');
mkdirSync(out + '/browser-tmp', { recursive: true });
mkdirSync(out + '/config', { recursive: true });
process.env.XDG_CONFIG_HOME = out + '/config';
process.chdir(out);
// The sandbox gives each command a mount namespace; Chromium's subprocesses
// can reliably address the writable scratch directory via their inherited cwd.
process.env.TMPDIR = '/proc/self/cwd/browser-tmp';
const executablePath = process.env.AEROAGENTSIM_CHROMIUM;
const probes = [];
for (const flags of [
  ['--use-gl=angle', '--use-angle=vulkan', '--enable-features=Vulkan', '--disable-vulkan-surface'],
  ['--use-gl=angle', '--use-angle=gl-egl'],
  ['--use-gl=egl'],
  ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
]) {
  let browser;
  try {
    console.log('Probe:', flags.join(' '));
    browser = await chromium.launch({ executablePath, headless: true, timeout: 20000,
      args: ['--no-sandbox', ...flags] });
    const page = await browser.newPage();
    const result = await Promise.race([page.evaluate(() => {
      const gl = document.createElement('canvas').getContext('webgl2');
      if (!gl) return { webgl2: false };
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      const renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      return { webgl2: true, renderer, hardware: !/swiftshader|llvmpipe|software/i.test(renderer) && /nvidia|rtx|geforce/i.test(renderer) };
    }), new Promise((_, reject) => { const timer = setTimeout(() => reject(Error('WebGL probe exceeded 15s')), 15000); timer.unref(); })]);
    probes.push({ flags, ...result, chromium: browser.version() });
  } catch (error) { probes.push({ flags, error: String(error) }); }
  finally {
    writeFileSync(out + '/gpu-probes.json', JSON.stringify(probes, null, 2) + '\n');
    await browser?.close();
  }
}
writeFileSync(out + '/gpu-probes.json', JSON.stringify(probes, null, 2) + '\n');
console.log(JSON.stringify(probes, null, 2));
