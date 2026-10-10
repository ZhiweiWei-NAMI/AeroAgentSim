import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { translate } from './translate';

const I18nContext = createContext({
  locale: 'en-US',
  setLocale: () => {},
  t: (key, vars = {}) => translate('en-US', key, vars),
});

export const STORAGE_KEY = 'aeroagentsim.locale';

export function initialLocale() {
  const requested = new URLSearchParams(window.location.search).get('lang');
  if (requested === 'zh' || requested === 'zh-CN') return 'zh-CN';
  if (requested === 'en' || requested === 'en-US') return 'en-US';
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'zh-CN' ? 'zh-CN' : 'en-US';
  } catch {
    return 'en-US';
  }
}

export function I18nProvider({ children }) {
  const [locale, updateLocale] = useState(initialLocale);
  const setLocale = (value) => {
    const next = value === 'zh-CN' ? 'zh-CN' : 'en-US';
    updateLocale(next);
    // An explicit switch must win over the URL on the next reload too.
    const url = new URL(window.location.href);
    if (url.searchParams.has('lang')) {
      url.searchParams.set('lang', next === 'zh-CN' ? 'zh' : 'en');
      window.history.replaceState(window.history.state, '', url);
    }
  };

  useEffect(() => {
    document.documentElement.lang = locale === 'zh-CN' ? 'zh-CN' : 'en';
    try { window.localStorage.setItem(STORAGE_KEY, locale); } catch { /* Private browsing can disable storage. */ }
  }, [locale]);

  const value = useMemo(
    () => ({
      locale,
      setLocale,
      t: (key, vars = {}) => translate(locale, key, vars),
    }),
    [locale]
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
