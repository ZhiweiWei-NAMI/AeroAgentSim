import { describe, expect, it } from 'vitest';
import ts from 'typescript';
import catalog from './messages';
import { localizedLabel } from '../pages/inspection-format';

const scenarios = import.meta.glob('../../../scenarios/templates/*/scenario.yaml', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

// Parse source rather than maintaining a handpicked list of page strings. New
// JSX copy, accessibility/help attributes, option labels and literal t() keys
// must be covered in Chinese, even on pages a smoke test never happens to open.
const sources = import.meta.glob([
  '../{console,newcomers,studio,pages,behaviours,trace,observations,viewport}/**/*.{js,jsx,ts,tsx}',
  '!../**/*.{test,spec}.{js,jsx,ts,tsx}', '!../**/*.e2e.ts', '!../**/*.config.ts', '!../**/.work/**',
], { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
const messages = catalog as Record<string, Record<string, string>>;
const properNames = new Set(['AeroAgentSim', 'AeroGraph', 'English', '中文', 'JSON', 'YAML', 'LangGraph', 'WebGL', 'ENU', 'ID', 'LLM', 'PX4', 'SUMO', 'NS-3', 'ns-3', 'V1', 'SVG']);
const attributes = new Set(['title', 'description', 'message', 'buttonLabel', 'placeholder', 'aria-label', 'alt', 'emptyText']);
function uiCopy(value: string) {
  return /[A-Za-z]{2}/.test(value) && !properNames.has(value) && !/^[\w.-]+\.(?:json|yaml|md)$/.test(value);
}
function sourceKeys(input = sources) {
  const found = new Map<string, string>(), raw = new Map<string, string>();
  for (const [path, source] of Object.entries(input)) {
    const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, /\.tsx$/.test(path) ? ts.ScriptKind.TSX : /\.ts$/.test(path) ? ts.ScriptKind.TS : ts.ScriptKind.JSX);
    const add = (text: string, node: ts.Node, normalize = false, untranslated = false) => {
      const key = normalize ? text.replace(/\s+/g, ' ').trim() : text;
      if (uiCopy(key)) {
        const where = `${path}:${file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1}`;
        found.set(key, where); if (untranslated) raw.set(key, where);
      }
    };
    const visit = (node: ts.Node) => {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
        const offset = node.expression.text === 'translate' ? 1 : node.expression.text === 't' ? 0 : -1;
        const key = offset >= 0 ? node.arguments[offset] : undefined;
        if (key && ts.isStringLiteralLike(key)) add(key.text, key);
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
  return { keys: found, raw };
}

describe('whole-console bilingual catalog', () => {
  it('translates every shipped entity and fleet label, not just static JSX copy', () => {
    expect(Object.keys(scenarios)).toHaveLength(8);
    const missing = Object.entries(scenarios).flatMap(([path, source]) => [...source.matchAll(/^\s+label: (.+)$/gm)].flatMap(match => {
      const label = match[1].trim().replace(/\{index\}/g, '7');
      return /[\u3400-\u9fff]/.test(localizedLabel(label, 'zh-CN')) ? [] : [`${path} → ${label}`];
    }));
    expect(missing).toEqual([]);
    expect(localizedLabel('My research drone', 'zh-CN')).toBe('My research drone');
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
  it('detects new untranslated JSX copy even if someone adds it to the catalog', () => {
    const sample = sourceKeys({ 'example.tsx': '<><button title={"New help"}>Run now</button><button>{"Missing label"}</button><p>{t("Translated copy")}</p><span>JSON</span></>' });
    expect([...sample.raw.keys()].sort()).toEqual(['Missing label', 'New help', 'Run now']);
    expect(sample.keys.has('Translated copy')).toBe(true);
  });
  it('does not render unlocalized main-page JSX text or accessibility attributes', () => {
    expect([...sourceKeys().raw].map(([key, where]) => `${where} → ${key}`)).toEqual([]);
  });
  it('covers every main-page UI literal, help label and option', () => {
    const missing = [...sourceKeys().keys].filter(([key]) => !messages['zh-CN'][key]);
    expect(missing.map(([key, where]) => `${where} → ${key}`)).toEqual([]);
  });
});
