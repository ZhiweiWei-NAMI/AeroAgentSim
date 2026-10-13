/**
 * jsdom lacks createSVGRect, so Leaflet's vector renderer falls through to nothing at
 * runtime; under real @types/leaflet the Browser flags are also typed read-only. Central
 * helper for the map tests: force the SVG renderer path (real browsers always take it).
 */
import * as L from 'leaflet';
import { vi } from 'vitest';

export function forceSvgRenderer(): void {
  const browser = L.Browser as unknown as { svg: boolean; inlineSvg?: boolean; vml: boolean };
  browser.svg = true;
  browser.inlineSvg = true;
  browser.vml = false;
  // Give real Leaflet a nonzero viewport: jsdom otherwise reports a 0×0 map.
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true,
    get() { return this.classList.contains('wb-map-leaflet') ? 640 : 0; } });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true,
    get() { return this.classList.contains('wb-map-leaflet') ? 360 : 0; } });
}

/** Observe actual Leaflet instances without mocking its projection, renderer or handlers. */
export function captureMaps(): L.Map[] {
  const maps: L.Map[] = [];
  const setView = L.Map.prototype.setView;
  vi.spyOn(L.Map.prototype, 'setView').mockImplementation(function (this: L.Map, center, zoom, options) {
    if (!maps.includes(this)) maps.push(this);
    return setView.call(this, center, zoom, options);
  });
  return maps;
}

export function mapLayers(map: L.Map): L.Layer[] {
  const layers: L.Layer[] = [];
  map.eachLayer(layer => { layers.push(layer); });
  return layers;
}
