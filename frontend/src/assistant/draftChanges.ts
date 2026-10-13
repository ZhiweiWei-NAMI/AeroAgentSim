import type { PlanPreview } from './api';
import type { Workspace } from '../studio/api';

export interface DraftChange { path: string[]; before: unknown; after: unknown }
const record = (value: unknown): value is Record<string,unknown> => !!value && typeof value==='object' && !Array.isArray(value);

/** Compare research fields, independent of object identity, key order and entity order. */
export function draftChanges(before: Record<string,unknown>, after: Record<string,unknown>): DraftChange[] {
  const changes: DraftChange[]=[];
  const compare=(a: unknown,b: unknown,path: string[])=>{
    if(Object.is(a,b))return;
    if(record(a)&&record(b)){
      for(const key of [...new Set([...Object.keys(a),...Object.keys(b)])].sort()){
        const beforeEngine=record(before.engines)?before.engines[path[1]]:undefined;
        const afterEngine=record(after.engines)?after.engines[path[1]]:undefined;
        // The portable behaviours are authoritative. Compiled IR packages are
        // recreated by the loader and carry diagnostic paths, not draft edits.
        if(key==='packages' && path.length===3 && path[0]==='engines' && path[2]==='config'
          && record(beforeEngine) && record(afterEngine) && beforeEngine.plugin==='behaviour' && afterEngine.plugin==='behaviour'
          && Array.isArray(before.behaviours) && Array.isArray(after.behaviours))continue;
        compare(a[key],b[key],[...path,key]);
      }
      return;
    }
    if(Array.isArray(a)&&Array.isArray(b)){
      const identified=(values: unknown[])=>values.every(row=>record(row)&&typeof row.id==='string') && new Set(values.map(row=>(row as Record<string,unknown>).id)).size===values.length;
      if(identified(a)&&identified(b) && (a.length||b.length)){
        const left=new Map(a.map(row=>[(row as Record<string,unknown>).id as string,row])),right=new Map(b.map(row=>[(row as Record<string,unknown>).id as string,row]));
        for(const id of [...new Set([...left.keys(),...right.keys()])].sort())compare(left.get(id),right.get(id),[...path,id]);
      }else if(a.length===b.length && a.every((row,index)=>Object.is(row,b[index])))return;
      else if(a.every(row=>!record(row)&&!Array.isArray(row)) && b.every(row=>!record(row)&&!Array.isArray(row)))changes.push({path,before:a,after:b});
      else for(let index=0;index<Math.max(a.length,b.length);index++)compare(a[index],b[index],[...path,String(index+1)]);
      return;
    }
    changes.push({path,before:a,after:b});
  };
  compare(before,after,[]);
  return changes;
}

/** Keep proposal geography separate from an already applied workspace's geography. */
export function proposalWorkspace(project: Workspace | undefined, preview: PlanPreview, sessionId: string, changesRegion: boolean): Workspace {
  return {
    ...(project ?? {id:`assistant-${sessionId}`,name:preview.name}), scenario:preview.scenario,
    demo_console:preview.demo_console ?? (changesRegion ? undefined : project?.demo_console),
    region:preview.region ?? (changesRegion ? undefined : project?.region),
    scene:preview.scene ?? (changesRegion ? undefined : project?.scene),
    scene_source:preview.scene_source ?? (changesRegion ? undefined : project?.scene_source),
    preview:preview.preview ?? (changesRegion ? undefined : project?.preview),
    proposal:true,
  };
}
