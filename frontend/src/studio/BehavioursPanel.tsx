import { useEffect, useRef, useState } from 'react';
import { Popconfirm } from 'antd';
import { mapping } from '../behaviours/model';
import { BehaviourEditor } from '../behaviours/BehaviourEditor';
import { StudioApi, type TypeRow, type Workspace } from './api';
import { Details } from '../console/Details';
import { useI18n } from '../i18n/I18nProvider';

type Package = Record<string, unknown>;
interface Props {
  initialTab?: string;
  api: StudioApi; workspace: Workspace; scenario: Record<string, any>; types: TypeRow[]; fields: Array<Record<string, unknown>>; relations: Array<Record<string, unknown>>;
  onChange: (scenario: Record<string, any>) => void; onApply: (workspace: Workspace) => void; save: () => Promise<Workspace>; onValidityChange: (valid: boolean) => void; issues?: Array<{ path: string; message: string }>;
}
export function BehavioursPanel({ api, workspace, scenario, types, fields, relations, onChange, onApply, save, onValidityChange, issues = [], initialTab }: Props) {
  const { t } = useI18n();
  const [index, setIndex] = useState(0), [error, setError] = useState(''), [errors, setErrors] = useState<Array<{ path: string; message: string }>>([]), [result, setResult] = useState<unknown>(), [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const collectionSupported = scenario.behaviours === undefined || Array.isArray(scenario.behaviours);
  const packages = Array.isArray(scenario.behaviours) ? scenario.behaviours as Package[] : [];
  // A removed package cannot leave the editor pointing beyond the remaining list.
  const selectedIndex = Math.min(index, Math.max(0, packages.length - 1));
  const value = packages[selectedIndex];
  const inline = mapping(value) && !('path' in value);
  const validWithoutEditor = collectionSupported && (value === undefined || mapping(value) && typeof value.path === 'string' && !!value.path);
  useEffect(() => { if (!inline) onValidityChange(validWithoutEditor); }, [inline, validWithoutEditor, onValidityChange]);
  useEffect(() => { setIndex(0); setErrors([]); setResult(undefined); setError(''); }, [workspace.id]);
  const base = `/v1/studio/workspaces/${workspace.id}`;
  const change = (next: Package) => { onChange({ ...scenario, behaviours: packages.map((row, i) => i === selectedIndex ? next : row) }); setErrors([]); setResult(undefined); };
  const perform = async (action: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try { await action(); } catch (problem) { setError(String(problem)); }
    finally { pending.current = false; setBusy(false); }
  };
  if (!collectionSupported) return <p role="alert">{t('Unsupported behaviour collection retained in scenario JSON. Correct its list shape in the Scenario editor before editing packages.')}</p>;
  return <section aria-label={t('Behaviour package authoring')} aria-busy={busy}>
    <h3>{t('Behaviour package')}</h3>
    <p>{t('A behaviour package describes how an entity reacts: triggers start a chain, guards check conditions, and actions update values or send commands. Changes stay in the shared scenario draft until you save.')}</p>
    <label>{t('Package')} <select aria-label={t('Behaviour package')} value={packages.length ? selectedIndex : ''} disabled={busy || !packages.length} onChange={event => { setIndex(Number(event.target.value)); setErrors([]); setResult(undefined); setError(''); }}>
      {!packages.length && <option value="">{t('No behaviour packages')}</option>}
      {packages.map((row, i) => <option key={i} value={i}>{mapping(row) ? String(row.id || row.path || t('Package {index}', { index: i + 1 })) : t('Unsupported package entry {index}', { index: i + 1 })}</option>)}
    </select></label>
    <button type="button" disabled={busy} onClick={() => {
      const next = { format: 'aeroagentsim.behaviour-package/v1', id: '', revision: 1, registry: { digest_ref: 'scenario.registry_digest' }, evaluator: { version: 'aerograph-predicate/1' }, budgets: { max_transitions_per_instance_per_ns: 64, max_instances: 10000 }, predicates: {}, chains: {}, bindings: [], conflicts: [], injection_points: [] };
      onChange({ ...scenario, behaviours: [...packages, next] }); setIndex(packages.length); setErrors([]); setResult(undefined);
    }}>{t('Add behaviour package')}</button>
    {value !== undefined && <Popconfirm title={t('Remove this behaviour package?')} description={t('Other references are kept. Validate the draft after removal.')} okText={t('Remove')} cancelText={t('Cancel')} onConfirm={() => { onChange({ ...scenario, behaviours: packages.filter((_, i) => i !== selectedIndex) }); setIndex(Math.max(0, selectedIndex - 1)); setErrors([]); setResult(undefined); }}><button type="button" disabled={busy}>{t('Remove behaviour package')}</button></Popconfirm>}
    {!packages.length && <p>{t('No behaviour packages yet. Add one, give it a package ID, then define a condition or state chain.')}</p>}
    {mapping(value) && 'path' in value ? <div>
      <p>{t('Referenced package {path}', { path: String(value.path) })}</p>
      <p>{t('This package is loaded from a file. Editing it here converts the saved file content into an inline package in this draft.')}</p>
      <Details title={t('Package reference')} buttonLabel={t('Reference details')}><pre>{JSON.stringify({ path: value.path }, null, 2)}</pre></Details>
      <button type="button" disabled={busy} onClick={() => void perform(async () => {
        await save();
        const output = await api.request<{ inline_package: Package | null; inline_errors: string[] }>(`${base}/behaviours/${selectedIndex}/export`);
        if (!mapping(output.inline_package)) throw Error(t('Referenced package cannot become inline: {errors}', { errors: output.inline_errors.join('; ') }));
        change(output.inline_package);
      })}>{t('Edit referenced package')}</button>
    </div> : mapping(value) ? <BehaviourEditor initialTab={initialTab} key={`${workspace.id}/${selectedIndex}`} value={value} onChange={change} capabilities={Object.keys(scenario.engines?.behaviour?.config?.capabilities ?? {})} events={(scenario.registry?.messages ?? []).filter((row: Record<string, unknown>) => row.kind === 'event').map((row: Record<string, unknown>) => String(row.id))} types={types} fields={fields} relations={relations} errors={[...errors, ...issues]} onValidityChange={onValidityChange}
      onValidate={() => void perform(async () => {
        await save();
        const validation = await api.request<{ valid: boolean; errors: Array<{ path: string; message: string }> }>(`${base}/behaviours/${selectedIndex}/validate`, {});
        setResult(validation); setErrors(validation.errors); onApply(await api.request<Workspace>(base));
      })}
      onImportYaml={async yaml => {
        await save(); await api.request(`${base}/behaviours`, { index: selectedIndex, yaml }); onApply(await api.request<Workspace>(base)); setErrors([]); setResult(undefined);
      }}
      onExportYaml={async () => { await save(); const output = await api.request<{ yaml: string }>(`${base}/behaviours/${selectedIndex}/export`); return output.yaml; }} /> : value !== undefined && <p role="alert">{t('Unsupported package entry retained: {value}. Edit the Scenario JSON.', { value: JSON.stringify(value) })}</p>}
    {busy && <p role="status">{t('Saving and checking behaviour package…')}</p>}
    {result !== undefined && <Details title={t('Server package validation')} buttonLabel={t('Server package validation')}><pre>{JSON.stringify(result, null, 2)}</pre></Details>}
    {error && <p role="alert">{t('Behaviour operation failed: {error}', { error })}</p>}
  </section>;
}
