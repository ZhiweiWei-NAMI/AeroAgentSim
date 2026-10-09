import { useState } from 'react';
import { mapping } from '../behaviours/model';
import { BehaviourEditor } from '../behaviours/BehaviourEditor';
import { StudioApi, type TypeRow, type Workspace } from './api';
import { Details } from '../console/Details';

type Package = Record<string, unknown>;
interface Props {
  initialTab?:string;
  api: StudioApi; workspace: Workspace; scenario: Record<string, any>; types: TypeRow[]; fields: Array<Record<string, unknown>>; relations:Array<Record<string,unknown>>;
  onChange: (scenario: Record<string, any>) => void; onApply: (workspace: Workspace) => void; save: () => Promise<Workspace>; onValidityChange:(valid:boolean)=>void; issues?:Array<{path:string;message:string}>;
}
export function BehavioursPanel({ api, workspace, scenario, types, fields, relations, onChange, onApply, save, onValidityChange, issues=[], initialTab }: Props) {
  const [index, setIndex] = useState(0), [error, setError] = useState(''), [errors, setErrors] = useState<Array<{ path: string; message: string }>>([]), [result, setResult] = useState<unknown>();
  const packages = Array.isArray(scenario.behaviours) ? scenario.behaviours as Package[] : [];
  const value = packages[index];
  const base = `/v1/studio/workspaces/${workspace.id}`;
  const change = (next: Package) => { onChange({ ...scenario, behaviours: packages.map((row, i) => i === index ? next : row) }); setErrors([]); setResult(undefined); };
  const perform = async (action: () => Promise<void>) => { setError(''); try { await action(); } catch (problem) { setError(String(problem)); } };
  if(scenario.behaviours!==undefined&&!Array.isArray(scenario.behaviours))return <p role="alert">Unsupported behaviour collection retained in scenario JSON. Correct its list shape in the Scenario editor before editing packages.</p>;
  return <section aria-label="Behaviour package authoring">
    <h3>Behaviour package</h3>
    <p>Edit chain states, typed triggers, guards and actions. Changes stay in the shared scenario draft until you save.</p>
    <label>Package <select aria-label="Behaviour package" value={index} onChange={event => { setIndex(Number(event.target.value)); setErrors([]); setResult(undefined); }}>{packages.map((row, i) => <option key={i} value={i}>{mapping(row)?String(row.id ?? row.path ?? `Package ${i + 1}`):`Unsupported package entry ${i+1}`}</option>)}</select></label>
    <button onClick={() => {
      const next = { format: 'aeroagentsim.behaviour-package/v1', id: '', revision: 1, registry: {digest_ref:'scenario.registry_digest'}, evaluator: {version:'aerograph-predicate/1'}, budgets: {max_transitions_per_instance_per_ns:64,max_instances:10000}, predicates:{}, chains:{}, bindings:[], conflicts:[], injection_points:[] };
      onChange({ ...scenario, behaviours: [...packages, next] }); setIndex(packages.length); setErrors([]); setResult(undefined);
    }}>Add behaviour package</button>
    {mapping(value) && 'path' in value ? <div><p>Referenced package {String(value.path)}</p><Details title="Package reference" buttonLabel="Reference details"><pre>{JSON.stringify({path:value.path},null,2)}</pre></Details><button onClick={()=>void perform(async()=>{await save();const output=await api.request<{inline_package:Package|null;inline_errors:string[]}>(`${base}/behaviours/${index}/export`);if(!mapping(output.inline_package))throw Error(`Referenced package cannot become inline: ${output.inline_errors.join('; ')}`);change(output.inline_package);})}>Edit referenced package</button></div> : mapping(value) ? <BehaviourEditor initialTab={initialTab} key={`${workspace.id}/${index}`} value={value} onChange={change} capabilities={Object.keys(scenario.engines?.behaviour?.config?.capabilities??{})} events={(scenario.registry?.messages??[]).filter((row:Record<string,unknown>)=>row.kind==='event').map((row:Record<string,unknown>)=>String(row.id))} types={types} fields={fields} relations={relations} errors={[...errors,...issues]} onValidityChange={onValidityChange}
      onValidate={() => void perform(async () => {
        await save();
        const validation = await api.request<{valid:boolean;errors:Array<{path:string;message:string}>}>(`${base}/behaviours/${index}/validate`, {});
        setResult(validation); setErrors(validation.errors); onApply(await api.request<Workspace>(base));
      })}
      onImportYaml={async yaml => {
        await save(); await api.request(`${base}/behaviours`, {index,yaml}); onApply(await api.request<Workspace>(base)); setErrors([]); setResult(undefined);
      }}
      onExportYaml={async () => { await save(); const output = await api.request<{yaml:string}>(`${base}/behaviours/${index}/export`); return output.yaml; }} /> : value!==undefined&&<p role="alert">Unsupported package entry retained: {JSON.stringify(value)}. Edit the Scenario JSON.</p>}
    {result !== undefined && <Details title="Server package validation" buttonLabel="Server package validation"><pre>{JSON.stringify(result, null, 2)}</pre></Details>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
