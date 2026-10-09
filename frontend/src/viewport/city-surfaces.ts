import * as T from 'three';
import type { MeshPackManifest, PackedBatch } from './mesh-pack';

type Surface='asphalt'|'paving'|'grass'|'water'|'wood'|'ground'|'marking'|'crossing'|'foliage';
function classify(batch: PackedBatch): Surface {
  const path=(batch.material.base_color_texture ?? '').toLowerCase();
  if(path.includes('road_marking_crossing'))return 'crossing';
  if(path.includes('road_marking'))return 'marking';
  if(path.includes('arbaro_tree'))return 'foliage';
  if(path.includes('water'))return 'water';
  if(path.includes('wood'))return 'wood';
  if(path.includes('ground042')||path.includes('grass'))return 'grass';
  if(path.includes('paving')||path.includes('concrete')||path.includes('brick'))return 'paving';
  if(path.includes('asphalt')||batch.layer==='roads')return 'asphalt';
  return 'ground';
}
function makeSurface(kind: Surface): T.MeshStandardMaterial {
  const colors: Record<Surface,string>={asphalt:'#424950',paving:'#b7b9b7',grass:'#779866',water:'#527e93',wood:'#999387',ground:'#b7b7a5',marking:'#efece2',crossing:'#efece2',foliage:'#567e49'};
  const material=new T.MeshStandardMaterial({color:colors[kind],roughness:kind==='water'?0.22:0.9,metalness:kind==='water'?0.25:0,side:kind==='foliage'?T.DoubleSide:T.FrontSide});
  material.userData={displaySurface:kind,displayEstimate:'procedural finish, existing surface geometry'};
  material.customProgramCacheKey=()=>`procedural-surface-v2-${kind}`;
  material.onBeforeCompile=shader=>{
    shader.vertexShader='varying vec3 surfacePosition;\nvarying vec2 surfaceUV;\n'+shader.vertexShader;
    shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\nsurfacePosition=position;surfaceUV=uv;');
    shader.fragmentShader=`varying vec3 surfacePosition;varying vec2 surfaceUV;
float surfaceHash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
`+shader.fragmentShader;
    let detail='float grain=surfaceHash(floor(surfacePosition.xz*5.0));diffuseColor.rgb*=0.96+grain*0.08;';
    if(kind==='paving'||kind==='wood')detail+=`vec2 paving=surfacePosition.xz/vec2(${kind==='wood'?'0.3,4.0':'1.5,0.75'});vec2 joints=abs(fract(paving)-0.5);float joint=smoothstep(0.475-max(fwidth(paving.x),fwidth(paving.y)),0.495,max(joints.x,joints.y));diffuseColor.rgb*=1.0-joint*0.19;`;
    if(kind==='grass')detail+='diffuseColor.rgb*=0.98+surfaceHash(floor(surfacePosition.xz/8.0))*0.04;';
    if(kind==='water')detail+='diffuseColor.rgb*=0.96+0.04*sin(surfacePosition.x*0.6+surfacePosition.z*0.8);';
    if(kind==='marking')detail='if(fract(surfaceUV.y)<0.5)discard;';
    if(kind==='crossing')detail='if(fract(surfaceUV.y*4.0)<0.48)discard;';
    if(kind==='foliage')detail='vec2 leaf=(surfaceUV-0.5)*vec2(2.0,1.65);if(dot(leaf,leaf)>0.85)discard;diffuseColor.rgb*=0.85+surfaceHash(floor(surfaceUV*80.0))*0.3;';
    shader.fragmentShader=shader.fragmentShader.replace('#include <color_fragment>',`#include <color_fragment>\n${detail}`);
  };
  return material;
}
export function surfaceMaterial(batch: PackedBatch): T.MeshStandardMaterial { return makeSurface(classify(batch)); }

