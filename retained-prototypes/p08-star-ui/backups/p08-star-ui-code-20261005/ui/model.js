import {kindOf,relationOf,textLabel,makeIndex} from './canonical-model.js';
export {kindOf,relationOf,textLabel,makeIndex};
export const TYPES={resource:{label:'资源 / 时空',color:'#368fb5',shape:'square'},constraint:{label:'约束 / 权限',color:'#d28a47',shape:'diamond'},decision:{label:'决策 / 目标',color:'#b768a5',shape:'diamond'},execution:{label:'行为 / 执行',color:'#697fcc',shape:'hex'},time:{label:'时间上下文',color:'#65a5af',shape:'ring'},entity:{label:'实体',color:'#3872c9',shape:'square'},role:{label:'声明角色',color:'#7f8caa',shape:'square'},state:{label:'状态 / 事实',color:'#00a3a3',shape:'circle'},rule:{label:'规则 / AST',color:'#d49b2a',shape:'diamond'},predicate:{label:'谓词',color:'#8e63cf',shape:'ring'},event:{label:'事件',color:'#e97767',shape:'hex'},other:{label:'其他类型',color:'#73839b',shape:'circle'}};
export const DOMAINS={hardware_em:'硬件与电磁',machine_network:'机器与网络',human_urban:'人类与城市',digital_twin:'数字孪生',runtime:'运行时参考',declared_roles:'声明角色',shared:'共享定义',authored:'案例编写记录',authored_c13_extension:'C13 编写扩展',agriculture:'农业',delivery:'配送',city:'城市',network:'网络'};
export function groupOf(n){const k=kindOf(n);if(/owner|role/.test(k))return 'role';if(/predicate/.test(k))return 'predicate';if(/constraint|authority|arbitration/.test(k))return 'constraint';if(/time_context/.test(k))return 'time';if(/resource|allocation|lease|spatial_zone/.test(k))return 'resource';if(/decision|strategy|task|objective|request|agent/.test(k))return 'decision';if(/module|capability|behavior|command|execution/.test(k))return 'execution';if(/rule|expression|operator|constant|state_ref|parameter_ref|ast/.test(k))return 'rule';if(/state|fact|observation/.test(k))return 'state';if(/event|result|receipt|evidence|outcome/.test(k))return 'event';if(/entity|resource|agent|capability|module|space|zone|lease/.test(k))return 'entity';return 'other';}
export function domainOf(n){return n.domain||n.view_domain||n.payload?.domain||(/^authored_/.test(n.provenance?.status||'')?'authored':'shared');}
export function nodeText(n){return [n.id,n.original_id,textLabel(n.label),kindOf(n),domainOf(n),n.family,n.payload?.family,DOMAINS[domainOf(n)],TYPES[groupOf(n)].label,JSON.stringify(n.aliases||[]),JSON.stringify(n.source_storage_aliases||[])].join(' ').toLowerCase();}
export function matches(index,{query='',domain='',type='',kind=''}={}){const terms=query.trim().toLowerCase().split(/\s+/).filter(Boolean);return [...index.nodes.values()].filter(n=>(!domain||domainOf(n)===domain)&&(!type||groupOf(n)===type||kindOf(n)===type)&&(!kind||kindOf(n)===kind)&&terms.every(t=>nodeText(n).includes(t)));}
function orderedAdjacent(index,id,relation){return (index.adjacent.get(id)||[]).filter(e=>!relation||relationOf(e)===relation).sort((a,b)=>relationOf(a).localeCompare(relationOf(b))||a.id.localeCompare(b.id));}
export function localScene(index,{focus,limit=160,hops=1,domain='',type='',kind='',relation=''}={}){
 const accepted=new Set(matches(index,{domain,type,kind}).map(n=>n.id)),seen=new Set(),ids=[],distance=new Map();let frontier=focus?[focus]:[];let candidateCount=0;
 for(let d=0;d<=hops;d++){const next=[];for(const id of frontier){if(seen.has(id)||!index.nodes.has(id))continue;seen.add(id);if(id===focus||accepted.has(id)){candidateCount++;if(ids.length<limit){ids.push(id);distance.set(id,d);}}
 if(d<hops)for(const e of orderedAdjacent(index,id,relation)){const other=e.source===id?e.target:e.source;if(!seen.has(other))next.push(other);}}
 frontier=[...new Set(next)];}
 const admitted=new Set(ids);const allEdges=[...index.edges.values()].filter(e=>(!relation||relationOf(e)===relation)&&admitted.has(e.source)&&admitted.has(e.target));
 const boundary=[...index.edges.values()].filter(e=>(!relation||relationOf(e)===relation)&&admitted.has(e.source)!==admitted.has(e.target)).length;
 const edges=allEdges.slice(0,1800);return{mode:'local',focus,nodes:ids.map(id=>index.nodes.get(id)),edges,distance,groups:[],candidateCount,omittedNodes:Math.max(0,candidateCount-ids.length),omittedEdges:allEdges.length-edges.length,boundaryEdges:boundary};
}
export function overviewScene(index,{domain='',type='',kind='',relation='',limit=280}={}){
 const eligible=matches(index,{domain,type,kind}),buckets=new Map();for(const n of eligible){const key=domainOf(n);if(!buckets.has(key))buckets.set(key,[]);buckets.get(key).push(n);}
 const selected=new Set(),groups=[],per=Math.max(12,Math.floor(limit/Math.max(1,buckets.size)));
 for(const [key,nodes]of buckets){const available=new Set(nodes.map(n=>n.id));const chosen=[];const ranked=nodes.filter(n=>['predicate','event'].includes(groupOf(n))).sort((a,b)=>(index.adjacent.get(b.id)?.length||0)-(index.adjacent.get(a.id)?.length||0));
 const seed=ranked[0]||nodes[0];const queue=seed?[seed.id]:[];
 while(queue.length&&chosen.length<per){const id=queue.shift();if(!available.has(id)||selected.has(id))continue;selected.add(id);chosen.push(id);for(const e of orderedAdjacent(index,id,relation)){const other=e.source===id?e.target:e.source;if(available.has(other)&&!selected.has(other))queue.push(other);}}
 for(const n of nodes)if(chosen.length<per&&!selected.has(n.id)){selected.add(n.id);chosen.push(n.id);}
 groups.push({id:key,label:DOMAINS[key]||key,count:nodes.length,ids:chosen});}
 const edges=[...index.edges.values()].filter(e=>selected.has(e.source)&&selected.has(e.target)&&(!relation||relationOf(e)===relation));
 return {mode:'overview',focus:null,nodes:[...selected].map(id=>index.nodes.get(id)),edges:edges.slice(0,1800),groups,distance:new Map(),candidateCount:eligible.length,omittedNodes:eligible.length-selected.size,omittedEdges:Math.max(0,edges.length-1800),boundaryEdges:0};
}
export function hash(s){let h=2166136261;for(const c of s){h^=c.charCodeAt(0);h=Math.imul(h,16777619);}return h>>>0;}
export function seeded(id){const a=hash(id)/4294967295,b=hash(id+'b')/4294967295;return {x:Math.cos(a*Math.PI*2)*Math.sqrt(1-(b*2-1)**2),y:Math.sin(a*Math.PI*2)*Math.sqrt(1-(b*2-1)**2),z:b*2-1};}
/** A bounded, genuine three-dimensional force simulation. Inputs are never mutated. */
export class Force3D{
 constructor(scene,old=new Map()){this.scene=scene;this.alpha=1;this.iteration=0;this.points=new Map();this.anchors=new Map();const groups=scene.groups||[];groups.forEach((g,i)=>{const angle=i*2*Math.PI/groups.length-.7;this.anchors.set(g.id,{x:Math.cos(angle)*300,y:Math.sin(angle)*220,z:Math.sin(angle*2)*150});});
 for(const n of scene.nodes){const v=seeded(n.id),anchor=scene.mode==='overview'?(this.anchors.get(domainOf(n))||{x:0,y:0,z:0}):{x:0,y:0,z:0};const prev=old.get(n.id),r=scene.mode==='overview'?60:60+(scene.distance.get(n.id)||1)*42;this.points.set(n.id,{id:n.id,x:prev?.x??anchor.x+v.x*r,y:prev?.y??anchor.y+v.y*r,z:prev?.z??anchor.z+v.z*r,vx:0,vy:0,vz:0,anchor,pinned:false});}this.links=scene.edges.map(e=>[this.points.get(e.source),this.points.get(e.target)]).filter(([a,b])=>a&&b&&a!==b);}
 tick(steps=1){for(let step=0;step<steps;step++){if(this.alpha<.004)return;const ps=[...this.points.values()],a=this.alpha;
 for(let i=0;i<ps.length;i++){const p=ps[i];for(let j=i+1;j<ps.length;j++){const q=ps[j];let dx=p.x-q.x,dy=p.y-q.y,dz=p.z-q.z;const d2=dx*dx+dy*dy+dz*dz+24,f=Math.min(1.4,450/d2)*a,d=Math.sqrt(d2);dx=dx/d*f;dy=dy/d*f;dz=dz/d*f;p.vx+=dx;p.vy+=dy;p.vz+=dz;q.vx-=dx;q.vy-=dy;q.vz-=dz;}}
 for(const [p,q]of this.links){const dx=q.x-p.x,dy=q.y-p.y,dz=q.z-p.z,d=Math.max(1,Math.hypot(dx,dy,dz)),f=(d-48)*.007*a;p.vx+=dx/d*f;p.vy+=dy/d*f;p.vz+=dz/d*f;q.vx-=dx/d*f;q.vy-=dy/d*f;q.vz-=dz/d*f;}
 for(const p of ps){if(p.pinned)continue;const strength=this.scene.mode==='overview'?.009:.0025;p.vx+=(p.anchor.x-p.x)*strength*a;p.vy+=(p.anchor.y-p.y)*strength*a;p.vz+=(p.anchor.z-p.z)*strength*a;if(p.id===this.scene.focus){p.vx+=-p.x*.035*a;p.vy+=-p.y*.035*a;p.vz+=-p.z*.035*a;}p.vx*=.78;p.vy*=.78;p.vz*=.78;p.x+=p.vx;p.y+=p.vy;p.z+=p.vz;}this.alpha*=.984;this.iteration++;}}
}
export function project(p,c,w,h){const cy=Math.cos(c.yaw),sy=Math.sin(c.yaw),cp=Math.cos(c.pitch),sp=Math.sin(c.pitch),x=p.x*cy+p.z*sy,z=-p.x*sy+p.z*cy,y=p.y*cp-z*sp,depth=p.y*sp+z*cp,scale=c.zoom*1000/Math.max(300,1000+depth);return{x:w/2+c.panX+x*scale,y:h/2+c.panY+y*scale,depth,scale};}
