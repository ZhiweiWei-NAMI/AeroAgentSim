import messages from './messages';

/** Translate a catalog message without React (for pure presentation helpers). */
export function translate(locale, key, vars = {}) {
  const template = messages[locale]?.[key] ?? messages['en-US']?.[key] ?? key;
  return Object.entries(vars).reduce(
    (result, [name, value]) => result.split(`{${name}}`).join(String(value)),
    template
  );
}

export function localized(value, field, locale) {
  return locale === 'zh-CN' ? value?.[`${field}_zh`] ?? value?.[field] : value?.[field];
}