interface Node {type:'node';id:number;lat:number;lon:number;tags?:Record<string,string>}
interface Way {type:'way';id:number;nodes:number[];tags?:Record<string,string>}
interface Relation {type:'relation';id:number;tags?:Record<string,string>;members:{type:string;ref:number;role:string}[]}
type Point=[number,number];
interface Polygon {outer:Point[];holes:Point[][];id:string;kind:Surface}
function inside(point:Point,ring:Point[]) {
  let result=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){
    const a=ring[i],b=ring[j];if((a[1]>point[1])!==(b[1]>point[1])&&point[0]<(b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0])result=!result;
  }return result;
}
function polygonContains(point:Point,polygon:Polygon){return inside(point,polygon.outer)&&!polygon.holes.some(hole=>inside(point,hole));}
function distanceToSegment(p:Point,a:Point,b:Point){const dx=b[0]-a[0],dz=b[1]-a[1],length=dx*dx+dz*dz;if(length===0)return Math.hypot(p[0]-a[0],p[1]-a[1]);const t=Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dz)/length));return Math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dz);}
function landuse(tags:Record<string,string>|undefined): Surface|undefined {
  if(!tags)return;
  if(tags.natural==='water'||tags.waterway==='riverbank'||tags.landuse==='reservoir'||tags.landuse==='basin')return 'water';
  if(['park','garden'].includes(tags.leisure)||['grass','forest','meadow','village_green','recreation_ground'].includes(tags.landuse)||['wood','grassland'].includes(tags.natural))return 'grass';
  if(tags.place==='square'||tags.highway==='pedestrian'&&tags.area==='yes')return 'paving';
}

