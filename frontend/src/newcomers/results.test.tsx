import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EventFeed } from './EventFeed';
import { KpiPanel } from './KpiPanel';
import { CompareRuns } from './CompareRuns';
import type { BusinessEvent, KpiValues, RunComparison, TemplateApi } from './api';

const events: BusinessEvent[] = [
  { at_ns: '1000', category: 'logistics', severity: 'info', text: 'Drone A picked up parcel P1 at Vertiport North', entities: ['drone-a'] },
  { at_ns: '2000', category: 'airspace', severity: 'critical', text: 'Separation conflict between Drone A and Drone B', entities: ['drone-a', 'drone-b'] },
];

describe('EventFeed', () => {
  it('renders plaintext cards with icon, category, severity word and filters, and reports selected events', () => {
    const onSelect = vi.fn();
    render(<EventFeed events={events} onSelect={onSelect} selectedEntities={['drone-a']} />);
    // Severity is readable without color: the word is always present on each card.
    const badges = document.querySelectorAll('.newc-feed-list .newc-severity');
    expect(badges.length).toBe(2);
    expect(badges[0]).toHaveTextContent('info');
    expect(badges[1]).toHaveTextContent('critical');
    expect(screen.getByText('Separation conflict between Drone A and Drone B')).toBeInTheDocument();
    // Category chips filter the feed truthfully.
    fireEvent.click(screen.getByRole('button', { name: 'Airspace' }));
    expect(screen.queryByText(/picked up parcel/)).toBeNull();
    expect(screen.getByText('Separation conflict between Drone A and Drone B')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Separation conflict/ }));
    expect(onSelect).toHaveBeenCalledWith(events[1]);
  });

  it('stays truthful when no events were declared', () => {
    render(<EventFeed events={[]} />);
    expect(screen.getByText(/No events yet at this moment/)).toBeInTheDocument();
  });
});

describe('KpiPanel', () => {
  it('shows actual values with units and sparklines, and never fakes null as 0', () => {
    const values: KpiValues = {
      tasks_completed: { value: 7, unit: '', label: 'Tasks completed' },
      mean_task_latency_s: { value: 12.345, unit: 's', label: 'Mean task latency' },
      energy_used_kj: { value: null, unit: 'kJ', label: 'Energy used' },
    };
    const series = [
      { at_ns: '1', kpis: { tasks_completed: { value: 3, unit: '', label: 'Tasks completed' } } },
      { at_ns: '2', kpis: { tasks_completed: { value: 7, unit: '', label: 'Tasks completed' } } },
    ];
    render(<KpiPanel values={values} series={series} />);
    expect(screen.getByText('Tasks completed')).toBeInTheDocument();
    expect(screen.getByText(/12\.35 s|12\.3 s/)).toBeInTheDocument();
    // Null is rendered as an explicit dash, never a fabricated 0.
    expect(screen.queryByText(/^0/)).not.toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(document.querySelector('.newc-kpi-spark path')).toBeInTheDocument();
  });
});

describe('CompareRuns', () => {
  it('fetches a real comparison from the API and highlights the best value per metric direction', async () => {
    const comparison: RunComparison = {
      runs: [
        { id: 'r1', title: 'Nearest fleet', template_id: 'parcel-delivery',
          kpis: { tasks_completed: { value: 5, unit: '', label: 'Tasks completed' }, energy_used_kj: { value: 900, unit: 'kJ', label: 'Energy used', direction: 'min' } },
          knobs: { fleet_size: 6 }, algorithms: { allocation: 'nearest' } },
        { id: 'r2', title: 'Auction fleet', template_id: 'parcel-delivery',
          kpis: { tasks_completed: { value: 9, unit: '', label: 'Tasks completed' }, energy_used_kj: { value: 700, unit: 'kJ', label: 'Energy used', direction: 'min' } },
          knobs: { fleet_size: 6 }, algorithms: { allocation: 'auction' } },
      ],
    };
    const api = { compare: vi.fn().mockResolvedValue(comparison) } as unknown as TemplateApi;
    render(<CompareRuns api={api} ids={['r1', 'r2']} onBack={() => {}} />);
    await waitFor(() => expect(screen.getByText('Nearest fleet')).toBeInTheDocument());
    // Two columns, one per run.
    expect(screen.getByText('Auction fleet')).toBeInTheDocument();
    // Energy used honors direction=min: the lower run (700) is the best, not the max.
    const energyCells = screen.getAllByText(/kJ/);
    expect(energyCells[1]).toHaveClass('is-best'); // r2 = 700 kJ, second column
    expect(energyCells[0]).not.toHaveClass('is-best');
    expect(api.compare).toHaveBeenCalledWith(['r1', 'r2'], expect.anything());
    expect(document.querySelectorAll('.newc-bar')).toHaveLength(4);
  });

  it('surfaces API errors with a retry instead of mocking data', async () => {
    const api = { compare: vi.fn().mockRejectedValue(new Error('HTTP 503: unavailable')) } as unknown as TemplateApi;
    render(<CompareRuns api={api} ids={['r1', 'r2']} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/HTTP 503/);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(api.compare).toHaveBeenCalledTimes(2));
  });
});
