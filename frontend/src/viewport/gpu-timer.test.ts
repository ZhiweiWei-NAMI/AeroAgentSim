import { describe, expect, it, vi } from 'vitest';
import { GpuTimer } from './gpu-timer';

function context(supported=true) {
  let disjoint=false,ready=false;
  const ext={TIME_ELAPSED_EXT:1,GPU_DISJOINT_EXT:2};
  const gl={ QUERY_RESULT_AVAILABLE:3,QUERY_RESULT:4,
    getExtension:()=>supported?ext:null,getParameter:()=>disjoint,
    createQuery:vi.fn(()=>({})),deleteQuery:vi.fn(),beginQuery:vi.fn(),endQuery:vi.fn(),
    getQueryParameter:(_query:unknown,key:number)=>key===3?ready:1_250_000,
  };
  return {gl:gl as unknown as WebGL2RenderingContext,mocks:gl,
    ready:()=>{ready=true;},disjoint:()=>{disjoint=true;}};
}
describe('asynchronous GPU evidence',()=>{
  it('leaves unsupported timings absent',()=>{
    const c=context(false),timer=new GpuTimer(c.gl);timer.begin();timer.end();
    expect(timer.available).toBe(false);expect(timer.milliseconds).toBeUndefined();
    expect(c.mocks.createQuery).not.toHaveBeenCalled();
  });
  it('reads only ready results and bounds the queue on a busy GPU',()=>{
    const c=context(),timer=new GpuTimer(c.gl);
    for(let i=0;i<10;i++){timer.begin();timer.end();}
    expect(timer.milliseconds).toBeUndefined();expect(c.mocks.createQuery).toHaveBeenCalledTimes(4);
    c.ready();timer.begin();timer.end();
    expect(timer.milliseconds).toBe(1.25);expect(timer.serial).toBe(4);
    timer.dispose();expect(c.mocks.deleteQuery).toHaveBeenCalledTimes(5);
  });
  it('rejects disjoint samples and clears previously reported timing',()=>{
    const c=context(),timer=new GpuTimer(c.gl);timer.begin();timer.end();c.ready();timer.begin();timer.end();
    expect(timer.milliseconds).toBe(1.25);
    c.disjoint();timer.begin();timer.end();expect(timer.milliseconds).toBeUndefined();
    expect(timer.serial).toBe(1);timer.dispose();
  });
});
