/** Pool raw Q7 repetitions without treating cached idle counters as new timings. */
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const [output, ...directories] = process.argv.slice(2);
if (!output || directories.length < 3) throw Error('Usage: q7-summarize.mjs OUTPUT.json REP_DIR REP_DIR REP_DIR [...]');
const percentile = (values, fraction) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a,b)=>a-b);
  return sorted[Math.ceil(sorted.length*fraction)-1];
};
const stats = values => values.length ? { p50:percentile(values,.5),p95:percentile(values,.95),p99:percentile(values,.99) } : null;
const groups = new Map();
for (const directory of directories) {
  const files = readdirSync(directory).filter(file=>/^measurements-.*\.json$/.test(file));
  if (files.length!==1) throw Error('Expected one measurement file: '+directory);
  const source=join(directory,files[0]),data=JSON.parse(readFileSync(source,'utf8'));
  if(data.failure || !data.finishedAt)throw Error('Incomplete measurement: '+source);
  for(const record of data.records){
    if(record.errors.length)throw Error('Page error: '+source+' '+record.label);
    const key=data.mode+'/'+record.label;
    let group=groups.get(key);
    if(!group){group={mode:data.mode,label:record.label,repetitions:[],frames:[],timings:{},calls:[],triangles:[],renderers:new Set()};groups.set(key,group);}
    const raw=data.rawSamples[record.label];
    group.frames.push(...raw.frames);
    group.renderers.add(record.diagnostics.gpu ?? data.probeRenderer);
    const actual=raw.diagnostics.filter((d,i,all)=>d.renderCount===undefined || (d.idle!=='true' && d.renderCount!==(i===0?raw.startDiagnostics?.renderCount:all[i-1].renderCount)));
    for(const field of ['renderMs','updateMs','entitiesMs','labelsMs','submitMs','gpuMs']){
      const samples=field==='gpuMs'?raw.diagnostics.filter((d,i,all)=>d.gpuSample!==undefined && i>0 && d.gpuSample!==all[i-1].gpuSample):actual;
      group.timings[field] ??= [];
      group.timings[field].push(...samples.filter(d=>d[field]!==undefined).map(d=>Number(d[field])));
    }
    group.calls.push(...raw.diagnostics.filter(d=>d.drawCalls!==undefined).map(d=>Number(d.drawCalls)));
    group.triangles.push(...raw.diagnostics.filter(d=>d.triangles!==undefined).map(d=>Number(d.triangles)));
    group.repetitions.push({source,frames:raw.frames.length,actualRenderSamples:record.diagnostics.renderCount===undefined?null:actual.length,frameMs:record.frameMs,gpuTimer:record.diagnostics.gpuTimer ?? null,jsHeapBytes:record.jsHeapBytes,diagnostics:record.diagnostics});
  }
}
const summary=[...groups.values()].map(group=>({
  mode:group.mode,label:group.label,renderers:[...group.renderers],repetitions:group.repetitions,
  frameMs:stats(group.frames),timings:Object.fromEntries(Object.entries(group.timings).map(([field,values])=>[field,stats(values)])),
  drawCalls:group.calls.length?{p50:percentile(group.calls,.5),max:Math.max(...group.calls)}:null,
  triangles:group.triangles.length?{p50:percentile(group.triangles,.5),max:Math.max(...group.triangles)}:null,
}));
if(summary.some(group=>group.repetitions.length<3))throw Error('Each case requires at least three repetitions');
writeFileSync(output,JSON.stringify(summary,null,2)+'\n');
console.log(summary.map(g=>({mode:g.mode,label:g.label,frameMs:g.frameMs,timings:g.timings})));
