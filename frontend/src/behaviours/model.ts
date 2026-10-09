export type Draft = Record<string, unknown>;
export type Path = Array<string | number>;
export const mapping = (value: unknown): value is Draft => !!value && typeof value === 'object' && !Array.isArray(value);
/** Path patches preserve every sibling, including unsupported extension data. */
export function patch(value: unknown, path: Path, next: unknown): unknown {
  if (!path.length) return next;
  const [key, ...rest] = path;
  if (Array.isArray(value) && typeof key === 'number') return value.map((item, index) => index === key ? patch(item, rest, next) : item);
  if (mapping(value) && typeof key === 'string') return { ...value, [key]: patch(value[key], rest, next) };
  throw Error(`Cannot edit authored path ${path.join('.')}; use raw JSON`);
}
export interface AuthorType { id: string; parents: string[]; abstract: boolean }
export function isA(type: string, ancestor: string, types: AuthorType[]): boolean {
  const pending = [type], seen = new Set<string>();
  while (pending.length) { const id = pending.pop()!; if (id === ancestor) return true; if (seen.has(id)) continue; seen.add(id); pending.push(...(types.find(row => row.id === id)?.parents ?? [])); }
  return false;
}
export function applicableFields(type: string, types: AuthorType[], fields: Draft[]): Draft[] {
  return fields.filter(field => { const owner = field.declaring_type ?? field.type ?? field.declaringClass; return typeof owner === 'string' && isA(type, owner, types); });
}
export function applicableRelations(source:string|undefined,target:string|undefined,types:AuthorType[],relations:Draft[]):Draft[] {
  return relations.filter(relation=>{
    const sourceType=relation.source_type??relation.sourceClass,targetType=relation.target_type??relation.targetClass;
    return (!source||typeof sourceType!=='string'||isA(source,sourceType,types))&&(!target||typeof targetType!=='string'||isA(target,targetType,types));
  });
}
export const arity: Record<string,number> = {not:1,if:3,implies:2,is_unknown:1,eq:2,ne:2,lt:2,lte:2,gt:2,gte:2,sub:2,div:2,abs:1,sqrt:1,pow:2,clamp:3,between:3,in:2,contains:2,subset:2,disjoint:2,set_equal:2,unique_count:1,len:1,count:1,sum:1,vector_norm:1,dot:2,cross:2,distance:2,date_time:1,interval_overlap:2,interval_contains:2};
export const variadic = ['and','or','add','mul','min','max','norm','same_identity'];
export const temporal = ['hold','all_window','any_window','count_window','delta','rate','rise','fall','changed','stable_window','entered','exited'];
export const operators = [...Object.keys(arity),...variadic,...temporal,'all','any','sequence'];
export function astKind(node: unknown): string {
  if (!mapping(node)) return 'unsupported';
  if ('op' in node) return typeof node.op === 'string' && operators.includes(node.op) ? 'operator' : 'unsupported';
  const leaves = ['field','literal','relation','parameter','var','time'].filter(name=>name in node);
  return leaves.length === 1 ? leaves[0] : 'unsupported';
}