/** Original OSM source -> real polygons, estimated landscaping. Pack coordinate recipe retained. */
export function createCityVegetation(source:unknown,pack:MeshPackManifest): T.Group {
  if(!source||typeof source!=='object'||!('elements' in source)||!Array.isArray(source.elements))throw Error('City landscaping requires original OSM elements');
  const elements=source.elements as Array<Node|Way|Relation>;
  const origin=pack.coordinate_contract.converter_origin,rad=Math.PI/180;
  const scale=pack.coordinate_contract.earth_circumference_m/(2*Math.PI)*Math.cos(origin.latitude_deg*rad);
  const mercY=(lat:number)=>Math.log(Math.tan(Math.PI/4+lat*rad/2));
  const [tx,tz]=pack.coordinate_contract.stored_translation_xz_m;
  const nodes=new Map<number,Point>(),treeNodes:Point[]=[];
  for(const element of elements)if(element.type==='node'){
    if(!Number.isFinite(element.lat)||!Number.isFinite(element.lon))throw Error(`Invalid OSM node coordinate: ${element.id}`);
    const p:Point=[Math.round(scale*(element.lon-origin.longitude_deg)*rad*1000)/1000+tx,Math.round(-scale*(mercY(element.lat)-mercY(origin.latitude_deg))*1000)/1000+tz];
    nodes.set(element.id,p);if(element.tags?.natural==='tree')treeNodes.push(p);
  }
  if(!nodes.size)throw Error('City source contains no geographic nodes');
  const ways=new Map<number,Way>(),wayPoints=new Map<number,Point[]>();
  for(const element of elements)if(element.type==='way'){
    ways.set(element.id,element);
    const points=element.nodes.map(id=>{const point=nodes.get(id);if(!point)throw Error(`OSM way ${element.id} references absent node ${id}`);return point;});
    wayPoints.set(element.id,points);
  }
  const polygons:Polygon[]=[],buildings:Polygon[]=[],roads:Array<{points:Point[];halfWidth:number;id:number}>=[];
  const closed=(points:Point[])=>points.length>=4&&points[0][0]===points.at(-1)![0]&&points[0][1]===points.at(-1)![1];
  for(const [id,way] of ways){
    const points=wayPoints.get(id)!;
    if(way.tags?.building&&closed(points))buildings.push({outer:points,holes:[],id:`w${id}`,kind:'ground'});
    const kind=landuse(way.tags);if(kind&&closed(points))polygons.push({outer:points,holes:[],id:`w${id}`,kind});
    if(way.tags?.highway&&way.tags.area!=='yes'&&points.length>=2){
      const rawWidth=way.tags.width,rawLanes=way.tags.lanes;
      const width=rawWidth!==undefined?Number(rawWidth):rawLanes!==undefined?Number(rawLanes)*3.2:undefined;
      if(width!==undefined&&(!Number.isFinite(width)||width<=0))throw Error(`Invalid OSM road width/lanes: ${id}`);
      const estimate=['footway','path','steps','pedestrian'].includes(way.tags.highway)?1.5:['residential','service'].includes(way.tags.highway)?6.4:10;
      roads.push({points,halfWidth:(width ?? estimate)/2,id});
    }
  }
  let unresolvedRings=0;
  for(const element of elements)if(element.type==='relation'&&element.tags?.type==='multipolygon'){
    const kind=landuse(element.tags),building=!!element.tags.building;if(!kind&&!building)continue;
    const join=(role:string)=>{
      const paths=element.members.filter(m=>m.type==='way'&&m.role===role).map(m=>{const p=wayPoints.get(m.ref);if(!p)throw Error(`OSM relation ${element.id} references absent way ${m.ref}`);return [...p];});
      const rings:Point[][]=[];
      while(paths.length){const ring=paths.shift()!;while(!closed(ring)){const last=ring.at(-1)!;const match=paths.findIndex(path=>[path[0],path.at(-1)!].some(p=>p[0]===last[0]&&p[1]===last[1]));if(match<0){unresolvedRings++;break;}const path=paths.splice(match,1)[0];if(path[0][0]!==last[0]||path[0][1]!==last[1])path.reverse();ring.push(...path.slice(1));}if(closed(ring))rings.push(ring);}
      return rings;
    };
    const outers=join('outer'),inners=join('inner');
    for(const outer of outers){const polygon={outer,holes:inners.filter(hole=>inside(hole[0],outer)),id:`r${element.id}`,kind:kind ?? 'ground' as Surface};if(kind)polygons.push(polygon);if(building)buildings.push(polygon);}
  }
  const root=new T.Group();root.name='display-osm-landscape';
  const ext=pack.extent;
  const inExtent=(p:Point)=>p[0]>=ext.west&&p[0]<=ext.east&&-p[1]>=ext.south&&-p[1]<=ext.north;
  const surfacePositions=new Map<Surface,number[]>();
  let surfaceCount=0;
  for(const polygon of polygons){
    // Landuse coordinates share pack space and retain multipolygon holes.
    const shape=new T.Shape(polygon.outer.map(([x,z])=>new T.Vector2(x,-z)));
    for(const hole of polygon.holes)shape.holes.push(new T.Path(hole.map(([x,z])=>new T.Vector2(x,-z))));
    const geometry=new T.ShapeGeometry(shape);geometry.rotateX(-Math.PI/2);
    const expanded=geometry.index?geometry.toNonIndexed():geometry;
    const positions=expanded.getAttribute('position'),normal=expanded.getAttribute('normal');
    const output=surfacePositions.get(polygon.kind) ?? [];
    for(let i=0;i<positions.count;i+=3){const center:Point=[(positions.getX(i)+positions.getX(i+1)+positions.getX(i+2))/3,(positions.getZ(i)+positions.getZ(i+1)+positions.getZ(i+2))/3];if(!inExtent(center))continue;for(let j=0;j<3;j++)output.push(positions.getX(i+j),polygon.kind==='water'?-0.03:-0.025,positions.getZ(i+j));}
    surfacePositions.set(polygon.kind,output);surfaceCount++;geometry.dispose();if(expanded!==geometry)expanded.dispose();
  }
  for(const [kind,positions] of surfacePositions){if(!positions.length)continue;const geometry=new T.BufferGeometry();geometry.setAttribute('position',new T.Float32BufferAttribute(positions,3));geometry.setAttribute('uv',new T.Float32BufferAttribute(new Float32Array(positions.length/3*2),2));geometry.computeVertexNormals();const mesh=new T.Mesh(geometry,makeSurface(kind));mesh.receiveShadow=true;mesh.userData.displayDecoration=true;root.add(mesh);}
  let randomState=79331;const random=()=>{randomState=(Math.imul(randomState,1664525)+1013904223)>>>0;return randomState/4294967296;};
  const trees:Point[]=[];let observedTrees=0,estimatedTrees=0;
  const safe=(p:Point)=>inExtent(p)&&!buildings.some(polygon=>polygonContains(p,polygon))&&!roads.some(road=>road.points.some((a,i)=>i>0&&distanceToSegment(p,road.points[i-1],a)<road.halfWidth+1.8))&&!polygons.some(polygon=>polygon.kind==='water'&&polygonContains(p,polygon))&&!trees.some(tree=>Math.hypot(tree[0]-p[0],tree[1]-p[1])<4);
  for(const p of treeNodes)if(inExtent(p)){trees.push(p);observedTrees++;}
  for(const polygon of polygons)if(polygon.kind==='grass'){
    const xs=polygon.outer.map(p=>p[0]),zs=polygon.outer.map(p=>p[1]);
    for(let x=Math.max(ext.west,Math.min(...xs))+8;x<Math.min(ext.east,Math.max(...xs));x+=18)for(let z=Math.max(-ext.north,Math.min(...zs))+8;z<Math.min(-ext.south,Math.max(...zs));z+=18){if(trees.length>=1200)break;const p:Point=[x+(random()-.5)*10,z+(random()-.5)*10];if(polygonContains(p,polygon)&&safe(p)){trees.push(p);estimatedTrees++;}}
  }
  for(const road of roads){if(trees.length>=1200)break;
    for(let i=1;i<road.points.length;i++){const a=road.points[i-1],b=road.points[i],length=Math.hypot(b[0]-a[0],b[1]-a[1]);if(length<22)continue;const dx=(b[0]-a[0])/length,dz=(b[1]-a[1])/length;for(let distance=12;distance<length-10&&trees.length<1200;distance+=24){for(const sign of [-1,1]){const offset=road.halfWidth+3.4;const p:Point=[a[0]+dx*distance-dz*offset*sign,a[1]+dz*distance+dx*offset*sign];if(safe(p)){trees.push(p);estimatedTrees++;}}}}
  }
  if(trees.length){
    const trunks=new T.InstancedMesh(new T.CylinderGeometry(0.16,0.24,3.5,6),new T.MeshStandardMaterial({color:'#685c48',roughness:1}),trees.length);
    const crowns=new T.InstancedMesh(new T.IcosahedronGeometry(1,1),new T.MeshStandardMaterial({color:'#ffffff',roughness:0.95}),trees.length);
    const matrix=new T.Matrix4(),dummy=new T.Object3D(),color=new T.Color();
    trees.forEach(([x,z],i)=>{const height=5+random()*2.5,radius=1.8+random()*0.9;dummy.position.set(x,1.75,z);dummy.scale.set(1,1,1);dummy.rotation.y=random()*Math.PI*2;dummy.updateMatrix();trunks.setMatrixAt(i,dummy.matrix);dummy.position.y=height-1.4;dummy.scale.set(radius,height*.36,radius);dummy.updateMatrix();crowns.setMatrixAt(i,dummy.matrix);color.setHSL(0.23+random()*.045,0.22+random()*.14,0.25+random()*.1);crowns.setColorAt(i,color);});
    for(const mesh of [trunks,crowns]){mesh.castShadow=true;mesh.receiveShadow=true;mesh.computeBoundingSphere();mesh.userData.displayDecoration=true;root.add(mesh);}
  }
  root.userData={source:'verified-original-osm',observedTrees,estimatedTrees,surfaceCount,unresolvedRings,displayDecoration:true,displayEstimate:'tree placement along roads/parks, road widths without width/lanes tags; foliage shape'};
  return root;
}
