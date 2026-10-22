import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { AssistantPanel, transcript } from './AssistantPanel';
import type { AssistantEvent, Connection, Session, TurnStatus } from './api';
import type { AssistantSession } from './useAssistant';

type EventPayload<T = AssistantEvent> = T extends AssistantEvent ? Omit<T, 'seq' | 'at'> : never;
const log = (...events: EventPayload[]): AssistantEvent[] => events.map((event, index) => ({ ...event, seq: index + 1, at: '' }));
type TranscriptItem = ReturnType<typeof transcript>[number];
const isAnswer = (item: TranscriptItem): item is Extract<TranscriptItem, { type: 'assistant' }> => item.type === 'assistant';
const connection: Connection = { configured: true, source: 'operator', base_url: 'http://api', model: 'test', has_key: true, status: 'ok', detail: null, tested_at: null };

function renderPanel(locale: string, events: AssistantEvent[], options: { turns?: Session['turns']; error?: string } = {}) {
  localStorage.setItem('aeroagentsim.locale', locale);
  const session: Session = {
    id: 'provenance', created_at: '', updated_at: '', workspace_id: null, title: 'Provenance', busy: false,
    last_seq: events.at(-1)?.seq ?? 0, events, plan: null, plan_versions: [], alternatives: [], applied: [], turns: options.turns,
  };
  const assistant: AssistantSession = {
    session, events, busy: false, pending: undefined, error: options.error, plan: undefined, applied: undefined,
    send: vi.fn(), choose: vi.fn(), cancel: vi.fn(), apply: vi.fn(), refresh: vi.fn(),
  };
  render(<I18nProvider><AssistantPanel assistant={assistant} connection={connection} connectionSaving={false}
    onConnect={async () => undefined} onTest={async () => undefined} onClose={vi.fn()} onShowPlan={vi.fn()} onShowChanges={vi.fn()} /></I18nProvider>);
  return screen.getByTestId('as-transcript');
}

function expectNoModelPresentation(node: Element) {
  expect(node).not.toHaveClass('as-bot');
  expect(node.querySelector('.as-avatar')).toBeNull();
  expect(node.querySelector('.as-bot')).toBeNull();
  expect(node.querySelector('.as-meta')).toBeNull();
}

afterEach(() => { cleanup(); localStorage.clear(); sessionStorage.clear(); });

