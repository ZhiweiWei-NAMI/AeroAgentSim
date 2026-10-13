import { useCallback, useEffect, useRef, useState } from 'react';
import { BehavioursPanel } from '../../studio/BehavioursPanel';
import type { TypeRow, Workspace } from '../../studio/api';
import { issueSection } from './issues';
import { operation } from './operations';
import type { Project } from '../api';
import { Leaf, pointer } from '../form';
import type { SectionProps } from './types';
import { engineOf, taskConfig } from './tasks/model';
import { OrderStreams } from './tasks/OrderStreams';
import { CooperationEditor } from './tasks/Cooperation';
import { OpApply } from './tasks/op-ui';
import './tasks.css';

type Catalog = { types: TypeRow[]; fields: Record<string, unknown>[]; relations: Record<string, unknown>[] };

export function TasksSection({ api, studioApi, project, draft, edit, replaceDraft, focus, zh, L, issues, onProject, saveDraft, reportValidity, goTo }: SectionProps) {
  const [ruleEditor, setRuleEditor] = useState(false), [catalog, setCatalog] = useState<Catalog>();
  const ruleRoot = useRef<HTMLDivElement>(null), [ruleError, setRuleError] = useState<string>();
  const reportRuleValidity = useCallback((valid: boolean) => reportValidity('/behaviours', valid), [reportValidity]);
  useEffect(() => () => reportValidity('/behaviours', true), [ruleEditor, reportValidity]);
  useEffect(() => { if (!ruleEditor || catalog) return; setRuleError(undefined); void studioApi.request<Catalog>(`/v1/studio/types?workspace=${encodeURIComponent(project.id)}`).then(setCatalog).catch(problem => setRuleError(String(problem))); }, [ruleEditor, project.id]);
  useEffect(() => { if (ruleEditor && catalog) ruleRoot.current?.scrollIntoView({ block: 'start' }); }, [ruleEditor, catalog]);
  const behaviours = (Array.isArray(draft.behaviours) ? draft.behaviours : []) as Array<Record<string, any>>;
  const tasks = engineOf(draft, 'lowalt_tasks') ?? ((draft.engines as Record<string, any> | undefined)?.tasks ? { name: 'tasks', engine: (draft.engines as Record<string, any>).tasks as Record<string, any> } : undefined);
  const config = taskConfig(draft);
  // When the low-altitude pack is absent the operations API can install it; no dead end (C11 add_lowalt_components).
  const lowaltMissing = !tasks;
  const opProps = { api, project, onProject, saveDraft, zh, goTo };
  return <>
    <p className="hint">{L('What work appears and the rules that decide what is allowed: order streams, cooperation, predicates with parameters, and event chains.', '会出现哪些工作，以及决定允许做什么的规则：订单流、协同、带参数的谓词与事件链。')}</p>
    {lowaltMissing && <div className="wb-empty" data-testid="wb-lowalt-missing">
      {L('This scenario has no low-altitude components (tasks engine, places, fleets), so order streams and cooperation have nothing to attach to.', '该场景没有低空组件（任务引擎、设施、机群），订单流与协同没有可依附的对象。')}
      <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
        operations={[operation('add_lowalt_components', {})]}
        label={L('Start with standard low-altitude components', '安装标准低空组件')} labelZh="安装标准低空组件" primary />
    </div>}
    {config?.generators && <><h4>{L('Order streams', '订单流')}</h4>
      <OrderStreams config={config} draft={draft} edit={edit} focus={focus} L={L} {...opProps} /></>}
    {!config?.generators && !lowaltMissing && <><h4>{L('Order streams', '订单流')}</h4>
      <div className="wb-empty">{L('The tasks engine exists but declares no generators array; add one in File (YAML).', '任务引擎存在但没有生成器数组；请在“文件”（YAML）中添加。')}</div></>}
    {config && tasks && <><h4>{L('Cooperation', '协同')}</h4>
      <CooperationEditor config={config} engineName={tasks.name} draft={draft} edit={edit} focus={focus} L={L} replaceDraft={replaceDraft} {...opProps} /></>}
    {!config && !lowaltMissing && <><h4>{L('Cooperation', '协同')}</h4>
      <div className="wb-empty">{L('The tasks engine exists but its config carries no cooperation yet — set one up above.', '任务引擎存在但尚未配置协同——请在上方配置。')}</div></>}
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
