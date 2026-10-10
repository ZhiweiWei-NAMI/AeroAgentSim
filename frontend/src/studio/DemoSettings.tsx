import { useRef, useState } from 'react';
import type { StudioApi, Workspace } from './api';
import { Details } from '../console/Details';
import { useI18n } from '../i18n/I18nProvider';

export function DemoSettings({ api, workspace, save, onApply, profiles }: { api: StudioApi; workspace: Workspace; save: () => Promise<Workspace>; onApply: (workspace: Workspace) => void; profiles: Record<string, Record<string, unknown>> }) {
  const { t } = useI18n();
  const [profile, setProfile] = useState(() => String(workspace.scenario.engines?.decisions?.config?.provider?.profile ?? ''));
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const apply = async (body: unknown) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {
      await save();
      onApply(await api.request(`/v1/studio/workspaces/${workspace.id}/decision-profile`, body));
    } catch (problem) { setError(String(problem)); }
    finally { pending.current = false; setBusy(false); }
  };
  const decisions = workspace.scenario.engines?.decisions;
  const live = decisions?.plugin === 'langgraph';
  const scripted = decisions?.plugin === 'traffic_decisions' && decisions?.config?.mode === 'stub';
  const mode = live ? t('live LangGraph') : scripted ? t('scripted') : t('custom or unconfigured');
  const readiness = (value: unknown) => {
    switch (value) {
      case 'explicit_stub_profile': return t('Explicit simulated profile');
      case 'configuration_contract_only': return t('Configuration contract only; native execution unverified');
      case undefined: return t('Readiness not declared');
      default: return String(value);
    }
  };
  return <section aria-label={t('Traffic demo profiles')} aria-busy={busy}>
    <h3>{t('Decision mode · {mode}', { mode })}</h3>
    <p>{live ? t('Model proposals and provider failures are journaled as LangGraph records.') : scripted ? t('Scripted decision fixture; no model service calls.') : t('This draft does not declare a known demo decision mode. Inspect its engine configuration before running.')}</p>
    <p>{t('Profile edits require validation and a new run. Scripted decisions are the simplest way to try the demo without a model service.')}</p>
    <button type="button" disabled={busy} onClick={() => void apply({ mode: 'scripted' })}>{t('Use scripted decisions')}</button>
    <Details title={t('Configure live LangGraph')} buttonLabel={t('Configure live LangGraph')}>
      <p>{t('Enter an operator profile already configured on the server. Do not paste an API key here. Live model proposals and failures remain available for causal tracing.')}</p>
      <label>{t('Operator profile')}<input aria-label={t('Live operator profile')} value={profile} disabled={busy} onChange={e => setProfile(e.target.value)} placeholder={t('Server-side profile name')} /></label>
      <button type="button" disabled={busy || !profile.trim()} onClick={() => void apply({ provider: { profile: profile.trim() } })}>{t('Use live LangGraph for next run')}</button>
    </Details>
    {busy && <p role="status">{t('Saving and applying decision mode…')}</p>}
    <h3>{t('Plugin / ownership profiles')}</h3>
    <p>{t('These profiles describe converters, field writers and native capabilities. They are contracts, not proof that SUMO or PX4 is installed; native deployment must pass full validation before a new run.')}</p>
    {Object.entries(profiles).map(([id, value]) => <div key={id} style={{ marginBottom: 4 }}>{id} · {readiness(value.readiness)} <Details title={t('Profile {id}', { id })} buttonLabel={t('Profile JSON')}><pre>{JSON.stringify(value, null, 2)}</pre></Details></div>)}
    {!Object.keys(profiles).length && <p>{t('No demo plugin profiles are available.')}</p>}
    {error && <p role="alert">{t('Could not change decision mode: {error}', { error })}</p>}
  </section>;
}
