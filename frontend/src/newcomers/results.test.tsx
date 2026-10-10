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

  it('collapses undeclared metrics under More metrics and explains only known metrics', () => {
    const values: KpiValues = {
      tasks_completed: { value: 7, unit: '', label: 'Tasks completed' },
      mean_task_latency_s: { value: 12.345, unit: 's', label: 'Mean task latency' },
      some_unknown_metric: { value: 3, unit: 'count', label: 'Some unknown metric' },
    };
    render(<KpiPanel values={values} series={[]} ids={['tasks_completed']} />);
    // Template-declared KPI first; the unrelated metric sits inside a collapsed group.
    expect(screen.getByText('Tasks completed')).toBeInTheDocument();
    const collapsed = document.querySelector('.newc-kpi-more:not([open])');
    expect(collapsed).not.toBeNull();
    expect(collapsed!.textContent).toContain('Some unknown metric');
    fireEvent.click(screen.getByText(/More metrics/));
    expect(screen.getByText('Some unknown metric')).toBeInTheDocument();
    // Known metric gets a help affordance; unknown ones get none (no invented help).
    expect(screen.getByRole('button', { name: 'About Tasks completed' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'About Some unknown metric' })).toBeNull();
  });

  it('expands the help text of a known metric', () => {
    const values: KpiValues = { mean_task_latency_s: { value: 3, unit: 's', label: 'Mean task latency' } };
    render(<KpiPanel values={values} series={[]} />);
    fireEvent.click(screen.getByRole('button', { name: 'About Mean task latency' }));
    expect(screen.getByText(/Average time between a task appearing and being completed/)).toBeInTheDocument();
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
    const api = { compare: vi.fn().mockResolvedValue(comparison), templates: vi.fn().mockResolvedValue([]) } as unknown as TemplateApi;
    render(<CompareRuns api={api} ids={['r1', 'r2']} onBack={() => {}} />);
    await waitFor(() => expect(screen.getByRole('heading',{name:'Run 1 · Nearest fleet'})).toBeInTheDocument());
    // Two columns, one per run.
    expect(screen.getByRole('heading',{name:'Run 2 · Auction fleet'})).toBeInTheDocument();
    // Energy used honors direction=min: the lower run (700) is the best, not the max.
    const energyCells = screen.getAllByText(/kJ/);
    expect(energyCells[1]).toHaveClass('is-best'); // r2 = 700 kJ, second column
    expect(energyCells[0]).not.toHaveClass('is-best');
    expect(api.compare).toHaveBeenCalledWith(['r1', 'r2'], expect.anything());
    expect(document.querySelectorAll('.newc-bar')).toHaveLength(4);
  });

  it('surfaces API errors with a retry instead of mocking data', async () => {
    const api = { compare: vi.fn().mockRejectedValue(new Error('HTTP 503: unavailable')), templates: vi.fn().mockRejectedValue(new Error('HTTP 503: unavailable')) } as unknown as TemplateApi;
    render(<CompareRuns api={api} ids={['r1', 'r2']} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/HTTP 503/);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(api.compare).toHaveBeenCalledTimes(2));
  });

  it('uses template knob labels and deduplicates allocator rows', async () => {
    const comparison: RunComparison = {
      runs: [
        { id: 'r1', title: 'Nearest', template_id: 'parcel-delivery',
          kpis: { tasks_completed: { value: 5, unit: '', label: 'Tasks completed' } },
          knobs: { allocator: 'nearest' }, algorithms: { allocation: 'nearest' } },
        { id: 'r2', title: 'Auction', template_id: 'parcel-delivery',
          kpis: { tasks_completed: { value: 9, unit: '', label: 'Tasks completed' } },
          knobs: { allocator: 'auction' }, algorithms: { allocation: 'auction' } },
      ],
    };
    const catalog = [{
      format: 'aeroagentsim.template/v1', id: 'parcel-delivery', title: 'Parcel delivery', summary: '', category: 'logistics',
      difficulty: 'beginner', duration_s: 300, scenario: 'scenario.yaml', observe: [], try: [],
      knobs: [{ id: 'allocator', label: 'Task allocation algorithm', type: 'choice', default: 'nearest', pointer: '/algorithms/allocation' }],
      kpis: ['tasks_completed'],
    }];
    const api = { compare: vi.fn().mockResolvedValue(comparison), templates: vi.fn().mockResolvedValue(catalog) } as unknown as TemplateApi;
    render(<CompareRuns api={api} ids={['r1', 'r2']} />);
    await screen.findByRole('heading', { name: 'Run 1 · Nearest' });
    // Knob label from the catalog replaces the machine id…
    expect(screen.getByText('Task allocation algorithm')).toBeInTheDocument();
    expect(screen.queryByText('Allocator')).toBeNull();
    // …and the pointed algorithm slot is deduplicated into the same row.
    expect(screen.queryByText('Allocation')).toBeNull();
  });

  it('stays available and honest when the catalog fetch fails', async () => {
    const comparison: RunComparison = {
      runs: [
        { id: 'r1', title: 'Nearest', kpis: { tasks_completed: { value: 5, unit: '', label: 'Tasks completed' } }, knobs: {}, algorithms: {} },
        { id: 'r2', title: 'Auction', kpis: { tasks_completed: { value: 9, unit: '', label: 'Tasks completed' } }, knobs: {}, algorithms: {} },
      ],
    };
    const api = { compare: vi.fn().mockResolvedValue(comparison), templates: vi.fn().mockRejectedValue(new Error('HTTP 503: unavailable')) } as unknown as TemplateApi;
    render(<CompareRuns api={api} ids={['r1', 'r2']} />);
    await screen.findByRole('heading', { name: 'Run 1 · Nearest' });
    // Comparison still rendered, with a small note instead of a dead end.
    expect(screen.getByRole('heading', { name: 'Run 2 · Auction' })).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent(/template details are unavailable/);
  });

  it('orders template-declared KPIs first and collapses the rest under More metrics', async () => {
    const comparison: RunComparison = {
      runs: [
        { id: 'r1', title: 'A', template_id: 'parcel-delivery',
          kpis: { tasks_completed: { value: 5, unit: '', label: 'Tasks completed' }, tasks_failed: { value: 1, unit: '', label: 'Tasks failed' } },
          knobs: {}, algorithms: {} },
        { id: 'r2', title: 'B', template_id: 'parcel-delivery',
          kpis: { tasks_completed: { value: 9, unit: '', label: 'Tasks completed' }, tasks_failed: { value: 2, unit: '', label: 'Tasks failed' } },
          knobs: {}, algorithms: {} },
      ],
    };
    const catalog = [{
      format: 'aeroagentsim.template/v1', id: 'parcel-delivery', title: 'Parcel delivery', summary: '', category: 'logistics',
      difficulty: 'beginner', duration_s: 300, scenario: 'scenario.yaml', observe: [], try: [],
      knobs: [], kpis: ['tasks_completed'],
    }];
    const api = { compare: vi.fn().mockResolvedValue(comparison), templates: vi.fn().mockResolvedValue(catalog) } as unknown as TemplateApi;
    render(<CompareRuns api={api} ids={['r1', 'r2']} />);
    await screen.findByRole('heading', { name: 'Run 1 · A' });
    expect(screen.getAllByText('Tasks completed').length).toBe(2);
    expect(document.querySelector('.newc-kpi-more:not([open])')).not.toBeNull();
    fireEvent.click(screen.getAllByText(/More metrics \(1\)/)[0]);
    expect(document.querySelector('.newc-kpi-more[open]')).not.toBeNull();
    expect(screen.getAllByText('Tasks failed').length).toBeGreaterThanOrEqual(1);
  });
});

describe('EventFeed cause trace', () => {
  it('opens the cause → effect story for an event', async () => {
    const { CAUSE_TRACE_EVENT } = await import('../trace/bus');
    const seen: unknown[] = [];
    const listener = (event: Event) => seen.push((event as CustomEvent).detail);
    window.addEventListener(CAUSE_TRACE_EVENT, listener);
    const events = [{ at_ns: '5000000000', category: 'logistics' as const, severity: 'info' as const, text: 'drone-1 picked up task-1', entities: ['drone-1', 'task-1'] }];
    render(<EventFeed events={events} />);
    fireEvent.click(screen.getByRole('button', { name: 'Why did this happen?' }));
    window.removeEventListener(CAUSE_TRACE_EVENT, listener);
    expect(seen).toEqual([{ kind: 'event', atNs: '5000000000', text: 'drone-1 picked up task-1', entities: ['drone-1', 'task-1'] }]);
  });
});
