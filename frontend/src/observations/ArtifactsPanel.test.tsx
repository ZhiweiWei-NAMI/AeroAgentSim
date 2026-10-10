import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
afterEach(()=>{cleanup();localStorage.clear();vi.restoreAllMocks();});
import { ArtifactsPanel } from './ArtifactsPanel';
import { RunsApi } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureCommits,fixtureHeader } from '../behaviours/extension-fixture';
it('retains actual storage failure and only seeks a recorded artifact source cut',async()=>{
 const api=new RunsApi('http://api'),request=vi.spyOn(api,'request').mockImplementation(async path=>{if(path.includes('/configuration'))return {scenario:{engines:{}}};throw Error('HTTP 409: corrupt blob');});
 const store=new TemporalFeedStore(fixtureHeader);fixtureCommits.forEach(row=>store.ingest(row));store.seek('10',3);
 const seek=vi.fn();render(<ArtifactsPanel api={api} runId="run" store={store} onSeek={seek}/>);
 await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('corrupt blob'));expect(screen.queryByText('No stored artifacts.')).toBeNull();
 request.mockResolvedValueOnce([{digest:'a'.repeat(64),byte_count:10,renderer_mode:'browser',request:{request_id:'photo',source_cut:{index:3,instant:[10,2]}}}]);
 fireEvent.click(screen.getByText('Refresh stored artifacts'));await screen.findByText('Seek photo moment');fireEvent.click(screen.getByText('Seek photo moment'));expect(seek).toHaveBeenCalledWith(3);
});
it('localizes photo guidance and storage status while preserving request IDs and exact source moments',async()=>{
 localStorage.setItem('aeroagentsim.locale','zh-CN');
 const api=new RunsApi('http://api');
 vi.spyOn(api,'request').mockImplementation(async path=>path.includes('/configuration')?{scenario:{engines:{}}}:[{digest:'b'.repeat(64),byte_count:10,renderer_mode:'browser',request:{request_id:'photo.raw.id',source_cut:{index:3,instant:[10,2]}}}]);
 const store=new TemporalFeedStore(fixtureHeader);fixtureCommits.forEach(row=>store.ingest(row));store.seek('10',3);
 const seek=vi.fn();render(<I18nProvider><ArtifactsPanel api={api} runId="run" store={store} onSeek={seek}/></I18nProvider>);
 await screen.findByText('photo.raw.id');
 expect(screen.getByText(/渲染方式：浏览器/)).toBeTruthy();
 expect(screen.getByText(/此时刻尚未记录/)).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'跳到拍摄时刻'}));expect(seek).toHaveBeenCalledWith(3);
 expect(screen.getByRole('button',{name:'打开照片'})).toBeTruthy();
 expect(screen.queryByText('Open photo')).toBeNull();
});
