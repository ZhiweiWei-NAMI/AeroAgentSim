import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import { FeedStore } from './feed-store';
import { EntityInspector } from './EntityInspector';
import { MissionTimeline } from './MissionTimeline';
import { messagesForEntity } from './message-subjects';

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation(query => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) });
});

beforeEach(() => { window.localStorage.clear(); });
afterEach(() => { cleanup(); window.localStorage.clear(); });
function LocaleButton() { const { locale, setLocale } = useI18n(); return <button onClick={() => setLocale(locale === 'en-US' ? 'zh-CN' : 'en-US')}>locale</button>; }

const key = { id: 'job-7', generation: 3 };
const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'subjects', registryDigest: 'fixture', start: { ns: '0', microstep: 0 }, types: [{ typeId: 'Job', displayName: 'Job', ancestors: [] }], fields: [], presentation: [] };
const message = (id: string, kind: 'event' | 'command', schemaId: string, subjects?: typeof key[]): FeedCommit['messages'][number] => ({ id, kind, schemaId, subjects, source: 'engine', at: { ns: '10', microstep: 1 }, payload: { entity: key.id } });
const messages = [message('finish', 'event', 'job.finished', [key]), message('cmd', 'command', 'transport.command', [key]), message('arrival', 'event', 'transport.arrived', [key]), message('unrelated', 'event', 'unrelated.string'), message('other-generation', 'event', 'older.finished', [{ ...key, generation: 2 }])];

describe('recorded message subjects', () => {
  it('uses only declared subjects and preserves generations', () => {
    expect(messagesForEntity(messages, key).map(m => m.id)).toEqual(['finish', 'cmd', 'arrival']);
    expect(messagesForEntity(messages)).toEqual([]);
  });

  it('shows nonspatial completion, command and arrival in inspector and timeline at the recorded cut', () => {
    const store = new FeedStore(header);
    store.ingest({ commitIndex: 1, at: { ns: '0', microstep: 0 }, created: [{ ...key, typeId: 'Job' }], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [] });
    store.ingest({ commitIndex: 2, at: { ns: '10', microstep: 1 }, created: [], removed: [], facts: [], retracted: [], edges: [], messages, receipts: [{ commandId: 'cmd', status: 'succeeded' }] });
    store.seek('10');
    render(<I18nProvider><EntityInspector store={store} selected={key} /><MissionTimeline store={store} selected={key} /></I18nProvider>);
    for (const schema of ['job.finished', 'transport.command', 'transport.arrived']) expect(screen.getAllByText(schema)).toHaveLength(2);
    expect(screen.queryByText('unrelated.string')).toBeNull();
    expect(screen.queryByText('older.finished')).toBeNull();
    expect(screen.getAllByText('Succeeded')).toHaveLength(2);
    cleanup();
    store.seek('0');
    render(<I18nProvider><EntityInspector store={store} selected={key} /><MissionTimeline store={store} selected={key} /></I18nProvider>);
    expect(screen.queryByText('job.finished')).toBeNull();
    expect(screen.getAllByText('No recorded items').length).toBeGreaterThan(0);
  });

  it('localizes inspector fact stamps and timeline prose without translating schema, IDs or raw JSON', () => {
    const store = new FeedStore(header);
    store.ingest({ commitIndex: 1, at: { ns: '0', microstep: 0 }, created: [{ ...key, typeId: 'Job' }], removed: [], retracted: [], edges: [],
      facts: [{ entity: key, fieldId: 'raw.state', producer: 'engine-id', value: { label: 'Running' }, validFrom: { ns: '0', microstep: 0 },
        available: { ns: '0', microstep: 0 }, acquired: { clockId: 'clock-id', mappingId: 'mapping-id', numerator: '7', denominator: '3' }, version: { journalIndex: 1, itemOrdinal: 2 } }],
      messages: [message('cmd', 'command', 'transport.command', [key])], receipts: [{ commandId: 'cmd', status: 'succeeded', result: { state: 'succeeded' } }] });
    store.seek('10');
    render(<I18nProvider><LocaleButton /><EntityInspector store={store} selected={key} /><MissionTimeline store={store} selected={key} /></I18nProvider>);
    expect(screen.getByText('Valid 0 s → Open-ended')).toBeInTheDocument();
    expect(screen.getByText('Version 1:2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'locale' }));
    expect(screen.getByText(/已提交的精确值/)).toBeInTheDocument();
    expect(screen.getByText(/有效期 0 s →/)).toBeInTheDocument();
    expect(screen.getByText('版本 1:2')).toBeInTheDocument();
    expect(screen.getByText('可用时间 0 s')).toBeInTheDocument();
    expect(screen.getByText('采集时间 7/3 · clock-id/mapping-id')).toBeInTheDocument();
    expect(screen.getAllByText('成功')).toHaveLength(2);
    expect(screen.getByText(/1 条指令 \/ 事件记录/)).toBeInTheDocument();
    expect(screen.getAllByText('transport.command')).toHaveLength(2);
    expect(screen.getByText('{"label":"Running"}', { selector: 'code' })).toBeInTheDocument();
    expect(screen.getByText('{"state":"succeeded"}', { selector: 'code' })).toBeInTheDocument();
    expect(screen.getByText('raw.state')).toBeInTheDocument();
    expect(screen.getByText('job-7', { selector: 'h4' })).toBeInTheDocument();
  });
});
