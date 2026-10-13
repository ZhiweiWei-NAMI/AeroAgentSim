import { afterEach, describe, expect, it, vi } from 'vitest';
import { AssistantApi, compiledParts, type PlanVersion } from './api';

afterEach(()=>{vi.unstubAllGlobals();});

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

it('T2-R2-003/T2-AS-015: transports run_id and the current locale in the supported message context',async()=>{
  const fetch=vi.fn(async()=>({ok:true,text:async()=>'{"turn":1,"seq":2}'}));vi.stubGlobal('fetch',fetch);
  await new AssistantApi('http://api').send('session/id','Summarise this run.',{run_id:'actual-run',locale:'en-US'});
  expect(fetch).toHaveBeenCalledTimes(1);
  const [url,options]=fetch.mock.calls[0] as unknown as [string,RequestInit];
  expect(url).toBe('http://api/v1/assistant/sessions/session%2Fid/messages');
  expect(JSON.parse(String(options.body))).toEqual({text:'Summarise this run.',context:{run_id:'actual-run',locale:'en-US'}});
});
