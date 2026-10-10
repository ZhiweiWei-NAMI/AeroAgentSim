import { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import { StudioApi } from '../api';
import { AgentsStep } from './AgentsStep';
vi.mock('../../i18n/messages', async () => ({ default: { 'zh-CN': (await import('../../i18n/studio-messages')).default } }));
beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
});
const create = (scenario: Record<string, any>) => ({ id: 'workspace', name: 'Agents', scenario });
it('explains that agents are optional without displaying an unusable provider switch on other templates', () => {
  const workspace = create({ id: 'parcel-demo', engines: {} });
  render(<I18nProvider><AgentsStep workspace={workspace} api={new StudioApi('/')} save={async () => workspace} onApply={() => {}} onChange={() => {}} /></I18nProvider>);
  expect(screen.getByText('No decision plugin configured')).toBeInTheDocument();
  expect(screen.getByText('This scenario can run with its domain controllers. A decision agent is optional; advanced setup is available in the scenario source.')).toBeInTheDocument();
  expect(screen.queryByLabelText('Live provider profile')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Use live LangGraph for next run' })).toBeNull();
});
it('saves before applying a trimmed server-side profile and explains API-key safety', async () => {
  const workspace = create({ id: 'traffic-accident', engines: { decisions: { plugin: 'traffic_decisions', config: { mode: 'stub' } } } });
  const api = new StudioApi('/'), order: string[] = [];
  const request = vi.spyOn(api, 'request').mockImplementation(async () => { order.push('request'); return workspace as any; });
  const applied = vi.fn();
  render(<I18nProvider><AgentsStep workspace={workspace} api={api} save={async () => { order.push('save'); return workspace; }} onApply={applied} onChange={() => {}} /></I18nProvider>);
  expect(screen.getByText('Use a server-side profile name; do not paste an API key here.')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Live provider profile'), { target: { value: ' research ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Use live LangGraph for next run' }));
  await waitFor(() => expect(applied).toHaveBeenCalledWith(workspace));
  expect(order).toEqual(['save', 'request']);
  expect(request).toHaveBeenCalledWith('/v1/studio/workspaces/workspace/decision-profile', { provider: { profile: 'research' } });
});
it('adds, edits and removes an explicit agent without losing unrelated engine or scenario settings', async () => {
  function Manager() {
    const [scenario, setScenario] = useState<Record<string, any>>({ id: 'parcel-demo', engines: { motion: { plugin: 'kinematic', config: { keep: true } } }, custom: { keep: true } });
    const workspace = create(scenario);
    return <I18nProvider><AgentsStep workspace={workspace} api={new StudioApi('/')} save={async () => workspace} onApply={() => {}} onChange={setScenario} /><output data-testid="scenario">{JSON.stringify(scenario)}</output></I18nProvider>;
  }
  render(<Manager />);
  fireEvent.click(screen.getByRole('button', { name: 'Add agent' }));
  let dialog = screen.getByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('Agent engine ID'), { target: { value: 'motion' } });
  expect(within(dialog).getByRole('button', { name: 'Add agent' })).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText('Agent engine ID'), { target: { value: 'my-agent' } });
  fireEvent.change(within(dialog).getByLabelText('Agent configuration JSON'), { target: { value: '{bad' } });
  expect(within(dialog).getByRole('button', { name: 'Add agent' })).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText('Agent configuration JSON'), { target: { value: '{"options":{"custom":1}}' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add agent' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Edit agent' }));
  dialog = screen.getByRole('dialog');
  expect(within(dialog).getByLabelText('Agent engine ID')).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText('Agent configuration JSON'), { target: { value: '{"options":{"custom":2}}' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Apply agent changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(JSON.parse(screen.getByTestId('scenario').textContent!).engines['my-agent'].config.options.custom).toBe(2);
  fireEvent.click(screen.getByRole('button', { name: 'Remove agent' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
  await waitFor(() => expect(JSON.parse(screen.getByTestId('scenario').textContent!).engines['my-agent']).toBeUndefined());
  expect(JSON.parse(screen.getByTestId('scenario').textContent!)).toEqual({ id: 'parcel-demo', engines: { motion: { plugin: 'kinematic', config: { keep: true } } }, custom: { keep: true } });
});

it('uses Chinese labels for the scripted profile and live-provider controls', () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  const workspace = create({ id: 'traffic-accident', engines: { decisions: { plugin: 'traffic_decisions', config: { mode: 'stub' } } } });
  render(<I18nProvider><AgentsStep workspace={workspace} api={new StudioApi('/')} save={async () => workspace} onApply={() => {}} onChange={() => {}} /></I18nProvider>);
  expect(screen.getAllByText('脚本决策')).toHaveLength(2);
  expect(screen.getByRole('button', { name: '使用脚本决策' })).toBeInTheDocument();
  expect(screen.getByLabelText('实时模型服务配置')).toBeInTheDocument();
});
