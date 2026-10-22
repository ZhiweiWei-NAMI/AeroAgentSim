import { useMemo } from 'react';
import { JsonEditor, pointer, TreeForm } from '../form';
import { KeyTrees, keysPresent } from './KeyTrees';
import { capabilities, operation } from './operations';
import type { SectionProps } from './types';
import { engineOf, nsToS, taskConfig } from './tasks/model';
import { WeatherBlock } from './tasks/Weather';
import { IncidentsBlock, TrafficBlock } from './tasks/Traffic';
import { OpApply, type FormNavigation } from './tasks/op-ui';

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
