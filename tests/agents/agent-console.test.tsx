import React from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import AgentConsole from '../../frontend/src/pages/AgentConsole';
const state = vi.hoisted(() => ({ commits: [] as any[], signal: undefined as AbortSignal | undefined }));
vi.mock('../../frontend/src/feeds/http', () => ({
  RunsApi: class {},
  HttpViewerFeed: class {
    async header() { return { start: { ns: '0' } }; }
    async subscribe(_: number, accept: (commit: any) => void, signal: AbortSignal) { state.signal = signal; state.commits.forEach(accept); }
  },
}));
afterEach(cleanup);
const record = (phase: string, data: unknown) => ({schemaId: 'aas.agent.record', at: {ns: '1000000000'}, payload: {decision_id: 'd/1', phase, data_json: JSON.stringify(data)}});
const commit = (messages: any[], receipts: any[] = []) => ({created: [{id: 'resource', generation: 0}], facts: [], edges: [], messages, receipts});
test('keeps multiple proposals and correlates full actual command receipt histories', async () => {
  state.commits = [
    commit([record('observation', {fields: [{field: 'position', entity: {id: 'resource'}, status: 'known', value: [1,2,3]}]}),
      record('command', {call_id: 'call-a', schema: 'move', target: 'motion', payload: {entity:'resource'}, decision_summary:'First'}),
      record('command', {call_id: 'call-b', schema: 'move', target: 'motion', payload: {entity:'resource'}, decision_summary:'Second'})]),
    commit([], [{commandId:'kernel-a',status:'submitted'},{commandId:'kernel-a',status:'succeeded'},{commandId:'kernel-b',status:'submitted'}]),
    commit([record('receipt', {command_id:'kernel-a',call_id:'call-a',status:'submitted'}),record('receipt', {command_id:'kernel-a',call_id:'call-a',status:'succeeded'}),record('receipt', {command_id:'kernel-b',call_id:'call-b',status:'submitted'})]),
  ];
  const page = render(<MemoryRouter initialEntries={['/agents/run-1?api=http://127.0.0.1:8002']}><AgentConsole /></MemoryRouter>);
  await waitFor(() => expect(screen.getAllByText('Proposed call')).toHaveLength(2));
  expect(screen.getAllByText('Committed receipt:')).toHaveLength(3);
  expect(screen.getByText('resource.position')).toBeInTheDocument();
  expect(screen.getByRole('link', {name:'resource'})).toHaveAttribute('href', '/runs/run-1?api=http%3A%2F%2F127.0.0.1%3A8002&entity=resource');
  expect(screen.getAllByText('kernel-a')).toHaveLength(2);
  page.unmount(); expect(state.signal?.aborted).toBe(true);
});
test('shows malformed observations as errors', async () => {
  state.commits = [commit([record('observation', {fields: [{field:'secret'}]})])];
  render(<MemoryRouter initialEntries={['/agents/run-1']}><AgentConsole /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText('Invalid observed field')).toBeInTheDocument());
});
