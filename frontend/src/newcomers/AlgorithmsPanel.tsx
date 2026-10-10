import './algorithms.css';
import {useEffect, useMemo, useState} from 'react';
import {Alert, Button, Select, Space, Tag} from 'antd';
import {Details} from '../console/Details';
import {useI18n} from '../i18n/I18nProvider';
import {localized} from '../i18n/translate';
import {TemplateApi, type AlgorithmDescriptor, type AlgorithmSlot} from './api';
import {algorithmSlots} from '../studio/guided-model';
import {algorithmSlotLabel} from './presentation';

// Documented effective defaults, never authored as new overrides or invented
// catalog entries. Server-advertised labels/descriptions are localized only.
const documentedDefaults: Partial<Record<AlgorithmSlot, string>> = {
  allocation: 'nearest', path_planning: 'corridor_astar',
  conflict_resolution: 'altitude_layers', charging: 'threshold_return',
};
export interface AlgorithmsPanelProps {
  base: string;
  algorithms: Partial<Record<AlgorithmSlot, string>>;
  onChange: (next: Partial<Record<AlgorithmSlot, string>>) => void;
  applicable?: boolean;
}
export function AlgorithmsPanel({base, algorithms, onChange, applicable = true}: AlgorithmsPanelProps) {
  const {t, locale} = useI18n();
  const api = useMemo(() => new TemplateApi(base), [base]);
  const [catalog, setCatalog] = useState<AlgorithmDescriptor[]>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!applicable) return;
    let active = true;
    setLoading(true); setError('');
    api.algorithms().then(rows => { if (active) { setCatalog(rows); setError(''); } })
      .catch(problem => { if (active) { setCatalog(undefined); setError(String(problem?.message ?? problem)); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api, attempt, applicable]);
  const bySlot = useMemo(() => {
    const map: Partial<Record<AlgorithmSlot, AlgorithmDescriptor[]>> = {};
    for (const row of catalog ?? []) if (algorithmSlots.includes(row.slot)) (map[row.slot] ??= []).push(row);
    return map;
  }, [catalog]);
  const slotNames: Record<AlgorithmSlot, string> = {
    allocation: t('Allocation'), path_planning: t('Path Planning'),
    conflict_resolution: t('Conflict Resolution'), charging: t('Charging'),
  };
  const describe = (slot: AlgorithmSlot, id: string | undefined): string | undefined => localized((bySlot[slot] ?? []).find(row => row.id === id), 'description', locale);
  const set = (slot: AlgorithmSlot, value?: string) => { const next = {...algorithms}; if (value) next[slot] = value; else delete next[slot]; onChange(next); };
  const unknown = algorithmSlots.filter(slot => algorithms[slot] && !(bySlot[slot] ?? []).some(row => row.id === algorithms[slot]));
  if (!applicable) return <div className="guided-algorithms" data-testid="studio-algorithms">
    <Alert type="info" showIcon data-testid="studio-algorithms-not-applicable" message={t('These algorithm choices are not used by this scenario')}
      description={t('This scenario follows its own authored rules. Choose a delivery or other task-based fleet scenario to compare task assignment, routing, conflict avoidance and charging choices.')} />
  </div>;
  return <div className="guided-algorithms" data-testid="studio-algorithms"><Space direction="vertical" style={{width: '100%'}} size="small">
    {error && <Alert type="warning" showIcon data-testid="studio-algorithms-error" message={t('Algorithm catalog is unavailable')}
      description={<Space direction="vertical" size="small"><span>{t('The simulation service did not answer the algorithm catalog request. Your saved choices are kept. Retry when the service is available.')}</span><Details title={t('Catalog request')}><p>{error}</p></Details><Button size="small" onClick={() => setAttempt(n => n + 1)}>{t('Retry')}</Button></Space>} />}
    {catalog && !catalog.length && <Alert type="info" showIcon message={t('No algorithm plug-ins are registered on this service yet.')} />}
    {catalog && catalog.length > 0 && unknown.map(slot => <Alert key={slot} type="info" showIcon message={t('An authored {slot} choice is not in the current catalog. Validation will check whether it can run.', {slot: slotNames[slot].toLowerCase()})} />)}
    <div className="guided-algorithm-grid">{algorithmSlots.map(slot => {
      const override = algorithms[slot];
      const effective = override ?? documentedDefaults[slot];
      const isDefault = !override;
      const chosen = (bySlot[slot] ?? []).find(row => row.id === effective);
      const chosenLabel = effective ? chosen ? localized(chosen, 'label', locale) : isDefault ? t('Documented default') : t('Saved choice') : undefined;
      const optionLabel = (label: string, defaultOption: boolean) => defaultOption ? t('{label} (default)', {label}) : label;
      const options = (bySlot[slot] ?? []).map(row => ({value: row.id, label: optionLabel(localized(row, 'label', locale), isDefault && row.id === effective), title: localized(row, 'description', locale)}));
      return <label key={slot} className="guided-algorithm-slot">
        <span className="guided-label">{algorithmSlotLabel(slot, locale)}{isDefault && effective && <Tag className="guided-algorithm-default-tag">{t('(default)')}</Tag>}</span>
        <Select data-testid={`algorithm-${slot}`} aria-label={slotNames[slot]} style={{width: '100%'}} loading={loading}
          placeholder={catalog ? t('Choose an algorithm') : t('Catalog unavailable')} labelInValue
          value={effective ? {value: effective, label: chosenLabel ? optionLabel(chosenLabel, isDefault) : effective} : undefined}
          disabled={!!error || loading}
          options={effective && !options.some(row => row.value === effective) ? [...options, {value: effective, label: optionLabel(chosenLabel!, isDefault), title: isDefault ? t('Documented default for this slot') : t('Authored choice')}] : options}
          onChange={value => set(slot, value.value)} />
        {describe(slot, effective) && <small className="guided-algorithm-description">{describe(slot, effective)}{isDefault && t(' This documented default applies until you pick another algorithm.')}</small>}
        {override && !describe(slot, override) && !error && <small className="guided-algorithm-description">{t('This saved choice is not described in the catalog.')}</small>}
        {override && <Button size="small" type="text" data-testid={`algorithm-reset-${slot}`} className="guided-algorithm-reset" onClick={() => set(slot, undefined)}>{t('Reset to default')}</Button>}
      </label>;
    })}</div>
    <Details title={t('Technical ids')} buttonLabel={t('Technical ids')}><pre>{JSON.stringify(algorithms, null, 2)}</pre>
      <p className="guided-muted">{t('Slots without a saved choice run the documented low-altitude defaults: {defaults}.', {defaults: algorithmSlots.map(slot => `${slot}=${documentedDefaults[slot]}`).join(', ')})}</p>
    </Details>
  </Space></div>;
}
export default AlgorithmsPanel;
