import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { StagedFormWorkspace, useStagedForm, useStagedFormStatus } from './useStagedForm';

beforeEach(() => sessionStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const records = () => Object.keys(sessionStorage).filter(key => key.startsWith('aas.staged.')).map(key => ({ key, ...JSON.parse(sessionStorage.getItem(key)!) }));
const unload = () => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; };

it('preserves exact raw input across close/refresh and restores its revision base (T1-R3-003 / T3-R3-003 / T4-R2-009)', () => {
  const wrapper = ({ children }: { children: React.ReactNode }) => <StagedFormWorkspace.Provider value={{ id: 'research', revision: 7 }}>{children}</StagedFormWorkspace.Provider>;
  const first = renderHook(() => useStagedForm('research', 'orders.add', { count: '3', rate: '3' }), { wrapper });
  expect(records()).toHaveLength(0);
  expect(unload()).toBe(false);
  act(() => first.result.current.setValues({ count: '7', rate: '12.' }));
  expect(records()[0]).toMatchObject({ workspace: 'research', form: 'orders.add', revision: 7, values: { count: '7', rate: '12.' } });
  first.unmount();
  expect(unload()).toBe(true);
  const restored = renderHook(() => useStagedForm('research', 'orders.add', { count: '3', rate: '3' }, 8));
  expect(restored.result.current).toMatchObject({ values: { count: '7', rate: '12.' }, restored: true, dirty: true, baseRevision: 7 });
  act(() => restored.result.current.discard());
  expect(restored.result.current.values).toEqual({ count: '3', rate: '3' });
  expect(records()).toHaveLength(0);
  expect(unload()).toBe(false);
});

it('isolates workspaces and forms; applying one form keeps the other pending buffers', () => {
  const a = renderHook(() => useStagedForm('w', 'duration', { text: '600' }, 1));
  const b = renderHook(() => useStagedForm('w', 'run.duration', { text: '0.1' }, 1));
  const c = renderHook(() => useStagedForm('w.part', 'duration', { text: '600' }, 1));
  const status = renderHook(() => useStagedFormStatus('w'));
  act(() => { a.result.current.setValues({ text: '777' }); b.result.current.setValues({ text: '2' }); c.result.current.setValues({ text: '240' }); });
  expect(records()).toHaveLength(3);
  expect(status.result.current).toBe(true);
  act(() => a.result.current.clear());
  expect(records()).toHaveLength(2);
  act(() => b.result.current.discard());
  expect(records()).toHaveLength(1);
  expect(status.result.current).toBe(false);
  expect(c.result.current.values.text).toBe('240');
});

it('does not turn empty or malformed numeric text into defaults when restoring', () => {
  const first = renderHook(() => useStagedForm('w', 'position', { east: '0', north: '0' }, 3));
  act(() => first.result.current.setValues({ east: '', north: '1e' }));
  first.unmount();
  const second = renderHook(() => useStagedForm('w', 'position', { east: '0', north: '0' }, 3));
  expect(second.result.current.values).toEqual({ east: '', north: '1e' });
});

it('reports damaged recovery data and preserves the record until explicit Discard', () => {
  sessionStorage.setItem('aas.staged.w.3.position', '{broken');
  const form = renderHook(() => useStagedForm('w', 'position', { east: '' }, 3));
  expect(form.result.current.error).toBeTruthy();
  expect(form.result.current.restored).toBe(false);
  expect(sessionStorage.getItem('aas.staged.w.3.position')).toBe('{broken');
  act(() => form.result.current.discard());
  expect(form.result.current.error).toBeUndefined();
  expect(records()).toHaveLength(0);
});

it('reports a storage write failure and still warns after the form unmounts', () => {
  const form = renderHook(() => useStagedForm('w', 'run', { seed: '1' }, 3));
  const blocked = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw Error('Storage unavailable'); });
  act(() => form.result.current.setValues({ seed: '73' }));
  expect(form.result.current.error).toContain('Storage unavailable');
  expect(form.result.current.values.seed).toBe('73');
  form.unmount();
  expect(unload()).toBe(true);
  blocked.mockRestore();
  // Explicit discard also clears the in-memory unload protection after the failed write.
  const reopened = renderHook(() => useStagedForm('w', 'run', { seed: '1' }, 3));
  expect(reopened.result.current.values.seed).toBe('73');
  expect(reopened.result.current.error).toContain('Storage unavailable');
  act(() => reopened.result.current.discard());
  expect(unload()).toBe(false);
});

it('does not clear input entered after the confirmed snapshot was submitted', () => {
  const form = renderHook(() => useStagedForm('w', 'run', { seed: '1' }, 3));
  act(() => form.result.current.setValues({ seed: '73' }));
  const applying = form.result.current.values;
  act(() => form.result.current.setValues({ seed: '74' }));
  act(() => form.result.current.clear(applying));
  expect(form.result.current.values.seed).toBe('74');
  expect(records()[0].values.seed).toBe('74');
});

it('keeps pending region, relation and advanced-object inputs across unrelated revisions (T1-R4-001 / T4-R4-001)', () => {
  const forms = ['space.region', 'relations.add', 'objects.advanced'].map(key => renderHook(
    ({ revision }) => useStagedForm('universal', key, { raw: 'initial' }, revision), { initialProps: { revision: 3 } }));
  act(() => forms.forEach((form, index) => form.result.current.setValues({ raw: ['12.3', 'traffic.coordinator-candidate', '1e'][index] })));
  forms.forEach(form => form.rerender({ revision: 4 }));
  expect(forms.map(form => form.result.current.baseRevision)).toEqual([3, 3, 3]);
  expect(unload()).toBe(true);
  forms.forEach(form => form.unmount());
  expect(records().map(record => record.form).sort()).toEqual(['objects.advanced', 'relations.add', 'space.region']);
});

it('rejects missing or wrong-shaped recovered fields visibly, without erasing the recovery record', () => {
  const key = 'aas.staged.shape.3.region';
  const damaged = JSON.stringify({ workspace: 'shape', form: 'region', revision: 3,
    values: { alt: null }, baseline: { alt: '0', level: '3.5' } });
  sessionStorage.setItem(key, damaged);
  const form = renderHook(() => useStagedForm('shape', 'region', { alt: '0', level: '3.5' }, 3));
  expect(form.result.current.error).toContain('Invalid unsaved form record');
  expect(form.result.current.restored).toBe(false);
  expect(sessionStorage.getItem(key)).toBe(damaged);
  expect(unload()).toBe(true);
  act(() => form.result.current.discard());
});

it('a late completion from a previous workspace cannot clear or overwrite the current form', () => {
  const form = renderHook(({ id }) => useStagedForm(id, 'region', { alt: '0' }, 3), { initialProps: { id: 'first' } });
  act(() => form.result.current.setValues({ alt: '12.3' }));
  const previous = form.result.current;
  form.rerender({ id: 'second' });
  act(() => form.result.current.setValues({ alt: '12.3' }));
  act(() => { previous.clear(previous.values); previous.setValues({ alt: '99' }); });
  expect(form.result.current.values).toEqual({ alt: '12.3' });
  expect(records()).toHaveLength(2);
});
