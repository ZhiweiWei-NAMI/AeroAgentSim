import { describe, expect, it } from 'vitest';
import * as T from 'three';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { translate } from '../i18n/translate';
import type { Assets } from './assets';
import { EntityLayer } from './entities';
import { FeedStore } from './feed-store';
import { businessStyle, displayName, semanticLabel, semanticVehicle } from './semantic';
import { SemanticLayer } from './semantic-layer';
import { Viewport } from './viewport';

const at = { ns: '0', microstep: 0 };
const key = { id: 'uav-7', generation: 0 }, pad = { id: 'charging-pad-north', generation: 0 };
const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'semantic-locale', registryDigest: 'test', start: at,
  types: [{ typeId: 'lowalt:UAV', displayName: 'UAV', ancestors: [] }, { typeId: 'lowalt:ChargingPad', displayName: 'Charging pad', ancestors: ['lowalt:Site'] }], fields: [],
  presentation: [{ typeId: 'lowalt:UAV', positionField: 'position', frame: 'enu', visual: { kind: 'marker' } }] };
function fixture() {
  const store = new FeedStore(header);
  const values = { position: [0, 0, 0], 'oo:name': 'Courier 7', 'lowalt.vehicle_class': 'parcel', 'lowalt.soc': 0.42, 'lowalt.charging': true };
  const facts: FeedCommit['facts'] = Object.entries(values).map(([fieldId, value]) => ({ entity: key, fieldId, value, producer: 'recorded', validFrom: at }));
  facts.push({ entity: pad, fieldId: 'lowalt.site_position', value: [20, 0, 0], producer: 'recorded', validFrom: at });
  store.ingest({ commitIndex: 1, at, created: [{ ...key, typeId: 'lowalt:UAV' }, { ...pad, typeId: 'lowalt:ChargingPad' }], removed: [], facts, retracted: [], edges: [], messages: [], receipts: [] });
  store.seek('0'); return store;
}

describe('locale-aware imperative viewport labels', () => {
  it('translates generated business/charge labels while leaving authored identity and committed facts exact', () => {
    const store = fixture(), entity = store.entities.get('uav-7:0')!;
    expect(semanticLabel(entity, undefined, header)).toBe('Courier 7 · Delivery · Charging · SoC 42%');
    const text = semanticLabel(entity, undefined, header, 'zh-CN');
    expect(text).toContain('Courier 7');
    expect(text).toContain(translate('zh-CN', 'Delivery'));
    expect(text).toContain(translate('zh-CN', 'Charging'));
    expect(text).toContain('电量 42%');
    expect(text).not.toMatch(/\bDelivery\b|\bCharging\b|\bSoC\b/);
    expect(businessStyle(semanticVehicle(entity), 'zh-CN')).toMatchObject({ glyph: 'package', color: '#e69f00', label: translate('zh-CN', 'Delivery') });
    expect(displayName(entity, header, 'zh-CN')).toBe('Courier 7');
    expect(entity.fields.get('lowalt.vehicle_class')!.value).toBe('parcel');
    expect(entity.fields.get('lowalt.soc')!.value).toBe(0.42);
  });

  it('localizes shipped numbered customer labels in Simple view without rewriting recorded facts', () => {
    const store = fixture(), entity = store.entities.get('uav-7:0')!;
    entity.fields.delete('oo:name');
    entity.fields.set('label', { entity: key, fieldId: 'label', value: 'Customer rooftop 14', producer: 'recorded', validFrom: at });
    expect(displayName(entity, header, 'zh-CN')).toBe('客户屋顶 14');
    expect(displayName(entity, header, 'en-US')).toBe('Customer rooftop 14');
    expect(entity.fields.get('label')?.value).toBe('Customer rooftop 14');
  });

  it('renders already-localized entity text on the next display update, not a DOM rewrite pass', () => {
    const store = fixture(), overlay = document.createElement('div');
    Object.defineProperties(overlay, { clientWidth: { value: 600 }, clientHeight: { value: 300 } });
    const layer = new EntityLayer(header, {} as Assets, overlay, () => {}, () => {});
    layer.semantic = true;
    const camera = new T.PerspectiveCamera(48, 2); camera.position.set(0, 10, 30); camera.lookAt(0, 0, 0);
    layer.update(store, '0', camera, key, false);
    expect(overlay.querySelector('button')).toHaveTextContent('Courier 7 · Delivery');
    layer.locale = 'zh-CN'; layer.update(store, '0', camera, key, false);
    expect(overlay.querySelector('button')).toHaveTextContent(translate('zh-CN', 'Delivery'));
    expect(layer.businessLegend(store)).toContainEqual([translate('zh-CN', 'Delivery'), '#e69f00']);
    layer.semantic = false; layer.update(store, '0', camera, key, false);
    expect(overlay.querySelector('button')).toHaveTextContent('uav-7');
    expect(overlay.querySelector('button')).not.toHaveAttribute('data-glyph');
    layer.dispose();
  });

  it('refreshes site labels at the same committed cut and keeps zone legend colors stable', () => {
    const store = fixture(), layer = new SemanticLayer(header);
    layer.update(store, '0', new T.Vector3());
    expect(layer.labelFor('charging-pad-north:0')).toBe('Charging pad North');
    layer.setLocale('zh-CN'); layer.update(store, '0', new T.Vector3());
    expect(layer.labelFor('charging-pad-north:0')).toBe(`${translate('zh-CN', 'Charging pad')} ${translate('zh-CN', 'North')}`);
    const en = SemanticLayer.zoneLegend(), zh = SemanticLayer.zoneLegend('zh-CN');
    expect(zh.map(([, color]) => color)).toEqual(en.map(([, color]) => color));
    expect(zh.map(([label]) => label)).toEqual(['No-fly zone', 'Restricted zone', 'Altitude band', 'Corridor'].map(label => translate('zh-CN', label)));
    expect(zh.map(([label]) => label).join(' ')).not.toMatch(/zone|band|Corridor/);
    layer.dispose();
  });

  it('localizes generated loader prose while preserving exact asset IDs and unknown technical diagnostics', () => {
    const format = (Viewport.prototype as unknown as { displayStatus(message: string): string }).displayStatus;
    const context = { options: { locale: 'zh-CN' } };
    expect(format.call(context, 'Models loaded · x500')).toBe('模型已加载 · x500');
    expect(format.call(context, 'Loading model: /m/x500.glb · stand-ins active')).toBe('正在加载模型：/m/x500.glb · 暂用替代模型');
    expect(format.call(context, 'Exact HTTP 404: /m/x500.glb')).toBe('Exact HTTP 404: /m/x500.glb');
  });
});
