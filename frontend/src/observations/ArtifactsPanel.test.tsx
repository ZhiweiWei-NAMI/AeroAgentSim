import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
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
 fireEvent.click(screen.getByText('Refresh stored artifacts'));await screen.findByText('Seek photo source cut 3');fireEvent.click(screen.getByText('Seek photo source cut 3'));expect(seek).toHaveBeenCalledWith(3);
});
