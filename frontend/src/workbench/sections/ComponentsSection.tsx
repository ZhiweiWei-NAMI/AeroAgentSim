import { JsonEditor, pointer, TreeForm } from '../form';
import { KeyTrees, keysPresent } from './KeyTrees';
import type { SectionProps } from './types';

export function ComponentsSection({ draft, edit, focus, L }: SectionProps) {
  return <>
    <p className="hint">{L('Engines that compute state, and which engine owns (writes) which fields.', '计算状态的引擎，以及哪个引擎拥有（写入）哪些字段。')}</p>
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
