/** Read-only graph projection. Never creates semantic edges or evaluates truth. */
export const SCHEMA = 'p08.typed-graph/v1';
export const scalar = value => value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
export const textLabel = value => typeof value === 'object' && value ? value.zh || value.en || scalar(value) : scalar(value);
export const kindOf = n => n.kind || n.raw_kind || 'unspecified';
export const relationOf = e => e.relation || e.raw_relation || e.type || 'unspecified';
export function scenariosOf(manifest) {
  const candidates = manifest.scenarios || manifest.cases || manifest.catalog || [];
  return (Array.isArray(candidates) ? candidates : Object.entries(candidates).map(([id,s]) => ({id,...s}))).map(s => ({...s,id:String(s.id || s.case_id || s.scenario_id),label:textLabel(s.label || s.name || s.title || s.id || s.case_id),path:s.path || s.file || s.url,domain:s.domain || s.source_domain || 'unspecified',activity:textLabel(s.activity || s.activity_name || s.workflow || '')}));
}
export function safeDataURL(path, base) {
  const url = new URL(path, base), origin = new URL(base);
  if(url.origin !== origin.origin || !['http:','https:'].includes(url.protocol)) throw new Error('Only same-origin graph data is accepted.');
  return url.href;
}
export function graphArrays(data) {
  const graph = data.graph || data;
  if(!Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) throw new Error('Graph file must contain nodes[] and edges[].');
  return graph;
}
export function makeIndex(graphs) {
  const nodes = new Map(), edges = new Map(), nodeCases = new Map(), edgeCases = new Map(), conflicts = [], diagnostics = [], schemas = new Set();
  let sourceNodeOccurrences = 0, sourceEdgeOccurrences = 0;
  for(const {data,scenario} of graphs) {
    const graph = graphArrays(data), caseId = String(scenario?.id || data.case_id || data.scenario_id || 'unknown');
    if(data.schema_version) schemas.add(data.schema_version);
    for(const n of graph.nodes) {
      if(typeof n.id !== 'string' || !n.id) {diagnostics.push({kind:'invalid_node_id',case_id:caseId,record:n});continue;}
      sourceNodeOccurrences++;
      if(!nodes.has(n.id)) nodes.set(n.id,n);
      else if(scalar(nodes.get(n.id).payload) !== scalar(n.payload) || kindOf(nodes.get(n.id)) !== kindOf(n) || nodes.get(n.id).semantic_level !== n.semantic_level || scalar(nodes.get(n.id).identity) !== scalar(n.identity)) conflicts.push({kind:'duplicate_node_payload',id:n.id,case_id:caseId,record:n});
      if(!nodeCases.has(n.id)) nodeCases.set(n.id,new Set()); nodeCases.get(n.id).add(caseId);
    }
    for(const e of graph.edges) {
      if(typeof e.id !== 'string' || !e.id) {diagnostics.push({kind:'invalid_edge_id',case_id:caseId,record:e});continue;}
      sourceEdgeOccurrences++;
      if(!edges.has(e.id)) edges.set(e.id,e);
      else if(scalar(edges.get(e.id)) !== scalar(e)) conflicts.push({kind:'duplicate_edge_payload',id:e.id,case_id:caseId,record:e});
      if(!edgeCases.has(e.id)) edgeCases.set(e.id,new Set());edgeCases.get(e.id).add(caseId);
    }
    for(const key of ['diagnostics','unsupported','gaps','conflicts']) if(Array.isArray(data[key])) diagnostics.push(...data[key].map(value=>({case_id:caseId,kind:key,value})));
  }
  const adjacent = new Map(), types = new Map(), relations = new Map(), levels = new Map();
  for(const n of nodes.values()) {adjacent.set(n.id,[]);types.set(kindOf(n),(types.get(kindOf(n))||0)+1);levels.set(n.semantic_level || 'unspecified',(levels.get(n.semantic_level || 'unspecified')||0)+1);}
  const dangling = [];
  for(const e of edges.values()) {
    relations.set(relationOf(e),(relations.get(relationOf(e))||0)+1);
    if(!nodes.has(e.source) || !nodes.has(e.target)) dangling.push(e);
    if(adjacent.has(e.source)) adjacent.get(e.source).push(e);
    if(e.source !== e.target && adjacent.has(e.target)) adjacent.get(e.target).push(e);
  }
  const searchText = new Map();
  for(const n of nodes.values()) searchText.set(n.id,[n.id,textLabel(n.label),kindOf(n),n.raw_kind,n.source_collection,n.semantic_level,scalar(n.aliases),scalar(n.identity),...(n.source_records||[]).map(s=>`${s.source_id || ''} ${s.case_id || ''}`)].join(' ').toLowerCase());
  return {nodes,edges,nodeCases,edgeCases,adjacent,types,relations,levels,searchText,conflicts,diagnostics,dangling,schemas,sourceNodeOccurrences,sourceEdgeOccurrences};
}
export function queryNodes(index,{query='',type='',level='',caseIds=null}={}) {
  const terms=query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return [...index.nodes.values()].filter(n=>(!type || kindOf(n)===type)&&(!level || n.semantic_level===level)&&(!caseIds || [...(index.nodeCases.get(n.id)||[])].some(id=>caseIds.has(id)))&&terms.every(t=>index.searchText.get(n.id).includes(t)));
}
export function chooseScene(index,{matches=[],focus=null,hops=1,limit=100,edgeLimit=700,relation='',caseIds=null}={}) {
  limit=Math.max(1,Math.min(600,Number(limit)||100));hops=Math.max(0,Math.min(3,Number(hops)||0));
  const eligible = new Set(matches.map(n=>n.id)), chosen = new Set(), distance = new Map();
  let neighborhoodTotal=0;
  if(focus && index.nodes.has(focus)) {
    const visited=new Set([focus]);let frontier=[focus];distance.set(focus,0);
    for(let d=0;d<=hops;d++) {
      const next=[];
      for(const id of frontier) {
        if(id===focus || eligible.has(id)) {neighborhoodTotal++;if(chosen.size<limit)chosen.add(id);}
        if(d<hops)for(const e of index.adjacent.get(id)||[]) {
          if(relation && relationOf(e)!==relation)continue;
          if(caseIds && ![...(index.edgeCases.get(e.id)||[])].some(id=>caseIds.has(id)))continue;
          const other=e.source===id?e.target:e.source;
          if(index.nodes.has(other)&&!visited.has(other)) {visited.add(other);distance.set(other,d+1);next.push(other);}
        }
      }
      frontier=next;
    }
  } else {
    neighborhoodTotal=matches.length;
    // Round-robin across kinds provides a truthful bounded preview, never synthetic representatives.
    const buckets=new Map();for(const n of matches){const k=kindOf(n);if(!buckets.has(k))buckets.set(k,[]);buckets.get(k).push(n);}
    let round=0;while(chosen.size<Math.min(limit,matches.length)) {for(const b of buckets.values())if(b[round]&&chosen.size<limit)chosen.add(b[round].id);round++;}
  }
  const eligibleEdges=[];let boundaryEdges=0;
  for(const e of index.edges.values()) {
    if(relation && relationOf(e)!==relation)continue;
    if(caseIds && ![...(index.edgeCases.get(e.id)||[])].some(id=>caseIds.has(id)))continue;
    const a=chosen.has(e.source),b=chosen.has(e.target);
    if(a&&b)eligibleEdges.push(e);else if(a||b)boundaryEdges++;
  }
  // Incident focus edges first, while preserving the source's labels and direction.
  if(focus)eligibleEdges.sort((a,b)=>Number(b.source===focus||b.target===focus)-Number(a.source===focus||a.target===focus));
  return {focus,nodes:[...chosen].map(id=>index.nodes.get(id)),edges:eligibleEdges.slice(0,edgeLimit),distance,neighborhoodTotal,omittedNodes:Math.max(0,neighborhoodTotal-chosen.size),omittedEdges:Math.max(0,eligibleEdges.length-edgeLimit),boundaryEdges,eligibleEdgeCount:eligibleEdges.length,focusOutsideFilter:!!focus&&!eligible.has(focus)};
}
export function factFields(n) {
  const p=n.payload||{}, identity=n.identity||{};
  const fields={};for(const k of ['value','unit','units','at_s','time','timestamp','timestamp_ns','valid_time_ns','available_time_ns','valid_until_ns','clock_domain','received_at_s','query_time_ns','availability_cutoff_ns','valid_time','observed_at','status','truth','native_truth','truth_status','definition_ast','ast','aliases','missing','unknown','missing_fields','unsupported_fields']) {
    if(Object.hasOwn(p,k))fields[k]=p[k];else if(Object.hasOwn(n,k))fields[k]=n[k];else if(Object.hasOwn(identity,k))fields[k]=identity[k];
  }
  return fields;
}
export function classify(n) {
  const k=kindOf(n).toLowerCase();
  if(/expression|operator|state_ref|constant|parameter_ref|ast/.test(k))return 'AST';
  if(/state|fact|observation/.test(k))return '状态 / 事实';
  if(/rule|predicate|constraint/.test(k))return '规则 / 谓词';
  if(/event|result|receipt|evidence|outcome/.test(k))return '事件 / 证据';
  if(/agent|strategy|objective|task|decision/.test(k))return '决策';
  if(/behavior|capability|module|command|execution/.test(k))return '执行';
  if(/entity|resource|node_type|relation_|space|zone|allocation|lease_|endpoint/.test(k))return '世界 / 类型';
  return '其他 / 源类型';
}
export function layoutScene(scene,flat=false) {
  const positions=new Map(),groups=new Map();
  for(const n of scene.nodes){const group=classify(n);if(!groups.has(group))groups.set(group,[]);groups.get(group).push(n);}
  const names=[...groups.keys()];
  if(flat && scene.focus) {
    const levels=new Map();for(const n of scene.nodes){const d=scene.distance.get(n.id)||0;if(!levels.has(d))levels.set(d,[]);levels.get(d).push(n);}
    for(const [d,list]of levels)list.forEach((n,i)=>positions.set(n.id,{x:d*300,y:(i-(list.length-1)/2)*88,z:0}));
  }else {
    names.forEach((g,gi)=>{const list=groups.get(g),cols=flat?Math.max(1,Math.ceil(Math.sqrt(list.length)/2)):Math.max(1,Math.ceil(Math.sqrt(list.length)));list.forEach((n,i)=>positions.set(n.id,{x:(gi-(names.length-1)/2)*400+(i%cols-(cols-1)/2)*190,y:(Math.floor(i/cols)-Math.floor((list.length-1)/cols)/2)*88,z:flat?0:(gi-(names.length-1)/2)*160}));});
  }
  return positions;
}
export function projectPoint(p,camera,width,height) {
  const cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw),cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch);
  const rx=p.x*cy+p.z*sy,rz=-p.x*sy+p.z*cy,ry=p.y*cp-rz*sp,depth=p.y*sp+rz*cp;
  const perspective=2400/Math.max(600,2400+depth);
  return {x:width/2+camera.panX+rx*perspective*camera.zoom,y:height/2+camera.panY+ry*perspective*camera.zoom,depth};
}
