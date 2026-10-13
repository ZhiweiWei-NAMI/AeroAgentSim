import { useI18n } from '../i18n/I18nProvider';
import type { PlanPreview, PlanVersion } from './api';
import { pick } from './present';

type Point = [number,number];
interface Geography { bounds: [number,number,number,number]; buildings: Point[][][]; roads: Array<{id: string; points: Point[]}>; truncated: boolean }
const point=(value: unknown): number[]=>{
  if(!Array.isArray(value)||value.length<2||!value.every(item=>typeof item==='number'&&Number.isFinite(item)))throw Error('Invalid geographic coordinate');
  return value;
};

/** Only source geometry is drawn. The compiled scene uses WGS84 polygons and ENU roads; C10 uses ENU throughout. */
export function proposalGeography(preview: PlanPreview | undefined): Geography | undefined {
  const source=preview?.region_preview ?? (preview?.preview ? { ...preview.preview, bounds: preview.preview.bounds ?? preview.region?.bounds } : undefined), scene=preview?.scene;
  if(!source && !scene)return;
  if(source && (!source.geometry || typeof source.geometry.truncated!=='boolean' || !source.geometry.buildings))throw Error('Invalid source geometry');
  const origin=source?source.origin:scene!.origin, bounds=source?source.bounds:scene!.bounds;
  if(!origin || ![origin.lon,origin.lat].every(Number.isFinite) || !Array.isArray(bounds)||bounds.length!==4||!bounds.every(Number.isFinite)||bounds[0]>=bounds[2]||bounds[1]>=bounds[3])throw Error('Invalid geographic bounds or origin');
  const rad=Math.PI/180, a=6378137, e2=6.69437999014e-3, denominator=1-e2*Math.sin(origin.lat*rad)**2;
  const eastPerDegree=a/Math.sqrt(denominator)*Math.cos(origin.lat*rad)*rad, northPerDegree=a*(1-e2)/denominator**1.5*rad;
  const geographic=(coordinate: unknown): Point=>{const [lon,lat]=point(coordinate);return [(lon-origin.lon)*eastPerDegree,(lat-origin.lat)*northPerDegree];};
  const local=(coordinate: unknown): Point=>{const [east,north]=point(coordinate);return [east,north];};
  const project=source?local:geographic;
  const features=source?source.geometry.buildings.features:scene!.geojson.features;
  if(!Array.isArray(features))throw Error('Invalid geographic buildings');
  const buildings=features.filter(feature=>{
    // Compiled GeoJSON also carries road centre lines, already drawn below
    // from its ENU road list. They are not building footprints.
    if(!source && feature.geometry?.type==='LineString')return false;
    return true;
  }).map(feature=>{
    const geometry=feature.geometry;
    if(!geometry || !['Polygon','MultiPolygon'].includes(geometry.type))throw Error('Invalid building polygon');
    const polygons=geometry.type==='Polygon'?[geometry.coordinates]:geometry.coordinates;
    if(!Array.isArray(polygons))throw Error('Invalid building polygon');
    return polygons.flatMap((polygon: unknown)=>{
      if(!Array.isArray(polygon))throw Error('Invalid building rings');
      return polygon.map((ring: unknown)=>{
        if(!Array.isArray(ring)||ring.length<4)throw Error('Invalid building ring');
        const projected=ring.map(project),first=projected[0],last=projected[projected.length-1];
        if(first[0]!==last[0]||first[1]!==last[1])throw Error('Invalid building ring');
        return projected;
      });
    }) as Point[][];
  });
  const rows=source?source.geometry.roads:scene!.roads;
  if(!Array.isArray(rows))throw Error('Invalid geographic roads');
  const roads=rows.map(row=>{if(!Array.isArray(row.points)||row.points.length<2)throw Error('Invalid road points');return {id:row.id,points:row.points.map(local)};});
  const [west,south]=geographic(bounds.slice(0,2)),[east,north]=geographic(bounds.slice(2));
  return {bounds:[west,south,east,north],buildings,roads,truncated:source?source.geometry.truncated:false};
}

export function RegionProposal({ plan,preview }: { plan: PlanVersion; preview?: PlanPreview }) {
  const {locale}=useI18n(),zh=locale==='zh-CN',L=(en: string,cn: string)=>zh?cn:en;
  if(!plan.operations.some(op=>op.op==='set_region'))return null;
  let geometry: Geography | undefined;
  try {geometry=proposalGeography(preview);}catch(problem){
    const messages: Record<string,string>={
      'Invalid geographic coordinate':L('Invalid geographic coordinate.','地理坐标无效。'),
      'Invalid geographic bounds or origin':L('Invalid geographic bounds or origin.','地理范围或原点无效。'),
      'Invalid geographic buildings':L('Invalid building geometry.','建筑几何无效。'),
      'Invalid building polygon':L('Invalid building polygon.','建筑多边形无效。'),
      'Invalid building rings':L('Invalid building rings.','建筑轮廓环无效。'),
      'Invalid building ring':L('Invalid building ring.','建筑轮廓环无效。'),
      'Invalid geographic roads':L('Invalid road geometry.','道路几何无效。'),
      'Invalid road points':L('Invalid road points.','道路坐标无效。'),
      'Invalid source geometry':L('Invalid source geometry.','源几何无效。'),
    };
    const detail=problem instanceof Error?messages[problem.message]:undefined;
    return <p className="wb-issues" role="alert">{L('The geographic preview could not be shown.','无法显示地理预览。')} {detail ?? L('Invalid source geometry.','源几何无效。')}</p>;
  }
  if(!geometry)return <p className="hint" data-testid="as-geography-after-apply">{L('Geography appears after applying.','应用后显示地理场景。')}</p>;
  const [west,south,east,north]=geometry.bounds;
  const path=(rings: Point[][])=>rings.map(ring=>ring.map(([x,y],index)=>`${index?'L':'M'}${x},${-y}`).join(' ')+' Z').join(' ');
  const points=(rows: Point[])=>rows.map(([x,y])=>`${x},${-y}`).join(' ');
  const radius=Math.min(east-west,north-south)/80;
  return <section className="as-region-proposal" data-testid="as-region-proposal">
    <h4>{L('Proposed geographic area','提议的地理区域')}</h4>
    <svg viewBox={`${west} ${-north} ${east-west} ${north-south}`} role="img" aria-label={L('Buildings, roads and proposed places in the selected area','所选区域中的建筑、道路与提议地点')}>
      <rect x={west} y={-north} width={east-west} height={north-south} className="as-region-bounds"/>
      {geometry.buildings.map((rings,index)=><path key={index} d={path(rings)} fillRule="evenodd" className="as-region-building" data-testid="as-region-building"/>)}
      {geometry.roads.map((road,index)=><polyline key={`${road.id}-${index}`} points={points(road.points)} className="as-region-road" vectorEffect="non-scaling-stroke" data-testid="as-region-road"/>)}
      {plan.scene?.places?.map(place=>Array.isArray(place.position)?<circle key={place.id} cx={place.position[0]} cy={-place.position[1]} r={radius} className="as-region-place"><title>{pick(place,zh)}</title></circle>:null)}
    </svg>
    <p className="hint">{L(`${geometry.buildings.length} buildings · ${geometry.roads.length} roads`,`${geometry.buildings.length} 栋建筑 · ${geometry.roads.length} 条道路`)}</p>
    {geometry.truncated&&<p className="hint">{L('This preview shows part of the source geometry.','此预览仅显示部分源几何。')}</p>}
  </section>;
}
