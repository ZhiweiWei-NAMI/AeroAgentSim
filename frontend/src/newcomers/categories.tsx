import type { Category, Severity } from './api';
import { translate } from '../i18n/translate';

/**
 * Shared category metadata for the template gallery and the event feed.
 * Colors come from a colourblind-safe (Okabe–Ito derived) palette and are
 * stable across the console so filters, cards and feed chips agree.
 */
export interface CategoryMetadata { label: string; color: string }

const t = (key: string) => translate('en-US', key);
export const CATEGORY_STYLE: Record<Category, { label: string; color: string }> = {
  logistics: { label: t('Logistics'), color: '#0072b2' },
  emergency: { label: t('Emergency'), color: '#d55e00' },
  inspection: { label: t('Inspection'), color: '#e69f00' },
  agriculture: { label: t('Agriculture'), color: '#009e73' },
  mobility: { label: t('Mobility'), color: '#56b4e9' },
  security: { label: t('Security'), color: '#cc79a7' },
  airspace: { label: t('Airspace'), color: '#5e4fa2' },
  weather: { label: t('Weather'), color: '#332288' },
  energy: { label: t('Energy'), color: '#b28d00' },
  mixed: { label: t('Mixed'), color: '#666666' },
};

export function categoryMetadata(category: Category, locale = 'en-US'): CategoryMetadata {
  const t = (key: string) => translate(locale, key);
  const labels: Record<Category, string> = {
    logistics: t('Logistics'), emergency: t('Emergency'), inspection: t('Inspection'),
    agriculture: t('Agriculture'), mobility: t('Mobility'), security: t('Security'),
    airspace: t('Airspace'), weather: t('Weather'), energy: t('Energy'), mixed: t('Mixed'),
  };
  return { ...CATEGORY_STYLE[category], label: labels[category] };
}

export function severityLabel(severity: Severity, locale = 'en-US'): string {
  const t = (key: string) => translate(locale, key);
  const labels: Record<Severity, string> = {
    info: t('info'), notice: t('notice'), warning: t('warning'), critical: t('critical'),
  };
  return labels[severity];
}

/** Small inline SVG glyphs, one per scenario category. */
export function CategoryIcon({ category, size = 20 }: { category: Category; size?: number }) {
  const color = categoryMetadata(category).color;
  const common = { width: size, height: size, viewBox: '0 0 24 24', 'aria-hidden': true as const, focusable: false as const };
  switch (category) {
    case 'logistics':
      return <svg {...common}><path fill={color} d="M12 2 3 6.5v11L12 22l9-4.5v-11L12 2zm0 2.2 6.4 3.2L12 10.6 5.6 7.4 12 4.2zM5 9l6 3v7.2l-6-3V9zm14 0v7.2l-6 3V12l6-3z" /></svg>;
    case 'emergency':
      return <svg {...common}><path fill={color} d="M9 2h6v7h7v6h-7v7H9v-7H2V9h7V2z" /></svg>;
    case 'inspection':
      return <svg {...common}><path fill="none" stroke={color} strokeWidth="2.6" d="M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13z" /><path stroke={color} strokeWidth="3" strokeLinecap="round" d="m15.5 15.5 5 5" /></svg>;
    case 'agriculture':
      return <svg {...common}><path fill={color} d="M12 22v-7c0-4 2-7 7-8-1 5-3 8-7 8zm0 0v-5c0-3.5-2-6-6-7 1 4.5 2.5 7 6 7z" /><path stroke={color} strokeWidth="2" strokeLinecap="round" d="M12 15v7" /></svg>;
    case 'mobility':
      return <svg {...common}><circle cx="12" cy="5" r="2.6" fill={color} /><path fill={color} d="M11 9h2l1 6h4l1 2h-6l-1-5-1 3-3 7-2-1 3-7 .5-3z" /></svg>;
    case 'security':
      return <svg {...common}><path fill={color} d="M12 2 4 5v6c0 5.2 3.4 9.4 8 11 4.6-1.6 8-5.8 8-11V5l-8-3zm-1.2 13.5-3.2-3.2 1.4-1.4 1.8 1.8 4.2-4.2 1.4 1.4-5.6 5.6z" /></svg>;
    case 'airspace':
      return <svg {...common}><path fill="none" stroke={color} strokeWidth="2" strokeDasharray="4 2.5" d="M12 4a8.5 8.5 0 1 1 0 17 8.5 8.5 0 0 1 0-17z" /><path fill={color} d="M12 8l3 6H9l3-6zm0 9.4-1.4-1.4h2.8L12 17.4z" /></svg>;
    case 'weather':
      return <svg {...common}><path fill={color} d="M7 18a4.5 4.5 0 0 1-.6-9A5.5 5.5 0 0 1 17 8.3 4 4 0 0 1 17 18H7z" /></svg>;
    case 'energy':
      return <svg {...common}><path fill={color} d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" /></svg>;
    default:
      return <svg {...common}><circle cx="8" cy="9" r="4" fill="#0072b2" /><circle cx="16" cy="9" r="4" fill="#d55e00" opacity="0.85" /><circle cx="12" cy="16" r="4" fill="#009e73" opacity="0.85" /></svg>;
  }
}

export const TEMPLATE_CATEGORIES: Category[] = ['logistics', 'emergency', 'inspection', 'agriculture', 'mobility', 'security', 'airspace', 'weather', 'energy', 'mixed'];
