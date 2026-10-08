import * as T from 'three';
import type { RunHeader } from '../contracts/viewer-feed';
import { Assets, disposeObject, loadCityPack } from '../viewport/assets';
import type { ScenePresentation } from './presentation';
import { createTrees, loadOsmBuildings, loadSumoRoads } from './source-geometry';

/** All sources share run-local east/up/south metres, then one floating origin. */
export async function loadSceneLayer(presentation: ScenePresentation, header: RunHeader, assets: Assets, signal: AbortSignal,
  attach: (object: T.Object3D) => void, status: (message: string) => void) {
  const jobs: Promise<void>[] = [];
  const add = (object: T.Object3D, offset?: [number, number, number]) => {
    if (signal.aborted) { disposeObject(object); return; }
    if (offset) object.position.add(new T.Vector3(...offset));
    attach(object);
  };
  if (presentation.city) jobs.push((async () => {
    const city = presentation.city!;
    status('Loading city geometry…');
    let object: T.Object3D;
    if (city.kind === 'osm2world') object = await loadCityPack(city.url, city.assetsBase ?? new URL('.', new URL(city.url, location.href)).href, header.origin, signal);
    else if (city.kind === 'geojson') {
      if (!header.origin) throw Error('GeoJSON city requires RunHeader.origin');
      object = await loadOsmBuildings(city.url, header.origin, signal);
    } else object = (await assets.model(city.url)).clone(true);
    object.name = 'city'; add(object, city.offset); status(`${presentation.id ?? 'City'} · geometry loaded`);
  })().catch(error => { if (!signal.aborted) status(`City unavailable: ${String(error)} · base scene active`); }));
  if (presentation.roads) jobs.push((async () => {
    const object = await loadSumoRoads(presentation.roads!.url, signal); add(object, presentation.roads!.offset);
  })().catch(error => { if (!signal.aborted) status(`SUMO roads unavailable: ${String(error)}`); }));
  if (presentation.trees?.length) add(createTrees(presentation.trees));
  await Promise.all(jobs);
}
