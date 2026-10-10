import {useEffect,useMemo,useState} from 'react';
import {Alert,Button,Space,Table,Tag} from 'antd';
import {Link,useNavigate} from 'react-router-dom';
import {isTerminalStatus,type RunInfo} from '../feeds/http';
import {PageHeader,PageState} from '../console/PageState';
import {readableLabel} from '../pages/inspection-format';
import {useI18n} from '../i18n/I18nProvider';
import {localized} from '../i18n/translate';
import {TemplateApi,type AlgorithmDescriptor} from './api';
import {metricLabel} from './metric-help';
import {runStatusLabel} from './presentation';
import {CompareRuns} from './CompareRuns';
import './simple.css';
export function RunsLibrary({runs,loaded,error,base,inspect=false,onRefresh,compare}:{runs:RunInfo[];loaded:boolean;error?:string;base:string;inspect?:boolean;onRefresh:()=>void;compare?:string[]}){
 const {t,locale}=useI18n();
 const api=useMemo(()=>new TemplateApi(base),[base]);
 const navigate=useNavigate(),[selected,setSelected]=useState<React.Key[]>([]),[algorithms,setAlgorithms]=useState<AlgorithmDescriptor[]>([]);
 const hasAlgorithms=runs.some(run=>run.algorithms&&Object.keys(run.algorithms).length);
 useEffect(()=>{setSelected(current=>current.filter(id=>runs.some(run=>run.id===id)));},[runs]);
 useEffect(()=>{setAlgorithms([]);if(locale!=='zh-CN'||!hasAlgorithms)return;const abort=new AbortController();void api.algorithms(abort.signal).then(rows=>{if(!abort.signal.aborted)setAlgorithms(rows);}).catch(()=>{/* Saved algorithm ids remain readable if metadata is unavailable. */});return()=>abort.abort();},[api,locale,hasAlgorithms]);
 const home=`/?api=${encodeURIComponent(base)}`,suffix=`?api=${encodeURIComponent(base)}`;
 if(compare)return <div className="console-page" data-testid="runs-compare"><CompareRuns api={api} ids={compare} onBack={()=>navigate(`/runs${suffix}`)}/></div>;
 const open=(run:RunInfo,live:boolean)=>`/${inspect?'inspect':'runs'}/${encodeURIComponent(run.id)}${suffix}&mode=${live?'live':'replay'}&view=simple${run.template_id?`&template=${encodeURIComponent(run.template_id)}`:''}`;
 const runTitle=(run:RunInfo)=>localized(run,'template_title',locale)??readableLabel(run.scenario,locale);
 const algorithmLabel=(id:string)=>{const row=algorithms.find(row=>row.id===id);return locale==='zh-CN'&&row?localized(row,'label',locale):readableLabel(id,locale);};
 return <div className="console-page" data-testid={inspect?'inspect-page':'runs-page'}>
  <PageHeader eyebrow={inspect?t('Watch again'):t('Your experiments')} title={inspect?t('Inspect a run'):t('Runs')} description={inspect?t('Choose a run to explore its events, results and recorded city.'):t('Start a scenario, then compare what changes when you try a different setting.')}/>
  <div className="run-list-tools"><Space><Button type="primary" data-testid="new-run" onClick={()=>navigate(home)}>{t('New run')}</Button><Button onClick={onRefresh}>{t('Refresh')}</Button></Space>{!inspect&&<div className="run-list-selections"><span>{selected.length?t('{count} selected',{count:selected.length}):t('Choose 2–4 runs to compare')}</span><Button data-testid="compare-runs" disabled={selected.length<2||selected.length>4} onClick={()=>navigate(`/runs${suffix}&compare=${selected.map(id=>encodeURIComponent(String(id))).join(',')}`)}>{t('Compare')}</Button></div>}</div>
  {error&&<Alert type="error" message={t('Could not load runs')} description={error} action={<Button onClick={onRefresh}>{t('Try again')}</Button>}/>}
  {!loaded&&!error&&<PageState kind="loading" title={t('Loading your runs')}/>}
  {loaded&&!runs.length&&<div><PageState kind="empty" title={inspect?t('Nothing to replay yet'):t('Start your first experiment')} description={t('Pick a scenario from Home. It comes with settings that are ready to run.')}/><Link className="console-btn console-btn-primary" to={home}>{t('Pick a scenario')}</Link></div>}
  {!!runs.length&&<Table<RunInfo> rowKey="id" dataSource={[...runs].reverse()} pagination={{pageSize:10,hideOnSinglePage:true}} locale={{emptyText:t('No runs yet')}} rowSelection={inspect?undefined:{selectedRowKeys:selected,onChange:setSelected,getCheckboxProps:run=>({disabled:selected.length>=4&&!selected.includes(run.id),'aria-label':t('Compare {title}',{title:runTitle(run)})})}} columns={[
   {title:t('Scenario'),render:(_,run)=><Link to={open(run,!isTerminalStatus(run.status))}>{runTitle(run)}</Link>},
   {title:t('Status'),render:(_,run)=><Tag color={run.status==='completed'?'green':run.status==='faulted'?'red':'blue'}>{runStatusLabel(run.status,locale)}</Tag>},
   {title:t('Key results'),render:(_,run)=>run.kpis?Object.entries(run.kpis).slice(0,2).map(([key,item])=><div key={key}>{metricLabel(key,item.label,locale,item.label_zh)}: <strong>{item.value===null?t('Not measured'):`${Number(item.value.toFixed(2))}${item.unit==='count'?'':` ${item.unit}`}`}</strong></div>):<span className="console-muted">{t('Results appear as the run progresses')}</span>},
   {title:t('Algorithms'),render:(_,run)=>run.algorithms&&Object.keys(run.algorithms).length?Object.values(run.algorithms).map(algorithmLabel).join(' · '):<span className="console-muted">{t('Scenario defaults')}</span>},
   {title:t('Open'),render:(_,run)=><Space>{!isTerminalStatus(run.status)&&<Link to={open(run,true)}>{t('Live')}</Link>}<Link to={open(run,false)}>{t('Replay')}</Link></Space>},
  ]}/>}
 </div>;
}
