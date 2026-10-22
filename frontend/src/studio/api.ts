import { parseAuthoringJson, stringifyLossless } from '../feeds/lossless-json';
import { requestJson, requestText } from '../feeds/transport';
export class StudioApi {
  constructor(readonly base: string) {}
  async request<T>(path: string, body?: unknown): Promise<T> {
    return await requestJson(this.base, path, body === undefined ? undefined : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: stringifyLossless(body),
    }, parseAuthoringJson) as T;
  }
  async export(id: string): Promise<string> {
    return requestText(this.base, `/v1/studio/workspaces/${id}/export`);
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
  revision?: number; created_at?: string; updated_at?: string;
  demo_console?: {capture_mode:string;city_available:boolean;scene?:import('../scene/presentation').ScenePresentation};
  id: string; name: string; scenario: Scenario; scene?: CityScene; registry_catalog?:{types:TypeRow[];fields:Array<Record<string,any>&{id:string}>;schemas:Record<string,any>};
  /** SHA-256 of real engine fixture files, keyed by their authored references. */
  fixture_digests?: Record<string, string>;
  region?: { extract: string; bounds: number[]; alt: number; level_height_m: number };
  scene_source?: { kind: string; extract?: string; attribution?: string; [key: string]: unknown };
  preview?: { origin: { lat: number; lon: number; alt: number }; bounds?: number[];
    geometry: { buildings: { type: string; features: any[] }; roads: Array<{ id: string; points: number[][] }>; truncated: boolean }; [key: string]: unknown };
  /** Compiled proposal geometry is local to this preview, not the saved workspace endpoint. */
  proposal?: boolean;
  network_state?: 'none' | 'generated' | 'stale';
  validation?: { revision?: number; scope?: string; valid: boolean; errors: string[]; issues?:Array<{source:string;path:string;message:string}> };
}
export interface TypeRow { id: string; name?: string; parents: string[]; abstract: boolean; directory?: string }
export interface TypeDetail { id: string; parents: string[]; abstract: boolean; fields: Array<{id: string; declaring_type: string; schema: any; metadata: any}> }
