import { describe, expect, it } from 'vitest';
import ts from 'typescript';
import catalog from './messages';
import { localized, translate } from './translate';
import { localizedLabel } from '../pages/inspection-format';
import { decodeTemplates } from '../newcomers/api-decode';
import type { ScenarioTemplate } from '../newcomers/api';

const scenarios = import.meta.glob('../../../scenarios/templates/*/scenario.yaml', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

// Parse source rather than maintaining a handpicked list of page strings. New
// JSX copy, accessibility/help attributes, option labels and literal t() keys
// must be covered in Chinese, even on pages a smoke test never happens to open.
// Workbench and assistant are product surfaces too, not an exception to the
// "whole console" claim.
const sources = import.meta.glob([
  '../{console,newcomers,studio,pages,behaviours,trace,observations,viewport,workbench,assistant,experiments}/**/*.{js,jsx,ts,tsx}',
  '!../**/*.{test,spec}.{js,jsx,ts,tsx}', '!../**/*.e2e.ts', '!../**/*.config.ts', '!../**/.work/**',
  // A test-only label fixture; it is not shipped UI copy.
  '!../**/mapTestFixtures.ts',
], { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
const messages = catalog as Record<string, Record<string, string>>;
const properNames = new Set([
  'AeroAgentSim', 'AeroGraph', 'English', '中文', 'JSON', 'YAML', 'LangGraph', 'WebGL', 'ENU', 'ID', 'LLM',
  'PX4', 'SUMO', 'NS-3', 'ns-3', 'V1', 'SVG', 'CPU', 'GPU', 'UAV', 'API', 'HTTP', 'HTTPS', 'URL', 'CSV', 'Esc',
  'true', 'false', 'null', 'EN', 'en', 'zh', 'en-US', 'zh-CN', 'id', 'Id', 'kg', 'WGS84', 'ODbL', 'OpenStreetMap',
]);
const attributes = new Set(['title', 'description', 'message', 'buttonLabel', 'placeholder', 'aria-label', 'alt', 'emptyText']);
const punctuation = /^[\s\d.,:;/×·%()（）+\-–—、]+$/;
const technicalPath = /^(?:GET|POST|PUT|DELETE|PATCH)?\s*\/[\w./{}:-]*$/;

function uiCopy(value: string) {
  return /[A-Za-z]{2}/.test(value) && !properNames.has(value) && !/^[\w.-]+\.(?:json|yaml|md)$/.test(value);
}

function isTechnicalCopy(value: string) {
  return properNames.has(value) || punctuation.test(value) || technicalPath.test(value);
}

function untranslatedPair(english: string, chinese: string) {
  if (!english.trim() || !chinese.trim()) return true;
  // Very short tokens ("id", "en") are language codes or ids, not prose.
  if (!uiCopy(english)) return false;
  if (isTechnicalCopy(chinese)) return false;
  return !/[\u3400-\u9fff]/.test(chinese);
}

type InlinePair = { en: string; zh: string; where: string };

function sourceKeys(input = sources) {
  const found = new Map<string, string>(), raw = new Map<string, string>(), pairs: InlinePair[] = [];
  const literal = (node: ts.Node | undefined) => node && ts.isStringLiteralLike(node) ? node.text : undefined;
  for (const [path, source] of Object.entries(input)) {
    const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, /\.tsx$/.test(path) ? ts.ScriptKind.TSX : /\.ts$/.test(path) ? ts.ScriptKind.TS : ts.ScriptKind.JSX);
    const where = (node: ts.Node) => `${path}:${file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1}`;
    const add = (text: string, node: ts.Node, normalize = false, untranslated = false) => {
      const key = normalize ? text.replace(/\s+/g, ' ').trim() : text;
      if (uiCopy(key)) {
        found.set(key, where(node)); if (untranslated) raw.set(key, where(node));
      }
    };
    const addPair = (english: string | undefined, chinese: string | undefined, node: ts.Node) => {
      if (english !== undefined && chinese !== undefined) pairs.push({ en: english, zh: chinese, where: where(node) });
    };
    const visit = (node: ts.Node) => {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
        const offset = node.expression.text === 'translate' ? 1 : node.expression.text === 't' ? 0 : -1;
        const key = offset >= 0 ? node.arguments[offset] : undefined;
        if (key && ts.isStringLiteralLike(key)) add(key.text, key);
        // Inline paired-language helpers take (English, Chinese). They are not
        // catalog keys, but their Chinese side still has to be Chinese.
        if (node.expression.text === 'L' && node.arguments.length >= 2) addPair(literal(node.arguments[0]), literal(node.arguments[1]), node.arguments[1]);
      }
      // `zh ? '中文' : 'English'` is the other common inline pattern. The
      // consequent is the Chinese branch by construction in these surfaces.
      if (ts.isConditionalExpression(node) && /\bzh\b/.test(node.condition.getText(file))) {
        const whenTrue = literal(node.whenTrue), whenFalse = literal(node.whenFalse);
        // These surfaces write `zh ? '中文' : 'English'`, but the toggle labels
        // are intentionally reversed (`zh ? 'EN' : '中文'`). Orient on CJK.
        if (whenTrue !== undefined && whenFalse !== undefined) {
          if (/[\u3400-\u9fff]/.test(whenTrue) && !/[\u3400-\u9fff]/.test(whenFalse)) addPair(whenFalse, whenTrue, node.whenTrue);
          else addPair(whenTrue, whenFalse, node.whenTrue);
        }
      }
      if (ts.isJsxText(node)) add(node.text, node, true, true);
      if (ts.isJsxAttribute(node) && attributes.has(node.name.getText(file)) && node.initializer && ts.isStringLiteral(node.initializer)) add(node.initializer.text, node, false, true);
      if (ts.isJsxExpression(node) && node.expression && ts.isStringLiteralLike(node.expression)) {
        if (!ts.isJsxAttribute(node.parent) || attributes.has(node.parent.name.getText(file))) add(node.expression.text, node.expression, false, true);
      }
      if (ts.isPropertyAssignment(node) && ['label', 'description', 'emptyText', 'tooltip'].includes(node.name.getText(file).replace(/['"]/g, '')) && ts.isStringLiteralLike(node.initializer)) add(node.initializer.text, node);
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  return { keys: found, raw, pairs, untranslatedPairs: pairs.filter(pair => untranslatedPair(pair.en, pair.zh)) };
}

// An installed contribution is server-supplied metadata, not a local YAML
// file. It may legitimately have no _zh siblings; the English value must stay
// original and the fallback must not invent a translation.
const installedEnglishOnly: ScenarioTemplate = {
  format: 'aeroagentsim.template/v1', id: 'external-patrol', title: 'External patrol',
  summary: 'Survey a perimeter declared by an installed contribution.',
  category: 'security', difficulty: 'intermediate', duration_s: 600, scenario: 'external-patrol.yaml',
  observe: ['Watch the perimeter.'], try: ['Change the patrol altitude.'],
  knobs: [{ id: 'patrol_altitude_m', label: 'Patrol altitude (m)', type: 'number', default: 80, min: 20, max: 120, pointer: '/patrol/altitude_m' }],
  kpis: ['distance_flown_km'],
};

describe('whole-console bilingual catalog', () => {
  it('translates every shipped entity and fleet label, not just static JSX copy', () => {
    const paths = Object.keys(scenarios);
    // Catalogue health is not a claim about how many templates currently ship.
    expect(paths.length).toBeGreaterThan(0);
    const missing = Object.entries(scenarios).flatMap(([path, source]) => [...source.matchAll(/^([ \t]+)label: (.+)(?:\r?\n\1label_zh: (.+))?$/gm)].flatMap(match => {
      const label = (match[3] ?? localizedLabel(match[2].trim(), 'zh-CN')).replace(/\{index\}/g, '7');
      return /[\u3400-\u9fff]/.test(localizedLabel(label, 'zh-CN')) ? [] : [`${path} → ${label}`];
    }));
    expect(missing).toEqual([]);
    expect(localizedLabel('My research drone', 'zh-CN')).toBe('My research drone');
  });
  it('keeps English-only installed metadata original with an honest fallback', () => {
    const decoded = decodeTemplates([installedEnglishOnly])[0];
    expect(decoded).toEqual(installedEnglishOnly);
    expect(decoded.title_zh).toBeUndefined();
    expect(localized(decoded, 'title', 'zh-CN')).toBe('External patrol');
    expect(localized(decoded, 'summary', 'zh-CN')).toBe('Survey a perimeter declared by an installed contribution.');
    expect(localized(decoded.knobs[0], 'label', 'zh-CN')).toBe('Patrol altitude (m)');
    // Falling through to the English key is the contract; no catalog entry is
    // invented and no generated Chinese is returned for an unknown key.
    expect(translate('zh-CN', decoded.title)).toBe(decoded.title);
  });
  it('has an English and nonempty Chinese translation for every catalog key', () => {
    expect(Object.keys(messages['en-US']).sort()).toEqual(Object.keys(messages['zh-CN']).sort());
    for (const [key, english] of Object.entries(messages['en-US'])) {
      const chinese = messages['zh-CN'][key];
      expect(chinese, key).toBeTruthy();
      const placeholders = (text: string) => [...text.matchAll(/\{([\w]+)\}/g)].map(match => match[1]).sort();
      expect(placeholders(chinese), key).toEqual(placeholders(english));
    }
  });
  it('detects new untranslated JSX copy and an English-only inline pair', () => {
    const sample = sourceKeys({ 'example.tsx': '<><button title={"New help"}>Run now</button><button>{"Missing label"}</button><p>{t("Translated copy")}</p><span>JSON</span><i>{L("Run now", "Run now")}</i><b>{zh ? "Still English" : "Still English"}</b></>' });
    expect([...sample.raw.keys()].sort()).toEqual(['Missing label', 'New help', 'Run now']);
    expect(sample.keys.has('Translated copy')).toBe(true);
    expect(sample.untranslatedPairs.map(pair => pair.zh)).toEqual(['Run now', 'Still English']);
  });
  it('does not render unlocalized product JSX text or accessibility attributes', () => {
    expect([...sourceKeys().raw].map(([key, where]) => `${where} → ${key}`)).toEqual([]);
  });
  it('covers every product UI literal, help label, option and inline language pair', () => {
    const missing = [...sourceKeys().keys].filter(([key]) => !messages['zh-CN'][key]);
    expect(missing.map(([key, where]) => `${where} → ${key}`)).toEqual([]);
    expect(sourceKeys().untranslatedPairs.map(pair => `${pair.where} → ${pair.en} ⇒ ${pair.zh}`)).toEqual([]);
  });
});
