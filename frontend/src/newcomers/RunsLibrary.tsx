import {useMemo,useState} from 'react';
import {Alert,Button,Space,Table,Tag} from 'antd';
import {Link,useNavigate} from 'react-router-dom';
import type {RunInfo} from '../feeds/http';
import {PageHeader,PageState} from '../console/PageState';
import {readableLabel} from '../pages/inspection-format';
import {TemplateApi} from './api';
import {CompareRuns} from './CompareRuns';
import './simple.css';
export function RunsLibrary({runs,loaded,error,base,inspect=false,onRefresh,compare}:{runs:RunInfo[];loaded:boolean;error?:string;base:string;inspect?:boolean;onRefresh:()=>void;compare?:string[]}){
 const api=useMemo(()=>new TemplateApi(base),[base]);
 const navigate=useNavigate(),[selected,setSelected]=useState<React.Key[]>([]);
 const home=`/?api=${encodeURIComponent(base)}`,suffix=`?api=${encodeURIComponent(base)}`;
 if(compare)return <div className="console-page" data-testid="runs-compare"><CompareRuns api={api} ids={compare} onBack={()=>navigate(`/runs${suffix}`)}/></div>;
 const open=(run:RunInfo,live:boolean)=>`/${inspect?'inspect':'runs'}/${encodeURIComponent(run.id)}${suffix}&mode=${live?'live':'replay'}&view=simple${run.template_id?`&template=${encodeURIComponent(run.template_id)}`:''}`;
 return <div className="console-page" data-testid={inspect?'inspect-page':'runs-page'}>
  <PageHeader eyebrow={inspect?'Watch again':'Your experiments'} title={inspect?'Inspect a run':'Runs'} description={inspect?'Choose a run to explore its events, results and recorded city.':'Start a scenario, then compare what changes when you try a different setting.'}/>
  <div className="run-list-tools"><Space><Button type="primary" data-testid="new-run" onClick={()=>navigate(home)}>New run</Button><Button onClick={onRefresh}>Refresh</Button></Space>{!inspect&&<div className="run-list-selections"><span>{selected.length?`${selected.length} selected`:'Choose 2–4 runs to compare'}</span><Button data-testid="compare-runs" disabled={selected.length<2||selected.length>4} onClick={()=>navigate(`/runs${suffix}&compare=${selected.map(id=>encodeURIComponent(String(id))).join(',')}`)}>Compare</Button></div>}</div>
  {error&&<Alert type="error" message="Could not load runs" description={error} action={<Button onClick={onRefresh}>Try again</Button>}/>}
  {!loaded&&!error&&<PageState kind="loading" title="Loading your runs"/>}
  {loaded&&!runs.length&&<div><PageState kind="empty" title={inspect?'Nothing to replay yet':'Start your first experiment'} description="Pick a scenario from Home. It comes with settings that are ready to run."/><Link className="console-btn console-btn-primary" to={home}>Pick a scenario</Link></div>}
  {!!runs.length&&<Table<RunInfo> rowKey="id" dataSource={[...runs].reverse()} pagination={{pageSize:10,hideOnSinglePage:true}} rowSelection={inspect?undefined:{selectedRowKeys:selected,onChange:setSelected,getCheckboxProps:run=>({disabled:selected.length>=4&&!selected.includes(run.id),'aria-label':`Compare ${run.template_title??readableLabel(run.scenario)}`})}} columns={[
   {title:'Scenario',render:(_,run)=><Link to={open(run,!['completed','stopped','faulted','interrupted'].includes(run.status))}>{run.template_title??readableLabel(run.scenario)}</Link>},
   {title:'Status',render:(_,run)=><Tag color={run.status==='completed'?'green':run.status==='faulted'?'red':'blue'}>{run.status==='waiting_for_input'?'Waiting for your event':readableLabel(run.status)}</Tag>},
   {title:'Key results',render:(_,run)=>run.kpis?Object.entries(run.kpis).slice(0,2).map(([key,item])=><div key={key}>{item.label}: <strong>{item.value===null?'Not measured':`${Number(item.value.toFixed(2))} ${item.unit}`}</strong></div>):<span className="console-muted">Results appear as the run progresses</span>},
   {title:'Algorithms',render:(_,run)=>run.algorithms&&Object.keys(run.algorithms).length?Object.values(run.algorithms).map(readableLabel).join(' · '):<span className="console-muted">Scenario defaults</span>},
   {title:'Open',render:(_,run)=><Space><Link to={open(run,true)}>Live</Link><Link to={open(run,false)}>Replay</Link></Space>},
  ]}/>}
 </div>;
}