describe.each(['en-US', 'zh-CN'])('assistant provenance in %s', locale => {
  const zh = locale === 'zh-CN';
  const platformLabel = zh ? '平台说明' : 'Platform note';
  const unknownLabel = zh ? '来源未知' : 'Unknown source';

  it('renders authoritative mixed parts with timing only on the last model part', () => {
    const events = log(
      { kind: 'turn_started', turn: 1 },
      { kind: 'assistant', text: 'COMPOSITE MUST NOT RENDER', source: 'model', status: 'completed', turn: 1, suggestions: ['Continue'], parts: [
        { text: 'Earlier model text', source: 'model' },
        { text: zh ? '请审阅变更' : 'Review the changes', source: 'platform' },
        { text: '**Provider final / 模型最终答复**', source: 'model' },
        { text: 'Unattributed content' },
      ] },
      { kind: 'turn_finished', turn: 1, status: 'completed', elapsed_s: 2.5, model_calls: 2 },
    );
    const rows = transcript(events, undefined, false, zh).filter(isAnswer);
    expect(rows.map(item => item.source)).toEqual(['model', 'platform', 'model', 'unknown']);
    expect(new Set(rows.map(item => item.key)).size).toBe(4);
    expect(rows.map(item => item.meta)).toEqual([undefined, undefined, zh ? '2.5 s · 2 次模型调用' : '2.5 s · 2 model calls', undefined]);

    const node = renderPanel(locale, events);
    expect(node).not.toHaveTextContent('COMPOSITE MUST NOT RENDER');
    const modelRows = node.querySelectorAll('[data-source="model"]');
    expect(modelRows).toHaveLength(2);
    expect(modelRows[0].querySelector('.as-avatar')).not.toBeNull();
    expect(modelRows[0].querySelector('.as-meta')).toBeNull();
    expect(modelRows[1].querySelector('.as-avatar')).not.toBeNull();
    // Provider text is not passed through the platform bilingual-string selector.
    expect(modelRows[1].querySelector('strong')).toHaveTextContent('Provider final / 模型最终答复');
    expect(modelRows[1].querySelector('.as-meta')).toHaveTextContent(rows[2].meta!);
    const platform = node.querySelector('[data-source="platform"]')!;
    expect(platform).toHaveTextContent(platformLabel);
    expect(platform).toHaveTextContent(zh ? '请审阅变更' : 'Review the changes');
    expect(platform).not.toHaveTextContent(zh ? 'Review the changes' : '请审阅变更');
    expectNoModelPresentation(platform);
    const unknown = node.querySelector('[data-source="unknown"]')!;
    expect(unknown).toHaveTextContent(unknownLabel);
    expect(unknown).toHaveTextContent('Unattributed content');
    expectNoModelPresentation(unknown);
    expect(within(screen.getByTestId('as-suggestions')).getByRole('button', { name: 'Continue' })).toBeVisible();
  });

  it('shows a platform-only summary without using session model_reply as an answer', () => {
    const parts = [{ text: zh ? '**平台摘要**' : '**Platform summary**', source: 'platform' as const }];
    const events = log(
      { kind: 'turn_started', turn: 1 },
      { kind: 'assistant', text: 'COMPATIBILITY SUMMARY', source: 'platform', status: 'completed', parts },
      { kind: 'turn_finished', turn: 1, status: 'completed', elapsed_s: 9, model_calls: 3 },
    );
    const node = renderPanel(locale, events, { turns: [
      { turn: 1, status: 'completed', model_reply: 'Exact provider final text, not a transport envelope', parts, elapsed_s: 9, model_calls: 3, tokens: 120 },
    ] });
    expect(node).toHaveTextContent(platformLabel);
    expect(node).toHaveTextContent(zh ? '平台摘要' : 'Platform summary');
    expect(node).not.toHaveTextContent('COMPATIBILITY SUMMARY');
    expect(node).not.toHaveTextContent('Exact provider final text');
    expectNoModelPresentation(node);
    expect(transcript(events, undefined, false, zh).filter(isAnswer)[0].meta).toBeUndefined();
  });

  it('preserves multiline platform Markdown containing slashes and Chinese evidence verbatim', () => {
    const text = '## Route evidence\n\nOrigin / destination: 上海站\n\n- Preserve segment A / B\n- Station name: 北京南站\n\nFinal evidence remains.';
    const events = log({ kind: 'assistant', text: 'COMPATIBILITY SUMMARY', parts: [{ text, source: 'platform' }] });
    expect(transcript(events, undefined, false, zh).filter(isAnswer)[0].text).toBe(text);
    const node = renderPanel(locale, events);
    const platform = node.querySelector('[data-source="platform"]')!;
    expect(platform).toHaveTextContent(platformLabel);
    expect(within(node).getByRole('heading', { name: 'Route evidence' })).toBeVisible();
    expect(platform).toHaveTextContent('Origin / destination: 上海站');
    expect(platform).toHaveTextContent('Preserve segment A / B');
    expect(platform).toHaveTextContent('Station name: 北京南站');
    expect(platform).toHaveTextContent('Final evidence remains.');
    expectNoModelPresentation(platform);
  });

  it('normalizes malformed wire sources to unknown instead of showing a Model label', () => {
    // Runtime responses may violate the TypeScript contract; provenance must fail closed.
    const events = [
      { seq: 1, at: '', kind: 'turn_started', turn: 1 },
      { seq: 2, at: '', kind: 'assistant', text: 'HIDDEN COMPOSITE', source: 'model', status: 'completed', parts: [{ text: 'Invalid assistant part', source: 'provider' }] },
      { seq: 3, at: '', kind: 'notice', text: 'Invalid notice source', source: 'provider' },
      { seq: 4, at: '', kind: 'error', message: 'Invalid error source', source: 'vendor' },
      { seq: 5, at: '', kind: 'turn_finished', turn: 1, status: 'completed', elapsed_s: 2, model_calls: 1 },
    ] as unknown as AssistantEvent[];
    const node = renderPanel(locale, events);
    expect(node.querySelectorAll('[data-source="unknown"]')).toHaveLength(3);
    expect(within(node).getAllByText(new RegExp(unknownLabel))).toHaveLength(3);
    expect(node.querySelector('[data-source="model"]')).toBeNull();
    expect(within(node).queryByText(zh ? '模型:' : 'Model:')).toBeNull();
    expectNoModelPresentation(node);
    expect(transcript(events, undefined, false, zh).filter(isAnswer)[0].meta).toBeUndefined();
  });

  it('labels both the budget notice and platform closing without a successful model answer', () => {
    const events = log(
      { kind: 'turn_started', turn: 1 },
      { kind: 'notice', text: 'COMPATIBILITY NOTICE', source: 'platform', status: 'budget_exhausted', turn: 1, parts: [
        { text: 'Tool budget reached / 工具预算已用尽', source: 'platform' },
      ] },
      { kind: 'assistant', text: 'COMPATIBILITY CLOSING', source: 'platform', status: 'budget_exhausted', parts: [
        { text: zh ? '平台收尾摘要' : 'Platform closing summary', source: 'platform' },
      ] },
      { kind: 'turn_finished', turn: 1, status: 'budget_exhausted', elapsed_s: 7, model_calls: 4 },
    );
    const node = renderPanel(locale, events, { turns: [{ turn: 1, status: 'budget_exhausted', model_reply: null, parts: [] }] });
    expect(within(node).getAllByText(new RegExp(platformLabel))).toHaveLength(2);
    expect(node).toHaveTextContent(zh ? '工具预算已用尽' : 'Tool budget reached');
    expect(node).toHaveTextContent(zh ? '平台收尾摘要' : 'Platform closing summary');
    expect(node).not.toHaveTextContent('COMPATIBILITY');
    expectNoModelPresentation(node);
  });

  it('keeps provider failures as platform alerts, not successful answers', () => {
    const events = log(
      { kind: 'turn_started', turn: 1 },
      { kind: 'error', message: 'COMPATIBILITY ERROR', code: 'provider_error', source: 'platform', status: 'provider_error', turn: 1, parts: [
        { text: 'Provider unavailable / 模型服务不可用', source: 'platform' },
      ] },
      { kind: 'turn_finished', turn: 1, status: 'provider_error', elapsed_s: 8, model_calls: 1 },
    );
    const node = renderPanel(locale, events);
    const alert = within(node).getByRole('alert');
    expect(alert).toHaveAttribute('data-source', 'platform');
    expect(alert).toHaveTextContent(platformLabel);
    expect(alert).toHaveTextContent(zh ? '模型服务不可用' : 'Provider unavailable');
    expect(node).not.toHaveTextContent('COMPATIBILITY ERROR');
    expectNoModelPresentation(node);
    expect(transcript(events, undefined, false, zh).filter(isAnswer)).toEqual([]);
  });

  it('labels legacy assistant text, notices, and errors unknown instead of inferring model authorship', () => {
    const events = log(
      { kind: 'turn_started', turn: 1 },
      { kind: 'assistant', text: '**Legacy assistant content**' },
      { kind: 'notice', text: 'Legacy notice / 旧版通知' },
      { kind: 'error', message: 'Legacy failure / 旧版错误' },
      { kind: 'turn_finished', turn: 1, status: 'completed', elapsed_s: 4, model_calls: 1 },
    );
    const node = renderPanel(locale, events);
    expect(node.querySelectorAll('[data-source="unknown"]')).toHaveLength(3);
    expect(within(node).getAllByText(new RegExp(unknownLabel))).toHaveLength(3);
    expect(node).toHaveTextContent('Legacy assistant content');
    expect(node).toHaveTextContent(zh ? '旧版通知' : 'Legacy notice');
    expect(within(node).getByRole('alert')).toHaveTextContent(zh ? '旧版错误' : 'Legacy failure');
    expectNoModelPresentation(node);
  });

  it('does not inherit notice/error event sources into missing part sources', () => {
    const events = log(
      { kind: 'notice', text: 'HIDDEN NOTICE', source: 'model', parts: [
        { text: 'Known note / 已知通知', source: 'platform' }, { text: 'Unknown note / 未知通知' },
      ] },
      { kind: 'error', message: 'HIDDEN ERROR', source: 'platform', parts: [
        { text: 'Unknown failure / 未知错误' },
      ] },
    );
    const rows = transcript(events, undefined, false, zh);
    expect(rows).toEqual([
      expect.objectContaining({ type: 'notice', text: 'Known note / 已知通知', source: 'platform' }),
      expect.objectContaining({ type: 'notice', text: 'Unknown note / 未知通知', source: 'unknown' }),
      expect.objectContaining({ type: 'error', text: 'Unknown failure / 未知错误', source: 'unknown' }),
    ]);
    const node = renderPanel(locale, events);
    expect(node.querySelector('[data-source="platform"]')).toHaveTextContent(platformLabel);
    expect(within(node).getAllByText(new RegExp(unknownLabel))).toHaveLength(2);
    expect(node).not.toHaveTextContent('HIDDEN');
    expectNoModelPresentation(node);
  });

  it('labels client-side request failures as platform alerts', () => {
    const node = renderPanel(locale, [], { error: 'Request failed / 请求失败' });
    expect(within(node).getByRole('alert')).toHaveTextContent(platformLabel);
    expect(within(node).getByRole('alert')).toHaveTextContent(zh ? '请求失败' : 'Request failed');
    expectNoModelPresentation(node);
  });

  it('isolates timing across model, platform-only, unknown, failed, and markerless turns', () => {
    const events = log(
      { kind: 'user', text: 'First' }, { kind: 'turn_started', turn: 1 },
      { kind: 'assistant', text: 'First model', source: 'model', status: 'completed' },
      { kind: 'turn_finished', turn: 1, status: 'completed', elapsed_s: 1, model_calls: 1 },
      { kind: 'user', text: 'Second' }, { kind: 'turn_started', turn: 2 },
      { kind: 'assistant', text: 'Only platform', parts: [{ text: 'Only platform', source: 'platform' }] },
      { kind: 'turn_finished', turn: 2, status: 'completed', elapsed_s: 22, model_calls: 2 },
      { kind: 'user', text: 'Third' }, { kind: 'turn_started', turn: 3 },
      { kind: 'assistant', text: 'Unknown answer' },
      { kind: 'turn_finished', turn: 3, status: 'completed', elapsed_s: 33, model_calls: 3 },
      { kind: 'user', text: 'Fourth' }, { kind: 'turn_started', turn: 4 },
      { kind: 'assistant', text: 'Partial model', source: 'model', status: 'running' },
      { kind: 'turn_finished', turn: 4, status: 'interrupted', elapsed_s: 44, model_calls: 4 },
      { kind: 'user', text: 'Fifth' }, { kind: 'turn_started', turn: 5 },
      { kind: 'assistant', text: 'Last model', source: 'model', status: 'completed' },
      { kind: 'notice', text: 'Trailing platform note', source: 'platform' },
      // A stale finish cannot annotate or close the currently active turn.
      { kind: 'turn_finished', turn: 1, status: 'completed', elapsed_s: 99, model_calls: 9 },
      { kind: 'turn_finished', turn: 5, status: 'completed', elapsed_s: 5, model_calls: 2 },
      { kind: 'turn_finished', turn: 5, status: 'completed', elapsed_s: 99, model_calls: 9 },
      { kind: 'user', text: 'Sixth, missing start marker' },
      { kind: 'assistant', text: 'Markerless model', source: 'model' },
      { kind: 'turn_finished', turn: 6, status: 'completed', elapsed_s: 66, model_calls: 6 },
    );
    const answers = transcript(events, undefined, false, zh).filter(isAnswer);
    expect(answers.map(item => item.meta)).toEqual([
      zh ? '1.0 s · 1 次模型调用' : '1.0 s · 1 model call', undefined, undefined, undefined,
      zh ? '5.0 s · 2 次模型调用' : '5.0 s · 2 model calls', undefined,
    ]);
    const node = renderPanel(locale, events);
    const metadata = node.querySelectorAll('.as-meta');
    expect(metadata).toHaveLength(2);
    expect(metadata[0].closest('[data-source]')).toHaveTextContent('First model');
    expect(metadata[1].closest('[data-source]')).toHaveTextContent('Last model');
    expect(node.querySelector('[data-source="platform"]')?.querySelector('.as-meta')).toBeNull();
    expect(node.querySelector('[data-source="unknown"]')?.querySelector('.as-meta')).toBeNull();
  });
});

