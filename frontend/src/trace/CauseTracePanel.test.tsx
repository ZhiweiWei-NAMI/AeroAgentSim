import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { InspectionPanel } from '../pages/InspectionPanel';
import { CauseTracePanel } from './CauseTracePanel';
import { subscribeCauseTrace, openCauseTrace } from './bus';
import { explain } from './cause-trace';
import { incident, traceStore } from './trace-fixture';

it('tells the story in plain words and lets each follow-up continue the walk', () => {
  const store = traceStore(), open = vi.fn();
  const model = explain(store, { kind: 'event', text: 'Car stopped' })!;
  render(<CauseTracePanel store={store} model={model} canGoBack={false} onOpen={open} onBack={() => {}} onClose={() => {}} onSelectEntity={() => {}} onSeek={() => {}} />);
  for (const heading of ['What set it off', 'What ran', 'What changed in the shared state', 'What happened next']) expect(screen.getByText(heading)).toBeTruthy();
  expect(screen.getByText("Car 1's speed = 0")).toBeTruthy();
  expect(screen.getAllByText(/Only event chains may write this field/)).toHaveLength(1);
  expect(screen.getByText('false')).toBeTruthy(); expect(screen.getByText('true')).toBeTruthy();
  fireEvent.click(screen.getByText(/Demo Watch: Wait → Seen/));
  expect(open).toHaveBeenCalledWith({ kind: 'step', instanceId: 'chain:b', revision: '1' });
  expect(screen.queryByText(/receipt|invocation/i)).toBeNull();
});

it('lets other pages open a trace through the bus and the entity panel opens moments and changes', () => {
  const seen = vi.fn(), off = subscribeCauseTrace(seen);
  openCauseTrace({ kind: 'cut', cut: 4 });
  off(); openCauseTrace({ kind: 'cut', cut: 5 });
  expect(seen).toHaveBeenCalledTimes(1);
  expect(seen).toHaveBeenCalledWith({ kind: 'cut', cut: 4 });
  const store = traceStore(), open = vi.fn();
  render(<InspectionPanel store={store} selected={incident} onSeek={() => {}} onOpenTrace={open} />);
  fireEvent.click(screen.getByText('Car stopped'));
  expect(open.mock.calls[0][0]).toMatchObject({ kind: 'event', text: 'Car stopped' });
  fireEvent.click(screen.getByText(/by Demo Alert/));
  expect(open.mock.calls[1][0]).toMatchObject({ kind: 'change', fieldId: 'demo.incident.active', cut: 4 });
});
