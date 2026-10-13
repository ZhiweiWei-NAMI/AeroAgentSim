/** Minimal ambient typing for the Leaflet ESM build — the package ships no bundled .d.ts and
 *  @types/leaflet is not installed in this snapshot. Only the surface RegionMap uses. */
declare module 'leaflet' {
  export interface LatLng { lat: number; lng: number }
  export interface LatLngBounds { getSouth(): number; getWest(): number; getNorth(): number; getEast(): number;
    getSouthWest(): LatLng; getNorthEast(): LatLng; getCenter(): LatLng; contains(other: LatLngBounds | LatLng): boolean;
    pad(amount: number): LatLngBounds; }
  export type LatLngExpression = LatLng | [number, number];
  export type LatLngBoundsExpression = LatLngBounds | [LatLngExpression, LatLngExpression];
  export type PointExpression = [number, number];

  export interface Layer { addTo(map: Map): this; remove(): this; removeFrom(map: Map): this; onRemove?(map: Map): void; bindTooltip(content: string, options?: { direction?: string; offset?: PointExpression; permanent?: boolean }): this; }
  export interface InteractiveLayer extends Layer { on(type: string, handler: (event: any) => void): this; off(type: string, handler?: (event: any) => void): this; }
  export interface Path extends InteractiveLayer { setStyle(style: Record<string, unknown>): this; setLatLngs(latlngs: LatLngExpression[] | LatLngExpression[][]): this; getBounds(): LatLngBounds; }
  export interface Polyline extends Path {}
  export interface Polygon extends Path {}
  export interface Rectangle extends Polygon { setBounds(bounds: LatLngBounds | LatLngBoundsExpression): this }
  export interface CircleMarker extends Path { setLatLng(latlng: LatLngExpression): this }
  export interface Marker extends InteractiveLayer { setIcon(icon: DivIcon): this; setLatLng(latlng: LatLngExpression): this; }
  export interface LayerGroup extends Layer { addLayer(layer: Layer): this; removeLayer(layer: Layer): this; clearLayers(): this; eachLayer(fn: (layer: Layer) => void): this; getLayers(): Layer[]; setLatLngs?(latlngs: LatLngExpression[]): void }
  export interface TileLayer extends Layer { readonly _url?: string }

  export interface DivIconOptions { className?: string; html?: string; iconSize?: PointExpression; iconAnchor?: PointExpression }
  export interface DivIcon { options: DivIconOptions }
  export function divIcon(options?: DivIconOptions): DivIcon;

  export interface MapOptions { zoomControl?: boolean; attributionControl?: boolean; scrollWheelZoom?: boolean; preferCanvas?: boolean; }
  export interface Map {
    setView(center: LatLngExpression, zoom: number, options?: { animate?: boolean }): this;
    fitBounds(bounds: LatLngBoundsExpression, options?: { animate?: boolean; padding?: PointExpression; maxZoom?: number }): this;
    remove(): void;
    on(type: string, handler: (event: any) => void): this;
    off(type: string, handler?: (event: any) => void): this;
    getBounds(): LatLngBounds;
    getZoom(): number;
    addLayer(layer: Layer): this;
    removeLayer(layer: Layer): this;
    hasLayer(layer: Layer): boolean;
    getContainer(): HTMLElement;
    invalidateSize(options?: { animate?: boolean }): this;
    options: MapOptions;
  }
  export interface TileLayerOptions { attribution?: string; maxZoom?: number; minZoom?: number; errorTileUrl?: string; }
  export interface PolylineOptions { color?: string; weight?: number; opacity?: number; fillOpacity?: number; dashArray?: string; fill?: boolean; fillColor?: string; interactive?: boolean; draggable?: boolean }
  export interface MarkerOptions { draggable?: boolean; keyboard?: boolean; icon?: DivIcon }

  export function map(element: HTMLElement | string, options?: MapOptions): Map;
  export function tileLayer(url: string, options?: TileLayerOptions): TileLayer;
  export function polyline(latlngs: LatLngExpression[], options?: PolylineOptions): Polyline;
  export function polygon(latlngs: LatLngExpression[], options?: PolylineOptions): Polygon;
  export function rectangle(bounds: LatLngBoundsExpression, options?: PolylineOptions): Rectangle;
  export function circleMarker(latlng: LatLngExpression, options?: CircleMarkerOptions): CircleMarker;
  export interface CircleMarkerOptions extends PolylineOptions { radius?: number }
  export function marker(latlng: LatLngExpression, options?: MarkerOptions): Marker;
  export function layerGroup(layers?: Layer[]): LayerGroup;
  export function latLngBounds(corner1: LatLngExpression, corner2: LatLngExpression): LatLngBounds;

  export const DomEvent: { disableScrollPropagation(el: HTMLElement): void; disableClickPropagation(el: HTMLElement): void; stop(event: Event): Event };
}
