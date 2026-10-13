import type { SectionId, ValidationIssue } from '../api';
import { pointer } from '../form';

export const SECTIONS: Array<{ id: SectionId; en: string; zh: string; keys: string[] }> = [
  { id: 'space', en: 'Scene & space', zh: '场景与空间', keys: ['city', 'presentation', 'region', 'airspace'] },
  { id: 'objects', en: 'Objects', zh: '对象', keys: ['entities', 'registry'] },
  { id: 'relations', en: 'Relations & resources', zh: '关系与资源', keys: ['relations', 'resources'] },
  { id: 'tasks', en: 'Tasks & behaviours', zh: '任务与行为', keys: ['generators', 'behaviours'] },
  { id: 'components', en: 'Execution components', zh: '执行组件', keys: ['engines', 'bindings'] },
  { id: 'algorithms', en: 'Algorithms & agents', zh: '算法与智能体', keys: ['algorithms', 'agents', 'decisions'] },
  { id: 'run', en: 'Run & recording', zh: '运行与记录', keys: ['run', 'outputs', 'ingress_streams'] },
];
const SECTION_OF: Record<string, SectionId> = Object.fromEntries(SECTIONS.flatMap(section => section.keys.map(key => [key, section.id])));
/** Section of a validation issue, from the server or from its authored path. */
export function issueSection(issue: ValidationIssue): SectionId | undefined {
  if (issue.section) return issue.section;
  const head = /^\/?\$?\.?([A-Za-z_]+)/.exec(issue.path ?? '')?.[1] ?? /^([A-Za-z_]+)[.[:]/.exec(issue.message)?.[1];
  return head ? SECTION_OF[head] : undefined;
}
/** Authored `$.entities[3].facts` → JSON pointer `/entities/3/facts`. */
export function issuePointer(path: string) {
  if (path.startsWith('/')) return path;
  return pointer(...path.replace(/^\$/, '').replace(/\[(\d+)\]/g, '.$1').split('.').filter(Boolean));
}
