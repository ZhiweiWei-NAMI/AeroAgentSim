import {expect,it,vi} from 'vitest';
import {awaitRunConfiguration,type RunsApi} from './http';
it('waits for the real committed run identity and retains unrelated configuration failures',async()=>{
 vi.useFakeTimers();
 try{
  const identity={kernel_run_id:'traffic-accident',epoch:'0'};
  const request=vi.fn().mockRejectedValueOnce(Error('HTTP 422: run WAL header is not committed yet')).mockResolvedValueOnce(identity);
  const pending=awaitRunConfiguration({request} as unknown as RunsApi,'new-run');
  await vi.runAllTimersAsync();expect(await pending).toBe(identity);expect(request).toHaveBeenCalledTimes(2);
  request.mockRejectedValueOnce(Error('HTTP 500: configuration corrupted'));
  await expect(awaitRunConfiguration({request} as unknown as RunsApi,'bad-run')).rejects.toThrow('configuration corrupted');
  expect(request).toHaveBeenCalledTimes(3);
 }finally{vi.useRealTimers();}
});
