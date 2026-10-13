import { Leaf, pointer, under, setAt, TreeForm } from '../../../workbench/form';
import type { SectionProps } from '../../../workbench/sections/types';
import type { Draft } from './model';
import { newCooperation, vehicleIds } from './model';

const RESPONDER_ORDER = ['respond_now', 'request_handoff', 'finish_then_respond', 'report_only'];

/** One cooperation stage: who notices, who decides, who helps. Writes the same tasks.config.cooperation
 *  structure as assistant set_cooperation / compiler._cooperation. */
export function CooperationEditor({ config, engineName, draft, edit, focus, L, replaceDraft }: {
  config: Draft; engineName: string; draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L']; replaceDraft: SectionProps['replaceDraft'];
}) {
  const base = pointer('engines', engineName, 'config', 'cooperation');
  const coop = config.cooperation as Draft | undefined;
  const vehicles = vehicleIds(draft);
  if (!coop) return <div>
    <p className="hint">{L('No cooperation configured: when someone needs help, nobody is assigned.', '未配置协同：出现需要帮助的情况时不会有人被指派。')}</p>
    <button className="wb-btn small" onClick={() => replaceDraft(setAt(draft, base, newCooperation()))}>Add cooperation</button>
  </div>;
  const set = (path: string, value: unknown) => edit(path, value);
  const agentMode = coop.responder?.mode === 'agent' || coop.helpers?.mode === 'agent';
  return <div className="wb-coop" data-path={base}>
    <p className="hint">{L('Who notices an incident, who decides the response and who helps; the run journals each handoff.', '谁发现事件、谁决策响应、谁去协助；运行日志记录每次交接。')}</p>
    <details open><summary>{L('Watchers (who notices)', '观察者（谁发现）')}</summary>
      <TreeForm value={coop.watchers?.vehicles ?? []} path={under(base, 'watchers', 'vehicles')} label="vehicles" onChange={set} focus={focus} depth={2} />
      <Leaf path={under(base, 'watchers', 'notice_radius_m')} label={`${L('Notice radius', '发现半径')} (m)`} value={coop.watchers?.notice_radius_m} onChange={set} focus={focus} />
    </details>
    <details open><summary>{L('Responder (who decides)', '响应者（谁决策）')}</summary>
      <label>{L('Mode', '模式')} <select value={String(coop.responder?.mode ?? '')} onChange={event => set(under(base, 'responder', 'mode'), event.target.value)}>
        <option value="prescribed">{L('Prescribed (fixed rule)', '预设（固定规则）')}</option>
        <option value="agent">{L('Agent (live model)', '智能体（实时模型）')}</option>
      </select></label>
      {coop.responder?.mode === 'agent' && <p className="hint">{L('Agent mode needs a connected model; without one the server rejects the draft.', '智能体模式需要已连接的模型；否则服务器会拒绝草稿。')}</p>}
      <Leaf path={under(base, 'responder', 'goal')} label={L('Goal', '目标')} value={coop.responder?.goal} onChange={set} focus={focus} />
      {Array.isArray(coop.responder?.order) && <div><p className="hint">{L('Response order (first match wins)', '响应顺序（先匹配先执行）')}</p>
        {coop.responder.order.map((step: string, index: number) => <label key={index}>{index + 1}
          <select value={String(step)} onChange={event => set(under(base, 'responder', 'order', index), event.target.value)}>
            {RESPONDER_ORDER.map(name => <option key={name} value={name}>{name}</option>)}
          </select>
          <button type="button" className="wb-btn small danger" aria-label={L(`Remove step ${index + 1}`, `移除步骤 ${index + 1}`)} onClick={() => set(under(base, 'responder', 'order'), coop.responder.order.filter((_: unknown, i: number) => i !== index))}>✕</button>
        </label>)}
        {coop.responder.order.length < RESPONDER_ORDER.length && <button type="button" className="wb-btn small" onClick={() => set(under(base, 'responder', 'order'), [...coop.responder.order, RESPONDER_ORDER.find(name => !coop.responder.order.includes(name))!])}>{L('Add step', '添加步骤')}</button>}
      </div>}
      <TreeForm value={coop.responder?.rules ?? {}} path={under(base, 'responder', 'rules')} label={L('Responder rules', '响应者规则')} onChange={set} focus={focus} depth={2} />
    </details>
    <details open><summary>{L('Helpers (who assists)', '协助者（谁帮忙）')}</summary>
      <TreeForm value={coop.helpers?.vehicles ?? []} path={under(base, 'helpers', 'vehicles')} label="vehicles" onChange={set} focus={focus} depth={2} />
      <datalist id="coop-helper-vehicles">{vehicles.map(id => <option key={id} value={id} />)}</datalist>
      <label>{L('Mode', '模式')} <select value={String(coop.helpers?.mode ?? '')} onChange={event => set(under(base, 'helpers', 'mode'), event.target.value)}>
        <option value="prescribed">{L('Prescribed (fixed rule)', '预设（固定规则）')}</option>
        <option value="agent">{L('Agent (live model)', '智能体（实时模型）')}</option>
      </select></label>
      <Leaf path={under(base, 'helpers', 'ask_radius_m')} label={`${L('Ask radius', '请求半径')} (m)`} value={coop.helpers?.ask_radius_m} onChange={set} focus={focus} />
      <label>{L('Selection', '挑选方式')} <select value={String(coop.helpers?.selection ?? '')} onChange={event => set(under(base, 'helpers', 'selection'), event.target.value)}>
        <option value="nearest">{L('Nearest', '最近')}</option><option value="first">{L('First', '第一个')}</option>
      </select></label>
      <TreeForm value={coop.helpers?.rules ?? {}} path={under(base, 'helpers', 'rules')} label={L('Helper rules', '协助者规则')} onChange={set} focus={focus} depth={2} />
    </details>
    <details><summary>{L('Handoff points & incidents', '交接点与事件')}</summary>
      <TreeForm value={coop.handoff_points ?? []} path={under(base, 'handoff_points')} label={L('Handoff points', '交接点')} onChange={set} focus={focus} depth={2} />
      <TreeForm value={coop.incidents ?? []} path={under(base, 'incidents')} label={L('Incidents', '事件')} onChange={set} focus={focus} depth={2} />
    </details>
    <details><summary>{L('Photograph', '拍照')}</summary>
      <Leaf path={under(base, 'photograph', 'dwell_s')} label={`${L('Dwell', '悬停')} (s)`} value={coop.photograph?.dwell_s} onChange={set} focus={focus} />
      <Leaf path={under(base, 'photograph', 'altitude_m')} label={`${L('Altitude', '高度')} (m)`} value={coop.photograph?.altitude_m} onChange={set} focus={focus} />
    </details>
    {agentMode && 'provider' in coop && <p className="hint">{L('A decision provider is pinned; it is added by the server when confirming agent mode.', '已固定决策提供方；确认智能体模式时由服务器添加。')}</p>}
    <button className="wb-btn small danger" onClick={() => set(base, undefined)}>Remove cooperation</button>
  </div>;
}