it('treats empty parts as authoritative for assistant, notice, and error events', () => {
  const events = log(
    { kind: 'assistant', text: 'Do not render the composite', source: 'model', parts: [] },
    { kind: 'notice', text: 'Do not render the notice', source: 'platform', parts: [] },
    { kind: 'error', message: 'Do not render the error', source: 'platform', parts: [] },
  );
  expect(transcript(events, undefined, false, false)).toEqual([]);
});

it('uses an event source only for text/message fallback when parts are absent', () => {
  const events = log(
    { kind: 'assistant', text: 'Model fallback', source: 'model' },
    { kind: 'assistant', text: 'Platform fallback', source: 'platform' },
    { kind: 'assistant', text: 'Explicit unknown', source: 'unknown' },
    { kind: 'notice', text: 'Platform notice', source: 'platform' },
    { kind: 'error', message: 'Platform error', source: 'platform' },
    { kind: 'assistant', text: 'No source after platform messages' },
  );
  expect(transcript(events, undefined, false, false)).toEqual([
    expect.objectContaining({ text: 'Model fallback', source: 'model' }),
    expect.objectContaining({ text: 'Platform fallback', source: 'platform' }),
    expect.objectContaining({ text: 'Explicit unknown', source: 'unknown' }),
    expect.objectContaining({ text: 'Platform notice', source: 'platform' }),
    expect.objectContaining({ text: 'Platform error', source: 'platform' }),
    expect.objectContaining({ text: 'No source after platform messages', source: 'unknown' }),
  ]);
});

