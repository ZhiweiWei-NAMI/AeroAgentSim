import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { validateConfig } from '../src/config.js';
import { safeImport, exportConfig } from '../src/runtime.js';
import { graphNodes, graphEdges, validateGraph, graphContractInventory } from '../src/graph-config.js';
import { executeGraphFixture, resolveBehaviorPrerequisites } from '../src/graph-runtime.js';
import { loadEmergencyProjection, projectEmergencyConfig, EMERGENCY_CONFIG_PATH } from '../scripts/emit-emergency-graph.mjs';
const P='demo.emergency_delivery.';
const load=async()=>safeImport(await readFile(EMERGENCY_CONFIG_PATH,'utf8'));

test('emergency example is one real authoring configuration and survives normal import/export',async()=>{
 const config=await load();assert.equal(config.schema_version,'aero-console.config/v1');assert.equal(config.graph.schema_version,'aeroagentsim.unified-graph/v1');assert.equal(validateConfig(config).valid,true);assert.equal(validateGraph(config.graph,config).valid,true);assert.deepEqual(safeImport(JSON.stringify(exportConfig(config))),config);
 const projection=projectEmergencyConfig(config);assert.deepEqual(projection.config,config);assert.deepEqual(projection.graph,config.graph);assert.deepEqual(projection.nodes,graphNodes(config.graph));assert.deepEqual(projection.edges,graphEdges(config.graph));assert.deepEqual(projection.inventory,graphContractInventory(config.graph));assert.equal(projection.config_digest,projection.roundtrip.config_digest);
});

test('canonical definitions and runtime instances have distinct identity and explicit source basis',async()=>{
 const projection=await loadEmergencyProjection(),canonical=new Map(projection.nodes.map(node=>[node.id,node]));assert.ok(canonical.has(P+'command.definition'));assert.ok(canonical.has(P+'event_def'));assert.equal(canonical.has(P+'command'),false);
 for(const node of projection.runtime_overlay.nodes){assert.ok(canonical.has(node.definition_id),node.definition_id);assert.equal(canonical.has(node.id),false);assert.ok(node.source_path.startsWith('fixture_runs['));assert.equal(node.actual_execution,'not_established');}
 for(const relation of ['PRODUCES','COMPOSES','FEEDS_BACK','INPUT_TO','USES_RULE','ARGUMENT','DEFINES'])assert.ok(projection.edges.some(edge=>edge.relation===relation),relation);
 assert.ok(projection.nodes.some(node=>node.collection==='expressions'&&node.read_only&&node.owner_rule_id===P+'rule.incident'));
});

test('UAV perception, report, edge decision and received grant are separate canonical objects',async()=>{
 const {graph}=await load();const find=(collection,suffix)=>graph[collection].find(node=>node.id===P+suffix);
 assert.equal(find('facts','fact.incident').producer_module_id,P+'perception');assert.equal(find('facts','fact.incident').entity_id,P+'person');assert.equal(find('modules','perception').kind,'compute');assert.equal(find('agents','agent').strategy_id,P+'strategy');assert.equal(find('agents','edge_agent').strategy_id,P+'edge_strategy');assert.equal(find('entities','edge_server').type_id,P+'type.edge_server');
 assert.equal(find('delivery_receipts','report_receipt').received_at_ns,'13000000000');assert.equal(find('lease_approvals','approval').issued_at_ns,'14000000000');assert.equal(find('delivery_receipts','grant_receipt').received_at_ns,'15000000000');assert.deepEqual(find('lease_approvals','approval').evidence_receipt_ids,[P+'report_receipt']);
 assert.deepEqual(find('lease_approvals','return_approval').evidence_receipt_ids,[P+'return_report_receipt']);assert.equal(find('delivery_receipts','return_report_receipt').received_at_ns,'42300000000');
});

