import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {readFile} from 'node:fs/promises';
import {DEFAULT_CONFIG,clone} from '../src/config.js';
import {DEFAULT_GRAPH,GRAPH_COLLECTIONS,graphEdges,graphNodes} from '../src/graph-config.js';
import {graphDisplayEdges,graphRelationLayers} from '../src/graph-display.js';
import {createGraphWorkbench} from '../src/graph-workbench.js';

// DOM interactions only. Pixel layout requires a separate actual Chromium run; this file does not verify it.
const dom=new JSDOM(await readFile(new URL('../index.html',import.meta.url),'utf8'),{url:'http://console.test/',pretendToBeVisual:true});
for(const key of ['window','document','localStorage','history','location','FormData'])globalThis[key]=dom.window[key];
const failures=[];dom.window.addEventListener('error',event=>failures.push(event.error||event.message));
await import('../src/app.js');
const $=selector=>document.querySelector(selector);
const click=selector=>{assert.ok($(selector),selector);$(selector).click();};
const change=(selector,value)=>{$(selector).value=String(value);$(selector).dispatchEvent(new window.Event('change',{bubbles:true}));};
const input=(selector,value)=>{$(selector).value=String(value);$(selector).dispatchEvent(new window.Event('input',{bubbles:true}));};
const workspace=()=>JSON.parse(localStorage.getItem('aero-console.workspace.v1'));
const select=id=>{const element=[...document.querySelectorAll('[data-gw-action="select"]')].find(node=>node.dataset.id===id);assert.ok(element,`Graph node ${id}`);element.click();};
const edit=()=>click('.gw-inspection [data-gw-action="edit"]');
const waitFor=async predicate=>{for(let i=0;i<100;i++){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,10));}assert.fail('Operation timed out');};

