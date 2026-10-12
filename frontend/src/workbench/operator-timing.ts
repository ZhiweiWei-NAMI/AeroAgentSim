import { translate } from '../i18n/translate';
import { mapping, type Draft } from '../behaviours/model';

type Translator = (key: string, vars?: Record<string, string | number>) => string;
export function exactNanos(value: unknown, label: string, t: Translator = (key, vars) => translate('en-US', key, vars)): bigint {
  const text = typeof value === 'number' && Number.isSafeInteger(value) ? String(value) : typeof value === 'string' ? value : mapping(value) ? value.$integer : undefined;
  if (typeof text !== 'string' || !/^(0|[1-9]\d*)$/.test(text)) throw Error(t('{field}: declared exact nonnegative integer required', { field: label }));
  return BigInt(text);
}
export function operatorTiming(scenario: Draft, t: Translator = (key, vars) => translate('en-US', key, vars)) {
  const stream = Array.isArray(scenario.ingress_streams) ? scenario.ingress_streams.find(row => mapping(row) && row.id === 'operator') : undefined;
  if (!mapping(scenario.run) || !mapping(stream)) throw Error(t('Operator timing requires the authored run and ingress source'));
  const step = exactNanos(scenario.run.advance_ns, t('Run advance'), t);
  if (step === 0n) throw Error(t('Run advance must be positive'));
  return { step, initial: exactNanos(stream.initial_watermark_ns, t('Operator initial watermark'), t) };
}

