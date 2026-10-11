import { expect, it } from 'vitest';
import { parseLosslessJson, stringifyLossless } from './lossless-json';
it('preserves signed large integers, quoted escapes and literal reserved records', () => {
  const raw = '{"ns":9007199254740993,"negative":-9007199254740993,"text":"\\\"9007199254740993","float":1.5}';
  expect(parseLosslessJson(raw)).toEqual({ ns: {$integer:'9007199254740993'}, negative: {$integer:'-9007199254740993'}, text:'"9007199254740993', float:1.5 });
  expect(stringifyLossless(parseLosslessJson(raw))).toBe(raw);
  expect(stringifyLossless({ literal: {$record:{$integer:'literal'}} })).toBe('{"literal":{"$integer":"literal"}}');
});
it('rejects rounded untagged integers and malformed tags', () => {
  expect(() => stringifyLossless(9007199254740992)).toThrow('Unsafe');
  expect(() => stringifyLossless({$integer:'01'})).toThrow('Invalid');
  expect(stringifyLossless({$number:'2'})).toBe('2.0');
});
it('retains authored float kinds without converting geometry widgets to tags',async()=>{
 const {parseAuthoringJson}=await import('./lossless-json');
 const parsed=parseAuthoringJson('{"scenario":{"engines":{"motion":{"config":{"speed":1.0}}},"behaviours":[{"value":1.0,"integer":1}]}}');
 expect(parsed).toEqual({scenario:{engines:{motion:{config:{speed:1}}},behaviours:[{value:{$number:'1.0'},integer:1}]}});
 expect(stringifyLossless(parsed)).toContain('"value":1.0');
});

it('tags only unsafe integer tokens across signed boundaries and escaped strings', () => {
  const text = 'quoted "9007199254740992", slash \\, newline\n12345678901234567890';
  const source = `[${JSON.stringify(text)},9007199254740991,-9007199254740991,9007199254740992,-9007199254740992,123456789012345678901234567890,-0,1e2,2.0]`;
  expect(parseLosslessJson(source)).toEqual([text, Number.MAX_SAFE_INTEGER, Number.MIN_SAFE_INTEGER,
    {$integer:'9007199254740992'}, {$integer:'-9007199254740992'}, {$integer:'123456789012345678901234567890'}, -0, 100, 2]);
  expect(parseLosslessJson('[1e2,2.0,2]', true)).toEqual([{$number:'1e2'}, {$number:'2.0'}, 2]);
  expect(parseLosslessJson('[1,2,3]')).toEqual([1, 2, 3]);
});

it.each(['[01]', '[1e]', '[1.]', '[+9007199254740992]', '{"n":9007199254740992 "x":0}', '"unterminated 9007199254740992', '"bad\\q9007199254740992"'])('still rejects invalid JSON: %s', source => {
  expect(() => parseLosslessJson(source)).toThrow();
});