test('unified graph shares the console draft, version, language and replay selection',async t=>{
  await t.test('graph is a first-class tab alongside every original form',()=>{
    for(const name of ['graph','scenario','entities','mobility','network','compute','semantics'])assert.ok($(`[data-tab="${name}"]`));
    click('[data-tab="graph"]');assert.ok($('[data-graph-workbench]'));assert.match($('.gw-inspection').textContent,/模块所有权/);
    for(const {key} of GRAPH_COLLECTIONS)assert.ok($(`[data-gw-action="collection"][data-key="${key}"]`),key);
    assert.match($('.gw-loop').textContent,/OUTPUT_MUST_SATISFY/);assert.match($('.gw-loop').textContent,/约束准入检查/);
  });
  await t.test('typed records expose explicit scopes, capabilities, behaviors and composition',()=>{
    click('[data-gw-action="collection"][data-key="agents"]');select('alpha-autonomy');assert.match($('.gw-inspection').textContent,/声明控制范围/);
    click('[data-gw-action="collection"][data-key="commands"]');select('return-alpha');assert.match($('.gw-inspection').textContent,/navigate-home/);assert.match($('.gw-inspection').textContent,/fly-home/);assert.match($('.gw-inspection').textContent,/land-home/);
    click('[data-gw-action="collection"][data-key="behaviors"]');select('recharge');assert.match($('.gw-inspection').textContent,/charge-capability/);assert.match($('.gw-inspection').textContent,/energy-module/);assert.match($('.gw-inspection').textContent,/pad-availability/);
  });
  await t.test('parallel typed edges remain individually inspectable with complete metadata',()=>{
    click('[data-gw-action="collection"][data-key="strategies"]');select('alpha-strategy');
    const parallel=graphEdges(DEFAULT_GRAPH).filter(edge=>edge.source==='alpha-battery'&&edge.target==='alpha-strategy');assert.ok(parallel.length>=2);
    for(const edge of parallel){assert.ok($(`[data-edge-id="${edge.id}"]`));click(`[data-gw-action="edge"][data-id="${edge.id}"]`);assert.match($('.gw-edge-inspector').textContent,new RegExp(edge.id));assert.match($('.gw-edge-inspector').textContent,/作用范围/);}
    click('[data-gw-action="close-edge"]');assert.equal($('.gw-edge-inspector'),null);
  });
  await t.test('five relation layers expose separate audited counts and filter only display edges',()=>{
    const canonical=graphEdges(DEFAULT_GRAPH),layers=graphRelationLayers(canonical),before=clone(workspace()?.draft.graph||DEFAULT_GRAPH);
    assert.deepEqual(layers.map(layer=>layer.declared_type_count),[38,27,11,8,8]);
    assert.equal(document.querySelectorAll('.gw-relation-layer').length,5);assert.doesNotMatch($('.gw-relation-review').textContent,/%/);
    for(const layer of layers){
      const card=$(`[data-layer-id="${layer.id}"]`);assert.equal(Number(card.dataset.contractCount),layer.declared_type_count);assert.equal(Number(card.dataset.presentCount),layer.present_relations.length);assert.equal(Number(card.dataset.edgeCount),layer.edge_count);
      click(`[data-gw-action="relation-layer"][data-key="${layer.id}"]`);assert.equal($(`[data-layer-id="${layer.id}"]`).getAttribute('aria-pressed'),'true');
      for(const edge of document.querySelectorAll('.gw-dependency-map .gw-edge-card'))assert.equal(edge.dataset.reviewLayer,layer.id);
    }
    change('#locale','en-US');assert.match($('.gw-relation-review').textContent,/Five-layer relation review/);change('#locale','zh-CN');assert.match($('.gw-relation-review').textContent,/五层关系审阅/);
    click('[data-gw-action="relation-layer"][data-key="all"]');assert.deepEqual(workspace().draft.graph,before);assert.deepEqual(graphEdges(DEFAULT_GRAPH),canonical);
  });
  await t.test('audited PRODUCES display bundles retain both complete canonical origin records',()=>{
    const canonical=graphEdges(DEFAULT_GRAPH),bundles=graphDisplayEdges(DEFAULT_GRAPH,canonical).filter(edge=>edge.display_bundle);assert.equal(bundles.length,3);
    select('alpha-strategy');const bundle=bundles.find(edge=>edge.source==='alpha-strategy');assert.ok(bundle);
    const displayed=[...document.querySelectorAll('.gw-edge-card')].find(element=>element.dataset.edgeId===bundle.id);assert.ok(displayed);assert.equal(displayed.dataset.displayBundle,'true');displayed.querySelector('.gw-edge-label').click();
    const origins=[...document.querySelectorAll('.gw-bundle-origins [data-canonical-edge-id]')];assert.deepEqual(origins.map(element=>element.dataset.canonicalEdgeId),bundle.canonical_edge_ids);assert.deepEqual(origins.map(element=>JSON.parse(element.querySelector('pre').textContent)),bundle.origin_edges);
    const nonMirror=canonical.filter(edge=>edge.source==='alpha-battery'&&edge.target==='alpha-strategy');for(const edge of nonMirror)assert.ok([...document.querySelectorAll('.gw-edge-card')].some(element=>element.dataset.edgeId===edge.id));
    assert.deepEqual(graphEdges(DEFAULT_GRAPH),canonical);click('[data-gw-action="close-edge"]');
  });
  await t.test('pending editor preserves user values across language and navigation; Cancel does not mutate',()=>{
    edit();const record=JSON.parse($('#gw-record-json').value);record.label='用户名称 · Keep exact';input('#gw-record-json',JSON.stringify(record,null,2));
    change('#locale','en-US');assert.equal(document.documentElement.lang,'en-US');assert.equal(JSON.parse($('#gw-record-json').value).label,record.label);
    click('[data-tab="scenario"]');click('[data-tab="graph"]');assert.equal(JSON.parse($('#gw-record-json').value).label,record.label);
    click('[data-gw-action="cancel-edit"]');assert.equal(workspace().draft.graph.strategies.find(item=>item.id===record.id).label,DEFAULT_GRAPH.strategies.find(item=>item.id===record.id).label);
  });
  await t.test('invalid references are diagnosed before Apply, and stable IDs cannot silently change',()=>{
    edit();const original=JSON.parse($('#gw-record-json').value);const bad={...original,output_command_ids:['not-a-command']};input('#gw-record-json',JSON.stringify(bad));click('[data-gw-action="review"]');assert.ok($('.gw-editor .gw-issue'));assert.equal($('.gw-editor [data-gw-action="apply"]'),null);
    input('#gw-record-json',JSON.stringify({...original,id:'changed-id'}));click('[data-gw-action="review"]');assert.match($('.gw-editor').textContent,/stable ID cannot be changed/);
    input('#gw-record-json','{"id":');click('[data-gw-action="review"]');assert.match($('.gw-editor').textContent,/Invalid JSON/);
    click('[data-gw-action="cancel-edit"]');
  });
  await t.test('reviewed record edits update the shared draft; versions retain the graph',()=>{
    click('[data-action="save"]');const before=clone(workspace().draft.graph);edit();const record=JSON.parse($('#gw-record-json').value);record.label='Local strategy revision';input('#gw-record-json',JSON.stringify(record));click('[data-gw-action="review"]');assert.match($('.gw-change-review').textContent,/Local strategy revision/);assert.deepEqual(workspace().draft.graph,before);
    click('[data-gw-action="apply"]');assert.equal(workspace().draft.graph.strategies.find(item=>item.id===record.id).label,record.label);click('[data-action="save"]');assert.equal(workspace().versions.at(-1).config.graph.strategies.find(item=>item.id===record.id).label,record.label);
    click('[data-nav="versions"]');assert.match(document.body.textContent,/Configuration versions/);click('[data-nav="configuration"]');click('[data-tab="graph"]');
  });
  await t.test('all four cases use the same edited graph and report fixture-only boundaries',()=>{
    for(const id of ['charging','logistics','comm-return','constraint-change']){change('#gw-scenario',id);click('[data-gw-action="execute"]');assert.match($('.gw-result').textContent,/Fixture executed/);assert.match($('.gw-result').textContent,/UNKNOWN/);assert.match($('.gw-result').textContent,new RegExp(id));}
    assert.match($('.gw-result').textContent,/PERMIT/);assert.match($('.gw-result').textContent,/BLOCK/);assert.match($('.gw-result').textContent,/fixture_paused/);
    click('[data-gw-action="collection"][data-key="strategies"]');select('alpha-strategy');edit();const record=JSON.parse($('#gw-record-json').value);record.label='After execution';input('#gw-record-json',JSON.stringify(record));click('[data-gw-action="review"]');click('[data-gw-action="apply"]');assert.equal($('.gw-result'),null);assert.match($('.gw-fixture').textContent,/previous case result is stale/);
  });
  await t.test('scene metadata and entity bindings invalidate fixture readiness without changing graph data',()=>{
    change('#gw-scenario','charging');click('[data-gw-action="execute"]');assert.ok($('.gw-result'));
    const graphBefore=clone(workspace().draft.graph);
    change('#locale','zh-CN');change('#locale','en-US');assert.ok($('.gw-result'),'Language switching preserves current fixture readiness');
    click('[data-tab="scenario"]');change('#field-metadata-description','Revised scene metadata');click('[data-tab="graph"]');assert.equal($('.gw-result'),null);assert.match($('.gw-fixture').textContent,/previous case result is stale/);assert.deepEqual(workspace().draft.graph,graphBefore);
    click('[data-gw-action="execute"]');assert.ok($('.gw-result'));
    click('[data-tab="entities"]');click('[data-action="entity-edit"][data-id="uav-alpha"]');change('#entity-radio','');click('[data-action="entity-save"]');click('[data-tab="graph"]');assert.equal($('.gw-result'),null);assert.match($('.gw-fixture').textContent,/previous case result is stale/);assert.deepEqual(workspace().draft.graph,graphBefore);
  });
  await t.test('definition branches separate states, parameters, operators and predicate dependencies',()=>{
    click('[data-gw-action="collection"][data-key="predicates"]');select('low-battery');assert.ok($('.gw-ast'));assert.match($('.gw-ast').textContent,/lt/);assert.match($('.gw-ast').textContent,/state/);assert.match($('.gw-ast').textContent,/param/);assert.match($('.gw-inspection').textContent,/Definition dependencies/);
    select('return-needed');assert.match($('.gw-ast').textContent,/or/);assert.match($('.gw-ast').textContent,/low-battery/);assert.match($('.gw-ast').textContent,/link-degraded/);
    select('energy-link-risk');assert.match($('.gw-ast').textContent,/battery/);assert.match($('.gw-ast').textContent,/link-quality/);
  });
  await t.test('canonical expression nodes remain linked, inspectable and readonly with edits routed to their rule',()=>{
    const expression=graphNodes(DEFAULT_GRAPH).find(entry=>entry.collection==='expressions'&&entry.node.owner_rule_id==='energy-link-definition');assert.ok(expression);
    click('[data-gw-action="collection"][data-key="expressions"]');select(expression.id);assert.match($('.gw-inspection').textContent,/Derived · read only/);assert.equal($('.gw-inspection .gw-missing'),null);
    assert.equal($('.gw-inspection [data-gw-action="edit"]').dataset.id,'energy-link-definition');edit();assert.equal(JSON.parse($('#gw-record-json').value).id,'energy-link-definition');click('[data-gw-action="cancel-edit"]');
    const attempt=document.createElement('button');attempt.dataset.gwAction='edit';attempt.dataset.id=expression.id;document.body.append(attempt);attempt.click();attempt.remove();assert.equal($('#gw-record-json'),null);assert.match(document.body.textContent,/Derived nodes are read only/);
    click('[data-gw-action="collection"][data-key="expression_types"]');const type=graphNodes(DEFAULT_GRAPH).find(entry=>entry.collection==='expression_types');select(type.id);assert.equal($('.gw-inspection [data-gw-action="edit"]'),null);
  });
  await t.test('shared selection honors replay lock and does not fabricate cursor-synchronized facts',async()=>{
    click('[data-action="prepare"]');await waitFor(()=>$('[data-action="run-open"]'));click('[data-action="run-open"]');change('#entity-select','uav-alpha');click('[data-action="selection-lock"]');input('#timeline','25');const cursor=workspace().view.cursor_s;
    click('[data-nav="configuration"]');click('[data-tab="graph"]');click('[data-gw-action="collection"][data-key="entities"]');select('uav-beta');assert.equal(workspace().view.entity_id,'uav-alpha');assert.equal(workspace().view.cursor_s,cursor);assert.match($('.gw-shared-selection').textContent,/Selection locked/);assert.match($('.gw-fixture').textContent,/independently of the replay view cursor/);
    click('[data-nav="replay"]');click('[data-action="selection-lock"]');click('[data-nav="configuration"]');click('[data-tab="graph"]');select('uav-beta');assert.equal(workspace().view.entity_id,'uav-beta');assert.equal(workspace().view.cursor_s,cursor);
  });
  assert.deepEqual(failures,[]);dom.window.dispatchEvent(new window.Event('beforeunload'));
});

