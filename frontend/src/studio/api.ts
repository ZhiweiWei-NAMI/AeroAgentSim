import { parseAuthoringJson, stringifyLossless } from '../feeds/lossless-json';
export class StudioApi {
  constructor(readonly base: string) {}
  async request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.base.replace(/\/$/, '')}${path}`, body === undefined ? undefined : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: stringifyLossless(body),
    });
    if (!response.ok) {
      const raw = await response.text();
      let detail = raw;
      try { const value = JSON.parse(raw).detail ?? JSON.parse(raw); detail = typeof value === 'string' ? value : JSON.stringify(value); } catch { /* Preserve non-JSON server error. */ }
      throw Error(`HTTP ${response.status}: ${detail}`);
    }
    return parseAuthoringJson(await response.text()) as T;
  }
  async export(id: string): Promise<string> {
    const response = await fetch(`${this.base}/v1/studio/workspaces/${id}/export`);
    if (!response.ok) throw Error(`HTTP ${response.status}: ${await response.text()}`);
    return response.text();
  }
}
export type Scenario = Record<string, any>;
export interface CityScene {
  origin: { lat: number; lon: number; alt: number }; bounds: number[];
  geojson: { type: string; features: any[] };
  roads: Array<{ id: string; points: number[][]; tags: Record<string, string> }>;
  ground: { width_m: number; depth_m: number }; diagnostics: string[]; attribution: string;
}
export interface Workspace {
  created_at?: string; updated_at?: string;
  demo_console?: {capture_mode:string;city_available:boolean;scene?:import('../scene/presentation').ScenePresentation};
  id: string; name: string; scenario: Scenario; scene?: CityScene; registry_catalog?:{types:TypeRow[];fields:Array<Record<string,any>&{id:string}>;schemas:Record<string,any>};
  region?: { extract: string; bounds: number[]; alt: number; level_height_m: number };
  validation?: { valid: boolean; errors: string[]; issues?:Array<{source:string;path:string;message:string}> };
}
export interface TypeRow { id: string; name?: string; parents: string[]; abstract: boolean; directory?: string }
export interface TypeDetail { id: string; parents: string[]; abstract: boolean; fields: Array<{id: string; declaring_type: string; schema: any; metadata: any}> }
