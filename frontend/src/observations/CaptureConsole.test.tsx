import {act,cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {I18nProvider} from '../i18n/I18nProvider';
import {CaptureConsole} from './CaptureConsole';
import {CaptureRenderer,type CaptureInput,type CapturePreparation,type CaptureReady} from './capture';
afterEach(()=>{cleanup();vi.restoreAllMocks();localStorage.clear();});
const input={service_run_id:'run',request:{request_id:'first'}} as CaptureInput;

it('reuses one renderer across requests and clears prior success after a failed capture',async()=>{
 const dispose=vi.spyOn(CaptureRenderer.prototype,'dispose');
 const draw=vi.spyOn(CaptureRenderer.prototype,'render');
 draw.mockResolvedValueOnce({request:input.request,png_data_url:'data:image/png;base64,test'}).mockRejectedValueOnce(Error('Capture timed out'));
 render(<I18nProvider><CaptureConsole apiBase="http://localhost" assetManifestUrl="http://localhost/manifest"/></I18nProvider>);
 await act(async()=>{await window.aeroCapture!.render(input);});
 expect(screen.getByText('Captured first')).toBeTruthy();
 await act(async()=>{await expect(window.aeroCapture!.render({...input,request:{request_id:'second'}})).rejects.toThrow('Capture timed out');});
 expect(screen.queryByText('Captured first')).toBeNull();
 expect(screen.getByRole('alert').textContent).toBe('Capture timed out');
 expect(draw).toHaveBeenCalledTimes(2);expect(draw.mock.instances[0]).toBe(draw.mock.instances[1]);
 cleanup();expect(dispose).toHaveBeenCalledTimes(1);expect(window.aeroCapture).toBeUndefined();
});

it('keeps the error receipt in English while showing the Chinese capture diagnostic',async()=>{
 localStorage.setItem('aeroagentsim.locale','zh-CN');
 vi.spyOn(CaptureRenderer.prototype,'render').mockRejectedValueOnce(Error('Capture timed out'));
 render(<I18nProvider><CaptureConsole apiBase="http://localhost" assetManifestUrl="http://localhost/manifest"/></I18nProvider>);
 await act(async()=>{await expect(window.aeroCapture!.render(input)).rejects.toThrow('Capture timed out');});
 expect(screen.getByRole('alert').textContent).toBe('拍摄超时');
 expect(screen.getByText('等待已记录的拍摄请求')).toBeTruthy();
});

it('initializes the same camera without publishing a photo and clears stale success when initialization fails',async()=>{
 const preparation={service_run_id:'run',asset_digest:'assets/v1',header:{runId:'run'},asset_manifest:{asset_id:'assets/v1'},oblique:true} as CapturePreparation;
 const ready:CaptureReady={contract:'aeroagentsim.viewer-assets-ready/v1',service_run_id:'run',asset_digest:'assets/v1'};
 const prepare=vi.spyOn(CaptureRenderer.prototype,'prepare').mockResolvedValueOnce(ready).mockRejectedValueOnce(Error('Capture timed out'));
 const draw=vi.spyOn(CaptureRenderer.prototype,'render').mockResolvedValue({request:input.request,png_data_url:'data:image/png;base64,test'});
 render(<I18nProvider><CaptureConsole apiBase="http://localhost" assetManifestUrl="http://localhost/manifest"/></I18nProvider>);
 await act(async()=>{expect(await window.aeroCapture!.prepare(preparation)).toEqual(ready);});
 expect(draw).not.toHaveBeenCalled();expect(screen.getByText('Waiting for recorded capture request')).toBeTruthy();
 await act(async()=>{await window.aeroCapture!.render(input);});expect(screen.getByText('Captured first')).toBeTruthy();
 await act(async()=>{await expect(window.aeroCapture!.prepare(preparation)).rejects.toThrow('Capture timed out');});
 expect(screen.queryByText('Captured first')).toBeNull();expect(screen.getByRole('alert').textContent).toBe('Capture timed out');
 expect(prepare.mock.instances[0]).toBe(draw.mock.instances[0]);expect(prepare).toHaveBeenCalledTimes(2);
});
