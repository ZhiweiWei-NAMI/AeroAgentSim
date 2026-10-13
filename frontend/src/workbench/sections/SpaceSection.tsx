import { KeyTrees, keysPresent } from './KeyTrees';
import type { SectionProps } from './types';
import { RegionBlock } from './space/RegionBlock';
import './space.css';

export function SpaceSection(props: SectionProps) {
  const { draft, edit, focus, L } = props;
  return <>
    <p className="hint">{L('Geographic scope, city assets and how types are drawn in the scene.', '地理范围、城市素材以及类型在场景中的呈现方式。')}</p>
    <RegionBlock {...props} />
    <label className="wb-field" data-path="/city">
      <span>{L('City', '城市')}</span>
      <select className="wb-search" value={(draft.city as string | undefined) ?? 'none'} aria-label={L('City', '城市')}
        onChange={event => edit('/city', event.target.value === 'none' ? undefined : event.target.value)}>
        <option value="huangpu">{L('huangpu — bundled lite OSM city of Huangpu, Shanghai; optional detailed appearance pack via `aeroagentsim assets fetch`', 'huangpu — 内置上海黄浦精简 OSM 城市；可通过 `aeroagentsim assets fetch` 获取可选精细外观包')}</option>
        <option value="none">{L('none — no fixed city; use the Region block above for real geography', 'none — 无固定城市；真实地理请使用上方的区域功能')}</option>
      </select>
      <small className="hint">{L(
        'huangpu reuses shared fixed-city assets and works only where the server attached its city descriptor (for example templates). none keeps the world on the local ENU plane or a compiled region.',
        'huangpu 复用共享的固定城市素材，仅当服务器已附带城市描述符（如模板）时生效。none 表示世界仅使用本地 ENU 平面或已编译区域。')}</small>
    </label>
    <KeyTrees keys={keysPresent(draft, ['presentation', 'region', 'airspace'])} draft={draft} edit={edit} focus={focus} />
  </>;
}
