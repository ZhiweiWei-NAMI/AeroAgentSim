import { KeyTrees, keysPresent } from './KeyTrees';
import type { SectionProps } from './types';

export function RunSection({ draft, edit, focus, L }: SectionProps) {
  return <>
    <p className="hint">{L('Duration, stepping, random seed, inputs and what is recorded.', '时长、步进、随机种子、输入源以及记录内容。')}</p>
    <KeyTrees keys={keysPresent(draft, ['run', 'outputs', 'ingress_streams'])} draft={draft} edit={edit} focus={focus} />
  </>;
}
