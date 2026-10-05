import { UI_PAIRS } from './translations.js';

export const LOCALE_KEY = 'aero-console.locale.v1';
export const LOCALES = Object.freeze(['zh-CN', 'en-US']);
let locale = 'zh-CN';
try {
  const saved = globalThis.localStorage?.getItem(LOCALE_KEY);
  if (LOCALES.includes(saved)) locale = saved;
} catch { /* A blocked preference store must not prevent in-memory editing. */ }

export const getLocale = () => locale;
export function setLocale(value) {
  if (!LOCALES.includes(value)) throw new TypeError('Unsupported locale');
  locale = value;
  let persisted = true;
  try { globalThis.localStorage?.setItem(LOCALE_KEY, value); } catch { persisted = false; }
  if (globalThis.document) document.documentElement.lang = value;
  return persisted;
}

const aliases = new Map();
for (const [zh, en] of UI_PAIRS) {
  aliases.set(zh, { 'zh-CN': zh, 'en-US': en });
  aliases.set(en, { 'zh-CN': zh, 'en-US': en });
}
const escapeRegex = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Machine status codes are localized as exact display labels, never in HTML
// attribute names, CSS classes, data-action values or contract identifiers.
const phrases = [...aliases.keys()].filter(key => !/^[a-z_]+$/.test(key)).sort((a, b) => b.length - a.length);
const matcher = new RegExp(phrases.map(escapeRegex).join('|'), 'g');

/** Only call on authored UI copy. Never pass user values, IDs or source quotes. */
export function t(copy) {
  const text = String(copy ?? '');
  if (aliases.has(text)) return aliases.get(text)[locale];
  return text.replace(matcher, match => aliases.get(match)[locale]);
}

/** Translate literal template parts; interpolated values are deliberately untouched. */
export function html(parts, ...values) {
  return parts.reduce((result, part, index) => result + t(part) + (index < values.length ? values[index] ?? '' : ''), '');
}

/** Localize known validation wording while retaining unknown browser exception text. */
export function message(copy) {
  const text = String(copy ?? '');
  if (locale === 'zh-CN') {
    const numeric = /^Must be a finite (safe integer|number)( greater than zero| at least (.*))?\.$/.exec(text);
    if (numeric) return `必须是有限${numeric[1] === 'safe integer' ? '安全整数' : '数值'}${numeric[2] === ' greater than zero' ? '且大于零' : numeric[3] !== undefined ? `且不小于 ${numeric[3]}` : ''}。`;
    if (text.startsWith('Supported values: ')) return '支持的值：' + text.slice('Supported values: '.length);
    if (text.startsWith('Duplicate expanded entity ID: ')) return '展开后的实体 ID 重复：' + text.slice('Duplicate expanded entity ID: '.length);
    if (text.startsWith('Invalid configuration: ')) return '配置无效：' + text.slice('Invalid configuration: '.length);
    const skipped = /^已跳过 (\d+) 条损坏记录，保留其余有效版本与运行。$/.exec(text);
    if (skipped) return text;
  } else {
    const skipped = /^已跳过 (\d+) 条损坏记录，保留其余有效版本与运行。$/.exec(text);
    if (skipped) return `Skipped ${skipped[1]} corrupt records; other valid versions and runs are retained.`;
  }
  return t(text);
}

if (globalThis.document) document.documentElement.lang = locale;
