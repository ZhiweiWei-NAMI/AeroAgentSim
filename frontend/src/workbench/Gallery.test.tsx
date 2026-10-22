import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { Gallery, localizedGuide, readyWorldsHeading } from './Gallery';
import { ApiError, type WorkbenchApi } from './api';
// Resolve the path on disk so Vite cannot rewrite the CSS import to a browser asset.
const galleryStyles = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'gallery.css'), 'utf8');
const template = { id: 'food-delivery', title: 'Meal delivery', title_zh: '外卖配送', summary: 'Meals', summary_zh: '餐食', category: 'logistics', difficulty: 'beginner', duration_s: 360, observe: [], try: [], knobs: [], kpis: ['orders_completed'] };
const external = { ...template, id: 'external-template', title: 'External patrol', title_zh: '外部巡检' };
const api = () => ({ base: '/api', templates: vi.fn().mockResolvedValue([template]), projects: vi.fn().mockResolvedValue([]), guide: vi.fn().mockResolvedValue({ markdown: '# Guide\n\n```json\n{"count":2}\n```' }) });
const inventory = {
  api_version: 1,
  scenarios: [
    { name: 'food-delivery', origin: 'builtin', distribution: null, templates: ['food-delivery'], apply_hooks: [], status: 'ok' },
    { name: 'lowalt', origin: 'builtin', distribution: null, templates: [], apply_hooks: ['lowalt.operator_mode'], status: 'ok' },
    { name: 'external', origin: 'entry-point', distribution: 'acme-plugins', templates: ['external-template'], apply_hooks: ['external.mode'], status: 'ok' },
  ],
  engines: [{ name: 'kinematic', origin: 'builtin', distribution: null, status: 'ok' }],
  algorithms: [],
  models: [],
};
beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('plugin inventory offline')));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('derives the heading count from the template list: words up to twelve, then numerals', () => {
  expect(readyWorldsHeading(9)).toEqual({ en: 'Nine ready-made worlds', zh: '9 个现成场景' });
  expect(readyWorldsHeading(12).en).toBe('Twelve ready-made worlds');
  expect(readyWorldsHeading(13)).toEqual({ en: '13 ready-made worlds', zh: '13 个现成场景' });
});
it('hides the ghosted page header behind the open gallery, not merely dims it', () => {
  expect(galleryStyles).toMatch(/\.wb:has\(\.wb-gallery-overlay\)\s*>\s*\.wb-top\s*\{[^}]*visibility:\s*hidden/s);
});
it('switches the same language setting while the gallery and guide cover the shell', async () => {
  render(<I18nProvider><Gallery api={api() as unknown as WorkbenchApi} onOpenProject={vi.fn()} onCreate={vi.fn()} onCreateBlank={vi.fn()} /></I18nProvider>);
  await screen.findByTestId('wb-template-food-delivery');
  fireEvent.click(within(screen.getByTestId('wb-gallery')).getByRole('button', { name: 'Switch to Chinese' }));
  expect(screen.getByRole('heading', { name: '1 个现成场景' })).toBeVisible();
  fireEvent.click(screen.getByTestId('wb-template-food-delivery'));
  expect(await screen.findByRole('heading', { name: '外卖配送' })).toBeVisible();
  fireEvent.click(within(screen.getByTestId('wb-guide')).getByRole('button', { name: '切换到英文' }));
  expect(screen.getByRole('heading', { name: 'Meal delivery' })).toBeVisible();
});
it('shows a project listing failure instead of claiming that no projects exist', async () => {
  const service = api(); service.projects.mockRejectedValue(new ApiError(501, 'project list unavailable'));
  render(<Gallery api={service as unknown as WorkbenchApi} onOpenProject={vi.fn()} onCreate={vi.fn()} onCreateBlank={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('project list unavailable');
  expect(screen.queryByText('No projects yet')).toBeNull();
});
it('renders the selected translation of the real template guide when the language changes', async () => {
  const source = '<!-- guide-language: en-US -->\n# English guide\n\n`fleet_size`\n\n<!-- guide-language: zh-CN -->\n# 中文指南\n\n`fleet_size`';
  expect(localizedGuide('# Ordinary guide', 'zh-CN')).toBe('# Ordinary guide');
  const service = api(); service.guide.mockResolvedValue({ markdown: source });
  render(<I18nProvider><Gallery api={service as unknown as WorkbenchApi} onOpenProject={vi.fn()} onCreate={vi.fn()} onCreateBlank={vi.fn()} initialGuide="food-delivery" /></I18nProvider>);
  expect(await screen.findByRole('heading', { name: 'English guide' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Switch to Chinese' }));
  expect(screen.getByRole('heading', { name: '中文指南' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'English guide' })).toBeNull();
  expect(within(screen.getByTestId('wb-guide')).getByText('fleet_size')).toBeVisible();
});
it('badges templates with declared plugin provenance and opens the inventory', async () => {
  const service = api(); service.templates.mockResolvedValue([template, external]);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(inventory), { status: 200 })));
  render(<I18nProvider><Gallery api={service as unknown as WorkbenchApi} onOpenProject={vi.fn()} onCreate={vi.fn()} onCreateBlank={vi.fn()} /></I18nProvider>);
  const builtinCard = await screen.findByTestId('wb-template-food-delivery');
  expect(await within(builtinCard).findByTestId('wb-template-provider-food-delivery')).toHaveTextContent('built-in');
  const externalCard = await screen.findByTestId('wb-template-external-template');
  expect(await within(externalCard).findByTestId('wb-template-provider-external-template')).toHaveTextContent('acme-plugins');
  fireEvent.click(screen.getByTestId('wb-plugin-toggle'));
  const panel = screen.getByTestId('wb-plugin-inventory');
  expect(within(panel).getByText('lowalt')).toBeVisible();
  expect(within(panel).getByText('lowalt.operator_mode')).toBeVisible();
  expect(within(panel).getByText(/configured template root/)).toBeVisible();
  expect(panel).not.toHaveTextContent(/ready to run/i);
});
it('leaves cards unbadged and explains when the plugin inventory is unavailable', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 })));
  render(<I18nProvider><Gallery api={api() as unknown as WorkbenchApi} onOpenProject={vi.fn()} onCreate={vi.fn()} onCreateBlank={vi.fn()} /></I18nProvider>);
  const card = await screen.findByTestId('wb-template-food-delivery');
  expect(within(card).queryByTestId('wb-template-provider-food-delivery')).toBeNull();
  fireEvent.click(screen.getByTestId('wb-plugin-toggle'));
  expect(await screen.findByText('Plugin inventory is unavailable on this server.')).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
});