test('outbound and return allocations preserve exact half-open intervals and no-fly restriction',async()=>{
 const {graph}=await load();const outbound=graph.space_time_allocations.find(node=>node.id===P+'allocation'),returning=graph.space_time_allocations.find(node=>node.id===P+'return_allocation');assert.deepEqual(outbound.window,{start_ns:'16000000000',end_ns:'22000000000'});assert.deepEqual(returning.window,{start_ns:'44000000000',end_ns:'78000000000'});assert.notEqual(outbound.approval_id,returning.approval_id);assert.equal(graph.spatial_zones.find(node=>node.kind==='no_fly_zone').access,'prohibited');
 for(const [behavior,time,expected]of[['approach','15500000000','BLOCK'],['approach','16000000000','PERMIT'],['approach','22000000000','BLOCK'],['return','43000000000','BLOCK'],['return','44000000000','PERMIT'],['return','78000000000','BLOCK']])assert.equal(resolveBehaviorPrerequisites(graph,P+'behavior.'+behavior,time).verdict,expected);
});

test('independent privacy/safety/exercise checks never clear the unverified real permission gate',async()=>{
 const projection=await loadEmergencyProjection();const capture=projection.fixture_runs.find(run=>run.scenario_id===P+'scenario.capture');assert.equal(capture.status,'fixture_executed');for(const check of ['check.safety','check.privacy','check.permission'])assert.ok(capture.check_results.some(result=>result.check_id===P+check&&result.verdict==='PERMIT'),check);
 const behavior=capture.behaviors.find(behavior=>behavior.id===P+'behavior.capture');assert.equal(behavior.admission.verdict,'UNKNOWN');assert.ok(behavior.admission.unchecked_constraint_ids.includes(P+'constraint.permission'));assert.equal(behavior.status,'fixture_partial_admission');assert.equal(capture.assessments.permission,'UNKNOWN');assert.equal(capture.predicate_truth,'UNKNOWN');assert.equal(capture.stages.modules,'not_executed');
});

test('success, failure and UNKNOWN uploads retain different receipts and preserve parcel custody',async()=>{
 const projection=await loadEmergencyProjection();const success=projection.fixture_runs.find(run=>run.scenario_id===P+'scenario.success'),failure=projection.fixture_runs.find(run=>run.scenario_id===P+'scenario.failure'),unknown=projection.fixture_runs.find(run=>run.scenario_id===P+'scenario.unknown');assert.equal(success.delivery_receipts[0].status,'acknowledged');assert.equal(failure.delivery_receipts.find(receipt=>receipt.id===P+'upload_failure_receipt').status,'rejected');assert.equal(unknown.delivery_receipts[0].status,'UNKNOWN');assert.equal(unknown.delivery_receipts[0].received_at_ns,null);
 for(const run of [success,failure,unknown]){for(const field of ['state.custody','state.attachment'])assert.equal(run.facts.find(fact=>fact.field_id===P+field).value,P+'uav');assert.ok(run.commands.every(command=>command.effect==='not_executed'));}
 for(const effect of projection.expected_effects){assert.equal(effect.record_kind,'authored_expected_effect');assert.equal(effect.actual_effect_established,false);assert.ok(effect.source_path.startsWith('graph.modules['));assert.equal(effect.parcel_attached,true);assert.equal(effect.custodian_entity_id,P+'uav');}
});

test('editing canonical evidence changes the derived fixture gate, never a second HTML evaluator',async()=>{
 const config=await load();config.graph.facts.find(fact=>fact.id===P+'fact.incident_refresh').value=0.2;const projection=projectEmergencyConfig(config);const capture=projection.fixture_runs.find(run=>run.scenario_id===P+'scenario.capture');assert.equal(capture.check_results.find(result=>result.check_id===P+'check.incident').verdict,'BLOCK');assert.equal(capture.behaviors.find(behavior=>behavior.id===P+'behavior.capture').admission.verdict,'BLOCK');assert.equal(projection.nodes.find(node=>node.id===P+'fact.incident_refresh').node.value,0.2);
});

test('inventory dispositions identify absence separately from implementation or mapping gaps',async()=>{
 const projection=await loadEmergencyProjection(),coverage=projection.coverage;assert.equal(coverage.accounted_items,coverage.total_items);assert.equal(coverage.disposition_coverage_percent,100);assert.ok(coverage.represented_items<coverage.total_items);assert.ok(coverage.rows.some(row=>row.root_cause==='not_applicable'));assert.ok(coverage.rows.some(row=>row.root_cause==='implementation_missing'));for(const relation of ['PRODUCES','COMPOSES','FEEDS_BACK'])assert.equal(coverage.rows.find(row=>row.category==='relation'&&row.key===relation).status,'represented');assert.match(coverage.claim,/does not mean every/);
});


