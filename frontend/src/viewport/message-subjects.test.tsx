import { beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { I18nProvider } from '../i18n/I18nProvider';
import { FeedStore } from './feed-store';
import { EntityInspector } from './EntityInspector';
import { MissionTimeline } from './MissionTimeline';
import { messagesForEntity } from './message-subjects';

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation(query => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) });
});

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
    expect(screen.getAllByText('succeeded')).toHaveLength(2);
    cleanup();
    store.seek('0');
    render(<I18nProvider><EntityInspector store={store} selected={key} /><MissionTimeline store={store} selected={key} /></I18nProvider>);
    expect(screen.queryByText('job.finished')).toBeNull();
    expect(screen.getAllByText('No recorded items').length).toBeGreaterThan(0);
  });
});
