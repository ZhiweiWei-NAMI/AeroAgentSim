import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EntityKey, FeedCommit } from '../contracts/viewer-feed';
import { ConsoleNotifications } from '../console/Notifications';
import { RunsApi } from '../feeds/http';
import { I18nProvider } from '../i18n/I18nProvider';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from '../viewport/clock';
import { at, emptyCommit, fixtureHeader } from '../behaviours/extension-fixture';
import { Dock, type DockTab } from './Dock';
import type { RunSession } from './useRunSession';

const selected: EntityKey = { id: 'drone.a', generation: 2 };

beforeEach(() => { localStorage.clear(); window.history.replaceState(null, '', '/'); });
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); window.history.replaceState(null, '', '/'); });

function record(decision: string, phase: string, data: unknown, sequence: number): FeedCommit['messages'][number] {
  return {
    id: `${decision}/${phase}`, schemaId: 'aas.langgraph.record', kind: 'event' as const,
    source: 'rescue_uav_drone.a', at: at('10'),
    payload: { decision_id: decision, phase, data_json: JSON.stringify(data) },
  };
}

function storeWith(records: FeedCommit['messages'], created: FeedCommit['created'] = [{ id: 'drone.a', generation: 2, typeId: 'vehicle' }]) {
  const store = new TemporalFeedStore({ ...fixtureHeader, runId: 'run-agents' });
  store.ingest({ ...emptyCommit(1, '10'), created, messages: records });
  store.seek('10', 1);
  return store;
}

function sessionFor(store: TemporalFeedStore): RunSession {
  const clock = new PlaybackClock('0', '10');
  clock.seek('10');
  return {
    store, clock, inputSource: { serialize: vi.fn() } as unknown as RunSession['inputSource'],
    status: 'completed', journalComplete: true, followingLive: false, revision: 1, cut: 1,
    seekOffset: vi.fn(), seekCut: vi.fn(), togglePlayback: vi.fn(), backToLive: vi.fn(), setSpeed: vi.fn(),
  };
}

function Harness({ session, tracked }: { session: RunSession; tracked?: EntityKey }) {
  const [tab, setTab] = useState<DockTab>('events');
  const [open, setOpen] = useState(false);
  return <I18nProvider><ConsoleNotifications><MemoryRouter><Dock session={session} events={[]} kpis={{}} series={[]} entityNames={new Map()}
    selected={tracked} tab={tab} onTab={setTab} open={open} onToggle={() => setOpen(value => !value)}
    onEvent={() => {}} /></MemoryRouter></ConsoleNotifications></I18nProvider>;
}

const observation = (id: string, generation: number) => ({
  initial: { observation: { fields: [{ entity: { id, generation }, field: 'phase', status: 'known', value: 'moving' }] } },
});