const nonSuccessful: Array<TurnStatus | undefined> = ['running', 'budget_exhausted', 'provider_error', 'error', 'cancelled', 'interrupted', 'unknown', undefined];
it.each(nonSuccessful)('does not present model timing as a normal success for status %s', status => {
  const events = log(
    { kind: 'turn_started', turn: 1 },
    { kind: 'assistant', text: 'Partial model output', parts: [{ text: 'Partial model output', source: 'model' }] },
    { kind: 'turn_finished', turn: 1, status, elapsed_s: 10, model_calls: 2 },
  );
  expect(transcript(events, undefined, false, false).filter(isAnswer)[0].meta).toBeUndefined();
});

it('honors explicit text-event turn IDs instead of annotating a stale model part', () => {
  const events = log(
    { kind: 'turn_started', turn: 2 },
    { kind: 'assistant', text: 'Current model answer', source: 'model', status: 'completed', turn: 2 },
    { kind: 'assistant', text: 'Stale model part', source: 'model', status: 'running', turn: 1 },
    { kind: 'notice', text: 'Stale budget notice', source: 'platform', status: 'budget_exhausted', turn: 1 },
    { kind: 'error', message: 'Stale failure', source: 'platform', status: 'provider_error', turn: 1 },
    { kind: 'turn_finished', turn: 2, elapsed_s: 2 },
  );
  expect(transcript(events, undefined, false, false).filter(isAnswer).map(item => item.meta)).toEqual(['2.0 s', undefined]);
});

