import { useMemo } from 'react';
import { JsonEditor, pointer, TreeForm } from '../form';
import { KeyTrees, keysPresent } from './KeyTrees';
import { operation } from './operations';
import type { SectionProps } from './types';
import { engineOf, nsToS, taskConfig } from './tasks/model';
import { WeatherBlock } from './tasks/Weather';
import { IncidentsBlock, TrafficBlock } from './tasks/Traffic';
import { OpApply, type FormNavigation } from './tasks/op-ui';

export function ComponentsSection({ api, project, draft, edit, focus, zh, L, onProject, saveDraft, goTo }: SectionProps & FormNavigation) {
  const environment = engineOf(draft, 'environment');
  const config = taskConfig(draft);
  const installOperations = useMemo(() => [operation('add_lowalt_components', {})], []);
  const opProps = { api, project, onProject, saveDraft, zh, goTo };
  return <div className="wb-form-section">
    <p className="hint">{L('Engines that move drones, run tasks, use energy and make weather.', '驱动飞行、任务、能耗与天气的引擎。')}</p>
    {!engineOf(draft, 'lowalt_tasks') && <div className="wb-empty" data-testid="wb-lowalt-missing">
      <strong>{L('Start with standard low-altitude components', '添加标准低空组件')}</strong>
      <p className="hint">{L('Adds the wind engine, task engine and execution components a drone scene needs. Review the changes before applying.', '添加无人机场景所需的风场引擎、任务引擎与执行组件，应用前请核对变更。')}</p>
      <OpApply {...opProps} operations={installOperations} label={L('Preview', '预览')} primary />
    </div>}
    {environment && <>
      <h4>{L('Weather', '天气')}</h4>
      <WeatherBlock environmentName={environment.name} environment={environment.engine} draft={draft} edit={edit} focus={focus} L={L} {...opProps} />
    </>}
    <h4>{L('Traffic', '交通')}</h4>
    <TrafficBlock draft={draft} edit={edit} focus={focus} L={L} />
    {config && <><h4>{L('Incidents', '事件')}</h4><IncidentsBlock config={config} duration_s={nsToS((draft.run as Record<string, unknown> | undefined)?.until_ns)} L={L} {...opProps} /></>}
    <details><summary>{L('Advanced', '高级')}</summary>
      {Object.entries((draft.engines ?? {}) as Record<string, any>).map(([name, engine]) => <details key={name} open={focus?.startsWith(pointer('engines', name) + '/')}>
        <summary><b>{name}</b> <span className="wb-tag wb-mono">{engine.plugin}</span></summary>
        {JSON.stringify(engine.config ?? {}).length > 6000
          ? <JsonEditor label={L(`${name} configuration`, `${name} 配置`)} value={engine.config} path={pointer('engines', name, 'config')} focus={focus} onApply={value => edit(pointer('engines', name, 'config'), value)} />
          : <TreeForm value={engine.config ?? {}} path={pointer('engines', name, 'config')} onChange={edit} focus={focus} />}
      </details>)}
      <KeyTrees keys={keysPresent(draft, ['bindings'])} draft={draft} edit={edit} focus={focus} />
    </details>
  </div>;
}
