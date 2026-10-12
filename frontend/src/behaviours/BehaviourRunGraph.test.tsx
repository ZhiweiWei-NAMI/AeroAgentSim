import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { BehaviourRunGraph } from './BehaviourRunGraph';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureHeader, fixtureCommits, task } from './extension-fixture';
vi.mock('./EntityGraph',()=>({EntityGraph:({store}:{store:TemporalFeedStore})=><div data-testid="topology-count">{store.entities.size}</div>}));
it('links recorded truth and chain transitions to exact entity generation and journal cut',()=>{
 const store=new TemporalFeedStore(fixtureHeader); fixtureCommits.forEach(commit=>store.ingest(commit));store.seek('10',2);
 const select=vi.fn(),seek=vi.fn(); const view=render(<BehaviourRunGraph store={store} selected={task} onSelect={select} onSeek={seek}/>);
 fireEvent.click(screen.getByText(/Conditions and event chains/));
 expect(screen.getByText('unknown · required input')).toBeTruthy();
 expect(screen.getAllByText(/required source history missing/)[0]).toBeTruthy();
 fireEvent.click(screen.getAllByText('Task · Task 1')[0]);expect(select).toHaveBeenCalledWith(task);
 store.seek('10',3);view.rerender(<BehaviourRunGraph store={store} selected={task} onSelect={select} onSeek={seek}/>);
 fireEvent.click(screen.getByText(/Seek launch → moving/));expect(seek).toHaveBeenCalledWith(3);
 fireEvent.change(screen.getByLabelText('Graph directory'),{target:{value:'tasks'}});expect(screen.getByTestId('topology-count').textContent).toBe('1');
});
it('names same-labelled types by vocabulary and lets a base type keep its specialisations',()=>{
 const header={...fixtureHeader,types:[...fixtureHeader.types,{typeId:'oo:Task',displayName:'Task',ancestors:[],directory:'tasks'}].map(row=>row.typeId==='task'?{...row,ancestors:['oo:Task','model']}:row)};
 const store=new TemporalFeedStore(header); fixtureCommits.forEach(commit=>store.ingest(commit));store.seek('10',2);
 render(<BehaviourRunGraph store={store} selected={undefined} onSelect={vi.fn()} onSeek={vi.fn()}/>);
 const labels=[...(screen.getByLabelText('Graph type') as HTMLSelectElement).options].map(option=>option.text);
 expect(labels).toContain('Task · oo'); expect(labels).toContain('Task · task');
 fireEvent.change(screen.getByLabelText('Graph type'),{target:{value:'oo:Task'}});expect(screen.getByTestId('topology-count').textContent).toBe('1');
});