test('invalid persisted graph records remain inspectable instead of crashing the UI',()=>{
  const config=clone(DEFAULT_CONFIG);config.graph.entities.push(null,'malformed');config.graph.facts.push(null);config.graph.modules.push(null);config.graph.strategies.push(null);
  const ui=createGraphWorkbench({getConfig:()=>config,requestRender(){},commitGraph(){},getSelection:()=>({})});
  const markup=ui.render();assert.match(markup,/E_SCHEMA/);assert.match(markup,/data-graph-workbench/);
});

test('graph case execution validates same-workspace scene references',()=>{
  const config=clone(DEFAULT_CONFIG);config.entities.find(entity=>entity.id==='uav-alpha').type='vehicle';
  const ui=createGraphWorkbench({getConfig:()=>config,requestRender(){},commitGraph(){},getSelection:()=>({})});
  const target=document.createElement('button');target.dataset.gwAction='execute';ui.handleClick({target});
  assert.equal(ui.getState().result,null);assert.match(ui.getState().notice,/did not pass case checks/);assert.doesNotMatch(ui.render(),/class="gw-result"/);
});

test('a legacy graph-free draft is only populated by explicit adoption',()=>{
  const config=clone(DEFAULT_CONFIG);delete config.graph;let commits=0;
  const ui=createGraphWorkbench({getConfig:()=>config,requestRender(){},commitGraph(graph){config.graph=graph;commits++;},getSelection:()=>({})});
  const container=document.createElement('div');container.innerHTML=ui.render();assert.equal(config.graph,undefined);assert.match(container.textContent,/no unified graph/);ui.handleClick({target:container.querySelector('[data-gw-action="initialize"]')});assert.equal(commits,1);assert.equal(config.graph.schema_version,DEFAULT_GRAPH.schema_version);
  dom.window.close();
});
