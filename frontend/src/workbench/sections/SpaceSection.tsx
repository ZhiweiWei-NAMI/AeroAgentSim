import { Leaf } from '../form';
import { KeyTrees, keysPresent } from './KeyTrees';
import type { SectionProps } from './types';

export function SpaceSection({ draft, edit, focus, L }: SectionProps) {
  return <>
    <p className="hint">{L('Geographic scope, city assets and how types are drawn in the scene.', '地理范围、城市素材以及类型在场景中的呈现方式。')}</p>
    <Leaf path="/city" label={L('City', '城市')} value={(draft.city as string | undefined) ?? 'none'} options={['huangpu', 'none']} onChange={(path, value) => edit(path, value === 'none' ? undefined : value)} focus={focus} />
    <KeyTrees keys={keysPresent(draft, ['presentation', 'region', 'airspace'])} draft={draft} edit={edit} focus={focus} />
  </>;
}
