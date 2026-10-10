import assert from 'node:assert/strict';
import test from 'node:test';
import { instrumentCityMapBundle } from './capture-city-platform-acceptance.mjs';

test('exposes one compiled viewer map without replacing its constructor or arguments', () => {
  const source = 'before;this.map=new A(this.shell.map.querySelector(`#city-map`),{foo:1});after;';
  assert.equal(instrumentCityMapBundle(source, 'viewer'),
    'before;window.__aeroVisualMap=this.map=new A(this.shell.map.querySelector(`#city-map`),{foo:1});after;');
});
test('exposes one compiled Studio map without replacing its constructor or arguments', () => {
  assert.equal(instrumentCityMapBundle('this.map=new B(this.mapRoot,{foo:1});', 'studio'),
    'window.__aeroStudioMap=this.map=new B(this.mapRoot,{foo:1});');
});
test('rejects a missing production map hook', () => {
  assert.throws(() => instrumentCityMapBundle('this.map = null;', 'viewer'), /exactly once/);
});
test('rejects ambiguous production hooks', () => {
  const source = 'this.map=new B(this.mapRoot,{});';
  assert.throws(() => instrumentCityMapBundle(source + source, 'studio'), /exactly once/);
});
test('rejects an undeclared map kind', () => {
  assert.throws(() => instrumentCityMapBundle('', 'other'), /Unknown map kind/);
});
