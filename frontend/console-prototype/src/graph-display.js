/** Lossless review projection. Canonical contracts and source edges are unchanged. */
import { GRAPH_EDGE_CONTRACTS, graphEdges } from './graph-config.js';

export const RELATION_LAYERS = [
  {
    "id": "business_semantics",
    "label_zh": "业务语义",
    "label": "Business semantics",
    "relations": [
      "CALLS",
      "INPUT_TO",
      "OUTPUT_MUST_SATISFY",
      "PRODUCES",
      "GENERATES",
      "COMPOSES",
      "EXECUTED_BY",
      "CONSTRAINED_BY",
      "READS",
      "USES",
      "UPDATES",
      "FEEDS_BACK",
      "WAITS_FOR",
      "REQUIRES",
      "CONTROLS",
      "OBSERVES",
      "AVAILABLE_TO",
      "PROVIDED_BY",
      "ACTS_ON",
      "REQUESTS_TASK",
      "ISSUED_BY",
      "IMPLEMENTS_TASK",
      "EXECUTOR",
      "PURSUES",
      "ALLOCATES",
      "TARGETS",
      "CHECKS",
      "CONDITIONED_ON",
      "CONDITIONS_BEHAVIOR",
      "MANAGED_BY",
      "REQUESTED_BY",
      "REQUESTS_ZONE",
      "DECIDES_REQUEST",
      "APPROVED_BY",
      "ALLOCATED_UNDER",
      "ALLOCATES_ZONE",
      "REQUIRES_ALLOCATION",
      "REQUIRES_RECEIPT"
    ]
  },
  {
    "id": "authoring_configuration_references",
    "label_zh": "配置引用",
    "label": "Configuration references",
    "relations": [
      "READ_SCOPE",
      "WRITE_SCOPE",
      "SCOPED_TO",
      "ACCEPTS_FIELD",
      "DECLARES_OUTPUT",
      "DECLARES_COMMAND",
      "APPLIES_WHEN",
      "DECLARES_EVENT",
      "SCENARIO_INPUT",
      "PROPOSES_DECISION",
      "SELECTS_TASK",
      "REPLAYS_EVENT",
      "REPLAYS_COMMAND",
      "SCHEDULES_CHECK",
      "SCHEDULES_EVENT",
      "SCHEDULES_UPDATE",
      "HANDLES",
      "RESPONDS_AS",
      "AFFECTS",
      "PROPOSES_REPLAN",
      "COVERS_TYPE",
      "ARBITRATES",
      "ARBITRATED_BY",
      "SELECTS_LEASE_REQUEST",
      "SELECTS_LEASE_APPROVAL",
      "SELECTS_ALLOCATION",
      "SELECTS_RECEIPT"
    ]
  },
  {
    "id": "definition_instance_structure",
    "label_zh": "定义与实例",
    "label": "Definitions & instances",
    "relations": [
      "INSTANCE_OF",
      "DECLARES",
      "OWNS",
      "DEPENDS_ON",
      "USES_RULE",
      "READS_DEFINITION",
      "DEFINES",
      "REFERENCES",
      "SUBJECT_TYPE",
      "OBJECT_TYPE",
      "USES_FIELD"
    ]
  },
  {
    "id": "ast_operand_structure",
    "label_zh": "AST 操作数",
    "label": "AST operands",
    "relations": [
      "HAS_EXPRESSION",
      "STATE_INPUT",
      "PARAMETER_INPUT",
      "ARGUMENT",
      "TARGET_REFERENCE",
      "BINDS_PARAMETER",
      "CONTROL_INPUT",
      "SCOPES"
    ]
  },
  {
    "id": "evidence_and_runtime_records",
    "label_zh": "证据与运行记录",
    "label": "Evidence & runtime records",
    "relations": [
      "EVIDENCED_BY",
      "BASED_ON",
      "ENTITY_REFERENCE",
      "CITED_FROM",
      "RESULT_OF",
      "RECEIPT_FOR",
      "SENT_BY",
      "RECEIVED_BY"
    ]
  }
];
export const RELATION_VARIANTS = {
  "PRODUCES": [
    "strategy → fixture decision: definition-to-fixture relation; strategy invocation is absent from current authoring collection"
  ],
  "COMPOSES": [
    "command definition → behavior definition: composition; execution-instance composition remains trace semantics"
  ],
  "READS": [
    "module/admission_check → field: declared read signature",
    "predicate → fact: concrete input record"
  ],
  "USES": [
    "admission_check → constraint: applicable constraint",
    "rule → parameter_definition: lexical parameter reference"
  ],
  "UPDATES": [
    "module → field: declared computing authority",
    "module → fact: fixture producer provenance; does not prove execution"
  ],
  "FEEDS_BACK": [
    "event definition → agent: configured routing; event occurrence → agent is separately typed trace semantics"
  ],
  "REQUIRES": [
    "behavior → capability: capability requirement",
    "behavior → resource: amount/unit demand, not proof of allocation or consumption"
  ],
  "INSTANCE_OF": [
    "node → matching node type",
    "entity → entity type",
    "fact → field is value instantiation, not arbitrary class identity",
    "AST expression → expression type"
  ],
  "OWNS": [
    "entity → field: declared state scope",
    "entity → fact: fact subject",
    "entity → resource: resource owner"
  ],
  "ARGUMENT": [
    "child expression → parent operator expression; role is ordered argument index"
  ],
  "EVIDENCED_BY": [
    "event → fact/check: definition trigger/evidence signature",
    "fact → source: source provenance",
    "delivery_receipt → fact: acknowledged-message evidence",
    "lease_approval → delivery_receipt: evidence available before approval"
  ],
  "BASED_ON": [
    "constraint → source: normative/authored-policy basis",
    "predicate_result → fact: actual-input provenance"
  ]
};
const layerByRelation = new Map(RELATION_LAYERS.flatMap(layer => layer.relations.map(relation => [relation, layer.id])));
const copy = value => structuredClone(value);
const metadata = edge => JSON.stringify([edge.condition, edge.time, edge.scope, edge.version, edge.source_id, edge.order]);

