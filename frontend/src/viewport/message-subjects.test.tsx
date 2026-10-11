import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import { FeedStore } from './feed-store';
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

});
