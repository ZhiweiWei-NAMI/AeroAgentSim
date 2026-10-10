import { localized, translate } from '../i18n/translate';
import type { AlgorithmDescriptor, AlgorithmSlot, BusinessEvent, KnobValue, TemplateKnob } from './api';

/** Presentation only: choice values and algorithm ids sent to the service never change. */
export function knobOptionLabel(knob: TemplateKnob, value: KnobValue, locale = 'en-US', algorithms?: AlgorithmDescriptor[]): string {
  const t = (key: string) => translate(locale, key);
  const index = knob.options?.indexOf(value) ?? -1;
  if (locale === 'zh-CN' && index >= 0 && knob.options_zh?.[index] !== undefined) return knob.options_zh[index];
  const algorithm = algorithms?.find(row => row.id === value);
  if (algorithm) return localized(algorithm, 'label', locale);
  if (typeof value === 'boolean') return value ? t('yes') : t('no');
  if (typeof value !== 'string') return String(value);
  if (value === 'scripted') return t('Recorded decisions');
  if (value === 'live') return t('Live model');
  return value.replace(/_/g, ' ');
}

export function algorithmSlotLabel(slot: AlgorithmSlot, locale = 'en-US'): string {
  const t = (key: string) => translate(locale, key);
  switch (slot) {
    case 'allocation': return t('Task assignment');
    case 'path_planning': return t('Finding a route');
    case 'conflict_resolution': return t('Avoiding conflicts');
    case 'charging': return t('When to recharge');
  }
}

/** Keep the established English labels; translate known runtime states, not ids. */
export function runStatusLabel(status: string, locale = 'en-US', rawEnglish = false): string {
  const t = (key: string) => translate(locale, key);
  const english = (() => {
    switch (status) {
      case 'created': return t('Created');
      case 'queued': return t('Queued');
      case 'starting': return t('Starting');
      case 'running': return t('Running');
      case 'paused': return t('Paused');
      case 'completed': return t('Completed');
      case 'stopped': return t('Stopped');
      case 'faulted': return t('Faulted');
      case 'failed': return t('Failed');
      case 'interrupted': return t('Interrupted');
      case 'waiting_for_input': return t('Waiting for your event');
      case 'input_timeout': return t('Input Timeout');
      default: return status.replace(/[_-]/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
    }
  })();
  return rawEnglish && locale !== 'zh-CN' ? status : english;
}

/** Feed cards and timeline markers share exactly the same localized narrative. */
export function eventNarrative(event: BusinessEvent, locale = 'en-US', entityNames?: Map<string, string>): string {
  return event.entities.reduce((text, id) => text.replace(
    new RegExp(`(?<![\\w-])${id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\w-])`, 'g'),
    entityNames?.get(id) ?? id,
  ), localized(event, 'text', locale));
}
