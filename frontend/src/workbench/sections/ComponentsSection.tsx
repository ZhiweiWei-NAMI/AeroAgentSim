import { JsonEditor, pointer, TreeForm } from '../form';
import { KeyTrees, keysPresent } from './KeyTrees';
import { operation } from './operations';
import type { SectionProps } from './types';
import { engineOf, taskConfig } from './tasks/model';
import { WeatherBlock } from './tasks/Weather';
import { IncidentsBlock, TrafficBlock } from './tasks/Traffic';
import { OpApply } from './tasks/op-ui';

export function ComponentsSection({ api, project, draft, edit, focus, zh, L, onProject, saveDraft }: SectionProps) {
  const environment = engineOf(draft, 'environment');
  const tasks = engineOf(draft, 'lowalt_tasks') ?? ((draft.engines as Record<string, any> | undefined)?.tasks ? { name: 'tasks', engine: (draft.engines as Record<string, any>).tasks as Record<string, any> } : undefined);
  const config = taskConfig(draft);
  const sumo = Object.entries((draft.engines ?? {}) as Record<string, any>).some(([, engine]) => engine?.plugin === 'sumo');
  const hasTrafficEngine = Object.entries((draft.engines ?? {}) as Record<string, any>).some(([, engine]) => engine?.plugin === 'sumo' || engine?.plugin === 'traffic_road_motion');
  const lowaltMissing = !tasks;
  const opProps = { api, project, onProject, saveDraft, zh };
  return <>
    <p className="hint">{L('Engines that compute state, and which engine owns (writes) which fields.', '计算状态的引擎，以及哪个引擎拥有（写入）哪些字段。')}</p>
    {lowaltMissing && <div className="wb-empty" data-testid="wb-lowalt-missing">
      {L('This scenario has no low-altitude components, so incidents (and the tasks engine they live in) do not exist here yet.', '该场景没有低空组件，因此这里还没有事件（及其任务引擎）。')}
      <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
        operations={[operation('add_lowalt_components', {})]}
        label={L('Start with standard low-altitude components', '安装标准低空组件')} labelZh="安装标准低空组件" primary />
    </div>}
    {environment && <><h4>{L('Weather & traffic', '天气与交通')}</h4>
      <WeatherBlock environmentName={environment.name} environment={environment.engine} draft={draft} edit={edit} focus={focus} L={L} {...opProps} />
      <TrafficBlock draft={draft} edit={edit} focus={focus} L={L} />
      {config && tasks && <IncidentsBlock config={config} api={api} project={project} onProject={onProject} saveDraft={saveDraft} focus={focus} L={L} />}
      {!sumo && hasTrafficEngine && <p className="hint">{L('No SUMO engine is configured, so road traffic here (if any) is scripted or decorative, never a SUMO simulation.', '未配置 SUMO 引擎，因此这里的道路交通（如有）是脚本或装饰性的，绝不是 SUMO 仿真。')}</p>}
    </>}
    {!environment && <><h4>{L('Weather & traffic', '天气与交通')}</h4>
      <div className="wb-empty">
        {L('Needs the environment engine for wind and gusts (and the tasks engine lowalt_tasks for incidents); this form cannot create engines yet. Start from a template in the gallery, or add the engine in File (YAML).', '需要 environment 引擎提供风与阵风（事件还需要任务引擎 lowalt_tasks）；此表单尚不能创建引擎。可从图库的模板开始，或在“文件”（YAML）中添加引擎。')}
        {!sumo && !hasTrafficEngine && ` ${L('No SUMO or road/traffic engine is configured either, so there is no ground traffic to configure here — and if any appears, it is scripted or decorative, never a SUMO simulation.', '同时也没有配置 SUMO 或道路/交通引擎，因此这里没有可配置的地面交通——即使出现，也是脚本或装饰性的，绝不是 SUMO 仿真。')}`}
      </div></>}
    {'engines' in draft && <div><h4 className="wb-mono">engines</h4>
      {Object.entries(draft.engines as Record<string, any>).map(([name, engine]) => <details key={name} open={focus?.startsWith(pointer('engines', name) + '/')}><summary><b>{name}</b> <span className="wb-tag wb-mono">{engine.plugin}</span></summary>
        {JSON.stringify(engine.config ?? {}).length > 6000
          ? <JsonEditor label={`${name} config`} value={engine.config} path={pointer('engines', name, 'config')} focus={focus} onApply={value => edit(pointer('engines', name, 'config'), value)} />
          : <TreeForm value={engine.config ?? {}} path={pointer('engines', name, 'config')} onChange={edit} focus={focus} />}
      </details>)}
    </div>}
    <KeyTrees keys={keysPresent(draft, ['bindings'])} draft={draft} edit={edit} focus={focus} />
  </>;
}
