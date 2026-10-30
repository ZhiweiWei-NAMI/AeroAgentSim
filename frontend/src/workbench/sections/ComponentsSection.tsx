import { useMemo } from 'react';
import { JsonEditor, pointer, TreeForm } from '../form';
import { KeyTrees, keysPresent } from './KeyTrees';
import { capabilities, operation } from './operations';
import type { SectionProps } from './types';
import { engineOf, nsToS, taskConfig } from './tasks/model';
import { WeatherBlock } from './tasks/Weather';
import { IncidentsBlock, TrafficBlock } from './tasks/Traffic';
import { OpApply, type FormNavigation } from './tasks/op-ui';

const KINEMATIC = 'kinematic';
const EXTERNAL_FLIGHT = 'px4_gazebo';
const MOTION_NAMES = ['motion', 'air_motion', 'native_air', 'flight'];
const EXTERNAL_SIMULATORS = new Set([EXTERNAL_FLIGHT, 'sumo', 'ns3']);

/** The flight-physics engine: a known flight backend plugin first, else a conventionally named motion engine. */
function motionEngine(draft: SectionProps['draft']): { name: string; plugin: string } | undefined {
  const engines = Object.entries((draft.engines ?? {}) as Record<string, { plugin?: unknown } | undefined>);
  const found = engines.find(([, engine]) => engine?.plugin === KINEMATIC || engine?.plugin === EXTERNAL_FLIGHT)
    ?? engines.find(([name]) => MOTION_NAMES.includes(name));
  return found && { name: found[0], plugin: String(found[1]?.plugin ?? '') };
}

function FlightPhysics({ draft, edit, L }: Pick<SectionProps, 'draft' | 'edit' | 'L'>) {
  const motion = motionEngine(draft);
  return <fieldset className="wb-form-card" data-testid="wb-flight-physics">
    <legend className="wb-form-card-title">{L('Flight Physics / Motion Engine', '飞行物理与调度引擎')}</legend>
    <p className="hint">{L(
      `The built-in AeroKernel + kinematic engine computes flight trajectories analytically at discrete event steps and directly replaces Gazebo for high-speed simulation; PX4 / Gazebo (${EXTERNAL_FLIGHT}) is an optional external lockstep backend.`,
      `内置 AeroKernel + kinematic 核心调度引擎通过离散事件解析计算飞行轨迹，在平台中等价替代 Gazebo 实现高加速比仿真；PX4 / Gazebo（${EXTERNAL_FLIGHT}）为可选外部锁步飞控后端。`,
    )}</p>
    {motion ? <div className="wb-form-grid">
      <label className="wb-field"><span>{L(`Flight physics backend (${motion.name})`, `飞行物理后端（${motion.name}）`)}</span>
        <select value={motion.plugin} onChange={event => edit(pointer('engines', motion.name, 'plugin'), event.target.value)}>
          <option value={KINEMATIC}>{L('kinematic — built-in, replaces Gazebo', 'kinematic — 内置，替代 Gazebo')}</option>
          <option value={EXTERNAL_FLIGHT}>{L(`${EXTERNAL_FLIGHT} — PX4 / Gazebo, external`, `${EXTERNAL_FLIGHT} — PX4 / Gazebo，外部`)}</option>
          {motion.plugin !== KINEMATIC && motion.plugin !== EXTERNAL_FLIGHT && <option value={motion.plugin}>{motion.plugin}</option>}
        </select>
      </label>
    </div> : <p className="hint">{L('This scenario has no flight motion engine; add one under Advanced or in File (YAML).', '此场景没有飞行运动引擎；可在“高级”或“文件”（YAML）中添加。')}</p>}
    {motion?.plugin === EXTERNAL_FLIGHT && <p className="hint wb-form-prerequisite" data-testid="wb-flight-external">{L(
      'This backend needs a running PX4 / Gazebo container endpoint (image, host and port in the engine configuration under Advanced); the configuration must match the selected backend before the scenario validates.',
      '此后端需要正在运行的 PX4 / Gazebo 容器端点（在“高级”中的引擎配置里设置 image、host 和 port）；配置须与所选后端匹配，场景才能通过校验。',
    )}</p>}
  </fieldset>;
}

export function ComponentsSection({ api, project, draft, edit, focus, zh, L, onProject, saveDraft, goTo }: SectionProps & FormNavigation) {
  const environment = engineOf(draft, 'environment');
  const tasks = engineOf(draft, 'lowalt_tasks');
  const config = taskConfig(draft);
  const lowalt = capabilities(project)?.lowalt ?? !!tasks;
  // The pack is an explicit opt-in for a valid generic scenario, not a repair step.
  const installOptional = !tasks && !lowalt;
  const installOperations = useMemo(() => [operation('add_lowalt_components', {})], []);
  const opProps = { api, project, onProject, saveDraft, zh, goTo };
  return <div className="wb-form-section">
    <p className="hint">{L('Engines that move drones, run tasks, use energy and make weather.', '驱动飞行、任务、能耗与天气的引擎。')}</p>
    {installOptional && <div className="wb-empty" data-testid="wb-lowalt-missing">
      <strong>{L('Standard low-altitude components are optional', '标准低空组件为可选项')}</strong>
      <p className="hint">{L('Install the wind, task and execution components only if this scenario needs drone operations. Generic engines remain editable under Advanced and in File (YAML).', '仅当此场景需要无人机运行时，才需安装风场、任务与执行组件。通用引擎仍可在“高级”和“文件”（YAML）中编辑。')}</p>
      <OpApply {...opProps} operations={installOperations} label={L('Preview', '预览')} primary />
    </div>}
    <FlightPhysics draft={draft} edit={edit} L={L} />
    {environment && <>
      <h4>{L('Weather', '天气')}</h4>
      <WeatherBlock environmentName={environment.name} environment={environment.engine} draft={draft} edit={edit} focus={focus} L={L} {...opProps} />
    </>}
    <h4>{L('Traffic', '交通')}</h4>
    <TrafficBlock draft={draft} edit={edit} focus={focus} L={L} />
    {config && <><h4>{L('Incidents', '事件')}</h4><IncidentsBlock config={config} duration_s={nsToS((draft.run as Record<string, unknown> | undefined)?.until_ns)} L={L} {...opProps} /></>}
    <details open={focus?.startsWith('/engines/') || focus?.startsWith('/bindings/')}><summary>{L('Advanced', '高级')}</summary>
      {Object.entries((draft.engines ?? {}) as Record<string, any>).map(([name, engine]) => <details key={name} open={focus === pointer('engines', name) || focus?.startsWith(pointer('engines', name) + '/')}>
        <summary><b>{name}</b> <span className="wb-tag wb-mono">{engine.plugin}</span>
          {engine.plugin === KINEMATIC && <> <span className="wb-tag">{L('replaces Gazebo', '替代 Gazebo')}</span></>}
          {EXTERNAL_SIMULATORS.has(engine.plugin) && <> <span className="wb-tag warn">{L('external simulator', '外部仿真器')}</span></>}</summary>
        {JSON.stringify(engine.config ?? {}).length > 6000
          ? <JsonEditor label={L(`${name} configuration`, `${name} 配置`)} value={engine.config} path={pointer('engines', name, 'config')} focus={focus} onApply={value => edit(pointer('engines', name, 'config'), value)} />
          : <TreeForm value={engine.config ?? {}} path={pointer('engines', name, 'config')} onChange={edit} focus={focus} />}
      </details>)}
      <KeyTrees keys={keysPresent(draft, ['bindings'])} draft={draft} edit={edit} focus={focus} />
    </details>
  </div>;
}
