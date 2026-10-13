/**
 * jsdom lacks createSVGRect, so Leaflet's vector renderer falls through to nothing at
 * runtime; under real @types/leaflet the Browser flags are also typed read-only. Central
 * helper for the map tests: force the SVG renderer path (real browsers always take it).
 */
import * as L from 'leaflet';

export function forceSvgRenderer(): void {
  const browser = L.Browser as unknown as { svg: boolean; inlineSvg?: boolean; vml: boolean };
  browser.svg = true;
  browser.inlineSvg = true;
  browser.vml = false;
}
