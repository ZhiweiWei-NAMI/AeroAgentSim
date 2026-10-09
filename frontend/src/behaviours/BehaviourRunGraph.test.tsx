import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { BehaviourRunGraph } from './BehaviourRunGraph';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureHeader, fixtureCommits, task } from './extension-fixture';
vi.mock('../viewport/NonspatialViews',()=>({NonspatialViews:({store}:{store:TemporalFeedStore})=><div data-testid="topology-count">{store.entities.size}</div>}));
it('links recorded truth and chain transitions to exact entity generation and journal cut',()=>{
 const store=new TemporalFeedStore(fixtureHeader); fixtureCommits.forEach(commit=>store.ingest(commit));store.seek('10',2);
 const select=vi.fn(),seek=vi.fn(); const view=render(<BehaviourRunGraph store={store} selected={task} onSelect={select} onSeek={seek}/>);
 expect(screen.getByText('unknown · required_input')).toBeTruthy();
 expect(screen.getAllByText(/required source history missing/)[0]).toBeTruthy();
 fireEvent.click(screen.getAllByText('task → task-1 · g1')[0]);expect(select).toHaveBeenCalledWith(task);
 store.seek('10',3);view.rerender(<BehaviourRunGraph store={store} selected={task} onSelect={select} onSeek={seek}/>);
 fireEvent.click(screen.getByText(/Seek launch → moving/));expect(seek).toHaveBeenCalledWith(3);
 fireEvent.change(screen.getByLabelText('Graph directory'),{target:{value:'tasks'}});expect(screen.getByTestId('topology-count').textContent).toBe('1');
});
