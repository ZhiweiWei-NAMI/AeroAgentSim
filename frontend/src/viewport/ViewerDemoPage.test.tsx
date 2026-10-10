import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import { translate } from '../i18n/translate';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import ViewerDemoPage from './ViewerDemoPage';

const presentation = vi.hoisted(() => ({ props: undefined as any }));
vi.mock('./ViewerPresentation', () => ({ ViewerPresentation: (props: any) => { presentation.props = props; return <div data-testid="demo-presentation" />; } }));
vi.mock('./EntityInspector', () => ({ EntityInspector: ({ selected }: any) => <output>{selected?.id}</output> }));
vi.mock('./demo-feed', () => ({ demoFeed: () => {
  const at = (ns: string) => ({ ns, microstep: 0 });
  const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'demo-controls', registryDigest: 'test', start: at('0'), end: at('5000000000'),
    types: [{ typeId: 'Unit', displayName: 'Unit', ancestors: [] }, { typeId: 'Record', displayName: 'Record', ancestors: [] }], fields: [],
    presentation: [{ typeId: 'Unit', positionField: 'position', frame: 'enu', visual: { kind: 'marker' } }] };
  const commit = (index: number, ns: string, created: FeedCommit['created'] = []): FeedCommit => ({ commitIndex: index, at: at(ns), created,
    removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [] });
  const commits = [commit(7, '0', [{ id: 'object-01', generation: 0, typeId: 'Unit' }, { id: 'record-1', generation: 0, typeId: 'Record' }]), commit(11, '1000000000')];
  return { header: async () => header, subscribe: async (_from: number, onCommit: (commit: FeedCommit) => void) => commits.forEach(onCommit) };
} }));

beforeEach(() => {
  window.localStorage.clear(); presentation.props = undefined;
  Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation(query => ({ matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) });
});
afterEach(() => { cleanup(); window.localStorage.clear(); });
function LocaleButton() { const { locale, setLocale } = useI18n(); return <button onClick={() => setLocale(locale === 'en-US' ? 'zh-CN' : 'en-US')}>locale</button>; }

describe('authored viewer demo controls', () => {
  it('uses real entity counts, recorded commit IDs and duration, and disables timeline endpoints', async () => {
    render(<MemoryRouter><I18nProvider><LocaleButton /><ViewerDemoPage /></I18nProvider></MemoryRouter>);
    await screen.findByTestId('demo-presentation');
    expect(screen.getByText('1 spatial entities')).toBeInTheDocument();
    expect(screen.getByText('0.00 / 5.00 s')).toBeInTheDocument();
    expect(screen.getByText('Commit #7 · μ0')).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: 'Commit timeline' })).toHaveAttribute('aria-valuetext', 'Commit #7');
    expect(screen.getByLabelText('Previous commit')).toBeDisabled();
    fireEvent.click(screen.getByLabelText('Next commit'));
    expect(screen.getByText('Commit #11 · μ0')).toBeInTheDocument();
    expect(screen.getByLabelText('Next commit')).toBeDisabled();
    expect(presentation.props.commitCut).toBe(11);
    expect(presentation.props.clock.ns).toBe('1000000000');
    expect(presentation.props.clock.playing).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();
    expect(presentation.props.commitCut).toBeUndefined();
    fireEvent.click(screen.getByRole('button', { name: 'locale' }));
    expect(screen.getByRole('button', { name: '暂停' })).toBeInTheDocument();
    expect(screen.getByText('1 个空间实体')).toBeInTheDocument();
    expect(screen.getByText('1.00 / 5.00 秒')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '镜头模式' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '画质' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: translate('zh-CN', 'Playback speed') })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '语言' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', { name: '轨迹' }));
    expect(presentation.props.trails).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '暂停' }));
    await waitFor(() => expect(presentation.props.clock.playing).toBe(false));
    expect(screen.getByRole('button', { name: '播放' })).toBeInTheDocument();
  });
});
