import { getAt, JsonEditor, pointer, TreeForm } from '../form';
import type { SectionProps } from './types';

/** Generic editors for top-level scenario keys; very large values fall back to a JSON editor. */
export function KeyTrees({ keys, draft, edit, focus }: Pick<SectionProps, 'draft' | 'edit' | 'focus'> & { keys: string[] }) {
  return <>{keys.map(key => {
    const value = getAt(draft, pointer(key));
    return <div key={key}><h4 className="wb-mono">{key}</h4>
      {value && typeof value === 'object' && JSON.stringify(value).length > 6000
        ? <JsonEditor label={key} value={value} path={pointer(key)} focus={focus} onApply={next => edit(pointer(key), next)} />
        : <TreeForm value={value} path={pointer(key)} label={key} onChange={edit} focus={focus} />}
    </div>;
  })}</>;
}

export const keysPresent = (draft: SectionProps['draft'], keys: string[]) => keys.filter(key => key in draft);
