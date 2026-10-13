import { useState } from 'react';
import { keysPresent } from './KeyTrees';
import { getAt, JsonEditor, pointer, TreeForm } from '../form';
import type { SectionProps } from './types';
import { RegionBlock } from './space/RegionBlock';
import './space.css';

export function SpaceSection(props: SectionProps) {
  const { draft, edit, focus, L } = props;
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const advancedKeys = keysPresent(draft, ['presentation', 'region', 'airspace']);
  const advancedFocused = advancedKeys.some(key => focus === pointer(key) || focus?.startsWith(`${pointer(key)}/`));
  return <>
    <p className="hint">{L('Geographic scope, city assets and how types are drawn in the scene.', '地理范围、城市素材以及类型在场景中的呈现方式。')}</p>
    <RegionBlock {...props} />
    <label className="wb-field" data-path="/city">
      <span>{L('City', '城市')}</span>
      <select className="wb-search" value={(draft.city as string | undefined) ?? 'none'} aria-label={L('City', '城市')}
        onChange={event => edit('/city', event.target.value === 'none' ? undefined : event.target.value)}>
        <option value="huangpu">{L('huangpu — use the bundled Huangpu city model', 'huangpu — 使用内置黄浦城市模型')}</option>
        <option value="none">{L('none — use the region you selected above', 'none — 使用上方选择的区域')}</option>
      </select>
      <small className="hint">{L(
        'Choose the applied region above or the bundled Huangpu city model for the scene.',
        '为场景选择上方已应用的区域或内置黄浦城市模型。')}</small>
    </label>
    {!!advancedKeys.length && <details open={advancedOpen || advancedFocused} onToggle={event => setAdvancedOpen(event.currentTarget.open)}>
      <summary>{L('Advanced', '高级')}</summary>
      {advancedKeys.map(key => {
        const value = getAt(draft, pointer(key));
        const label = key === 'presentation' ? L('Presentation', '视觉呈现') : key === 'region' ? L('Region settings', '区域设置') : L('Airspace', '空域');
        const large = !!value && typeof value === 'object' && JSON.stringify(value).length > 6000;
        const objectBranch = !!value && typeof value === 'object' && !Array.isArray(value);
        // Array TreeForm already supplies its own label/disclosure. Objects at depth 0 do not.
        return <div key={key}>
          {(large || objectBranch) && <h4>{label}</h4>}
          {large ? <JsonEditor label={label} value={value} path={pointer(key)} focus={focus} onApply={next => edit(pointer(key), next)} />
            : <TreeForm value={value} path={pointer(key)} label={label} onChange={edit} focus={focus} />}
        </div>;
      })}
    </details>}
  </>;
}
