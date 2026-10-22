import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { PluginInventory } from './PluginInventory';
import type { PluginInventory as Inventory } from './api';

const inventory: Inventory = {
  api_version: 1,
  scenarios: [
    { name: 'food-delivery', origin: 'builtin', distribution: null, status: 'ok', conflict: [], templates: ['food-delivery'], apply_hooks: [] },
    { name: 'lowalt', origin: 'builtin', distribution: null, status: 'ok', conflict: [], templates: [], apply_hooks: ['lowalt.operator_mode'] },
    { name: 'external', origin: 'entry-point', distribution: 'acme-plugins', status: 'ok', conflict: [], templates: ['external-template'], apply_hooks: ['external.mode'] },
    { name: 'shared', origin: 'entry-point', distribution: 'acme-plugins', status: 'duplicate', conflict: ['acme-plugins', 'other-plugins'], templates: [], apply_hooks: [] },
  ],
  engines: [{ name: 'kinematic', origin: 'builtin', distribution: null, status: 'ok', conflict: [] }],
  algorithms: [
    { name: 'allocation.nearest', origin: 'builtin', distribution: null, status: 'ok', conflict: [] },
    { name: 'allocation.auction', origin: 'entry-point', distribution: 'acme-plugins', status: 'ok', conflict: [] },
  ],
  models: [],
};
beforeEach(() => localStorage.clear());
afterEach(cleanup);

it('lists scenario plugins with origin, distribution, templates, hooks and duplicate warnings', () => {
  render(<I18nProvider><PluginInventory inventory={inventory} /></I18nProvider>);
  const builtin = screen.getByTestId('wb-plugin-scenario-food-delivery');
  expect(within(builtin).getByText('built-in')).toBeVisible();
  expect(within(builtin).getByText('food-delivery', { selector: 'strong' })).toBeVisible();
  expect(within(builtin).getByText('food-delivery', { selector: '.wb-tag' })).toBeVisible();
  expect(within(builtin).getByText('discovered')).toBeVisible();

  const external = screen.getByTestId('wb-plugin-scenario-external');
  expect(within(external).getByText('entry point')).toBeVisible();
  expect(within(external).getByText('acme-plugins')).toBeVisible();
  expect(within(external).getByText('external-template')).toBeVisible();
  expect(within(external).getByText('external.mode')).toBeVisible();

  const hookOnly = screen.getByTestId('wb-plugin-scenario-lowalt');
  expect(within(hookOnly).getByText('none (hook-only)')).toBeVisible();
  expect(within(hookOnly).getByText('lowalt.operator_mode')).toBeVisible();

  const duplicate = screen.getByTestId('wb-plugin-scenario-shared');
  expect(within(duplicate).getByText('duplicate name')).toBeVisible();
  expect(within(duplicate).getByText(/Declared by acme-plugins, other-plugins/)).toBeVisible();
  expect(within(duplicate).getAllByText('unknown (duplicate names)')).toHaveLength(2);
  expect(screen.getByRole('alert')).toHaveTextContent('Duplicate scenario plugin names: shared.');
});

it('detects duplicate names from an older inventory that has no status field', () => {
  const older: Inventory = {
    ...inventory,
    scenarios: [
      { name: 'shared', origin: 'entry-point', distribution: 'acme-plugins', status: 'ok', conflict: [], templates: ['shared'], apply_hooks: [] },
      { name: 'shared', origin: 'entry-point', distribution: 'other-plugins', status: 'ok', conflict: [], templates: ['other'], apply_hooks: [] },
    ],
  };
  render(<I18nProvider><PluginInventory inventory={older} /></I18nProvider>);
  const rows = screen.getAllByTestId('wb-plugin-scenario-shared');
  expect(rows).toHaveLength(2);
  for (const row of rows) {
    expect(within(row).getByText('duplicate name')).toBeVisible();
    expect(within(row).getByText(/acme-plugins, other-plugins/)).toBeVisible();
  }
  expect(screen.getByRole('alert')).toHaveTextContent('Duplicate scenario plugin names: shared.');
});

it('marks empty contributions as unknown when duplicate names force metadata-only discovery', () => {
  const degraded: Inventory = {
    ...inventory,
    scenarios: [
      { name: 'food-delivery', origin: 'builtin', distribution: null, status: 'ok', conflict: [], templates: [], apply_hooks: [] },
      { name: 'shared', origin: 'entry-point', distribution: 'acme-plugins', status: 'duplicate', conflict: ['acme-plugins', 'other-plugins'], templates: [], apply_hooks: [] },
    ],
  };
  render(<I18nProvider><PluginInventory inventory={degraded} /></I18nProvider>);
  const builtin = screen.getByTestId('wb-plugin-scenario-food-delivery');
  expect(within(builtin).getAllByText('unknown (duplicate names)')).toHaveLength(2);
  expect(within(builtin).queryByText('none (hook-only)')).toBeNull();
});

it('expands engine, algorithm and model counts into their catalog rows', () => {
  render(<I18nProvider><PluginInventory inventory={inventory} /></I18nProvider>);
  expect(screen.queryByText('kinematic')).toBeNull();
  fireEvent.click(screen.getByTestId('wb-plugin-catalog-engines'));
  expect(screen.getByText('kinematic')).toBeVisible();
  fireEvent.click(screen.getByTestId('wb-plugin-catalog-engines'));
  expect(screen.queryByText('kinematic')).toBeNull();

  fireEvent.click(screen.getByTestId('wb-plugin-catalog-algorithms'));
  expect(screen.getByText('allocation.auction')).toBeVisible();
  expect(screen.getByTestId('wb-plugin-catalog-algorithms')).toHaveTextContent('2');
  fireEvent.click(screen.getByTestId('wb-plugin-catalog-models'));
  expect(screen.getByText('None discovered.')).toBeVisible();
});

it('switches the inventory copy to Chinese without touching the shared catalogs', () => {
  localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  render(<I18nProvider><PluginInventory inventory={inventory} /></I18nProvider>);
  expect(screen.getByText('已安装插件')).toBeVisible();
  expect(screen.getAllByText('内置').length).toBeGreaterThan(0);
  expect(screen.getAllByText('模板').length).toBeGreaterThan(0);
  expect(screen.getAllByText('入口点').length).toBeGreaterThan(0);
  expect(screen.getByText('名称重复')).toBeVisible();
});