it('does not carry timing across a user boundary when the next start marker is missing', () => {
  const events = log(
    { kind: 'turn_started', turn: 1 },
    { kind: 'assistant', text: 'Unfinished earlier answer', source: 'model', status: 'completed' },
    { kind: 'user', text: 'Next request without a turn_started event' },
    { kind: 'assistant', text: 'Next model text', source: 'model' },
    { kind: 'turn_finished', turn: 1, status: 'completed', elapsed_s: 10 },
    { kind: 'turn_finished', turn: 2, status: 'completed', elapsed_s: 20 },
  );
  expect(transcript(events, undefined, false, false).filter(isAnswer).map(item => item.meta)).toEqual([undefined, undefined]);
});

it('honors an explicit text-event outcome when a legacy finish has no status', () => {
  const events = log(
    { kind: 'turn_started', turn: 1 },
    { kind: 'assistant', text: 'Completed model', source: 'model', status: 'completed' },
    { kind: 'turn_finished', turn: 1, elapsed_s: 1 },
    { kind: 'turn_started', turn: 2 },
    { kind: 'assistant', text: 'Partial model', source: 'model' },
    { kind: 'notice', text: 'Budget exhausted', source: 'platform', status: 'budget_exhausted' },
    { kind: 'assistant', text: 'Platform closing', source: 'platform', status: 'budget_exhausted' },
    { kind: 'turn_finished', turn: 2, elapsed_s: 20 },
    { kind: 'turn_started', turn: 3 },
    { kind: 'assistant', text: 'Earlier model text', source: 'model', status: 'completed' },
    { kind: 'error', message: 'Provider failed', source: 'platform' },
    { kind: 'turn_finished', turn: 3, elapsed_s: 30 },
  );
  expect(transcript(events, undefined, false, false).filter(isAnswer).map(item => item.meta)).toEqual(['1.0 s', undefined, undefined, undefined]);
});
