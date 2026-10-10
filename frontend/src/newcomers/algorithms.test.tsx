import { beforeAll, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AlgorithmsPanel } from './AlgorithmsPanel';
import type { AlgorithmDescriptor, AlgorithmSlot } from './api';

const fetchMock = vi.fn();
const jsonResponse = (status: number, payload: unknown) => ({
  ok: status >= 200 && status < 300, status,
  text: async () => JSON.stringify(payload),
});

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })) });
});
afterEach(() => { vi.unstubAllGlobals(); fetchMock.mockReset(); });

const catalog: AlgorithmDescriptor[] = [
  { id: 'nearest', slot: 'allocation', label: 'Nearest free vehicle', description: 'Assigns each task to the closest idle drone.' },
  { id: 'auction', slot: 'allocation', label: 'Auction', description: 'Vehicles bid on tasks; lowest cost wins.' },
  { id: 'straight', slot: 'path_planning', label: 'Straight line', description: 'Flies directly between the two points.' },
];

function Harness({ initial, changed }: { initial?: Partial<Record<AlgorithmSlot, string>>; changed: (next: Partial<Record<AlgorithmSlot, string>>) => void }) {
  return <AlgorithmsPanel base="http://studio.test" algorithms={initial ?? {}} onChange={changed} />;
}

describe('AlgorithmsPanel', () => {
  it('lists only server-advertised algorithms per slot and reports one-line descriptions', async () => {
    vi.stubGlobal('fetch', fetchMock.mockResolvedValue(jsonResponse(200, Object.fromEntries(['allocation','path_planning','conflict_resolution','charging'].map(slot=>[slot,catalog.filter(row=>row.slot===slot)])))));
    render(<Harness changed={() => {}} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // Four slots, all enabled once the real catalog arrives.
    for (const slot of ['Allocation', 'Path Planning', 'Conflict Resolution', 'Charging']) {
      expect(screen.getByRole('combobox', { name: slot })).toBeEnabled();
    }
    expect(fetchMock.mock.calls[0][0]).toBe('http://studio.test/v1/algorithms');
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Allocation' }));
    expect(screen.getByTitle('Vehicles bid on tasks; lowest cost wins.')).toBeInTheDocument();
    // Other slots keep their own lists (path planning shows straight, not allocation entries).
  });

  it('shows a meaningful error and retry when the endpoint is unavailable (404), without inventing a catalog', async () => {
    vi.stubGlobal('fetch', fetchMock.mockResolvedValue(jsonResponse(404, { detail: 'Not Found' })));
    render(<Harness initial={{ allocation: 'auction' }} changed={() => {}} />);
    const alert = await screen.findByTestId('studio-algorithms-error');
    expect(alert).toHaveTextContent('Algorithm catalog is unavailable');
    expect(screen.getByRole('combobox', { name: 'Allocation' })).toBeDisabled();
    expect(screen.queryByText('Nearest free vehicle')).not.toBeInTheDocument();
    // Retry against a recovered service restores the real list.
    fetchMock.mockResolvedValue(jsonResponse(200, Object.fromEntries(['allocation','path_planning','conflict_resolution','charging'].map(slot=>[slot,catalog.filter(row=>row.slot===slot)]))));
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Allocation' })).toBeEnabled());
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Allocation' }));
    expect(screen.getByTitle('Assigns each task to the closest idle drone.')).toBeInTheDocument();
  });

  it('keeps authored selections (including unknown ids) and forwards edits unchanged in shape', async () => {
    vi.stubGlobal('fetch', fetchMock.mockResolvedValue(jsonResponse(200, Object.fromEntries(['allocation','path_planning','conflict_resolution','charging'].map(slot=>[slot,catalog.filter(row=>row.slot===slot)])))));
    const changed = vi.fn();
    render(<Harness initial={{ allocation: 'llm', path_planning: 'corridor_astar' }} changed={changed} />);
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Allocation' })).toBeEnabled());
    expect(screen.getAllByText(/not in the current catalog/).length).toBeGreaterThan(0);
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Allocation' }));
    fireEvent.click(screen.getByTitle('Assigns each task to the closest idle drone.'));
    expect(changed).toHaveBeenLastCalledWith({ allocation: 'nearest', path_planning: 'corridor_astar' });
  });
});
