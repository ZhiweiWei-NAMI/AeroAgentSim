// Run only beside copies of upstream runtimes in pytest's scratch directory.
// This bridge adapts input representation; all operators execute upstream code.
const fs = require('node:fs');
const { PortableEngine } = require('./original_runtime.js');
const { AeroGraphExpandedRuntime } = require('./expanded_runtime.js');
const commands = JSON.parse(fs.readFileSync(0, 'utf8'));
function walk(n, visit) {
  visit(n);
  for (const [key,value] of Object.entries(n)) {
    if (key === 'literal') continue;
    if (Array.isArray(value)) value.forEach(x => { if (x && typeof x === 'object') walk(x,visit); });
    else if (value && typeof value === 'object') walk(value,visit);
  }
}
function schema(v) {
  if (v === null || v === undefined) return {type:'number',nullable:true};
  if (Array.isArray(v)) return {type:'array'};
  if (typeof v === 'object') return {type:'record',members:Object.fromEntries(Object.entries(v).map(([k,x])=>[k,schema(x)]))};
  return {type: typeof v};
}
function original(c) {
  const fields=[];
  walk(c.ast,n=>{if(n.field){const id=n.role+'|'+n.field;if(!fields.includes(id)) fields.push(id);}});
  function compact(n) {
    if ('literal' in n) return ['c',n.literal];
    if ('field' in n) {
      if ((n.path||[]).length) throw Error('original field path is not native');
      return ['s',fields.indexOf(n.role+'|'+n.field)];
    }
    if ('parameter' in n) return ['p',n.parameter];
    if ('time' in n) throw Error('original time leaf is not native');
    const op=n.op==='entered'?'rise':n.op==='exited'?'fall':n.op;
    const args=(n.args||[]).map(compact);
    if (n.asScope) {
      if(args.length===2) args.push(['c',null]);
      args.push(n.asScope.args.map(compact));
    }
    return [op,...args];
  }
  const engine=new PortableEngine({F:fields,T:[['target','target','test','test',compact(c.ast),null,null]]});
  const frame=f=>({t:f.t,states:Object.fromEntries(fields.map(id=>{const split=id.indexOf('|');return [id,f.fields?.[id.slice(0,split)]?.[id.slice(split+1)]];}))});
  const input={...frame(c.history.at(-1)),history:c.history.slice(0,-1).map(frame),parameters:c.history.at(-1).parameters||{}};
  const context=engine.makeContext(input,0);
  return {status:'known',value:engine.fns[0](context)};
}
function expanded(c) {
  const roles=new Set(), fields=new Map(), parameters=new Map(),relations=new Map();
  walk(c.ast,n=>{
    if(n.field){roles.add(n.role);const observed=c.history.map(f=>f.fields?.[n.role]?.[n.field]).find(x=>x!==undefined&&x!==null);fields.set(n.field,{id:n.field,declaringClass:'T',valueSchema:schema(observed)});}
    if(n.parameter)parameters.set(n.parameter,{id:n.parameter,valueSchema:schema(c.history.at(-1).parameters?.[n.parameter])});
    if(n.relation){roles.add(n.sourceRole);roles.add(n.targetRole);relations.set(n.relation,{id:n.relation,sourceClass:'T',targetClass:'T'});}
  });
  const contract={id:'test',roles:[...roles].map(id=>({id,typeIds:['T']})),parameters:[...parameters.values()],expression:c.ast};
  const runtime=new AeroGraphExpandedRuntime({entities:[{id:'T',parent:null}],fields:[...fields.values()],relations:[...relations.values()],contracts:[contract],events:c.transition?[{id:'event',transitionOf:'test'}]:[]});
  function frame(f) {
    const instances=Object.fromEntries([...roles].map(id=>[id,{instanceId:id,entityTypeId:'T',runId:'test',epoch:'0',generation:0,source:'test',clock:'test',observedAt:f.t}]));
    const relationSets={};
    for(const [id] of relations) {
      const edges=[]; let supplied=false;
      walk(c.ast,n=>{
        if(n.relation!==id)return;
        const key=id+'|'+n.sourceRole+'|'+n.targetRole;
        if(Object.hasOwn(f.relations||{},key))supplied=true;
        if(f.relations?.[key]===true)edges.push({sourceRole:n.sourceRole,targetRole:n.targetRole,relationInstanceId:key,validFrom:0,validTo:1000000000000,source:'test'});
      });
      if(supplied)relationSets[id]={complete:true,observedAt:f.t,clock:'test',source:'test',edges};
    }
    return {t:f.t,clock:'test',instances,states:f.fields||{},parameters:f.parameters||{},relations:relationSets};
  }
  const input=frame(c.history.at(-1)); input.history=c.history.slice(0,-1).map(frame);
  return c.transition?runtime.evaluate('event',input):runtime.evaluateAst(contract,c.ast,input);
}
console.log(JSON.stringify(commands.map(c=>c.dialect==='original'?original(c):expanded(c))));
