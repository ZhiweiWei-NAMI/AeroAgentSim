/** Typed client for `GET /v1/plugins` (plugin inventory API version 1). */

export const PLUGIN_API_VERSION = 1;

export type PluginOrigin = 'builtin' | 'entry-point';
export type PluginStatus = 'ok' | 'duplicate';

/** One catalog row. `status` and `conflict` are additive: older servers omit them. */
export interface PluginRow {
  name: string;
  origin: PluginOrigin;
  distribution: string | null;
  status: PluginStatus;
  /** Every distribution that declared a duplicated installed name; empty otherwise. */
  conflict: (string | null)[];
}

export interface ScenarioPlugin extends PluginRow {
  templates: string[];
  apply_hooks: string[];
}

export interface PluginInventory {
  api_version: typeof PLUGIN_API_VERSION;
  scenarios: ScenarioPlugin[];
  engines: PluginRow[];
  algorithms: PluginRow[];
  models: PluginRow[];
}

export interface PluginInventoryError {
  /** HTTP status when a response was received; absent for network and decode failures. */
  status?: number;
  message: string;
}

/** Inventory loading never rejects: callers can degrade without a badge or panel. */
export type PluginInventoryResult =
  | { ok: true; inventory: PluginInventory }
  | { ok: false; error: PluginInventoryError };

const object = (value: unknown, what: string): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`The plugin inventory has an invalid ${what}.`);
  return value as Record<string, unknown>;
};

const text = (value: unknown, field: string): string => {
  if (typeof value !== 'string' || !value) throw new Error(`The plugin inventory has an invalid ${field}.`);
  return value;
};

const stringList = (value: unknown, field: string): string[] => {
  if (!Array.isArray(value) || !value.every(item => typeof item === 'string')) throw new Error(`The plugin inventory has an invalid ${field} list.`);
  return value as string[];
};

const nullableStringList = (value: unknown, field: string): (string | null)[] => {
  if (!Array.isArray(value) || !value.every(item => item === null || typeof item === 'string')) throw new Error(`The plugin inventory has an invalid ${field} list.`);
  return value as (string | null)[];
};

const origin = (value: unknown, field: string): PluginOrigin => {
  if (value !== 'builtin' && value !== 'entry-point') throw new Error(`The plugin inventory has an invalid ${field} origin.`);
  return value;
};

/** Missing status means the server predates duplicate reporting; names still reveal duplicates. */
const status = (value: unknown, field: string): PluginStatus => {
  if (value === undefined) return 'ok';
  if (value !== 'ok' && value !== 'duplicate') throw new Error(`The plugin inventory has an invalid ${field} status.`);
  return value;
};

function decodeRow(value: unknown, section: string): PluginRow {
  const data = object(value, `${section} entry`);
  const name = text(data.name, `${section} entry name`);
  const where = `${section} entry ${name}`;
  if (data.distribution !== undefined && data.distribution !== null && typeof data.distribution !== 'string') throw new Error(`The plugin inventory has an invalid distribution for ${name}.`);
  return {
    name,
    origin: origin(data.origin, where),
    distribution: typeof data.distribution === 'string' ? text(data.distribution, `${where} distribution`) : null,
    status: status(data.status, where),
    conflict: data.conflict === undefined ? [] : nullableStringList(data.conflict, `${where} conflict`),
  };
}

function decodeScenario(value: unknown): ScenarioPlugin {
  const data = object(value, 'scenario entry');
  return {
    ...decodeRow(data, 'scenario'),
    templates: stringList(data.templates, 'template'),
    apply_hooks: stringList(data.apply_hooks, 'hook'),
  };
}

/** Strictly decode a v1 response; unknown additive fields are ignored, not rejected. */
export function decodePluginInventory(value: unknown): PluginInventory {
  const data = object(value, 'document');
  if (data.api_version !== PLUGIN_API_VERSION) throw new Error(`The plugin inventory uses unsupported API version ${String(data.api_version)}.`);
  const rows = (key: 'engines' | 'algorithms' | 'models') => {
    const catalog = data[key];
    if (!Array.isArray(catalog)) throw new Error(`The plugin inventory is missing its ${key} catalog.`);
    return catalog.map((entry: unknown) => decodeRow(entry, key));
  };
  if (!Array.isArray(data.scenarios)) throw new Error('The plugin inventory is missing its scenario catalog.');
  return {
    api_version: PLUGIN_API_VERSION,
    scenarios: data.scenarios.map(decodeScenario),
    engines: rows('engines'),
    algorithms: rows('algorithms'),
    models: rows('models'),
  };
}

const messageOf = (problem: unknown) => problem instanceof Error ? problem.message : String(problem);

const errorDetail = (body: string, status: number) => {
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
  } catch { /* Keep the raw text when the error body is not JSON. */ }
  return body.trim() || `HTTP ${status}`;
};

/** Fetch and decode the inventory, returning a typed failure instead of throwing. */
export async function loadPluginInventory(base: string, signal?: AbortSignal): Promise<PluginInventoryResult> {
  let response: Response;
  try {
    response = await fetch(`${base.replace(/\/$/, '')}/v1/plugins`, { signal, headers: { Accept: 'application/json' } });
  } catch (problem) {
    return { ok: false, error: { message: messageOf(problem) } };
  }
  let body: string;
  try {
    body = await response.text();
  } catch (problem) {
    return { ok: false, error: { status: response.status, message: messageOf(problem) } };
  }
  if (!response.ok) return { ok: false, error: { status: response.status, message: errorDetail(body, response.status) } };
  let value: unknown;
  try {
    value = JSON.parse(body);
  } catch {
    return { ok: false, error: { message: 'The plugin inventory is not valid JSON.' } };
  }
  try {
    return { ok: true, inventory: decodePluginInventory(value) };
  } catch (problem) {
    return { ok: false, error: { message: messageOf(problem) } };
  }
}

/**
 * The one scenario plugin that declares a template id. Ambiguous ids (two owners,
 * or a duplicated plugin name) deliberately return nothing: a template can also
 * come from a configured root, so a single matching id is the strongest evidence
 * the inventory can provide.
 */
export function scenarioPluginForTemplate(inventory: PluginInventory | undefined, templateId: string): ScenarioPlugin | undefined {
  if (!inventory) return undefined;
  const owners = inventory.scenarios.filter(row => row.status !== 'duplicate' && row.conflict.length === 0 && row.templates.includes(templateId));
  if (owners.length !== 1) return undefined;
  const sameName = inventory.scenarios.filter(row => row.name === owners[0].name);
  return sameName.length === 1 ? owners[0] : undefined;
}
