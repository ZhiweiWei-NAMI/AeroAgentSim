import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';

import messages from './messages';

const I18nContext = createContext({
  locale: 'en-US',
  setLocale: () => {},
  t: (key) => key,
});

const STORAGE_KEY = 'aeroagentsim.locale';

function interpolate(message, vars = {}) {
  return Object.entries(vars).reduce(
    (result, [key, value]) => result.replace(new RegExp(`\\{${key}\\}`, 'g'), String(value)),
    message
  );
}

export function I18nProvider({ children }) {
  const [locale, setLocale] = useState(() => window.localStorage.getItem(STORAGE_KEY) || 'en-US');

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, locale);
  }, [locale]);

  const value = useMemo(
    () => ({
      locale,
      setLocale,
      t: (key, vars = {}) => {
        const template =
          messages[locale]?.[key] ??
          messages['en-US']?.[key] ??
          key;
        return interpolate(template, vars);
      },
    }),
    [locale]
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
