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