describe('Dock agent decisions', () => {
  it('lists canonical decisions at the cut with phases, model calls, source ids and the full-console link', () => {
    const store = storeWith([
      record('rescue_uav_drone.a/1', 'observation', observation('drone.a', 2), 0),
      record('rescue_uav_drone.a/1', 'model_call', { identity: [0, 'drone.a', 'choose', 0], request: {}, response: {} }, 1),
      // A projector alias is the same recorded call, not a second one.
      {
        id: 'rescue_uav_drone.a/1/model_call/console',
        schemaId: 'aas.agent.record', kind: 'event' as const, source: 'rescue_uav_drone.a', at: at('10'),
        payload: { decision_id: 'rescue_uav_drone.a/1', phase: 'model_call', data_json: JSON.stringify({ identity: [0, 'drone.a', 'choose', 0], source_schema: 'aas.langgraph.record', source_message_id: 'rescue_uav_drone.a/1/model_call' }) },
      },
      record('rescue_uav_drone.a/1', 'output', { kind: 'event', schema: 'demo.bid', payload: { accept: true } }, 2),
      record('rescue_uav_drone.a/1', 'receipt', { command_id: 'cmd-1', call_id: 'call-1', status: 'succeeded' }, 3),
    ]);
    render(<Harness session={sessionFor(store)} tracked={selected} />);

    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.getAllByTestId('wb-agent-decision')).toHaveLength(1);
    const card = screen.getByTestId('wb-agent-decision');
    expect(card).toHaveAttribute('data-decision-id', 'rescue_uav_drone.a/1');
    expect(screen.getByText('Observation')).toBeInTheDocument();
    expect(screen.getAllByTestId('wb-agent-record-id').map(row => row.textContent)).toContain('rescue_uav_drone.a/1/observation');
    expect(screen.getAllByTestId('wb-agent-record-id')).toHaveLength(4);
    expect(screen.queryByText('rescue_uav_drone.a/1/model_call/console')).toBeNull();
    expect(screen.getByText('4 records')).toBeInTheDocument();
    expect(screen.getByTestId('wb-agent-model-calls')).toHaveTextContent('Model calls');
    expect(screen.getByTestId('wb-agent-model-calls')).toHaveTextContent('drone.a');
    expect(within(screen.getByTestId('wb-agent-model-calls')).getByText('1', { selector: '.wb-tag' })).toBeInTheDocument();
    expect(screen.queryByTestId('wb-agent-generation-unknown')).toBeNull();
    expect(screen.getByTestId('wb-agent-console-link')).toHaveAttribute('href', '/agents/run-agents?cut=1');
    // An event proposal is a proposal; only the recorded receipt is a receipt.
    expect(screen.getByTestId('wb-agent-proposals')).toHaveTextContent('Proposed event');
    expect(screen.getByTestId('wb-agent-proposals')).not.toHaveTextContent('Actual receipts');
    expect(screen.getByTestId('wb-agent-receipts')).toHaveTextContent('Actual receipts');
    expect(screen.getByTestId('wb-agent-receipts')).toHaveTextContent('cmd-1');
  });

  it('filters to the selected drone generation and hides the tab when the run has no agent records', () => {
    const store = storeWith([
      record('rescue_uav_drone.a/1', 'observation', observation('drone.a', 2), 0),
      record('rescue_uav_drone.a/2', 'observation', observation('drone.a', 1), 1),
      record('rescue_uav_drone.b/1', 'observation', observation('drone.b', 2), 2),
    ], [{ id: 'drone.a', generation: 2, typeId: 'vehicle' }, { id: 'drone.b', generation: 2, typeId: 'vehicle' }]);
    const view = render(<Harness session={sessionFor(store)} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.getAllByTestId('wb-agent-decision')).toHaveLength(1);
    expect(screen.getByTestId('wb-agent-decision')).toHaveAttribute('data-decision-id', 'rescue_uav_drone.a/1');
    view.unmount();

    const empty = new TemporalFeedStore({ ...fixtureHeader, runId: 'run-agents' });
    empty.ingest(emptyCommit(1, '10')); empty.seek('10', 1);
    render(<Harness session={sessionFor(empty)} />);
    expect(screen.queryByRole('button', { name: /Agents/ })).toBeNull();
  });

  it('does not attribute a decision to an ambiguous id whose generation was never recorded', () => {
    const store = storeWith([
      record('rescue_uav_drone.a/1', 'observation', {
        initial: { observation: { fields: [{ entity: { id: 'drone.a' }, field: 'phase', status: 'known', value: 'moving' }] } },
      }, 0),
    ], [{ id: 'drone.a', generation: 1, typeId: 'vehicle' }]);
    render(<Harness session={sessionFor(store)} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.queryByTestId('wb-agent-decision')).toBeNull();
    expect(screen.getByTestId('wb-agent-list')).toHaveTextContent(/No agent decisions for the selected drone and generation/);
  });

  it.each(['rescue_uav_drone_a', 'rescue_uav_DRONE.A', 'unrelated.drone.a', 'unrelated_drone.a', 'unrelated:drone.a', 'unrelated/drone.a', 'drone.a'])(
    'does not infer an entity identity from engine name %s', engine => {
      const store = storeWith([record(`${engine}/1`, 'model_call', { request: {}, response: {} }, 0)], [
        { ...selected, typeId: 'vehicle' }, { id: 'drone_a', generation: 1, typeId: 'vehicle' },
      ]);
      const session = sessionFor(store), view = render(<Harness session={session} tracked={selected} />);
      fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
      expect(screen.queryByTestId('wb-agent-decision')).toBeNull();
      view.rerender(<Harness session={session} />);
      expect(screen.getByTestId('wb-agent-decision')).toHaveAttribute('data-decision-id', `${engine}/1`);
      expect(screen.getByTestId('wb-agent-generation-unknown')).toHaveTextContent('generation unknown');
    },
  );

  it.each(['command', 'output'])('does not attribute %s routing partitions to unrelated entities with the same id', phase => {
    const other = { id: 'drone.b', generation: 2 };
    const store = storeWith([], [{ ...selected, typeId: 'vehicle' }, { ...other, typeId: 'vehicle' }]);
    const proposal = {
      kind: 'command', call_id: 'call-1', schema: 'demo.move', target: selected.id,
      source: selected.id, payload: {}, decision_summary: 'Move the observed drone',
    };
    store.ingest({ ...emptyCommit(2, '20'), messages: [
      record('arbitrary-engine/1', 'observation', observation(other.id, other.generation), 0),
      record('arbitrary-engine/1', phase, proposal, 1),
    ] });
    store.seek('20', 2);
    const session = { ...sessionFor(store), cut: 2 };
    const view = render(<Harness session={session} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.queryByTestId('wb-agent-decision')).toBeNull();
    expect(screen.getByTestId('wb-agent-list')).toHaveTextContent(/No agent decisions for the selected drone and generation/);
    view.rerender(<Harness session={session} tracked={other} />);
    expect(screen.getByTestId('wb-agent-decision')).toHaveAttribute('data-decision-id', 'arbitrary-engine/1');
    expect(screen.queryByTestId('wb-agent-generation-unknown')).toBeNull();
  });

  it.each([
    { target: selected.id, source: selected.id },
    { vehicle_status: selected },
    { metadata: selected },
    { entity: selected },
    { fields: [{ entity: selected }] },
  ])('does not treat arbitrary model data as an entity identity: %j', data => {
    const store = storeWith([record('arbitrary-engine/1', 'model_call', data, 0)]);
    const session = sessionFor(store), view = render(<Harness session={session} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.queryByTestId('wb-agent-decision')).toBeNull();
    view.rerender(<Harness session={session} />);
    expect(screen.getByTestId('wb-agent-generation-unknown')).toHaveTextContent('generation unknown');
  });

  it('retains explicit typed references inside a command payload, including entity target/source fields', () => {
    const store = storeWith([record('arbitrary-engine/1', 'command', {
      call_id: 'call-1', schema: 'demo.move', target: 'engine-partition', decision_summary: 'Move',
      payload: { target: { $ref: selected }, source: { $ref: { id: 'drone.b', generation: 1 } } },
    }, 0)]);
    render(<Harness session={sessionFor(store)} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.getByTestId('wb-agent-decision')).toHaveAttribute('data-decision-id', 'arbitrary-engine/1');
    expect(screen.queryByTestId('wb-agent-generation-unknown')).toBeNull();
  });

  it.each(['source', 'target'])('retains schema-defined observation relation %s identities', endpoint => {
    const store = storeWith([record('arbitrary-engine/1', 'observation', {
      fields: [], relations: [{ schema: 'demo.link', edges: [{ [endpoint]: selected }] }],
    }, 0)]);
    render(<Harness session={sessionFor(store)} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.getByTestId('wb-agent-decision')).toHaveAttribute('data-decision-id', 'arbitrary-engine/1');
    expect(screen.queryByTestId('wb-agent-generation-unknown')).toBeNull();
  });

  it('uses exact recorded subjects even when punctuation and case distinguish otherwise similar ids', () => {
    const subject = { id: 'drone_a', generation: 1 };
    const store = storeWith([{ ...record('rescue_uav_drone_a/1', 'model_call', {}, 0), subjects: [subject] }], [
      { ...selected, typeId: 'vehicle' }, { ...subject, typeId: 'vehicle' },
    ]);
    const session = sessionFor(store), view = render(<Harness session={session} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.queryByTestId('wb-agent-decision')).toBeNull();
    view.rerender(<Harness session={session} tracked={subject} />);
    expect(screen.getByTestId('wb-agent-decision')).toHaveAttribute('data-decision-id', 'rescue_uav_drone_a/1');
    expect(screen.queryByTestId('wb-agent-generation-unknown')).toBeNull();
    view.rerender(<Harness session={session} tracked={{ ...subject, id: 'DRONE_A' }} />);
    expect(screen.queryByTestId('wb-agent-decision')).toBeNull();
  });

  it.each(['en-US', 'zh-CN'])('keeps generation-less records unknown while rewound before creation (%s)', locale => {
    localStorage.setItem('aeroagentsim.locale', locale);
    const store = storeWith([record('rescue_uav_drone.a/1', 'observation', {
      initial: { observation: { fields: [{ entity: { id: selected.id }, field: 'phase', status: 'known', value: 'moving' }] } },
    }, 0)], []);
    // A retained selection and a future creation are not evidence at the record's cut.
    store.ingest({ ...emptyCommit(2, '20'), created: [{ ...selected, typeId: 'vehicle' }] });
    store.seek('10', 1);
    const session = sessionFor(store), view = render(<Harness session={session} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: locale === 'zh-CN' ? /智能体/ : /Agents/ }));
    expect(screen.queryByTestId('wb-agent-decision')).toBeNull();
    view.rerender(<Harness session={session} />);
    expect(screen.getByTestId('wb-agent-decision')).toHaveAttribute('data-decision-id', 'rescue_uav_drone.a/1');
    expect(screen.getByTestId('wb-agent-generation-unknown')).toHaveTextContent(locale === 'zh-CN' ? '代次未知' : 'generation unknown');
    // Advancing the view does not retroactively assign the earlier record a generation.
    store.seek('20', 2);
    view.rerender(<Harness session={{ ...session, cut: 2 }} tracked={selected} />);
    expect(screen.queryByTestId('wb-agent-decision')).toBeNull();
  });

  it('resolves an id-only recorded subject only with one prior recorded generation', () => {
    const store = storeWith([record('arbitrary-engine/1', 'model_call', { subject: selected.id }, 0)]);
    const session = sessionFor(store), view = render(<Harness session={session} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.getByTestId('wb-agent-decision')).toBeInTheDocument();
    view.rerender(<Harness session={session} tracked={{ ...selected, generation: 1 }} />);
    expect(screen.queryByTestId('wb-agent-decision')).toBeNull();
  });

  it.each(['en-US', 'zh-CN'])('counts model completions, not declared or returned tools (%s)', locale => {
    localStorage.setItem('aeroagentsim.locale', locale);
    for (const toolCalls of [[], [{ id: 'tool-1' }, { id: 'tool-2' }]]) {
      const store = storeWith([record('engine/1', 'model_call', {
        request: { tools: [] }, response: { content: 'An ordinary completion', tool_calls: toolCalls },
      }, 0)]);
      const view = render(<Harness session={sessionFor(store)} />);
      fireEvent.click(screen.getByRole('button', { name: locale === 'zh-CN' ? /智能体/ : /Agents/ }));
      const calls = screen.getByTestId('wb-agent-model-calls');
      expect(calls).toHaveTextContent(locale === 'zh-CN' ? '模型调用' : 'Model calls');
      expect(within(calls).getByText('1', { selector: '.wb-tag' })).toBeInTheDocument();
      expect(screen.queryByText(/Tool calls|工具调用/)).toBeNull();
      view.unmount();
    }
  });

  it.each([false, true])('opens actual typed non-agent events and commands in the raw disclosure (mixed journal: %s)', async mixed => {
    window.history.replaceState(null, '', '/w/project-1?run=run-agents&api=https%3A%2F%2Fapi.example&cut=1');
    const bases: string[] = [];
    const request = vi.spyOn(RunsApi.prototype, 'request').mockImplementation(async function (this: RunsApi, path) {
      bases.push(this.base);
      if (path.endsWith('/configuration')) return { scenario: { registry: { messages: [] } } };
      if (path.includes('/admissions')) return { cursor: 0, receipts: [] };
      return { status: 'completed' };
    });
    const ordinary: FeedCommit['messages'] = [
      { id: 'typed-event', schemaId: 'demo.ordinary_event', kind: 'event', source: 'engine', at: at('10'), payload: { marker: 'non-agent event' } },
      { id: 'typed-command', schemaId: 'demo.ordinary_command', kind: 'command', source: 'operator', target: 'engine', at: at('10'), payload: { marker: 'non-agent command' } },
    ];
    const store = storeWith([...ordinary, ...(mixed ? [record('agent/1', 'model_call', {}, 0)] : [])]);
    // A later cut at the very same physical time must remain out of view.
    store.ingest({ ...emptyCommit(2, '10', 1), messages: [{ ...ordinary[0], id: 'future-event', schemaId: 'demo.future_event', at: at('10', 1) }] });
    store.seek('10', 1);
    const session = sessionFor(store);
    render(<Harness session={session} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: /^Events/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Explore recorded activity' }));
    const raw = screen.getByTestId('wb-raw-records');
    const summary = within(raw).getByText('Decision / LangGraph and typed event records at this cut', { selector: 'summary' });
    expect(summary.closest('details')).toHaveAttribute('open');
    expect(within(raw).getByText('Demo Ordinary Event')).toBeVisible();
    expect(within(raw).getByText('Demo Ordinary Command')).toBeVisible();
    expect(within(raw).queryByText('Demo Future Event')).toBeNull();
    const details = within(summary.closest('details')!).getAllByRole('button', { name: 'Details' });
    fireEvent.click(details[0]);
    expect(await screen.findByText(/non-agent event/)).toBeVisible();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
    fireEvent.click(details[1]);
    expect(await screen.findByText(/non-agent command/)).toBeVisible();
    expect(screen.queryByTestId('event-feed-raw-records')).toBeNull();
    expect(request).toHaveBeenCalled();
    expect(new Set(bases)).toEqual(new Set(['https://api.example']));
    expect(request.mock.calls.every(([path, options]) => path.includes('/runs/run-agents/') && (!options?.method || options.method === 'GET'))).toBe(true);
    expect(screen.getByRole('button', { name: 'Submit typed ingress' })).toBeDisabled();
    expect(session.store.viewCursor?.knownAt).toBe(1);
    expect(window.location.pathname).toBe('/w/project-1');
  });

  it('shows only decisions committed at or before the viewed cut', () => {
    const store = new TemporalFeedStore({ ...fixtureHeader, runId: 'run-agents' });
    store.ingest({ ...emptyCommit(1, '10'), created: [{ id: 'drone.a', generation: 2, typeId: 'vehicle' }],
      messages: [record('rescue_uav_drone.a/1', 'observation', observation('drone.a', 2), 0)] });
    store.ingest({ ...emptyCommit(2, '10'),
      messages: [record('rescue_uav_drone.a/2', 'observation', observation('drone.a', 2), 0)] });
    store.seek('10', 1);
    render(<Harness session={sessionFor(store)} tracked={selected} />);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.getAllByTestId('wb-agent-decision')).toHaveLength(1);
    expect(screen.getByTestId('wb-agent-decision')).toHaveAttribute('data-decision-id', 'rescue_uav_drone.a/1');
  });
});
