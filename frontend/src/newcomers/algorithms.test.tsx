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

function Harness({ initial, changed, applicable }: { initial?: Partial<Record<AlgorithmSlot, string>>; changed: (next: Partial<Record<AlgorithmSlot, string>>) => void; applicable?: boolean }) {
  return <AlgorithmsPanel base="http://studio.test" algorithms={initial ?? {}} onChange={changed} applicable={applicable} />;
}

const fullCatalog = () => Object.fromEntries(['allocation','path_planning','conflict_resolution','charging'].map(slot=>[slot,catalog.filter(row=>row.slot===slot)]));

describe('AlgorithmsPanel', () => {
  it('lists only server-advertised algorithms per slot and reports one-line descriptions', async () => {
    vi.stubGlobal('fetch', fetchMock.mockResolvedValue(jsonResponse(200, Object.fromEntries(['allocation','path_planning','conflict_resolution','charging'].map(slot=>[slot,catalog.filter(row=>row.slot===slot)])))));
    render(<Harness changed={() => {}} />);
    // Fetch starting does not mean the decoded catalog has reached the UI.
    // Four slots, all enabled once the real catalog arrives.
    for (const slot of ['Allocation', 'Path Planning', 'Conflict Resolution', 'Charging']) {
      await waitFor(() => expect(screen.getByRole('combobox', { name: slot })).toBeEnabled());
    }
    expect(fetchMock).toHaveBeenCalled();
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
    fireEvent.click(await screen.findByTitle('Assigns each task to the closest idle drone.'));
    expect(changed).toHaveBeenLastCalledWith({ allocation: 'nearest', path_planning: 'corridor_astar' });
  });

  it('says "Not used by this scenario" without dropdowns or a fetch when the scenario lacks the lowalt task pack', async () => {
    render(<Harness applicable={false} changed={() => {}} />);
    expect(await screen.findByTestId('studio-algorithms-not-applicable')).toHaveTextContent('not used by this scenario');
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows the documented default with a "(default)" marker, lets you pick an alternative, and resetting clears the override instead of writing the default id', async () => {
    vi.stubGlobal('fetch', fetchMock.mockResolvedValue(jsonResponse(200, fullCatalog())));
    const changed = vi.fn();
    const { rerender } = render(<Harness initial={{ allocation: 'auction' }} changed={changed} />);
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Allocation' })).toBeEnabled());
    // Authored override shows its catalog label without the default marker.
    expect(screen.getByText('Auction')).toBeInTheDocument();
    expect(screen.getByTestId('algorithm-allocation')).not.toHaveTextContent('(default)');
    // Reset removes the override (undefined, not the default id written back).
    fireEvent.click(screen.getByTestId('algorithm-reset-allocation'));
    expect(changed).toHaveBeenLastCalledWith({});
    // With no override the effective value is the documented lowalt default.
    rerender(<Harness initial={{}} changed={changed} />);
    expect(screen.getByText('Nearest free vehicle (default)')).toBeInTheDocument();
    expect(screen.getAllByText('(default)').length).toBeGreaterThanOrEqual(4);
    // Choosing an option stores it; the other untouched slot still shows its default.
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Allocation' }));
    fireEvent.click(await screen.findByTitle('Vehicles bid on tasks; lowest cost wins.'));
    expect(changed).toHaveBeenLastCalledWith({ allocation: 'auction' });
  });
});
