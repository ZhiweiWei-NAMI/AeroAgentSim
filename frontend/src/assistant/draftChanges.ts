import type { PlanPreview } from './api';
import type { Workspace } from '../studio/api';

export interface DraftChange { path: string[]; before: unknown; after: unknown }
const record = (value: unknown): value is Record<string,unknown> => !!value && typeof value==='object' && !Array.isArray(value);

/** Exact decimal value, shared by native numbers and lossless wire tags. */
function canonicalNumber(value: unknown): string | undefined {
  const token = typeof value === 'number' && Number.isFinite(value) ? String(value)
    : record(value) && Object.keys(value).length === 1
      ? ('$integer' in value ? value.$integer : value.$number) : undefined;
  if (typeof token !== 'string') return undefined;
  const parts = /^(-?)(0|[1-9]\d*)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(token);
  if (!parts) return undefined;
  const fraction = parts[3] ?? '', digits = (parts[2] + fraction).replace(/^0+/, '');
  if (!digits) return '0';
  const trailing = /0+$/.exec(digits)?.[0].length ?? 0;
  const exponent = BigInt(parts[4] ?? '0') - BigInt(fraction.length) + BigInt(trailing);
  return `${parts[1]}${digits.slice(0, digits.length - trailing)}e${exponent}`;
}
const atomic = (value: unknown) => !record(value) && !Array.isArray(value) || canonicalNumber(value) !== undefined;
const sameScalar = (a: unknown, b: unknown) => Object.is(a,b) || canonicalNumber(a) !== undefined && canonicalNumber(a) === canonicalNumber(b);

export interface FixtureDigests { before?: Record<string,string>; after?: Record<string,string> }

/** Compare research fields, independent of object identity, key order and entity order. */
export function draftChanges(before: Record<string,unknown>, after: Record<string,unknown>, fixtures: FixtureDigests = {}): DraftChange[] {
  const changes: DraftChange[]=[];
  const compare=(a: unknown,b: unknown,path: string[])=>{
    // File placement is not a research input. Compare verified bytes even when
    // the path itself stayed the same but the referenced file was edited.
    if(path.at(-1)==='fixture_path' && typeof a==='string' && typeof b==='string'){
      const left=fixtures.before?.[a], right=fixtures.after?.[b];
      if(left && right){
        if(left!==right)changes.push({path,before:left,after:right});
        return;
      }
    }
    if(atomic(a) && atomic(b) && sameScalar(a,b))return;
    if(record(a)&&record(b) && !atomic(a) && !atomic(b)){
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
      }else if(a.length===b.length && a.every((row,index)=>atomic(row) && atomic(b[index]) && sameScalar(row,b[index])))return;
      else if(a.every(atomic) && b.every(atomic))changes.push({path,before:a,after:b});
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
    ...(project ?? {id:`assistant-${sessionId}`,name:preview.name}), scenario:preview.scenario, fixture_digests:preview.fixture_digests,
    demo_console:preview.demo_console ?? (changesRegion ? undefined : project?.demo_console),
    region:preview.region ?? (changesRegion ? undefined : project?.region),
    scene:preview.scene ?? (changesRegion ? undefined : project?.scene),
    scene_source:preview.scene_source ?? (changesRegion ? undefined : project?.scene_source),
    preview:preview.preview ?? (changesRegion ? undefined : project?.preview),
    proposal:true,
  };
}
