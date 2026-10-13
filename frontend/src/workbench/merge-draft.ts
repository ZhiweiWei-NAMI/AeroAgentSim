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

const sameJson = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export interface ChangedSection { section: string; mine: unknown; server: unknown }

/** Top-level sections whose content differs between the local draft and the server draft. */
export function changedSections(mine: Scenario, server: Scenario): ChangedSection[] {
  return sectionsOf({ ...mine, ...server }).filter(section => !sameJson(mine?.[section], server?.[section]))
    .map(section => ({ section, mine: mine?.[section], server: server?.[section] }));
}

/**
 * Apply per-section choices to the local draft. Only the chosen sections come
 * from the server; everything not listed in `server` cannot be taken and
 * stays as authored locally. Unknown choices are rejected, never defaulted.
 */
export function mergeSections(mine: Scenario, server: Scenario, choices: SectionChoice[]): Scenario {
  const changed = changedSections(mine, server);
  const known = new Set(changed.map(row => row.section));
  const taken = new Set<string>();
  for (const choice of choices) {
    if (!known.has(choice.section)) throw Error(`Section "${choice.section}" is not in conflict`);
    if (taken.has(choice.section)) throw Error(`Duplicate choice for section "${choice.section}"`);
    taken.add(choice.section);
  }
  const merged: Scenario = { ...mine };
  for (const row of changed) {
    const choice = choices.find(item => item.section === row.section);
    if (choice?.take === 'server' && row.server !== undefined) merged[row.section] = row.server;
  }
  return merged;
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
