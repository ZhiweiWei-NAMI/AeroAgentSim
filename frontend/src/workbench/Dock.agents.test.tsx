import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { EntityKey } from '../contracts/viewer-feed';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from '../viewport/clock';
import { at, emptyCommit, fixtureHeader } from '../behaviours/extension-fixture';
import { Dock, type DockTab } from './Dock';
import type { RunSession } from './useRunSession';

const selected: EntityKey = { id: 'drone.a', generation: 2 };

function record(decision: string, phase: string, data: unknown, sequence: number) {
  return {
    id: `${decision}/${phase}`, schemaId: 'aas.langgraph.record', kind: 'event' as const,
    source: 'rescue_uav_drone.a', at: at('10'),
    payload: { decision_id: decision, phase, data_json: JSON.stringify(data) },
  };
}

function storeWith(records: Array<ReturnType<typeof record>>, created = [{ id: 'drone.a', generation: 2, typeId: 'vehicle' }]) {
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
  return <MemoryRouter><Dock session={session} events={[]} kpis={{}} series={[]} entityNames={new Map()}
    selected={tracked} tab={tab} onTab={setTab} open={open} onToggle={() => setOpen(value => !value)}
    onEvent={() => {}} /></MemoryRouter>;
}

const observation = (id: string, generation: number) => ({
  initial: { observation: { fields: [{ entity: { id, generation }, field: 'phase', status: 'known', value: 'moving' }] } },
});

describe('Dock agent decisions', () => {
  it('lists canonical decisions at the cut with phases, tool calls, source ids and the full-console link', () => {
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
    expect(screen.getByTestId('wb-agent-tool-calls')).toHaveTextContent('Tool calls');
    expect(screen.getByTestId('wb-agent-tool-calls')).toHaveTextContent('drone.a');
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
