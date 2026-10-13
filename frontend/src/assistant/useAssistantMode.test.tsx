import { act,cleanup,renderHook,waitFor } from '@testing-library/react';
import { afterEach,beforeEach,expect,it,vi } from 'vitest';
import { useAssistantMode } from './useAssistantMode';
import type { AssistantSession } from './useAssistant';
import type { PlanPreview,PlanVersion,Session } from './api';
import type { Project } from '../workbench/api';

const server=vi.hoisted(()=>({preview:vi.fn(),plan:vi.fn(),story:vi.fn(),assistant:undefined as unknown as AssistantSession}));
vi.mock('./api',async importOriginal=>({...await importOriginal<typeof import('./api')>(),AssistantApi:class{preview=server.preview;plan=server.plan;story=server.story;}}));
vi.mock('./useAssistant',()=>({useAssistantSession:()=>server.assistant,useConnection:()=>({})}));
beforeEach(()=>{server.preview.mockReset();server.plan.mockReset();server.story.mockReset().mockResolvedValue({run_id:'run-project-v1',expectations:[],events:[]});});
afterEach(cleanup);
const plan:PlanVersion={version:1,created_at:'',note:'',base:{workspace:'w',revision:1},title:'Plan',summary:'',operations:[],assumptions:[],open_questions:[],compile:{ok:true,errors:[],warnings:[]},validation:{state:'valid'},scene:{}};
const applied={plan_version:1,workspace_id:'w',version:1,revision:2,applied_at:'',report:{created:[],changed:[],removed:[]}};
const session:Session={id:'s',created_at:'',updated_at:'',workspace_id:'w',title:'Scene',busy:false,last_seq:0,events:[],plan,plan_versions:[],alternatives:[],applied:[applied]};
const original={entities:[{id:'pad',facts:{'lowalt.site_capacity':2,'lowalt.site_position':[0,0,0]}}]};
function agent(saved=true){server.assistant={session:{...session,applied:saved?[applied]:[]},plan,applied:saved?applied:undefined,busy:false,events:[],send:vi.fn(),choose:vi.fn(),apply:vi.fn(),cancel:vi.fn(),refresh:vi.fn(),error:undefined,pending:undefined};}
function project(scenario=original,dirty=false):Project{return {id:'w',name:'World',revision:3,updated_at:'',scenario,dirty,latest_version:{version:2,revision:3,digest:'actual-fixture',created_at:'',validation:{valid:true,errors:[]}}};}
const args={apiBase:'http://api',open:true,sessionId:'s',projectId:'w',locale:'en-US',terminal:false,update:vi.fn()};

