import { JsonEditor, pointer, TreeForm } from '../form';
import { KeyTrees, keysPresent } from './KeyTrees';
import type { SectionProps } from './types';
import { engineOf, taskConfig } from './tasks/model';
import { WeatherBlock } from './tasks/Weather';
import { IncidentsBlock, TrafficBlock } from './tasks/Traffic';

export function ComponentsSection({ draft, edit, focus, L }: SectionProps) {
  const environment = engineOf(draft, 'environment');
  const tasks = engineOf(draft, 'lowalt_tasks') ?? ((draft.engines as Record<string, any> | undefined)?.tasks ? { name: 'tasks', engine: (draft.engines as Record<string, any>).tasks as Record<string, any> } : undefined);
  const config = taskConfig(draft);
  const sumo = Object.entries((draft.engines ?? {}) as Record<string, any>).some(([, engine]) => engine?.plugin === 'sumo');
  return <>
    <p className="hint">{L('Engines that compute state, and which engine owns (writes) which fields.', '计算状态的引擎，以及哪个引擎拥有（写入）哪些字段。')}</p>
    {environment && <><h4>{L('Weather & traffic', '天气与交通')}</h4>
      <WeatherBlock environmentName={environment.name} environment={environment.engine} draft={draft} edit={edit} focus={focus} L={L} />
      <TrafficBlock draft={draft} edit={edit} focus={focus} L={L} />
      {config && tasks && <IncidentsBlock config={config} engineName={tasks.name} edit={edit} focus={focus} L={L} />}
      {!sumo && <p className="hint">{L('No SUMO engine is configured, so road traffic here (if any) is scripted or decorative, never a SUMO simulation.', '未配置 SUMO 引擎，因此这里的道路交通（如有）是脚本或装饰性的，绝不是 SUMO 仿真。')}</p>}
    </>}
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
