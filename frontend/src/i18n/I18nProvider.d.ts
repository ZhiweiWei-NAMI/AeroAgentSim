import type { ReactNode } from 'react';
export type Locale = 'en-US' | 'zh-CN';
export interface I18nValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string, vars?: Record<string, unknown>) => string;
}
export const STORAGE_KEY: string;
export function initialLocale(): Locale;
export function I18nProvider(props: { children: ReactNode }): JSX.Element;
export function useI18n(): I18nValue;
