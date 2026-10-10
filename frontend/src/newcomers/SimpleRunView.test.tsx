import {fireEvent,render,screen} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import {TemporalFeedStore} from '../feeds/temporal-store';
import {PlaybackClock} from '../viewport/clock';
import {fixtureHeader,emptyCommit} from '../behaviours/extension-fixture';
import {SimpleRunView} from './SimpleRunView';
import type {TemplateApi} from './api';
vi.mock('../viewport/ViewerPresentation',()=>({ViewerPresentation:()=> <div/>}));
it('resumes a paused live simulation and plays history only after leaving live follow',()=>{
 const store=new TemporalFeedStore(fixtureHeader);store.ingest(emptyCommit(1));store.seek('0');store.liveFollow=false;
 const control=vi.fn(async()=>{}),play=vi.fn(),noop=()=>{};
 const props={api:{kpis:vi.fn(async()=>({run_id:fixtureHeader.runId,final:{},series:[]}))} as unknown as TemplateApi,runId:fixtureHeader.runId,store,clock:new PlaybackClock('0','1000000000'),status:'paused',mode:'live',camera:'orbit' as const,onSelect:noop,onEvent:noop,onTick:noop,onError:noop,onSeek:noop,onPlay:play,onLive:noop,onControl:control,onInject:noop,hasInjection:false,onExpert:noop,followingLive:true,untilNs:'140000000000'};
 const {rerender}=render(<SimpleRunView {...props}/>);
 fireEvent.click(screen.getByRole('button',{name:'Resume simulation'}));expect(control).toHaveBeenCalledWith('resume');expect(play).not.toHaveBeenCalled();
 expect(screen.getByText('00:00.0 / 02:20.0')).toBeInTheDocument();
 rerender(<SimpleRunView {...props} followingLive={false}/>);
 fireEvent.click(screen.getByRole('button',{name:/^Play$/}));expect(play).toHaveBeenCalledTimes(1);expect(control).toHaveBeenCalledTimes(1);
});
