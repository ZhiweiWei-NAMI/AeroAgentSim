/**
 * C11/T4-01: a 409 save means the server draft moved ahead of ours. We never
 * discard local edits; the user compares the changed top-level sections and
 * chooses per section — keep mine, or take the server — then saves the merged
 * draft against the server's current revision.
 */
export type Scenario = Record<string, unknown>;

export interface SectionChoice { section: string; take: 'mine' | 'server' }

/** Top-level scenario sections shown for comparison, in a stable order. */
const SECTION_ORDER = ['run', 'city', 'region', 'presentation', 'airspace', 'entities', 'registry', 'relations', 'resources', 'generators', 'behaviours', 'engines', 'bindings', 'algorithms', 'agents', 'decisions', 'outputs', 'ingress_streams'];

const sectionsOf = (scenario: Scenario): string[] => {
  const keys = Object.keys(scenario ?? {});
  return [...keys].sort((a, b) => {
    const ia = SECTION_ORDER.indexOf(a), ib = SECTION_ORDER.indexOf(b);
    return (ia === -1 ? SECTION_ORDER.length : ia) - (ib === -1 ? SECTION_ORDER.length : ib) || a.localeCompare(b);
  });
};

const record = (value: unknown): value is Scenario => value !== null && typeof value === 'object' && !Array.isArray(value);
const keysOf = (value: Scenario) => Object.keys(value).filter(key => value[key] !== undefined);
/** JSON values compare by content. Undefined object fields represent authored deletions. */
export function sameDraftValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, index) => sameDraftValue(item, b[index]));
  if (record(a) && record(b)) {
    const keys = keysOf(a);
    return keys.length === keysOf(b).length && keys.every(key => sameDraftValue(a[key], b[key]));
  }
  return false;
}

export interface ChangedSection { section: string; mine: unknown; server: unknown }

/** Top-level sections whose content differs between the local draft and the server draft. */
export function changedSections(mine: Scenario, server: Scenario): ChangedSection[] {
  return sectionsOf({ ...mine, ...server }).filter(section => !sameDraftValue(mine[section], server[section]))
    .map(section => ({ section, mine: mine?.[section], server: server?.[section] }));
}

/**
 * Apply per-section choices to the local draft. Only the chosen sections come
 * from the server, including deleted sections. Every difference needs an
 * explicit choice; missing, duplicate and unknown choices are rejected.
 */
export function mergeSections(mine: Scenario, server: Scenario, choices: SectionChoice[]): Scenario {
  const changed = changedSections(mine, server);
  const known = new Set(changed.map(row => row.section));
  const taken = new Set<string>();
  for (const choice of choices) {
    if (!known.has(choice.section)) throw Error(`Section "${choice.section}" is not in conflict`);
    if (taken.has(choice.section)) throw Error(`Duplicate choice for section "${choice.section}"`);
    if (choice.take !== 'mine' && choice.take !== 'server') throw Error(`Invalid choice for section "${choice.section}"`);
    taken.add(choice.section);
  }
  if (taken.size !== known.size) throw Error('Choose a side for every changed section');
  const merged: Scenario = { ...mine };
  for (const row of changed) {
    const choice = choices.find(item => item.section === row.section);
    if (choice?.take === 'server') {
      if (row.server === undefined) delete merged[row.section];
      else merged[row.section] = row.server;
    }
  }
  return merged;
}

export interface FieldDifference { path: string; mine: unknown; server: unknown }
const fieldPath = (path: string, key: string | number) => `${path}/${String(key).replace(/~/g, '~0').replace(/\//g, '~1')}`;
/** Expand changed records and arrays so the comparison shows the actual authored field values. */
export function changedFields(mine: unknown, server: unknown, path = ''): FieldDifference[] {
  if (sameDraftValue(mine, server)) return [];
  if (record(mine) && record(server)) return [...new Set([...Object.keys(mine), ...Object.keys(server)])]
    .flatMap(key => changedFields(mine[key], server[key], fieldPath(path, key)));
  if (Array.isArray(mine) && Array.isArray(server)) return Array.from({ length: Math.max(mine.length, server.length) }, (_, index) => index)
    .flatMap(index => changedFields(mine[index], server[index], fieldPath(path, index)));
  return [{ path, mine, server }];
}

/** Reapply local changes to a newly returned server draft. Disjoint changes combine;
 * overlapping changes remain local and are reported for explicit comparison. */
export function rebaseDraft(base: Scenario, mine: Scenario, server: Scenario): { scenario: Scenario; conflicts: string[] } {
  const conflicts: string[] = [];
  const keyed = (items: unknown[]): items is Array<Scenario & { id: string }> => items.every(item => record(item) && typeof item.id === 'string')
    && new Set(items.map(item => (item as Scenario).id)).size === items.length;
  const merge = (before: unknown, local: unknown, remote: unknown, path: string): unknown => {
    if (sameDraftValue(local, before)) return remote;
    if (sameDraftValue(remote, before) || sameDraftValue(local, remote)) return local;
    if (record(local) && record(remote) && (before === undefined || record(before))) {
      const result: Scenario = {};
      const original = before === undefined ? {} : before;
      for (const key of new Set([...Object.keys(original), ...Object.keys(local), ...Object.keys(remote)])) {
        const value = merge(original[key], local[key], remote[key], fieldPath(path, key));
        if (value !== undefined) result[key] = value;
      }
      return result;
    }
    if (Array.isArray(before) && Array.isArray(local) && Array.isArray(remote)) {
      if (keyed(before) && keyed(local) && keyed(remote)) {
        const original = new Map(before.map(item => [item.id, item])), own = new Map(local.map(item => [item.id, item])), fresh = new Map(remote.map(item => [item.id, item]));
        return [...new Set([...fresh.keys(), ...own.keys()])].flatMap(id => {
          const value = merge(original.get(id), own.get(id), fresh.get(id), fieldPath(path, id));
          return value === undefined ? [] : [value];
        });
      }
      if (before.length === local.length && before.length === remote.length) return local.map((item, index) => merge(before[index], item, remote[index], fieldPath(path, index)));
    }
    conflicts.push(path);
    return local;
  };
  return { scenario: merge(base, mine, server, '') as Scenario, conflicts };
}

/** A short per-section description of the change, for the comparison rows. */
export function sectionSummary(section: string, value: unknown, locale: string): string {
  const zh = locale === 'zh-CN';
  const describe = (item: unknown): string => {
    if (item === undefined) return zh ? '（无）' : '(none)';
    if (Array.isArray(item)) return zh ? `${item.length} 项` : `${item.length} item(s)`;
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>, keys = Object.keys(record);
      return zh ? `${keys.length} 个字段` : `${keys.length} field(s)`;
    }
    return String(item);
  };
  return describe(value);
}