it('T2-AS-007: supplies real draft field changes after a Form edit while retaining the applied stage',async()=>{
  agent();server.preview.mockResolvedValue({name:'Plan',scenario:original});
  const edited=project({entities:[{id:'pad',facts:{'lowalt.site_capacity':4,'lowalt.site_position':[50,0,0]}}]},true);
  const {result}=renderHook(()=>useAssistantMode({...args,project:edited}));
  await waitFor(()=>expect(result.current.assistant.draftState?.changes).toHaveLength(2));
  expect(result.current.stage).toBe('configured');
  expect(result.current.previewWorkspace).toBeUndefined();
  expect(result.current.overlay).toBeUndefined();
  expect(result.current.assistant.draftState?.latestVersion).toBe(2);
  expect(result.current.assistant.draftState?.dirty).toBe(true);
  expect(result.current.assistant.draftState?.changes?.map(row=>row.path.at(-1))).toEqual(['lowalt.site_capacity','lowalt.site_position']);
});
it('does not mark equal research fields changed on refresh or recompute the preview for a render identity change',async()=>{
  agent();server.preview.mockResolvedValue({name:'Plan',scenario:original});
  const {result,rerender}=renderHook(({draft})=>useAssistantMode({...args,project:draft}),{initialProps:{draft:project()}});
  await waitFor(()=>expect(result.current.assistant.draftState?.changes).toEqual([]));
  rerender({draft:project(JSON.parse(JSON.stringify(original)))});
  await act(async()=>Promise.resolve());
  expect(result.current.assistant.draftState?.changes).toEqual([]);
  expect(server.preview).toHaveBeenCalledTimes(1);
});
it('T2-R4-002: uses server fixture digests and recomputes when file bytes change without a scenario edit',async()=>{
  agent();
  const planPath='/plan/files/decisions.json', workspacePath='/workspace/fixtures/decisions.json';
  const scenario=(fixture_path:string)=>({engines:{decisions:{config:{fixture_path}}}});
  server.preview.mockResolvedValue({name:'Plan',scenario:scenario(planPath),fixture_digests:{[planPath]:'sha256:same'}});
  const saved:Project={...project(),scenario:scenario(workspacePath),fixture_digests:{[workspacePath]:'sha256:same'}};
  const {result,rerender}=renderHook(({draft})=>useAssistantMode({...args,project:draft}),{initialProps:{draft:saved}});
  await waitFor(()=>expect(result.current.assistant.draftState?.changes).toEqual([]));
  rerender({draft:{...saved,fixture_digests:{[workspacePath]:'sha256:changed'}}});
  expect(result.current.assistant.draftState?.changes).toEqual([
    {path:['engines','decisions','config','fixture_path'],before:'sha256:same',after:'sha256:changed'},
  ]);
  expect(server.preview).toHaveBeenCalledTimes(1);
});
it('T4-R2-016: forwards region preview geometry and the new scene instead of inheriting the old scene',async()=>{
  agent(false);server.assistant={...server.assistant,plan:{...plan,operations:[{id:'region',op:'set_region',params:{extract:'wujiaochang'}}]}};
  const old=project();
  const preview:PlanPreview={name:'New area',scenario:original,region:{extract:'wujiaochang',bounds:[121.49,31.29,121.51,31.31],alt:0,level_height_m:3.5},region_preview:{origin:{lon:121.5,lat:31.3,alt:0},bounds:[121.49,31.29,121.51,31.31],geometry:{buildings:{type:'FeatureCollection',features:[]},roads:[],truncated:false}}};
  server.preview.mockResolvedValue(preview);
  const {result}=renderHook(()=>useAssistantMode({...args,project:old}));
  await waitFor(()=>expect(result.current.assistant.planPreview?.region_preview).toEqual(preview.region_preview));
  expect(result.current.previewWorkspace?.region).toEqual(preview.region);
  expect(result.current.previewWorkspace?.scene).toBeUndefined();
});
it('shows a real preview failure instead of silently leaving an old preview on screen',async()=>{
  agent();server.preview.mockRejectedValue(Error('HTTP 500: Preview unavailable'));
  const {result}=renderHook(()=>useAssistantMode({...args,project:project()}));
  await waitFor(()=>expect(result.current.assistant.draftState?.error).toContain('Preview unavailable'));
  expect(result.current.assistant.draftState?.changes).toBeUndefined();
  expect(result.current.previewWorkspace).toBeUndefined();
});

it.each([false,true])('T2-R3-001: an unapplied revision keeps its proposal stage with an older run (terminal=%s)',async terminal=>{
  agent();
  const revision={...plan,version:3};
  server.assistant={...server.assistant,plan:revision,applied:undefined,session:{...session,plan:revision}};
  server.preview.mockResolvedValue({name:'Revised plan',scenario:original});
  const {result}=renderHook(()=>useAssistantMode({...args,project:project(),runId:'run-project-v1',workspaceVersion:1,terminal}));
  await waitFor(()=>expect(result.current.assistant.planPreview?.version).toBe(3));
  expect(result.current.stage).toBe('proposed');
  expect(result.current.previewWorkspace).toBeDefined();
  expect(result.current.assistant.session?.applied).toEqual([applied]);
  expect(server.story).not.toHaveBeenCalled();
});

it('keeps the pinned run stage for the plan actually applied to that run',async()=>{
  agent();
  server.preview.mockResolvedValue({name:'Applied plan',scenario:original});
  const {result}=renderHook(()=>useAssistantMode({...args,project:project(),runId:'run-project-v1',workspaceVersion:1,terminal:true}));
  await waitFor(()=>expect(result.current.story?.run_id).toBe('run-project-v1'));
  await waitFor(()=>expect(result.current.assistant.planPreview?.version).toBe(1));
  expect(result.current.stage).toBe('running');
});
