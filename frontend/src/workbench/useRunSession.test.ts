import { expect, it } from 'vitest';
import { emptyCommit, fixtureCommits, fixtureHeader } from '../behaviours/extension-fixture';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from '../viewport/clock';
import { runClockTime } from './useRunSession';

it('keeps the live simulation clock at the recorded head while browsing an earlier moment', () => {
  const store = new TemporalFeedStore(fixtureHeader); fixtureCommits.forEach(commit => store.ingest(commit));
  const clock = new PlaybackClock(fixtureHeader.start.ns, fixtureCommits.at(-1)!.at.ns); clock.seek(fixtureHeader.start.ns);
  expect(runClockTime('live', { store, clock })).toBe(fixtureCommits.at(-1)!.at.ns);
  expect(runClockTime('replay', { store, clock })).toBe(fixtureHeader.start.ns);
});
it('shows the reported input boundary even if the journal has no event at that boundary', () => {
  const store = new TemporalFeedStore(fixtureHeader); fixtureCommits.slice(0, 3).forEach(commit => store.ingest(commit));
  const clock = new PlaybackClock(fixtureHeader.start.ns, fixtureCommits.at(-1)!.at.ns);
  const waiting = { stream_ids: ['operator'], at_ns: '1000000000' };
  expect(runClockTime('live', { store, clock, waiting })).toBe(waiting.at_ns);
  expect(runClockTime('live', { store, clock, waiting, reportedBoundaryNs: '2000000000' })).toBe('2000000000');
  expect(runClockTime('replay', { store, clock, waiting })).toBe(clock.ns);
});
it('retains the observed input boundary after pausing or stopping and yields to later real commits', () => {
  const store = new TemporalFeedStore(fixtureHeader), clock = new PlaybackClock(fixtureHeader.start.ns, '1000000000');
  expect(runClockTime('live', { store, clock, reportedBoundaryNs: '1000000000' })).toBe('1000000000');
  store.ingest(emptyCommit(1, '2000000000'));
  expect(runClockTime('live', { store, clock, reportedBoundaryNs: '1000000000' })).toBe('2000000000');
  expect(runClockTime('replay', { store, clock, reportedBoundaryNs: '1000000000' })).toBe(clock.ns);
});
