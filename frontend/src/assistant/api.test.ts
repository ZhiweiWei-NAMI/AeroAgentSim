import { describe, expect, it } from 'vitest';
import { compiledParts, type PlanVersion } from './api';

const plan = (ok: boolean) => ({
  version: 2, created_at: '', note: '', base: {}, title: 't', summary: '', operations: [], assumptions: [], open_questions: [],
  compile: { ok, errors: ok ? [] : [{ message: 'bad' }], warnings: [] }, validation: { state: ok ? 'pending' : 'invalid' },
  scene: {}, interactions: {}, expectations: [], diff_base: {}, diff_previous: null,
}) as unknown as PlanVersion;

describe('compiledParts', () => {
  it('drops the empty scene parts of a draft that did not compile', () => {
    const shown = compiledParts(plan(false));
    expect(shown.scene).toBeUndefined();
    expect(shown.interactions).toBeUndefined();
    expect(shown.compile.errors).toHaveLength(1);
  });
  it('keeps a compiled plan as it is', () => {
    const compiled = plan(true);
    expect(compiledParts(compiled)).toBe(compiled);
  });
});
