import { useCallback, useEffect, useRef, useState } from 'react';
import { BehavioursPanel } from '../../studio/BehavioursPanel';
import type { TypeRow, Workspace } from '../../studio/api';
import { issueSection } from './issues';
import type { Project } from '../api';
import { Leaf, pointer } from '../form';
import type { SectionProps } from './types';
import { engineOf, taskConfig } from './tasks/model';
import { OrderStreams } from './tasks/OrderStreams';
import { CooperationEditor } from './tasks/Cooperation';
import './tasks.css';

type Catalog = { types: TypeRow[]; fields: Record<string, unknown>[]; relations: Record<string, unknown>[] };

export function TasksSection({ project, studioApi, draft, edit, replaceDraft, focus, L, issues, onProject, saveDraft, reportValidity }: SectionProps) {
  const [ruleEditor, setRuleEditor] = useState(false), [catalog, setCatalog] = useState<Catalog>();
  const ruleRoot = useRef<HTMLDivElement>(null), [ruleError, setRuleError] = useState<string>();
  const reportRuleValidity = useCallback((valid: boolean) => reportValidity('/behaviours', valid), [reportValidity]);
  useEffect(() => () => reportValidity('/behaviours', true), [ruleEditor, reportValidity]);
  useEffect(() => { if (!ruleEditor || catalog) return; setRuleError(undefined); void studioApi.request<Catalog>(`/v1/studio/types?workspace=${encodeURIComponent(project.id)}`).then(setCatalog).catch(problem => setRuleError(String(problem))); }, [ruleEditor, project.id]);
  useEffect(() => { if (ruleEditor && catalog) ruleRoot.current?.scrollIntoView({ block: 'start' }); }, [ruleEditor, catalog]);
  const behaviours = (Array.isArray(draft.behaviours) ? draft.behaviours : []) as Array<Record<string, any>>;
  const tasks = engineOf(draft, 'lowalt_tasks') ?? ((draft.engines as Record<string, any> | undefined)?.tasks ? { name: 'tasks', engine: (draft.engines as Record<string, any>).tasks as Record<string, any> } : undefined);
  const config = taskConfig(draft);
  return <>
    <p className="hint">{L('What work appears and the rules that decide what is allowed: order streams, cooperation, predicates with parameters, and event chains.', '会出现哪些工作，以及决定允许做什么的规则：订单流、协同、带参数的谓词与事件链。')}</p>
    {config?.generators && <><h4>{L('Order streams', '订单流')}</h4>
      <OrderStreams config={config} draft={draft} edit={edit} focus={focus} L={L} /></>}
    {config && tasks && <><h4>{L('Cooperation', '协同')}</h4>
      <CooperationEditor config={config} engineName={tasks.name} draft={draft} edit={edit} focus={focus} L={L} replaceDraft={replaceDraft} /></>}
    {behaviours.map((behaviour, b) => <div key={b}>
      <h4>{L('Rules', '规则')} · <span className="wb-mono">{behaviour.id ?? b}</span></h4>
      {Object.entries(behaviour.predicates ?? {}).map(([name, predicate]: [string, any]) => <details key={name} open><summary><b>{name}</b></summary>
        {predicate?.parameters ? Object.entries(predicate.parameters).map(([key, value]) => <Leaf key={key} path={pointer('behaviours', b, 'predicates', name, 'parameters', key)} label={key} value={value} onChange={edit} focus={focus} />) : <p className="hint">{L('No parameters.', '无参数。')}</p>}
      </details>)}
      {behaviour.chains && <><h4>{L('Event chains', '事件链')}</h4><div className="wb-card-meta">{Object.keys(behaviour.chains).map(chain => <span key={chain} className="wb-tag wb-mono">{chain}</span>)}</div></>}
    </div>)}
    <div style={{ marginTop: 14 }}><button className="wb-btn" aria-pressed={ruleEditor} onClick={() => setRuleEditor(value => !value)}>{ruleEditor ? L('Hide the rule editor', '收起规则编辑器') : L('Edit predicates, triggers and event chains…', '编辑谓词、触发条件和事件链…')}</button></div>
    {ruleEditor && (catalog ? <div className="wb-rule-editor" ref={ruleRoot}><BehavioursPanel api={studioApi} workspace={{ ...project, scenario: draft } as unknown as Workspace} scenario={draft} types={catalog.types} fields={catalog.fields} relations={catalog.relations}
        onChange={replaceDraft} onApply={workspace => onProject(workspace as unknown as Project)}
        save={async () => { const saved = await saveDraft(); if (!saved) throw Error(L('Save failed', '保存失败')); return saved as unknown as Workspace; }} onValidityChange={reportRuleValidity}
        issues={issues.filter(issue => issueSection(issue) === 'tasks')} /></div> : <p className="hint" role={ruleError ? 'alert' : 'status'}>{ruleError ?? L('Loading the contract catalog…', '正在加载契约目录…')}</p>)}
    {!behaviours.length && !config && <div className="wb-empty">{L('No task or behaviour definitions.', '没有任务或行为定义。')}</div>}
  </>;
}
