import { beforeEach, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { I18nProvider } from '../i18n/I18nProvider';
import { DemoSettings } from './DemoSettings';

beforeEach(() => { window.localStorage.setItem('aeroagentsim.locale', 'en-US'); });
const workspace: any = { id: 'demo', scenario: { engines: { decisions: { plugin: 'traffic_decisions', config: { mode: 'stub' } } } } };

it('explains server-side profiles, rejects whitespace and saves before switching decisions', async () => {
  const calls: string[] = [], applied = vi.fn();
  const save = vi.fn(async () => { calls.push('save'); return workspace; });
  const request = vi.fn(async () => { calls.push('request'); return workspace; });
  render(<I18nProvider><DemoSettings api={{ request } as any} workspace={workspace} save={save} onApply={applied} profiles={{}} /></I18nProvider>);
  expect(screen.getByText('Decision mode · scripted')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Configure live LangGraph' }));
  const input = screen.getByLabelText('Live operator profile');
  const apply = screen.getByRole('button', { name: 'Use live LangGraph for next run' });
  fireEvent.change(input, { target: { value: '   ' } });
  expect(apply).toBeDisabled();
  fireEvent.change(input, { target: { value: ' operator-demo ' } });
  await act(async () => { fireEvent.click(apply); });
  expect(calls).toEqual(['save', 'request']);
  expect(request).toHaveBeenCalledWith('/v1/studio/workspaces/demo/decision-profile', { provider: { profile: 'operator-demo' } });
  expect(applied).toHaveBeenCalledWith(workspace);
});

it('does not describe an unknown decision plugin as scripted', () => {
  render(<I18nProvider><DemoSettings api={{ request: vi.fn() } as any} workspace={{ ...workspace, scenario: { engines: { decisions: { plugin: 'custom' } } } }} save={vi.fn()} onApply={vi.fn()} profiles={{}} /></I18nProvider>);
  expect(screen.getByText('Decision mode · custom or unconfigured')).toBeInTheDocument();
  expect(screen.queryByText('Scripted decision fixture; no model service calls.')).toBeNull();
});

it('prevents overlapping mode changes while the draft is saving', async () => {
  let resolve!: (workspace: any) => void;
  const save = vi.fn(() => new Promise<any>(done => { resolve = done; }));
  const request = vi.fn(async () => workspace);
  render(<I18nProvider><DemoSettings api={{ request } as any} workspace={workspace} save={save} onApply={vi.fn()} profiles={{}} /></I18nProvider>);
  const button = screen.getByRole('button', { name: 'Use scripted decisions' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(button).toBeDisabled();
  expect(save).toHaveBeenCalledTimes(1);
  await act(async () => { resolve(workspace); });
  expect(request).toHaveBeenCalledTimes(1);
});
