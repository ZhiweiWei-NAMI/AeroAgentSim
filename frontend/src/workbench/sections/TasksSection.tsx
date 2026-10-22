import { useCallback, useEffect, useRef, useState } from 'react';
import { BehavioursPanel } from '../../studio/BehavioursPanel';
import type { TypeRow, Workspace } from '../../studio/api';
import { issueSection } from './issues';
import { capabilities, operation } from './operations';
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
  // Low-altitude task editing belongs to the plugin the server installs, never to an engine merely called `tasks`.
  const tasks = engineOf(draft, 'lowalt_tasks');
  const config = taskConfig(draft);
  const lowalt = capabilities(project)?.lowalt ?? !!tasks;
  // Installing the pack is an explicit opt-in for a valid generic scenario, not a repair step.
  const installOptional = !tasks && !lowalt;
  const opProps = { api, project, onProject, saveDraft, zh, goTo };
  return <>
    <p className="hint">{L('Orders, cooperation and rules that drive the work.', '订单、协同与规则——驱动任务的部分。')}</p>
    {installOptional && <div className="wb-empty" data-testid="wb-lowalt-missing">
      <strong>{L('Standard low-altitude components are optional', '标准低空组件为可选项')}</strong>
      <p className="hint">{L('Order streams and cooperation need the standard low-altitude task engine. This scenario can stay generic; edit its engines under Execution components or File (YAML).', '订单流与协同需要标准低空任务引擎。此场景可以保持通用；可在“执行组件”或“文件”（YAML）中编辑其引擎。')}</p>
      <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
        operations={[operation('add_lowalt_components', {})]}
        label={L('Preview installation', '预览安装')} labelZh="预览安装" primary />
    </div>}
    {config?.generators && <><h4>{L('Order streams', '订单流')}</h4>
      <OrderStreams config={config} draft={draft} edit={edit} focus={focus} L={L} {...opProps} /></>}
    {!config?.generators && tasks && <><h4>{L('Order streams', '订单流')}</h4>
      <div className="wb-empty">{L('The tasks engine exists but declares no generators array; add one in File (YAML).', '任务引擎存在但没有生成器数组；请在“文件”（YAML）中添加。')}</div></>}
    {config && tasks && <><h4>{L('Cooperation', '协同')}</h4>
      <CooperationEditor config={config} engineName={tasks.name} draft={draft} edit={edit} focus={focus} L={L} replaceDraft={replaceDraft} {...opProps} /></>}
    {!config && tasks && <><h4>{L('Cooperation', '协同')}</h4>
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
