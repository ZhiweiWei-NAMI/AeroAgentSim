import type { SectionProps } from '../../../workbench/sections/types';
import type { Draft } from './model';
import { Leaf, pointer, TreeForm } from '../../../workbench/form';

/** Traffic block: states the provenance honestly. SUMO (adapter 'sumo'), procedural road motion
 *  (traffic_road_motion with authored actors/routes) and render-only traffic are different things. */
export function TrafficBlock({ draft, edit, focus, L }: { draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'] }) {
  const engines = (draft.engines ?? {}) as Record<string, Draft>;
  const sumo = Object.entries(engines).find(([, engine]) => engine?.plugin === 'sumo');
  const procedural = Object.entries(engines).filter(([, engine]) => engine?.plugin === 'traffic_road_motion');
  const assessment = Object.entries(engines).filter(([, engine]) => engine?.plugin === 'traffic_assessment');
  return <div className="wb-traffic" data-testid="wb-traffic">
    {!sumo && !procedural.length && <div className="wb-empty">{L('No road/traffic engine in this scenario; there is no ground traffic to configure.', '该场景没有道路/交通引擎；没有可配置的地面交通。')}</div>}
    {sumo && <details open><summary>SUMO <span className="wb-tag wb-mono">{sumo[0]}</span></summary>
      <p className="hint">{L('Real SUMO network traffic. Vehicle counts, arrival rates and routes are set in the SUMO configuration/network, not here; this draft only holds the engine contract.', '真实 SUMO 路网交通。车辆数量、到达率与路线在 SUMO 配置/路网中设置，不在这里；此草稿只保存引擎契约。')}</p>
      <TreeForm value={sumo[1].config ?? {}} path={pointer('engines', sumo[0], 'config')} onChange={edit} focus={focus} depth={1} />
    </details>}
    {procedural.map(([name, engine]) => <details key={name} open><summary>{L('Procedural road traffic', '程序化道路交通')} <span className="wb-tag wb-mono">{name}</span> <span className="wb-tag warn">{L('scripted, not SUMO', '脚本生成，非 SUMO')}</span></summary>
      <p className="hint">{L('Actors follow authored routes with authored speeds; this is traceable scripted traffic, not a SUMO simulation. Density or arrival-rate fields do not exist, so none are shown.', '参与者沿已编写的路线以已编写的速度行驶；这是可追溯的脚本交通，不是 SUMO 仿真。不存在密度或到达率字段，因此不展示。')}</p>
      <p className="hint">{L(`Actors: ${(Array.isArray(engine.config?.actors) ? engine.config.actors.length : 0)} · Routes: ${Array.isArray(engine.config?.routes) ? engine.config.routes.length : 0}`, `参与者：${Array.isArray(engine.config?.actors) ? engine.config.actors.length : 0} · 路线：${Array.isArray(engine.config?.routes) ? engine.config.routes.length : 0}`)}</p>
      {['step_ns', 'clearance_m', 'bypass_clearance_m'].map(key => key in (engine.config ?? {}) && <Leaf key={key} path={pointer('engines', name, 'config', key)} label={key} value={(engine.config as Draft)[key]} onChange={edit} focus={focus} />)}
      <TreeForm value={engine.config ?? {}} path={pointer('engines', name, 'config')} onChange={edit} focus={focus} depth={0} />
    </details>)}
    {assessment.map(([name]) => <p key={name} className="hint">{L(`Traffic assessment engine "${name}" bids nearby UAVs for the incident task; it does not create vehicles.`, `交通评估引擎“${name}”让附近无人机竞标事件任务；它不创建车辆。`)}</p>)}
  </div>;
}

/** Incidents block, if the tasks cooperation carries incidents (assistant add_incident writes there). */
export function IncidentsBlock({ config, engineName, edit, focus, L }: { config: Draft; engineName: string; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'] }) {
  const incidents = (config.cooperation as Draft | undefined)?.incidents;
  if (!Array.isArray(incidents)) return <p className="hint">{L('No incidents configured; the assistant can add one, or add it under Cooperation.', '未配置事件；可由助手添加，或在“协同”下添加。')}</p>;
  return <div>
    {incidents.map((incident, index) => <div key={index} className="wb-gust-row" data-path={pointer('engines', engineName, 'config', 'cooperation', 'incidents', index)}>
      <span className="wb-mono">{String(incident.id ?? index)}</span>
      <span className="wb-tag">{String(incident.kind ?? '?')}</span>
      <span className="wb-tag">{incident.at_s !== undefined ? `${incident.at_s} s` : L('no time', '无时间')}</span>
      <Leaf path={pointer('engines', engineName, 'config', 'cooperation', 'incidents', index, 'at_s')} label={`${L('Time', '时间')} (s)`} value={incident.at_s} onChange={edit} focus={focus} />
    </div>)}
  </div>;
}