export function graphRelationLayers(edges = []) {
  return RELATION_LAYERS.map(layer => ({
    ...copy(layer),
    declared_type_count: layer.relations.length,
    present_relations: layer.relations.filter(relation => edges.some(edge => edge.relation === relation)),
    edge_count: edges.filter(edge => layer.relations.includes(edge.relation)).length,
    variants: Object.fromEntries(layer.relations.filter(relation => RELATION_VARIANTS[relation]).map(relation => [relation, copy(RELATION_VARIANTS[relation])])),
    note: 'A review layer does not erase endpoint, role, scope, time, definition/instance or provenance distinctions.',
  }));
}

/** Only the audited reciprocal strategy-decision declaration pair is bundleable. */
export function graphDisplayEdges(graph, inputEdges = graphEdges(graph)) {
  const groups = new Map();
  inputEdges.forEach((edge, index) => {
    if (edge.relation !== 'PRODUCES' || edge.origin !== 'typed_reference' || !['decision_ids', 'strategy_id'].includes(edge.role)) return;
    const strategy = graph.strategies?.find(node => node?.id === edge.source);
    const decision = graph.decisions?.find(node => node?.id === edge.target);
    if (!strategy?.decision_ids?.includes(decision?.id) || decision?.strategy_id !== strategy.id) return;
    const key = JSON.stringify([edge.source, edge.target, metadata(edge)]);
    groups.set(key, [...(groups.get(key) || []), { edge, index }]);
  });
  const bundles = new Map(), removed = new Set();
  for (const pair of groups.values()) {
    if (pair.length !== 2 || new Set(pair.map(item => item.edge.role)).size !== 2) continue;
    const ordered = pair.sort((a, b) => a.index - b.index);
    bundles.set(ordered[0].index, ordered);
    removed.add(ordered[1].index);
  }
  return inputEdges.flatMap((edge, index) => {
    if (removed.has(index)) return [];
    const origins = bundles.get(index) || [{ edge, index }];
    return [{
      ...copy(edge),
      ...(origins.length === 2 ? { id: `display-${encodeURIComponent(JSON.stringify(origins.map(item => item.edge.id)))}`, role: 'fixture_decision', display_bundle: true } : { display_bundle: false }),
      review_layer: layerByRelation.get(edge.relation) || 'unmapped',
      canonical_edge_ids: origins.map(item => item.edge.id),
      origin_edges: origins.map(item => copy(item.edge)),
      origin_indices: origins.map(item => item.index),
    }];
  });
}

/** Exact reverse is provided for tests/export tools; display bundling is never authoring. */
export function expandGraphDisplayEdges(edges) {
  return edges.flatMap(edge => edge.origin_edges.map((origin, index) => ({ index: edge.origin_indices[index], edge: copy(origin) })))
    .sort((a, b) => a.index - b.index).map(item => item.edge);
}

export function validateRelationLayerInventory() {
  const known = Object.keys(GRAPH_EDGE_CONTRACTS), mapped = RELATION_LAYERS.flatMap(layer => layer.relations);
  return { valid: new Set(mapped).size === mapped.length && known.length === mapped.length && known.every(relation => layerByRelation.has(relation)),
    missing: known.filter(relation => !layerByRelation.has(relation)), unknown: mapped.filter(relation => !GRAPH_EDGE_CONTRACTS[relation]) };
}