test('emergency digests use the same property-order-neutral identity as console fixtures', async () => {
 const { canonicalJSON, sha256 } = await import('../src/runtime.js');
 const config = JSON.parse(await readFile(EMERGENCY_CONFIG_PATH, 'utf8'));
 const projection = projectEmergencyConfig(config);
 assert.equal(projection.config_digest, await sha256(config));
 const reordered = Object.fromEntries(Object.entries(config).reverse());
 assert.equal(projectEmergencyConfig(reordered).config_digest, projection.config_digest);
 assert.equal(canonicalJSON(config), canonicalJSON(reordered));
});

test('fixture module read scopes follow delivered report authority without inventing raw sensor execution',async()=>{
 const {graph}=await load(),modules=new Map(graph.modules.map(module=>[module.id,module]));
 assert.deepEqual(modules.get(P+'edge_compute').reads,[{entity_ids:[P+'edge_server'],field_ids:[P+'state.report_received']}]);
 assert.deepEqual(modules.get(P+'edge_compute').writes,[]);
 assert.deepEqual(graph.agents.find(agent=>agent.id===P+'edge_agent').observable_fact_ids,[P+'fact.report_received']);
 assert.deepEqual(graph.strategies.find(strategy=>strategy.id===P+'edge_strategy').observable_fact_ids,[P+'fact.report_received']);
 assert.ok(modules.get(P+'ns3').reads.some(scope=>scope.entity_ids.includes(P+'person')&&scope.field_ids.includes(P+'state.confidence')));
 assert.ok(modules.get(P+'ns3').reads.some(scope=>scope.entity_ids.includes(P+'camera')&&scope.field_ids.includes(P+'state.media')));
 assert.equal(graph.facts.find(fact=>fact.id===P+'fact.incident').producer_module_id,P+'perception');
 assert.equal(graph.facts.find(fact=>fact.id===P+'fact.report_received').producer_module_id,P+'ns3');
 assert.equal(modules.get(P+'perception').parameters.raw_sensor_input_mapping.status,'schema_mapping_gap');
 const projection=projectEmergencyConfig(await load());assert.equal(projection.coverage.rows.find(row=>row.category==='relation'&&row.key==='READ_SCOPE').status,'represented');assert.equal(projection.mapping_diagnostics[0].root_cause,'schema_mapping_gap');
});

test('missing/rejected grant previews are explicit read-only counterfactual artifacts',async()=>{
 const projection=await loadEmergencyProjection();const missing=projection.counterfactual_probes.find(probe=>probe.name==='missing_grant_receipt'),denied=projection.counterfactual_probes.find(probe=>probe.name==='rejected_approval');
 assert.equal(missing.validation.valid,true);assert.equal(missing.result.verdict,'UNKNOWN');assert.equal(missing.result.allowed_in_fixture,false);assert.equal(denied.validation.valid,false);assert.equal(denied.result.verdict,'BLOCK');assert.equal(denied.result.allowed_in_fixture,false);
 for(const probe of [missing,denied]){assert.equal(probe.authoritative,false);assert.equal(probe.base_config_digest,projection.config_digest);assert.ok(probe.overrides.length);assert.notEqual(probe.variant_config_digest,projection.config_digest);}
 assert.equal(projection.config.graph.lease_approvals.find(record=>record.id===P+'approval').decision,'approved');assert.equal(projection.config.graph.delivery_receipts.find(record=>record.id===P+'grant_receipt').status,'acknowledged');
});

test('scenario default altitude and primary scene pose match the authored corridor route start',async()=>{
 const config=await load();const route=config.graph.commands.find(command=>command.id===P+'command.definition').parameters.route_plan;
 assert.equal(config.mobility.uav.altitude_m,15);assert.deepEqual(config.entities.find(entity=>entity.id==='uav-alpha').position_enu_m,route.waypoints[0]);
 const envelope=config.graph.spatial_zones.find(zone=>zone.id===P+'corridor').altitude;assert.ok(config.mobility.uav.altitude_m>=envelope.min_m&&config.mobility.uav.altitude_m<=envelope.max_m);
});
