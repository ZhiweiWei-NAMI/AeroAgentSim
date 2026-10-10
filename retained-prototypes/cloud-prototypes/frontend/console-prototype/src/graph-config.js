/** Versioned authoring-only graph contract. No simulator or predicate evaluator. */
export const GRAPH_SCHEMA_VERSION = 'aeroagentsim.unified-graph/v1';
// Kept identical to schemas/unified-graph-v1.schema.json by the contract test.
export const GRAPH_SCHEMA = {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:aeroagentsim:unified-graph:v1",
  "title": "AeroAgentSim unified desired graph v1",
  "description": "Local authoring contract. Static shape/reference validation and deterministic fixture execution do not establish physical, legal, or native semantic truth.",
  "type": "object",
  "properties": {
    "schema_version": {
      "const": "aeroagentsim.unified-graph/v1"
    },
    "provenance": {
      "type": "object",
      "properties": {
        "kind": {
          "const": "authored_fixture"
        },
        "note": {
          "type": "string",
          "minLength": 1
        }
      },
      "required": [
        "kind",
        "note"
      ],
      "additionalProperties": false
    },
    "node_types": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/node_type"
      },
      "maxItems": 2000
    },
    "entity_types": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/entity_type"
      },
      "maxItems": 2000
    },
    "entities": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/entity"
      },
      "maxItems": 2000
    },
    "fields": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/field"
      },
      "maxItems": 2000
    },
    "facts": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/fact"
      },
      "maxItems": 2000
    },
    "modules": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/module"
      },
      "maxItems": 2000
    },
    "agents": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/agent"
      },
      "maxItems": 2000
    },
    "strategies": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/strategy"
      },
      "maxItems": 2000
    },
    "capabilities": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/capability"
      },
      "maxItems": 2000
    },
    "behaviors": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/behavior"
      },
      "maxItems": 2000
    },
    "requests": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/request"
      },
      "maxItems": 2000
    },
    "objectives": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/objective"
      },
      "maxItems": 2000
    },
    "decisions": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/decision"
      },
      "maxItems": 2000
    },
    "tasks": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/task"
      },
      "maxItems": 2000
    },
    "resources": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/resource"
      },
      "maxItems": 2000
    },
    "constraints": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/constraint"
      },
      "maxItems": 2000
    },
    "predicates": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/predicate"
      },
      "maxItems": 2000
    },
    "rules": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/rule"
      },
      "maxItems": 2000
    },
    "events": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/event"
      },
      "maxItems": 2000
    },
    "commands": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/command"
      },
      "maxItems": 2000
    },
    "arbitrations": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/arbitration"
      },
      "maxItems": 2000
    },
    "sources": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/source"
      },
      "maxItems": 2000
    },
    "scenarios": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/scenario"
      },
      "maxItems": 2000
    },
    "admission_checks": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/admission_check"
      },
      "maxItems": 2000
    },
    "feedback_policies": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/feedback_policy"
      },
      "maxItems": 2000
    },
    "predicate_results": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/predicate_result"
      },
      "maxItems": 2000
    },
    "relation_definitions": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/relation_definition"
      },
      "maxItems": 2000
    },
    "parameter_definitions": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/parameter_definition"
      },
      "maxItems": 2000
    },
    "edges": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/edge"
      },
      "maxItems": 10000
    },
    "spatial_zones": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/spatial_zone"
      },
      "maxItems": 2000
    },
    "lease_requests": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/lease_request"
      },
      "maxItems": 2000
    },
    "lease_approvals": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/lease_approval"
      },
      "maxItems": 2000
    },
    "space_time_allocations": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/space_time_allocation"
      },
      "maxItems": 2000
    },
    "delivery_receipts": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/delivery_receipt"
      },
      "maxItems": 2000
    }
  },
  "required": [
    "schema_version",
    "provenance",
    "node_types",
    "entity_types",
    "entities",
    "fields",
    "facts",
    "modules",
    "agents",
    "strategies",
    "capabilities",
    "behaviors",
    "requests",
    "objectives",
    "decisions",
    "tasks",
    "resources",
    "constraints",
    "predicates",
    "rules",
    "events",
    "commands",
    "arbitrations",
    "sources",
    "scenarios",
    "admission_checks",
    "feedback_policies",
    "predicate_results",
    "relation_definitions",
    "parameter_definitions",
    "edges",
    "spatial_zones",
    "lease_requests",
    "lease_approvals",
    "space_time_allocations",
    "delivery_receipts"
  ],
  "additionalProperties": false,
  "$defs": {
    "binding": {
      "type": "object",
      "properties": {
        "mode": {
          "enum": [
            "fixture",
            "stub"
          ]
        },
        "status": {
          "enum": [
            "declared",
            "static_checked",
            "fixture_executed"
          ]
        },
        "adapter": {
          "type": "string",
          "minLength": 1
        }
      },
      "required": [
        "mode",
        "status",
        "adapter"
      ],
      "additionalProperties": false
    },
    "scope": {
      "type": "object",
      "properties": {
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "field_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        }
      },
      "required": [
        "entity_ids",
        "field_ids"
      ],
      "additionalProperties": false
    },
    "assessments": {
      "type": "object",
      "properties": {
        "feasibility": {
          "enum": [
            "TRUE",
            "FALSE",
            "UNKNOWN"
          ]
        },
        "permission": {
          "enum": [
            "TRUE",
            "FALSE",
            "UNKNOWN"
          ]
        },
        "suitability": {
          "enum": [
            "TRUE",
            "FALSE",
            "UNKNOWN"
          ]
        }
      },
      "required": [
        "feasibility",
        "permission",
        "suitability"
      ],
      "additionalProperties": false
    },
    "entity_type": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "field_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "compatible_config_types": {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          }
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "field_ids",
        "compatible_config_types",
        "type_id"
      ],
      "additionalProperties": false
    },
    "entity": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "scene_entity_id": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^[a-z][a-z0-9_.-]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "type_id"
      ],
      "additionalProperties": false
    },
    "field": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "value_type": {
          "enum": [
            "number",
            "integer",
            "string",
            "boolean",
            "vector3",
            "entity_ref"
          ]
        },
        "unit": {
          "type": "string",
          "minLength": 1
        },
        "frame": {
          "type": "string",
          "minLength": 1
        },
        "temporal": {
          "enum": [
            "static",
            "dynamic"
          ]
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "value_type",
        "unit",
        "frame",
        "temporal",
        "type_id"
      ],
      "additionalProperties": false
    },
    "fact": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "entity_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "field_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "value": {},
        "unit": {
          "type": "string",
          "minLength": 1
        },
        "frame": {
          "type": "string",
          "minLength": 1
        },
        "valid_time_ns": {
          "type": "string",
          "pattern": "^(0|[1-9][0-9]*)$"
        },
        "available_time_ns": {
          "type": "string",
          "pattern": "^(0|[1-9][0-9]*)$"
        },
        "producer_module_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "valid_until_ns": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^(0|[1-9][0-9]*)$"
            },
            {
              "type": "null"
            }
          ]
        },
        "record_kind": {
          "const": "fixture_fact"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "entity_id",
        "field_id",
        "value",
        "unit",
        "frame",
        "valid_time_ns",
        "available_time_ns",
        "producer_module_id",
        "source_id",
        "valid_until_ns",
        "record_kind",
        "type_id"
      ],
      "additionalProperties": false
    },
    "module": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "kind": {
          "enum": [
            "dynamics",
            "network",
            "compute",
            "environment",
            "cargo",
            "energy",
            "fixture"
          ]
        },
        "reads": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "entity_ids": {
                "type": "array",
                "items": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9_.-]*$"
                },
                "uniqueItems": true
              },
              "field_ids": {
                "type": "array",
                "items": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9_.-]*$"
                },
                "uniqueItems": true
              }
            },
            "required": [
              "entity_ids",
              "field_ids"
            ],
            "additionalProperties": false
          }
        },
        "writes": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "entity_ids": {
                "type": "array",
                "items": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9_.-]*$"
                },
                "uniqueItems": true
              },
              "field_ids": {
                "type": "array",
                "items": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9_.-]*$"
                },
                "uniqueItems": true
              }
            },
            "required": [
              "entity_ids",
              "field_ids"
            ],
            "additionalProperties": false
          }
        },
        "binding": {
          "$ref": "#/$defs/binding"
        },
        "parameters": {
          "type": "object"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "kind",
        "reads",
        "writes",
        "binding",
        "parameters",
        "type_id"
      ],
      "additionalProperties": false
    },
    "agent": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "controls": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "entity_ids": {
                "type": "array",
                "items": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9_.-]*$"
                },
                "uniqueItems": true
              },
              "mode": {
                "enum": [
                  "exclusive",
                  "shared"
                ]
              },
              "arbitration_id": {
                "anyOf": [
                  {
                    "type": "string",
                    "pattern": "^[a-z][a-z0-9_.-]*$"
                  },
                  {
                    "type": "null"
                  }
                ]
              }
            },
            "required": [
              "entity_ids",
              "mode",
              "arbitration_id"
            ],
            "additionalProperties": false
          },
          "minItems": 1
        },
        "observable_fact_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "strategy_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "controls",
        "observable_fact_ids",
        "strategy_id",
        "type_id"
      ],
      "additionalProperties": false
    },
    "strategy": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "observable_fact_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "request_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "objective_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "constraint_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "input_fields": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "field_id": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_.-]*$"
              },
              "value_type": {
                "enum": [
                  "number",
                  "integer",
                  "string",
                  "boolean",
                  "vector3",
                  "entity_ref"
                ]
              },
              "unit": {
                "type": "string",
                "minLength": 1
              },
              "frame": {
                "type": "string",
                "minLength": 1
              }
            },
            "required": [
              "field_id",
              "value_type",
              "unit",
              "frame"
            ],
            "additionalProperties": false
          }
        },
        "decision_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "output_command_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "binding": {
          "$ref": "#/$defs/binding"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "observable_fact_ids",
        "request_ids",
        "objective_ids",
        "constraint_ids",
        "input_fields",
        "decision_ids",
        "output_command_ids",
        "binding",
        "type_id"
      ],
      "additionalProperties": false
    },
    "request": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "task_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "entity_ids",
        "task_ids",
        "source_id",
        "type_id"
      ],
      "additionalProperties": false
    },
    "objective": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "description": {
          "type": "string",
          "minLength": 1
        },
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "preference": {
          "enum": [
            "soft"
          ]
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "description",
        "entity_ids",
        "type_id",
        "preference"
      ],
      "additionalProperties": false
    },
    "decision": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "strategy_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "agent_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "task_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "command_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "assessments": {
          "$ref": "#/$defs/assessments"
        },
        "rationale": {
          "type": "string",
          "minLength": 1
        },
        "record_kind": {
          "const": "fixture_decision"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "strategy_id",
        "agent_id",
        "task_id",
        "command_ids",
        "assessments",
        "rationale",
        "record_kind",
        "type_id"
      ],
      "additionalProperties": false
    },
    "task": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "executor_agent_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "command_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "objective_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "constraint_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "resource_claims": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "resource_id": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_.-]*$"
              },
              "amount": {
                "type": "number",
                "minimum": 0
              },
              "unit": {
                "type": "string",
                "minLength": 1
              }
            },
            "required": [
              "resource_id",
              "amount",
              "unit"
            ],
            "additionalProperties": false
          }
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "executor_agent_id",
        "entity_ids",
        "command_ids",
        "objective_ids",
        "constraint_ids",
        "resource_claims",
        "type_id"
      ],
      "additionalProperties": false
    },
    "resource": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "kind": {
          "enum": [
            "charging_slot",
            "energy",
            "cargo_capacity",
            "bandwidth",
            "compute",
            "airspace"
          ]
        },
        "owner_entity_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "capacity": {
          "type": "number",
          "exclusiveMinimum": 0
        },
        "unit": {
          "type": "string",
          "minLength": 1
        },
        "constraint_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "kind",
        "owner_entity_id",
        "capacity",
        "unit",
        "constraint_ids",
        "type_id"
      ],
      "additionalProperties": false
    },
    "constraint": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "kind": {
          "enum": [
            "physical",
            "operational",
            "legal"
          ]
        },
        "expression": {
          "type": "string",
          "minLength": 1
        },
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "enforcement": {
          "enum": [
            "hard"
          ]
        },
        "condition": {
          "anyOf": [
            {
              "type": "object",
              "properties": {
                "operator": {
                  "enum": [
                    "boolean_equals",
                    "number_lte",
                    "number_gte"
                  ]
                },
                "expected": {
                  "type": [
                    "boolean",
                    "number"
                  ]
                },
                "expected_unit": {
                  "type": "string",
                  "minLength": 1
                }
              },
              "required": [
                "operator",
                "expected",
                "expected_unit"
              ],
              "additionalProperties": false
            },
            {
              "type": "null"
            }
          ]
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "kind",
        "expression",
        "entity_ids",
        "source_id",
        "type_id",
        "enforcement",
        "condition"
      ],
      "additionalProperties": false
    },
    "predicate": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "input_fact_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "meaning": {
          "type": "string",
          "minLength": 1
        },
        "truth": {
          "enum": [
            "UNKNOWN"
          ]
        },
        "binding": {
          "$ref": "#/$defs/binding"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "rule_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "subpredicate_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "operator": {
          "enum": [
            "leaf",
            "and",
            "or",
            "not"
          ]
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "input_fact_ids",
        "meaning",
        "truth",
        "binding",
        "type_id",
        "rule_ids",
        "subpredicate_ids",
        "operator"
      ],
      "additionalProperties": false
    },
    "rule": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "predicate_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "event_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "command_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "constraint_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "description": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "kind": {
          "enum": [
            "policy_rule",
            "predicate_definition_rule",
            "event_definition_rule"
          ]
        },
        "operator": {
          "enum": [
            "declared_policy",
            "lt",
            "and",
            "or",
            "not",
            "identity"
          ]
        },
        "state_field_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "relation_definition_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "parameter_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "subpredicate_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "output_predicate_id": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^[a-z][a-z0-9_.-]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "definition_ast": {
          "$ref": "#/$defs/expression"
        },
        "applicability_ast": {
          "$ref": "#/$defs/expression"
        },
        "label_zh": {
          "type": "string"
        },
        "output_event_id": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^[a-z][a-z0-9_.-]*$"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "id",
        "label",
        "predicate_ids",
        "event_ids",
        "command_ids",
        "constraint_ids",
        "description",
        "type_id",
        "kind",
        "operator",
        "state_field_ids",
        "relation_definition_ids",
        "parameter_ids",
        "subpredicate_ids",
        "output_predicate_id"
      ],
      "additionalProperties": false
    },
    "event": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "kind": {
          "const": "event"
        },
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "fact_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "description": {
          "type": "string",
          "minLength": 1
        },
        "record_kind": {
          "const": "event_definition"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "trigger": {
          "anyOf": [
            {
              "type": "object",
              "properties": {
                "check_id": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9_.-]*$"
                },
                "outcome": {
                  "enum": [
                    "PERMIT",
                    "BLOCK",
                    "UNKNOWN"
                  ]
                }
              },
              "required": [
                "check_id",
                "outcome"
              ],
              "additionalProperties": false
            },
            {
              "type": "null"
            }
          ]
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "kind",
        "entity_ids",
        "fact_ids",
        "source_id",
        "description",
        "record_kind",
        "type_id",
        "trigger"
      ],
      "additionalProperties": false
    },
    "command": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "kind": {
          "const": "command"
        },
        "agent_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "target_entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "task_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "action": {
          "type": "string",
          "minLength": 1
        },
        "parameters": {
          "type": "object"
        },
        "binding": {
          "$ref": "#/$defs/binding"
        },
        "composition": {
          "type": "object",
          "properties": {
            "mode": {
              "enum": [
                "sequence",
                "parallel",
                "conditional"
              ]
            },
            "behavior_ids": {
              "type": "array",
              "items": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_.-]*$"
              },
              "uniqueItems": true,
              "minItems": 1
            },
            "conditions": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "behavior_id": {
                    "type": "string",
                    "pattern": "^[a-z][a-z0-9_.-]*$"
                  },
                  "predicate_id": {
                    "type": "string",
                    "pattern": "^[a-z][a-z0-9_.-]*$"
                  },
                  "expected": {
                    "enum": [
                      "TRUE",
                      "FALSE"
                    ]
                  }
                },
                "required": [
                  "behavior_id",
                  "predicate_id",
                  "expected"
                ],
                "additionalProperties": false
              }
            }
          },
          "required": [
            "mode",
            "behavior_ids",
            "conditions"
          ],
          "additionalProperties": false
        },
        "record_kind": {
          "const": "command_definition"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "kind",
        "agent_id",
        "target_entity_ids",
        "task_id",
        "action",
        "parameters",
        "binding",
        "composition",
        "record_kind",
        "type_id"
      ],
      "additionalProperties": false
    },
    "arbitration": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "agent_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "policy": {
          "enum": [
            "priority",
            "round_robin",
            "manual"
          ]
        },
        "description": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "agent_ids",
        "entity_ids",
        "policy",
        "description",
        "type_id"
      ],
      "additionalProperties": false
    },
    "source": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "kind": {
          "enum": [
            "authored_fixture",
            "legal_reference"
          ]
        },
        "citation": {
          "type": "string",
          "minLength": 1
        },
        "uri": {
          "type": [
            "string",
            "null"
          ]
        },
        "jurisdiction": {
          "type": [
            "string",
            "null"
          ]
        },
        "effective_date": {
          "type": [
            "string",
            "null"
          ]
        },
        "verification": {
          "enum": [
            "unverified",
            "reference_only"
          ]
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "issuer": {
          "type": [
            "string",
            "null"
          ]
        },
        "applicable_entity_type_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "activities": {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          }
        },
        "effective_from": {
          "type": [
            "string",
            "null"
          ]
        },
        "effective_until": {
          "type": [
            "string",
            "null"
          ]
        },
        "version": {
          "type": [
            "string",
            "null"
          ]
        },
        "clause": {
          "type": [
            "string",
            "null"
          ]
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "kind",
        "citation",
        "uri",
        "jurisdiction",
        "effective_date",
        "verification",
        "type_id",
        "issuer",
        "applicable_entity_type_ids",
        "activities",
        "effective_from",
        "effective_until",
        "version",
        "clause"
      ],
      "additionalProperties": false
    },
    "scenario": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "description": {
          "type": "string",
          "minLength": 1
        },
        "fact_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "decision_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "task_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "event_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "command_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "observed_at_ns": {
          "type": "string",
          "pattern": "^(0|[1-9][0-9]*)$"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "admission_timeline": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "at_ns": {
                "type": "string",
                "pattern": "^(0|[1-9][0-9]*)$"
              },
              "check_ids": {
                "type": "array",
                "items": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9_.-]*$"
                },
                "uniqueItems": true
              },
              "event_ids": {
                "type": "array",
                "items": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9_.-]*$"
                },
                "uniqueItems": true
              },
              "updated_fact_ids": {
                "type": "array",
                "items": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9_.-]*$"
                },
                "uniqueItems": true
              }
            },
            "required": [
              "at_ns",
              "check_ids",
              "event_ids",
              "updated_fact_ids"
            ],
            "additionalProperties": false
          }
        },
        "label_zh": {
          "type": "string"
        },
        "lease_request_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "lease_approval_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "allocation_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "delivery_receipt_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        }
      },
      "required": [
        "id",
        "label",
        "description",
        "fact_ids",
        "decision_ids",
        "task_ids",
        "event_ids",
        "command_ids",
        "observed_at_ns",
        "type_id",
        "admission_timeline"
      ],
      "additionalProperties": false
    },
    "node_type": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "collection": {
          "type": "string",
          "minLength": 1
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "collection"
      ],
      "additionalProperties": false
    },
    "capability": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "module_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "description": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "entity_ids",
        "module_ids",
        "description",
        "type_id"
      ],
      "additionalProperties": false
    },
    "behavior": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "capability_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "module_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "resource_claims": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "resource_id": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_.-]*$"
              },
              "amount": {
                "type": "number",
                "minimum": 0
              },
              "unit": {
                "type": "string",
                "minLength": 1
              }
            },
            "required": [
              "resource_id",
              "amount",
              "unit"
            ],
            "additionalProperties": false
          }
        },
        "constraint_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "description": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "after_behavior_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "label_zh": {
          "type": "string"
        },
        "required_allocation_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "required_receipt_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        }
      },
      "required": [
        "id",
        "label",
        "entity_ids",
        "capability_ids",
        "module_ids",
        "resource_claims",
        "constraint_ids",
        "description",
        "type_id",
        "after_behavior_ids"
      ],
      "additionalProperties": false
    },
    "admission_check": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "entity_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "field_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "constraint_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "behavior_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "binding": {
          "$ref": "#/$defs/binding"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "entity_id",
        "field_id",
        "constraint_id",
        "behavior_ids",
        "binding",
        "type_id"
      ],
      "additionalProperties": false
    },
    "feedback_policy": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "event_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "agent_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "behavior_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "response": {
          "enum": [
            "pause",
            "abort",
            "replan"
          ]
        },
        "decision_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "description": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "event_ids",
        "agent_id",
        "behavior_ids",
        "response",
        "decision_ids",
        "description",
        "type_id"
      ],
      "additionalProperties": false
    },
    "predicate_result": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "predicate_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "input_fact_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "value": {
          "enum": [
            "UNKNOWN"
          ]
        },
        "valid_time_ns": {
          "type": "string",
          "pattern": "^(0|[1-9][0-9]*)$"
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "record_kind": {
          "const": "fixture_predicate_result"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "predicate_id",
        "input_fact_ids",
        "value",
        "valid_time_ns",
        "source_id",
        "record_kind",
        "type_id"
      ],
      "additionalProperties": false
    },
    "edge": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "source": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "target": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "relation": {
          "type": "string",
          "minLength": 1
        },
        "role": {
          "type": "string",
          "minLength": 1
        },
        "condition": {
          "type": [
            "string",
            "null"
          ]
        },
        "time": {
          "type": [
            "string",
            "null"
          ]
        },
        "scope": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "version": {
          "const": 1
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        }
      },
      "required": [
        "id",
        "source",
        "target",
        "relation",
        "role",
        "condition",
        "time",
        "scope",
        "version",
        "source_id"
      ],
      "additionalProperties": false
    },
    "relation_definition": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "subject_type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "object_type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "field_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "description": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "subject_type_id",
        "object_type_id",
        "field_ids",
        "description",
        "type_id"
      ],
      "additionalProperties": false
    },
    "parameter_definition": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "value_type": {
          "enum": [
            "number",
            "boolean",
            "string"
          ]
        },
        "unit": {
          "type": "string",
          "minLength": 1
        },
        "value": {},
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "value_type",
        "unit",
        "value",
        "source_id",
        "type_id"
      ],
      "additionalProperties": false
    },
    "expression": {
      "anyOf": [
        {
          "type": "object",
          "properties": {
            "op": {
              "const": "literal"
            },
            "value": {},
            "unit": {
              "type": "string"
            },
            "value_type": {
              "enum": [
                "number",
                "boolean",
                "string",
                "UNKNOWN"
              ]
            }
          },
          "required": [
            "op",
            "value"
          ],
          "additionalProperties": false
        },
        {
          "type": "object",
          "properties": {
            "op": {
              "const": "unknown"
            },
            "reason": {
              "type": "string"
            }
          },
          "required": [
            "op"
          ],
          "additionalProperties": false
        },
        {
          "type": [
            "number",
            "boolean",
            "string",
            "null"
          ]
        },
        {
          "type": "object",
          "properties": {
            "state": {
              "type": "string",
              "pattern": "^[a-z][a-z0-9_.-]*$"
            }
          },
          "required": [
            "state"
          ],
          "additionalProperties": false
        },
        {
          "type": "object",
          "properties": {
            "param": {
              "type": "string",
              "pattern": "^[a-z][a-z0-9_.-]*$"
            }
          },
          "required": [
            "param"
          ],
          "additionalProperties": false
        },
        {
          "type": "object",
          "properties": {
            "const": {},
            "unit": {
              "type": "string",
              "minLength": 1
            }
          },
          "required": [
            "const"
          ],
          "additionalProperties": false
        },
        {
          "type": "object",
          "properties": {
            "unknown": {
              "type": "string",
              "minLength": 1
            }
          },
          "required": [
            "unknown"
          ],
          "additionalProperties": false
        },
        {
          "type": "object",
          "properties": {
            "predicate": {
              "type": "string",
              "pattern": "^[a-z][a-z0-9_.-]*$"
            },
            "parameters": {
              "type": "object"
            }
          },
          "required": [
            "predicate"
          ],
          "additionalProperties": false
        },
        {
          "type": "object",
          "properties": {
            "event": {
              "type": "string",
              "pattern": "^[a-z][a-z0-9_.-]*$"
            },
            "parameters": {
              "type": "object"
            }
          },
          "required": [
            "event"
          ],
          "additionalProperties": false
        },
        {
          "type": "object",
          "properties": {
            "op": {
              "type": "string",
              "minLength": 1
            },
            "args": {
              "type": "array",
              "items": {
                "$ref": "#/$defs/expression"
              }
            },
            "window_s": {
              "$ref": "#/$defs/expression"
            },
            "duration_seconds": {
              "$ref": "#/$defs/expression"
            },
            "max_gap_s": {
              "$ref": "#/$defs/expression"
            },
            "scope": {
              "type": "array",
              "items": {
                "$ref": "#/$defs/expression"
              }
            },
            "enter_s": {
              "$ref": "#/$defs/expression"
            },
            "clear_s": {
              "$ref": "#/$defs/expression"
            }
          },
          "required": [
            "op",
            "args"
          ],
          "additionalProperties": false
        }
      ]
    },
    "time_window": {
      "type": "object",
      "properties": {
        "start_ns": {
          "type": "string",
          "pattern": "^(0|[1-9][0-9]*)$"
        },
        "end_ns": {
          "type": "string",
          "pattern": "^(0|[1-9][0-9]*)$"
        }
      },
      "required": [
        "start_ns",
        "end_ns"
      ],
      "additionalProperties": false
    },
    "altitude_envelope": {
      "type": "object",
      "properties": {
        "min_m": {
          "type": "number"
        },
        "max_m": {
          "type": "number"
        }
      },
      "required": [
        "min_m",
        "max_m"
      ],
      "additionalProperties": false
    },
    "spatial_zone": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "kind": {
          "enum": [
            "corridor",
            "no_fly_zone"
          ]
        },
        "geometry": {
          "type": "object",
          "properties": {
            "frame": {
              "enum": [
                "ENU",
                "WGS84"
              ]
            },
            "axis_order": {
              "enum": [
                "east_north",
                "longitude_latitude"
              ]
            },
            "unit": {
              "enum": [
                "m",
                "deg"
              ]
            },
            "geometry_type": {
              "enum": [
                "polygon",
                "polyline"
              ]
            },
            "coordinates": {
              "type": "array",
              "items": {
                "type": "array",
                "items": {
                  "type": "number"
                },
                "minItems": 2,
                "maxItems": 2
              },
              "minItems": 2
            }
          },
          "required": [
            "frame",
            "axis_order",
            "unit",
            "geometry_type",
            "coordinates"
          ],
          "additionalProperties": false
        },
        "altitude": {
          "$ref": "#/$defs/altitude_envelope"
        },
        "time_windows": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/time_window"
          },
          "minItems": 1
        },
        "direction": {
          "enum": [
            "bidirectional",
            "forward",
            "reverse"
          ]
        },
        "access": {
          "enum": [
            "approval_required",
            "restricted",
            "prohibited"
          ]
        },
        "authority_agent_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "capacity_slots": {
          "type": "integer",
          "minimum": 0
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "type_id",
        "kind",
        "geometry",
        "altitude",
        "time_windows",
        "direction",
        "access",
        "authority_agent_ids",
        "capacity_slots",
        "source_id"
      ],
      "additionalProperties": false
    },
    "lease_request": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "record_kind": {
          "const": "fixture_lease_request"
        },
        "agent_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "zone_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "requested_window": {
          "$ref": "#/$defs/time_window"
        },
        "requested_altitude": {
          "$ref": "#/$defs/altitude_envelope"
        },
        "direction": {
          "enum": [
            "forward",
            "reverse"
          ]
        },
        "issued_at_ns": {
          "type": "string",
          "pattern": "^(0|[1-9][0-9]*)$"
        },
        "purpose": {
          "type": "string",
          "minLength": 1
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "type_id",
        "record_kind",
        "agent_id",
        "entity_ids",
        "zone_id",
        "requested_window",
        "requested_altitude",
        "direction",
        "issued_at_ns",
        "purpose",
        "source_id"
      ],
      "additionalProperties": false
    },
    "lease_approval": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "record_kind": {
          "const": "fixture_lease_approval"
        },
        "request_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "authority_agent_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "decision": {
          "enum": [
            "approved",
            "rejected",
            "pending"
          ]
        },
        "approved_window": {
          "anyOf": [
            {
              "$ref": "#/$defs/time_window"
            },
            {
              "type": "null"
            }
          ]
        },
        "approved_altitude": {
          "anyOf": [
            {
              "$ref": "#/$defs/altitude_envelope"
            },
            {
              "type": "null"
            }
          ]
        },
        "constraint_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "evidence_receipt_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "issued_at_ns": {
          "type": "string",
          "pattern": "^(0|[1-9][0-9]*)$"
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "type_id",
        "record_kind",
        "request_id",
        "authority_agent_id",
        "decision",
        "approved_window",
        "approved_altitude",
        "constraint_ids",
        "evidence_receipt_ids",
        "issued_at_ns",
        "source_id"
      ],
      "additionalProperties": false
    },
    "space_time_allocation": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "record_kind": {
          "const": "fixture_space_time_allocation"
        },
        "approval_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "zone_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "entity_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "window": {
          "$ref": "#/$defs/time_window"
        },
        "altitude": {
          "$ref": "#/$defs/altitude_envelope"
        },
        "direction": {
          "enum": [
            "forward",
            "reverse"
          ]
        },
        "slots": {
          "type": "integer",
          "minimum": 1
        },
        "status": {
          "enum": [
            "declared",
            "cancelled"
          ]
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        }
      },
      "required": [
        "id",
        "label",
        "type_id",
        "record_kind",
        "approval_id",
        "zone_id",
        "entity_ids",
        "window",
        "altitude",
        "direction",
        "slots",
        "status",
        "source_id"
      ],
      "additionalProperties": false
    },
    "delivery_receipt": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label": {
          "type": "string",
          "minLength": 1
        },
        "type_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "record_kind": {
          "const": "fixture_delivery_receipt"
        },
        "command_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "task_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "sender_entity_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "receiver_entity_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "status": {
          "enum": [
            "acknowledged",
            "rejected",
            "UNKNOWN"
          ]
        },
        "emitted_at_ns": {
          "type": "string",
          "pattern": "^(0|[1-9][0-9]*)$"
        },
        "received_at_ns": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^(0|[1-9][0-9]*)$"
            },
            {
              "type": "null"
            }
          ]
        },
        "evidence_fact_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_.-]*$"
          },
          "uniqueItems": true
        },
        "source_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_.-]*$"
        },
        "label_zh": {
          "type": "string"
        },
        "receipt_kind": {
          "enum": [
            "message_delivery",
            "command_acceptance",
            "parcel_custody"
          ]
        }
      },
      "required": [
        "id",
        "label",
        "type_id",
        "record_kind",
        "command_id",
        "task_id",
        "sender_entity_id",
        "receiver_entity_id",
        "status",
        "emitted_at_ns",
        "received_at_ns",
        "evidence_fact_ids",
        "source_id",
        "receipt_kind"
      ],
      "additionalProperties": false
    }
  }
};
export const GRAPH_COLLECTIONS = [
  {
    "key": "node_types",
    "label": "Node types",
    "label_zh": "节点类型"
  },
  {
    "key": "entity_types",
    "label": "Entity types",
    "label_zh": "实体类型"
  },
  {
    "key": "entities",
    "label": "Entity instances",
    "label_zh": "实体实例"
  },
  {
    "key": "fields",
    "label": "Field definitions",
    "label_zh": "字段定义"
  },
  {
    "key": "facts",
    "label": "Timed facts",
    "label_zh": "时态事实"
  },
  {
    "key": "modules",
    "label": "Modules",
    "label_zh": "模块"
  },
  {
    "key": "agents",
    "label": "Agents",
    "label_zh": "智能体"
  },
  {
    "key": "strategies",
    "label": "Strategies",
    "label_zh": "策略"
  },
  {
    "key": "capabilities",
    "label": "Capabilities",
    "label_zh": "能力"
  },
  {
    "key": "behaviors",
    "label": "Behaviors",
    "label_zh": "行为"
  },
  {
    "key": "requests",
    "label": "Requests",
    "label_zh": "请求"
  },
  {
    "key": "objectives",
    "label": "Objectives",
    "label_zh": "目标"
  },
  {
    "key": "decisions",
    "label": "Decisions",
    "label_zh": "决策"
  },
  {
    "key": "tasks",
    "label": "Tasks",
    "label_zh": "任务"
  },
  {
    "key": "resources",
    "label": "Resources",
    "label_zh": "资源"
  },
  {
    "key": "constraints",
    "label": "Constraints",
    "label_zh": "约束"
  },
  {
    "key": "predicates",
    "label": "Predicates",
    "label_zh": "谓词"
  },
  {
    "key": "rules",
    "label": "Rules",
    "label_zh": "规则"
  },
  {
    "key": "events",
    "label": "Events",
    "label_zh": "事件"
  },
  {
    "key": "commands",
    "label": "Commands",
    "label_zh": "命令"
  },
  {
    "key": "arbitrations",
    "label": "Arbitration",
    "label_zh": "仲裁"
  },
  {
    "key": "sources",
    "label": "Sources",
    "label_zh": "来源"
  },
  {
    "key": "scenarios",
    "label": "Fixture cases",
    "label_zh": "样例案例"
  },
  {
    "key": "admission_checks",
    "label": "Admission checks",
    "label_zh": "准入检查"
  },
  {
    "key": "feedback_policies",
    "label": "Feedback policies",
    "label_zh": "反馈策略"
  },
  {
    "key": "predicate_results",
    "label": "Predicate results",
    "label_zh": "谓词结果"
  },
  {
    "key": "relation_definitions",
    "label": "Relation definitions",
    "label_zh": "关系定义"
  },
  {
    "key": "parameter_definitions",
    "label": "Parameter definitions",
    "label_zh": "参数定义"
  },
  {
    "key": "spatial_zones",
    "label": "Spatial zones",
    "label_zh": "空间区域"
  },
  {
    "key": "lease_requests",
    "label": "Lease requests",
    "label_zh": "时空申请"
  },
  {
    "key": "lease_approvals",
    "label": "Lease approvals",
    "label_zh": "时空批准"
  },
  {
    "key": "space_time_allocations",
    "label": "Space-time allocations",
    "label_zh": "时空分配"
  },
  {
    "key": "delivery_receipts",
    "label": "Delivery receipts",
    "label_zh": "交付回执"
  }
];
export const DEFAULT_GRAPH = {
  "schema_version": "aeroagentsim.unified-graph/v1",
  "provenance": {
    "kind": "authored_fixture",
    "note": "Independently authored graph. All bindings are fixture/stub. No native Atlas truth, remote simulator, verified law, or actuator execution."
  },
  "entity_types": [
    {
      "id": "aircraft",
      "label": "Aircraft",
      "field_ids": [
        "position",
        "battery",
        "link-quality"
      ],
      "compatible_config_types": [
        "uav"
      ],
      "type_id": "entity-type-type"
    },
    {
      "id": "ground-vehicle",
      "label": "Ground vehicle",
      "field_ids": [
        "position"
      ],
      "compatible_config_types": [
        "vehicle"
      ],
      "type_id": "entity-type-type"
    },
    {
      "id": "charging-station",
      "label": "Charging station",
      "field_ids": [
        "position",
        "pad-available"
      ],
      "compatible_config_types": [
        "base_station"
      ],
      "type_id": "entity-type-type"
    },
    {
      "id": "parcel",
      "label": "Parcel",
      "field_ids": [
        "custody"
      ],
      "compatible_config_types": [],
      "type_id": "entity-type-type"
    },
    {
      "id": "compute-node",
      "label": "Compute node",
      "field_ids": [
        "compute-load"
      ],
      "compatible_config_types": [
        "edge",
        "cloud"
      ],
      "type_id": "entity-type-type"
    }
  ],
  "entities": [
    {
      "id": "uav-alpha",
      "label": "UAV Alpha",
      "type_id": "aircraft",
      "scene_entity_id": "uav-alpha"
    },
    {
      "id": "uav-beta",
      "label": "UAV Beta",
      "type_id": "aircraft",
      "scene_entity_id": "uav-beta"
    },
    {
      "id": "vehicle-1",
      "label": "Ground courier 1",
      "type_id": "ground-vehicle",
      "scene_entity_id": "vehicle-1"
    },
    {
      "id": "station-west",
      "label": "West charging station",
      "type_id": "charging-station",
      "scene_entity_id": "station-west"
    },
    {
      "id": "parcel-one",
      "label": "Parcel 001",
      "type_id": "parcel",
      "scene_entity_id": null
    },
    {
      "id": "edge-west",
      "label": "West edge computer",
      "type_id": "compute-node",
      "scene_entity_id": "edge-west"
    }
  ],
  "fields": [
    {
      "id": "position",
      "label": "Position",
      "value_type": "vector3",
      "unit": "m",
      "frame": "ENU",
      "temporal": "dynamic",
      "type_id": "field-type"
    },
    {
      "id": "battery",
      "label": "Battery state of charge",
      "value_type": "number",
      "unit": "%",
      "frame": "none",
      "temporal": "dynamic",
      "type_id": "field-type"
    },
    {
      "id": "link-quality",
      "label": "Observed link quality",
      "value_type": "number",
      "unit": "ratio",
      "frame": "none",
      "temporal": "dynamic",
      "type_id": "field-type"
    },
    {
      "id": "custody",
      "label": "Cargo custody",
      "value_type": "entity_ref",
      "unit": "1",
      "frame": "none",
      "temporal": "dynamic",
      "type_id": "field-type"
    },
    {
      "id": "compute-load",
      "label": "Compute load",
      "value_type": "number",
      "unit": "ratio",
      "frame": "none",
      "temporal": "dynamic",
      "type_id": "field-type"
    },
    {
      "id": "pad-available",
      "label": "Charging pad availability",
      "type_id": "field-type",
      "value_type": "boolean",
      "unit": "1",
      "frame": "none",
      "temporal": "dynamic"
    }
  ],
  "facts": [
    {
      "id": "alpha-position",
      "label": "Alpha position",
      "entity_id": "uav-alpha",
      "field_id": "position",
      "value": [
        -80,
        -45,
        35
      ],
      "unit": "m",
      "frame": "ENU",
      "valid_time_ns": "0",
      "available_time_ns": "0",
      "producer_module_id": "mobility-module",
      "source_id": "fixture-source",
      "record_kind": "fixture_fact",
      "valid_until_ns": "60000000000",
      "type_id": "fact-type"
    },
    {
      "id": "alpha-battery",
      "label": "Alpha battery: 18%",
      "entity_id": "uav-alpha",
      "field_id": "battery",
      "value": 18,
      "unit": "%",
      "frame": "none",
      "valid_time_ns": "0",
      "available_time_ns": "0",
      "producer_module_id": "energy-module",
      "source_id": "fixture-source",
      "record_kind": "fixture_fact",
      "valid_until_ns": "60000000000",
      "type_id": "fact-type"
    },
    {
      "id": "alpha-link",
      "label": "Alpha degraded link",
      "entity_id": "uav-alpha",
      "field_id": "link-quality",
      "value": 0.2,
      "unit": "ratio",
      "frame": "none",
      "valid_time_ns": "1000000000",
      "available_time_ns": "1200000000",
      "producer_module_id": "network-module",
      "source_id": "fixture-source",
      "record_kind": "fixture_fact",
      "valid_until_ns": "60000000000",
      "type_id": "fact-type"
    },
    {
      "id": "beta-position",
      "label": "Beta position",
      "entity_id": "uav-beta",
      "field_id": "position",
      "value": [
        65,
        50,
        45
      ],
      "unit": "m",
      "frame": "ENU",
      "valid_time_ns": "0",
      "available_time_ns": "0",
      "producer_module_id": "mobility-module",
      "source_id": "fixture-source",
      "record_kind": "fixture_fact",
      "valid_until_ns": "60000000000",
      "type_id": "fact-type"
    },
    {
      "id": "parcel-custody",
      "label": "Parcel custody",
      "entity_id": "parcel-one",
      "field_id": "custody",
      "value": "vehicle-1",
      "unit": "1",
      "frame": "none",
      "valid_time_ns": "0",
      "available_time_ns": "0",
      "producer_module_id": "cargo-module",
      "source_id": "fixture-source",
      "record_kind": "fixture_fact",
      "valid_until_ns": "60000000000",
      "type_id": "fact-type"
    },
    {
      "id": "edge-load",
      "label": "Edge load",
      "entity_id": "edge-west",
      "field_id": "compute-load",
      "value": 0.4,
      "unit": "ratio",
      "frame": "none",
      "valid_time_ns": "0",
      "available_time_ns": "0",
      "producer_module_id": "compute-module",
      "source_id": "fixture-source",
      "record_kind": "fixture_fact",
      "valid_until_ns": "60000000000",
      "type_id": "fact-type"
    },
    {
      "id": "pad-open",
      "label": "Pad available",
      "type_id": "fact-type",
      "entity_id": "station-west",
      "field_id": "pad-available",
      "value": true,
      "unit": "1",
      "frame": "none",
      "valid_time_ns": "0",
      "available_time_ns": "0",
      "valid_until_ns": "1999999999",
      "producer_module_id": "pad-module",
      "source_id": "fixture-source",
      "record_kind": "fixture_fact"
    },
    {
      "id": "pad-closed",
      "label": "Pad unavailable",
      "type_id": "fact-type",
      "entity_id": "station-west",
      "field_id": "pad-available",
      "value": false,
      "unit": "1",
      "frame": "none",
      "valid_time_ns": "2000000000",
      "available_time_ns": "2000000000",
      "valid_until_ns": "60000000000",
      "producer_module_id": "pad-module",
      "source_id": "fixture-source",
      "record_kind": "fixture_fact"
    }
  ],
  "modules": [
    {
      "id": "mobility-module",
      "label": "Motion fixture",
      "kind": "dynamics",
      "reads": [],
      "writes": [
        {
          "entity_ids": [
            "uav-alpha",
            "uav-beta",
            "vehicle-1",
            "station-west"
          ],
          "field_ids": [
            "position"
          ]
        }
      ],
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "parameters": {
        "frame": "ENU"
      },
      "type_id": "module-type"
    },
    {
      "id": "energy-module",
      "label": "Energy fixture",
      "kind": "energy",
      "reads": [
        {
          "entity_ids": [
            "uav-alpha",
            "uav-beta"
          ],
          "field_ids": [
            "position"
          ]
        }
      ],
      "writes": [
        {
          "entity_ids": [
            "uav-alpha",
            "uav-beta"
          ],
          "field_ids": [
            "battery"
          ]
        }
      ],
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "parameters": {},
      "type_id": "module-type"
    },
    {
      "id": "network-module",
      "label": "Link fixture",
      "kind": "network",
      "reads": [
        {
          "entity_ids": [
            "uav-alpha",
            "uav-beta"
          ],
          "field_ids": [
            "position"
          ]
        }
      ],
      "writes": [
        {
          "entity_ids": [
            "uav-alpha",
            "uav-beta"
          ],
          "field_ids": [
            "link-quality"
          ]
        }
      ],
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "parameters": {},
      "type_id": "module-type"
    },
    {
      "id": "cargo-module",
      "label": "Cargo fixture",
      "kind": "cargo",
      "reads": [],
      "writes": [
        {
          "entity_ids": [
            "parcel-one"
          ],
          "field_ids": [
            "custody"
          ]
        }
      ],
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "parameters": {},
      "type_id": "module-type"
    },
    {
      "id": "compute-module",
      "label": "Compute fixture",
      "kind": "compute",
      "reads": [],
      "writes": [
        {
          "entity_ids": [
            "edge-west"
          ],
          "field_ids": [
            "compute-load"
          ]
        }
      ],
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "parameters": {},
      "type_id": "module-type"
    },
    {
      "id": "pad-module",
      "label": "Pad availability fixture",
      "type_id": "module-type",
      "kind": "environment",
      "reads": [],
      "writes": [
        {
          "entity_ids": [
            "station-west"
          ],
          "field_ids": [
            "pad-available"
          ]
        }
      ],
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "parameters": {}
    }
  ],
  "agents": [
    {
      "id": "alpha-autonomy",
      "label": "Alpha autonomy",
      "controls": [
        {
          "entity_ids": [
            "uav-alpha"
          ],
          "mode": "exclusive",
          "arbitration_id": null
        }
      ],
      "observable_fact_ids": [
        "alpha-position",
        "alpha-battery",
        "alpha-link"
      ],
      "strategy_id": "alpha-strategy",
      "type_id": "agent-type"
    },
    {
      "id": "fleet-dispatch",
      "label": "Fleet dispatch",
      "controls": [
        {
          "entity_ids": [
            "uav-beta",
            "vehicle-1"
          ],
          "mode": "exclusive",
          "arbitration_id": null
        }
      ],
      "observable_fact_ids": [
        "beta-position",
        "parcel-custody",
        "edge-load"
      ],
      "strategy_id": "logistics-strategy",
      "type_id": "agent-type"
    }
  ],
  "strategies": [
    {
      "id": "alpha-strategy",
      "label": "Energy / link response",
      "observable_fact_ids": [
        "alpha-position",
        "alpha-battery",
        "alpha-link"
      ],
      "request_ids": [
        "charge-request",
        "return-request"
      ],
      "objective_ids": [
        "safe-operation"
      ],
      "constraint_ids": [
        "reserve-policy",
        "airspace-policy"
      ],
      "input_fields": [
        {
          "value_type": "vector3",
          "unit": "m",
          "frame": "ENU",
          "field_id": "position"
        },
        {
          "value_type": "number",
          "unit": "%",
          "frame": "none",
          "field_id": "battery"
        },
        {
          "value_type": "number",
          "unit": "ratio",
          "frame": "none",
          "field_id": "link-quality"
        }
      ],
      "decision_ids": [
        "choose-charge",
        "choose-return"
      ],
      "output_command_ids": [
        "charge-alpha",
        "return-alpha"
      ],
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "type_id": "strategy-type"
    },
    {
      "id": "logistics-strategy",
      "label": "Multi-entity dispatch",
      "observable_fact_ids": [
        "beta-position",
        "parcel-custody",
        "edge-load"
      ],
      "request_ids": [
        "delivery-request"
      ],
      "objective_ids": [
        "delivery-objective"
      ],
      "constraint_ids": [
        "cargo-policy",
        "airspace-policy"
      ],
      "input_fields": [
        {
          "value_type": "vector3",
          "unit": "m",
          "frame": "ENU",
          "field_id": "position"
        },
        {
          "value_type": "entity_ref",
          "unit": "1",
          "frame": "none",
          "field_id": "custody"
        },
        {
          "value_type": "number",
          "unit": "ratio",
          "frame": "none",
          "field_id": "compute-load"
        }
      ],
      "decision_ids": [
        "choose-delivery"
      ],
      "output_command_ids": [
        "deliver-parcel"
      ],
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "type_id": "strategy-type"
    }
  ],
  "requests": [
    {
      "id": "charge-request",
      "label": "Recharge request",
      "entity_ids": [
        "uav-alpha"
      ],
      "task_ids": [
        "charge-task"
      ],
      "source_id": "fixture-source",
      "type_id": "request-type"
    },
    {
      "id": "return-request",
      "label": "Return request",
      "entity_ids": [
        "uav-alpha"
      ],
      "task_ids": [
        "return-task"
      ],
      "source_id": "fixture-source",
      "type_id": "request-type"
    },
    {
      "id": "delivery-request",
      "label": "Delivery request",
      "entity_ids": [
        "parcel-one",
        "uav-beta",
        "vehicle-1"
      ],
      "task_ids": [
        "delivery-task"
      ],
      "source_id": "fixture-source",
      "type_id": "request-type"
    }
  ],
  "objectives": [
    {
      "id": "safe-operation",
      "label": "Preserve reserve / return safely",
      "description": "Authoring objective; feasibility, permission and suitability remain independent UNKNOWN dimensions.",
      "entity_ids": [
        "uav-alpha"
      ],
      "type_id": "objective-type",
      "preference": "soft"
    },
    {
      "id": "delivery-objective",
      "label": "Complete parcel transfer",
      "description": "Explicit parcel custody, vehicle scope and capacity constraint.",
      "entity_ids": [
        "parcel-one",
        "uav-beta",
        "vehicle-1"
      ],
      "type_id": "objective-type",
      "preference": "soft"
    }
  ],
  "decisions": [
    {
      "id": "choose-charge",
      "label": "Authored charging decision",
      "strategy_id": "alpha-strategy",
      "agent_id": "alpha-autonomy",
      "task_id": "charge-task",
      "command_ids": [
        "charge-alpha"
      ],
      "assessments": {
        "feasibility": "UNKNOWN",
        "permission": "UNKNOWN",
        "suitability": "UNKNOWN"
      },
      "rationale": "Fixture-selected response to authored battery observation, not evaluated policy truth.",
      "record_kind": "fixture_decision",
      "type_id": "decision-type"
    },
    {
      "id": "choose-delivery",
      "label": "Authored dispatch decision",
      "strategy_id": "logistics-strategy",
      "agent_id": "fleet-dispatch",
      "task_id": "delivery-task",
      "command_ids": [
        "deliver-parcel"
      ],
      "assessments": {
        "feasibility": "UNKNOWN",
        "permission": "UNKNOWN",
        "suitability": "UNKNOWN"
      },
      "rationale": "Fixture-selected dispatch with explicit parcel and two controlled entities.",
      "record_kind": "fixture_decision",
      "type_id": "decision-type"
    },
    {
      "id": "choose-return",
      "label": "Authored return decision",
      "strategy_id": "alpha-strategy",
      "agent_id": "alpha-autonomy",
      "task_id": "return-task",
      "command_ids": [
        "return-alpha"
      ],
      "assessments": {
        "feasibility": "UNKNOWN",
        "permission": "UNKNOWN",
        "suitability": "UNKNOWN"
      },
      "rationale": "Fixture-selected response to authored degraded link; native predicate remains UNKNOWN.",
      "record_kind": "fixture_decision",
      "type_id": "decision-type"
    }
  ],
  "tasks": [
    {
      "id": "charge-task",
      "label": "Charge Alpha",
      "executor_agent_id": "alpha-autonomy",
      "entity_ids": [
        "uav-alpha",
        "station-west"
      ],
      "command_ids": [
        "charge-alpha"
      ],
      "objective_ids": [
        "safe-operation"
      ],
      "constraint_ids": [
        "reserve-policy",
        "charger-policy",
        "airspace-policy",
        "pad-availability"
      ],
      "resource_claims": [
        {
          "resource_id": "west-charge-slot",
          "amount": 1,
          "unit": "slot"
        }
      ],
      "type_id": "task-type"
    },
    {
      "id": "delivery-task",
      "label": "Transfer parcel",
      "executor_agent_id": "fleet-dispatch",
      "entity_ids": [
        "uav-beta",
        "vehicle-1",
        "parcel-one"
      ],
      "command_ids": [
        "deliver-parcel"
      ],
      "objective_ids": [
        "delivery-objective"
      ],
      "constraint_ids": [
        "cargo-policy",
        "airspace-policy"
      ],
      "resource_claims": [
        {
          "resource_id": "cargo-capacity",
          "amount": 2,
          "unit": "kg"
        },
        {
          "resource_id": "edge-compute",
          "amount": 1,
          "unit": "core"
        }
      ],
      "type_id": "task-type"
    },
    {
      "id": "return-task",
      "label": "Return Alpha",
      "executor_agent_id": "alpha-autonomy",
      "entity_ids": [
        "uav-alpha"
      ],
      "command_ids": [
        "return-alpha"
      ],
      "objective_ids": [
        "safe-operation"
      ],
      "constraint_ids": [
        "reserve-policy",
        "airspace-policy"
      ],
      "resource_claims": [
        {
          "resource_id": "return-corridor",
          "amount": 1,
          "unit": "slot"
        }
      ],
      "type_id": "task-type"
    }
  ],
  "resources": [
    {
      "id": "west-charge-slot",
      "label": "West charging slot",
      "kind": "charging_slot",
      "owner_entity_id": "station-west",
      "capacity": 1,
      "unit": "slot",
      "constraint_ids": [
        "charger-policy"
      ],
      "type_id": "resource-type"
    },
    {
      "id": "cargo-capacity",
      "label": "Courier cargo capacity",
      "kind": "cargo_capacity",
      "owner_entity_id": "vehicle-1",
      "capacity": 5,
      "unit": "kg",
      "constraint_ids": [
        "cargo-policy"
      ],
      "type_id": "resource-type"
    },
    {
      "id": "edge-compute",
      "label": "Edge cores",
      "kind": "compute",
      "owner_entity_id": "edge-west",
      "capacity": 4,
      "unit": "core",
      "constraint_ids": [
        "compute-policy"
      ],
      "type_id": "resource-type"
    },
    {
      "id": "return-corridor",
      "label": "Return corridor reservation",
      "kind": "airspace",
      "owner_entity_id": "station-west",
      "capacity": 1,
      "unit": "slot",
      "constraint_ids": [
        "airspace-policy"
      ],
      "type_id": "resource-type"
    }
  ],
  "constraints": [
    {
      "id": "reserve-policy",
      "label": "Battery reserve",
      "kind": "operational",
      "expression": "reserve_soc_percent >= 15 (declared only)",
      "entity_ids": [
        "uav-alpha"
      ],
      "source_id": "fixture-source",
      "type_id": "constraint-type",
      "enforcement": "hard",
      "condition": null
    },
    {
      "id": "charger-policy",
      "label": "Exclusive charging slot",
      "kind": "physical",
      "expression": "simultaneous claimed slots <= capacity",
      "entity_ids": [
        "station-west"
      ],
      "source_id": "fixture-source",
      "type_id": "constraint-type",
      "enforcement": "hard",
      "condition": null
    },
    {
      "id": "cargo-policy",
      "label": "Cargo mass capacity",
      "kind": "physical",
      "expression": "claimed cargo mass <= declared capacity",
      "entity_ids": [
        "vehicle-1"
      ],
      "source_id": "fixture-source",
      "type_id": "constraint-type",
      "enforcement": "hard",
      "condition": null
    },
    {
      "id": "compute-policy",
      "label": "Compute capacity",
      "kind": "physical",
      "expression": "claimed cores <= declared capacity",
      "entity_ids": [
        "edge-west"
      ],
      "source_id": "fixture-source",
      "type_id": "constraint-type",
      "enforcement": "hard",
      "condition": null
    },
    {
      "id": "airspace-policy",
      "label": "Unverified airspace permission",
      "kind": "legal",
      "expression": "Authorization source must be supplied and independently verified before real operation.",
      "entity_ids": [
        "uav-alpha",
        "uav-beta"
      ],
      "source_id": "legal-source",
      "type_id": "constraint-type",
      "enforcement": "hard",
      "condition": null
    },
    {
      "id": "pad-availability",
      "label": "Pad must remain available",
      "type_id": "constraint-type",
      "kind": "operational",
      "expression": "Fixture-only boolean equality: pad available must equal true.",
      "entity_ids": [
        "station-west"
      ],
      "source_id": "fixture-source",
      "enforcement": "hard",
      "condition": {
        "operator": "boolean_equals",
        "expected": true,
        "expected_unit": "1"
      }
    }
  ],
  "predicates": [
    {
      "id": "low-battery",
      "label": "Low battery condition",
      "input_fact_ids": [
        "alpha-battery"
      ],
      "meaning": "Native predicate contract placeholder; no scalar evaluator.",
      "truth": "UNKNOWN",
      "binding": {
        "mode": "stub",
        "status": "declared",
        "adapter": "atlas-not-connected"
      },
      "type_id": "predicate-type",
      "rule_ids": [
        "low-battery-definition"
      ],
      "subpredicate_ids": [],
      "operator": "leaf"
    },
    {
      "id": "link-degraded",
      "label": "Link degraded condition",
      "input_fact_ids": [
        "alpha-link"
      ],
      "meaning": "Native predicate contract placeholder; fixture event is authored separately.",
      "truth": "UNKNOWN",
      "binding": {
        "mode": "stub",
        "status": "declared",
        "adapter": "atlas-not-connected"
      },
      "type_id": "predicate-type",
      "rule_ids": [],
      "subpredicate_ids": [],
      "operator": "leaf"
    },
    {
      "id": "return-needed",
      "label": "Composite return-needed definition",
      "type_id": "predicate-type",
      "input_fact_ids": [
        "alpha-battery",
        "alpha-link"
      ],
      "meaning": "Declared OR composition only; Atlas not connected.",
      "truth": "UNKNOWN",
      "binding": {
        "mode": "stub",
        "status": "declared",
        "adapter": "atlas-not-connected"
      },
      "rule_ids": [
        "return-needed-definition"
      ],
      "subpredicate_ids": [
        "low-battery",
        "link-degraded"
      ],
      "operator": "or"
    },
    {
      "id": "energy-link-risk",
      "label": "Multi-state energy / link definition",
      "type_id": "predicate-type",
      "input_fact_ids": [
        "alpha-battery",
        "alpha-link"
      ],
      "meaning": "Multiple typed state inputs to one predicate; no evaluator connected.",
      "truth": "UNKNOWN",
      "binding": {
        "mode": "stub",
        "status": "declared",
        "adapter": "atlas-not-connected"
      },
      "rule_ids": [
        "energy-link-definition"
      ],
      "subpredicate_ids": [],
      "operator": "leaf"
    }
  ],
  "rules": [
    {
      "id": "charge-rule",
      "label": "Charging rule",
      "predicate_ids": [
        "low-battery"
      ],
      "event_ids": [
        "charge-requested"
      ],
      "command_ids": [
        "charge-alpha"
      ],
      "constraint_ids": [
        "reserve-policy",
        "airspace-policy"
      ],
      "description": "Declared relation only. Fixture event does not evaluate or authorize this command.",
      "type_id": "rule-type",
      "kind": "policy_rule",
      "operator": "declared_policy",
      "state_field_ids": [],
      "relation_definition_ids": [],
      "parameter_ids": [],
      "subpredicate_ids": [],
      "output_predicate_id": null,
      "definition_ast": null,
      "applicability_ast": null
    },
    {
      "id": "return-rule",
      "label": "Communication return rule",
      "predicate_ids": [
        "link-degraded"
      ],
      "event_ids": [
        "link-degraded-event"
      ],
      "command_ids": [
        "return-alpha"
      ],
      "constraint_ids": [
        "airspace-policy"
      ],
      "description": "Separate observation event and requested actuator command.",
      "type_id": "rule-type",
      "kind": "policy_rule",
      "operator": "declared_policy",
      "state_field_ids": [],
      "relation_definition_ids": [],
      "parameter_ids": [],
      "subpredicate_ids": [],
      "output_predicate_id": null,
      "definition_ast": null,
      "applicability_ast": null
    },
    {
      "id": "low-battery-definition",
      "label": "Low battery definition rule",
      "type_id": "rule-type",
      "predicate_ids": [],
      "event_ids": [],
      "command_ids": [],
      "constraint_ids": [],
      "description": "Definition-level battery < reserve-threshold binding. Not evaluated by this prototype.",
      "kind": "predicate_definition_rule",
      "operator": "lt",
      "state_field_ids": [
        "battery"
      ],
      "relation_definition_ids": [],
      "parameter_ids": [
        "reserve-threshold"
      ],
      "subpredicate_ids": [],
      "output_predicate_id": "low-battery",
      "definition_ast": {
        "op": "lt",
        "args": [
          {
            "state": "battery"
          },
          {
            "param": "reserve-threshold"
          }
        ]
      },
      "applicability_ast": null
    },
    {
      "id": "return-needed-definition",
      "label": "Composite return definition rule",
      "type_id": "rule-type",
      "predicate_ids": [],
      "event_ids": [],
      "command_ids": [],
      "constraint_ids": [],
      "description": "Definition-level OR composition; no native truth claim.",
      "kind": "predicate_definition_rule",
      "operator": "or",
      "state_field_ids": [],
      "relation_definition_ids": [],
      "parameter_ids": [],
      "subpredicate_ids": [
        "low-battery",
        "link-degraded"
      ],
      "output_predicate_id": "return-needed",
      "definition_ast": {
        "op": "or",
        "args": [
          {
            "predicate": "low-battery"
          },
          {
            "predicate": "link-degraded"
          }
        ]
      },
      "applicability_ast": null
    },
    {
      "id": "energy-link-definition",
      "label": "Multi-state expression definition",
      "type_id": "rule-type",
      "predicate_ids": [],
      "event_ids": [],
      "command_ids": [],
      "constraint_ids": [],
      "description": "Typed, ordered AST authoring contract only.",
      "kind": "predicate_definition_rule",
      "operator": "and",
      "state_field_ids": [
        "battery",
        "link-quality"
      ],
      "relation_definition_ids": [],
      "parameter_ids": [
        "reserve-threshold"
      ],
      "subpredicate_ids": [],
      "output_predicate_id": "energy-link-risk",
      "definition_ast": {
        "op": "and",
        "args": [
          {
            "op": "lt",
            "args": [
              {
                "state": "battery"
              },
              {
                "param": "reserve-threshold"
              }
            ]
          },
          {
            "op": "lt",
            "args": [
              {
                "state": "link-quality"
              },
              {
                "const": 0.3,
                "unit": "ratio"
              }
            ]
          }
        ]
      },
      "applicability_ast": null
    }
  ],
  "events": [
    {
      "id": "charge-requested",
      "label": "Charging requested",
      "kind": "event",
      "entity_ids": [
        "uav-alpha"
      ],
      "fact_ids": [
        "alpha-battery"
      ],
      "source_id": "fixture-source",
      "description": "Authored fixture observation; no actuator effect.",
      "record_kind": "event_definition",
      "type_id": "event-type",
      "trigger": null
    },
    {
      "id": "parcel-transfer-requested",
      "label": "Parcel transfer requested",
      "kind": "event",
      "entity_ids": [
        "parcel-one",
        "vehicle-1"
      ],
      "fact_ids": [
        "parcel-custody"
      ],
      "source_id": "fixture-source",
      "description": "Authored cargo request event; not proof of delivery.",
      "record_kind": "event_definition",
      "type_id": "event-type",
      "trigger": null
    },
    {
      "id": "link-degraded-event",
      "label": "Communication degraded",
      "kind": "event",
      "entity_ids": [
        "uav-alpha"
      ],
      "fact_ids": [
        "alpha-link"
      ],
      "source_id": "fixture-source",
      "description": "Authored observation; does not establish native link predicate truth.",
      "record_kind": "event_definition",
      "type_id": "event-type",
      "trigger": null
    },
    {
      "id": "pad-unavailable-event",
      "label": "Charging pad unavailable",
      "type_id": "event-type",
      "kind": "event",
      "record_kind": "event_definition",
      "entity_ids": [
        "station-west",
        "uav-alpha"
      ],
      "fact_ids": [
        "pad-closed"
      ],
      "source_id": "fixture-source",
      "description": "Fixture admission BLOCK observation; invokes only a declared feedback policy.",
      "trigger": {
        "check_id": "pad-admission",
        "outcome": "BLOCK"
      }
    }
  ],
  "commands": [
    {
      "id": "charge-alpha",
      "label": "Request charging",
      "kind": "command",
      "agent_id": "alpha-autonomy",
      "target_entity_ids": [
        "uav-alpha"
      ],
      "task_id": "charge-task",
      "action": "request_charge",
      "parameters": {
        "station_entity_id": "station-west"
      },
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "composition": {
        "mode": "sequence",
        "behavior_ids": [
          "recharge"
        ],
        "conditions": []
      },
      "record_kind": "command_definition",
      "type_id": "command-type"
    },
    {
      "id": "deliver-parcel",
      "label": "Request parcel transfer",
      "kind": "command",
      "agent_id": "fleet-dispatch",
      "target_entity_ids": [
        "uav-beta",
        "vehicle-1"
      ],
      "task_id": "delivery-task",
      "action": "request_cargo_transfer",
      "parameters": {
        "parcel_entity_id": "parcel-one",
        "destination_entity_id": "uav-beta"
      },
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "composition": {
        "mode": "sequence",
        "behavior_ids": [
          "transfer-cargo"
        ],
        "conditions": []
      },
      "record_kind": "command_definition",
      "type_id": "command-type"
    },
    {
      "id": "return-alpha",
      "label": "Request return",
      "kind": "command",
      "agent_id": "alpha-autonomy",
      "target_entity_ids": [
        "uav-alpha"
      ],
      "task_id": "return-task",
      "action": "request_return",
      "parameters": {
        "destination_enu_m": [
          -100,
          30,
          0
        ]
      },
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "local-authored-fixture"
      },
      "composition": {
        "mode": "sequence",
        "behavior_ids": [
          "navigate-home",
          "fly-home",
          "land-home"
        ],
        "conditions": []
      },
      "record_kind": "command_definition",
      "type_id": "command-type"
    }
  ],
  "arbitrations": [],
  "sources": [
    {
      "id": "fixture-source",
      "label": "Independent authored fixture",
      "kind": "authored_fixture",
      "citation": "AeroAgentSim local editor fixture; no measured telemetry or external source catalog.",
      "uri": null,
      "jurisdiction": null,
      "effective_date": null,
      "verification": "unverified",
      "type_id": "source-type",
      "issuer": null,
      "applicable_entity_type_ids": [],
      "activities": [],
      "effective_from": null,
      "effective_until": null,
      "version": null,
      "clause": null
    },
    {
      "id": "legal-source",
      "label": "Legal source placeholder",
      "kind": "legal_reference",
      "citation": "No verified legal authority has been connected. Replace with applicable primary authority before real operation.",
      "uri": null,
      "jurisdiction": "Unspecified",
      "effective_date": null,
      "verification": "unverified",
      "type_id": "source-type",
      "issuer": null,
      "applicable_entity_type_ids": [],
      "activities": [],
      "effective_from": null,
      "effective_until": null,
      "version": null,
      "clause": null
    }
  ],
  "scenarios": [
    {
      "id": "charging",
      "label": "Charging",
      "description": "Resolve battery → strategy → authored decision → charging task / slot → event and command.",
      "fact_ids": [
        "alpha-position",
        "alpha-battery"
      ],
      "decision_ids": [
        "choose-charge"
      ],
      "task_ids": [
        "charge-task"
      ],
      "event_ids": [
        "charge-requested"
      ],
      "command_ids": [
        "charge-alpha"
      ],
      "observed_at_ns": "2000000000",
      "type_id": "scenario-type",
      "admission_timeline": []
    },
    {
      "id": "logistics",
      "label": "Logistics",
      "description": "Resolve fleet agent with two controlled entities, independent parcel, custody fact and declared capacity.",
      "fact_ids": [
        "beta-position",
        "parcel-custody",
        "edge-load"
      ],
      "decision_ids": [
        "choose-delivery"
      ],
      "task_ids": [
        "delivery-task"
      ],
      "event_ids": [
        "parcel-transfer-requested"
      ],
      "command_ids": [
        "deliver-parcel"
      ],
      "observed_at_ns": "2000000000",
      "type_id": "scenario-type",
      "admission_timeline": []
    },
    {
      "id": "comm-return",
      "label": "Communication degradation → return",
      "description": "Keep authored degraded-link event separate from a return command; native semantic truth remains UNKNOWN.",
      "fact_ids": [
        "alpha-position",
        "alpha-battery",
        "alpha-link"
      ],
      "decision_ids": [
        "choose-return"
      ],
      "task_ids": [
        "return-task"
      ],
      "event_ids": [
        "link-degraded-event"
      ],
      "command_ids": [
        "return-alpha"
      ],
      "observed_at_ns": "2000000000",
      "type_id": "scenario-type",
      "admission_timeline": []
    },
    {
      "id": "constraint-change",
      "label": "Charging pad changes during execution",
      "type_id": "scenario-type",
      "description": "Bounded fixture adapter checks available pad facts at start and continue; declared feedback pauses on BLOCK.",
      "fact_ids": [
        "alpha-position",
        "alpha-battery",
        "pad-open",
        "pad-closed"
      ],
      "decision_ids": [
        "choose-charge"
      ],
      "task_ids": [
        "charge-task"
      ],
      "event_ids": [
        "charge-requested",
        "pad-unavailable-event"
      ],
      "command_ids": [
        "charge-alpha"
      ],
      "observed_at_ns": "3000000000",
      "admission_timeline": [
        {
          "at_ns": "1000000000",
          "check_ids": [
            "pad-admission"
          ],
          "event_ids": [],
          "updated_fact_ids": []
        },
        {
          "at_ns": "2000000000",
          "check_ids": [
            "pad-admission"
          ],
          "event_ids": [
            "pad-unavailable-event"
          ],
          "updated_fact_ids": [
            "pad-closed"
          ]
        }
      ]
    }
  ],
  "capabilities": [
    {
      "id": "flight-capability",
      "label": "Flight capability",
      "entity_ids": [
        "uav-alpha",
        "uav-beta"
      ],
      "module_ids": [
        "mobility-module"
      ],
      "description": "Declared movement capability; no dynamics engine connected.",
      "type_id": "capability-type"
    },
    {
      "id": "charge-capability",
      "label": "Charging capability",
      "entity_ids": [
        "uav-alpha"
      ],
      "module_ids": [
        "energy-module"
      ],
      "description": "Declared recharge capability; no real charger connected.",
      "type_id": "capability-type"
    },
    {
      "id": "cargo-capability",
      "label": "Cargo transfer capability",
      "entity_ids": [
        "uav-beta",
        "vehicle-1"
      ],
      "module_ids": [
        "cargo-module"
      ],
      "description": "Declared multi-entity cargo transfer; no custody effect executed.",
      "type_id": "capability-type"
    }
  ],
  "behaviors": [
    {
      "id": "navigate-home",
      "label": "Navigate home",
      "entity_ids": [
        "uav-alpha"
      ],
      "capability_ids": [
        "flight-capability"
      ],
      "module_ids": [
        "mobility-module"
      ],
      "resource_claims": [],
      "constraint_ids": [
        "airspace-policy"
      ],
      "description": "First authored return step.",
      "type_id": "behavior-type",
      "after_behavior_ids": []
    },
    {
      "id": "fly-home",
      "label": "Fly home",
      "entity_ids": [
        "uav-alpha"
      ],
      "capability_ids": [
        "flight-capability"
      ],
      "module_ids": [
        "mobility-module"
      ],
      "resource_claims": [
        {
          "resource_id": "return-corridor",
          "amount": 1,
          "unit": "slot"
        }
      ],
      "constraint_ids": [
        "reserve-policy",
        "airspace-policy"
      ],
      "description": "Second authored return step, no movement effect.",
      "type_id": "behavior-type",
      "after_behavior_ids": [
        "navigate-home"
      ]
    },
    {
      "id": "land-home",
      "label": "Land",
      "entity_ids": [
        "uav-alpha"
      ],
      "capability_ids": [
        "flight-capability"
      ],
      "module_ids": [
        "mobility-module"
      ],
      "resource_claims": [],
      "constraint_ids": [
        "airspace-policy"
      ],
      "description": "Third authored return step.",
      "type_id": "behavior-type",
      "after_behavior_ids": [
        "fly-home"
      ]
    },
    {
      "id": "recharge",
      "label": "Recharge",
      "entity_ids": [
        "uav-alpha"
      ],
      "capability_ids": [
        "charge-capability"
      ],
      "module_ids": [
        "energy-module"
      ],
      "resource_claims": [
        {
          "resource_id": "west-charge-slot",
          "amount": 1,
          "unit": "slot"
        }
      ],
      "constraint_ids": [
        "charger-policy",
        "reserve-policy",
        "pad-availability"
      ],
      "description": "Declared charging behavior.",
      "type_id": "behavior-type",
      "after_behavior_ids": []
    },
    {
      "id": "transfer-cargo",
      "label": "Transfer cargo custody",
      "entity_ids": [
        "uav-beta",
        "vehicle-1"
      ],
      "capability_ids": [
        "cargo-capability"
      ],
      "module_ids": [
        "cargo-module"
      ],
      "resource_claims": [
        {
          "resource_id": "cargo-capacity",
          "amount": 2,
          "unit": "kg"
        }
      ],
      "constraint_ids": [
        "cargo-policy"
      ],
      "description": "Declared custody transfer with independent parcel state.",
      "type_id": "behavior-type",
      "after_behavior_ids": []
    }
  ],
  "node_types": [
    {
      "id": "entity-type-type",
      "label": "Entity types definition",
      "collection": "entity_types"
    },
    {
      "id": "field-type",
      "label": "Field definitions definition",
      "collection": "fields"
    },
    {
      "id": "fact-type",
      "label": "Timed facts definition",
      "collection": "facts"
    },
    {
      "id": "module-type",
      "label": "Modules definition",
      "collection": "modules"
    },
    {
      "id": "agent-type",
      "label": "Agents definition",
      "collection": "agents"
    },
    {
      "id": "strategy-type",
      "label": "Strategies definition",
      "collection": "strategies"
    },
    {
      "id": "capability-type",
      "label": "Capabilities definition",
      "collection": "capabilities"
    },
    {
      "id": "behavior-type",
      "label": "Behaviors definition",
      "collection": "behaviors"
    },
    {
      "id": "request-type",
      "label": "Requests definition",
      "collection": "requests"
    },
    {
      "id": "objective-type",
      "label": "Objectives definition",
      "collection": "objectives"
    },
    {
      "id": "decision-type",
      "label": "Decisions definition",
      "collection": "decisions"
    },
    {
      "id": "task-type",
      "label": "Tasks definition",
      "collection": "tasks"
    },
    {
      "id": "resource-type",
      "label": "Resources definition",
      "collection": "resources"
    },
    {
      "id": "constraint-type",
      "label": "Constraints definition",
      "collection": "constraints"
    },
    {
      "id": "predicate-type",
      "label": "Predicates definition",
      "collection": "predicates"
    },
    {
      "id": "rule-type",
      "label": "Rules definition",
      "collection": "rules"
    },
    {
      "id": "event-type",
      "label": "Events definition",
      "collection": "events"
    },
    {
      "id": "command-type",
      "label": "Commands definition",
      "collection": "commands"
    },
    {
      "id": "arbitration-type",
      "label": "Arbitration definition",
      "collection": "arbitrations"
    },
    {
      "id": "source-type",
      "label": "Sources definition",
      "collection": "sources"
    },
    {
      "id": "scenario-type",
      "label": "Fixture cases definition",
      "collection": "scenarios"
    },
    {
      "id": "admission-check-type",
      "label": "Admission checks definition",
      "collection": "admission_checks"
    },
    {
      "id": "feedback-policy-type",
      "label": "Feedback policies definition",
      "collection": "feedback_policies"
    },
    {
      "id": "predicate-result-type",
      "label": "Predicate results definition",
      "collection": "predicate_results"
    },
    {
      "id": "relation-definition-type",
      "label": "Relation definitions definition",
      "collection": "relation_definitions"
    },
    {
      "id": "parameter-definition-type",
      "label": "Parameter definitions definition",
      "collection": "parameter_definitions"
    },
    {
      "id": "spatial-zone-type",
      "label": "Spatial zones definition",
      "collection": "spatial_zones"
    },
    {
      "id": "lease-request-type",
      "label": "Lease requests definition",
      "collection": "lease_requests"
    },
    {
      "id": "lease-approval-type",
      "label": "Lease approvals definition",
      "collection": "lease_approvals"
    },
    {
      "id": "space-time-allocation-type",
      "label": "Space-time allocations definition",
      "collection": "space_time_allocations"
    },
    {
      "id": "delivery-receipt-type",
      "label": "Delivery receipts definition",
      "collection": "delivery_receipts"
    }
  ],
  "admission_checks": [
    {
      "id": "pad-admission",
      "label": "Charging pad admission",
      "type_id": "admission-check-type",
      "entity_id": "station-west",
      "field_id": "pad-available",
      "constraint_id": "pad-availability",
      "behavior_ids": [
        "recharge"
      ],
      "binding": {
        "mode": "fixture",
        "status": "declared",
        "adapter": "bounded-fixture-admission-v1"
      }
    }
  ],
  "feedback_policies": [
    {
      "id": "pause-on-pad-loss",
      "label": "Pause charging on pad loss",
      "type_id": "feedback-policy-type",
      "event_ids": [
        "pad-unavailable-event"
      ],
      "agent_id": "alpha-autonomy",
      "behavior_ids": [
        "recharge"
      ],
      "response": "pause",
      "decision_ids": [],
      "description": "Configured pause only. No automatic remote command or undeclared replan."
    }
  ],
  "predicate_results": [
    {
      "id": "low-battery-unknown",
      "label": "Unexecuted low battery result",
      "type_id": "predicate-result-type",
      "predicate_id": "low-battery",
      "input_fact_ids": [
        "alpha-battery"
      ],
      "value": "UNKNOWN",
      "valid_time_ns": "0",
      "source_id": "fixture-source",
      "record_kind": "fixture_predicate_result"
    }
  ],
  "edges": [
    {
      "id": "alpha-battery-observation",
      "source": "alpha-battery",
      "target": "alpha-strategy",
      "relation": "INPUT_TO",
      "role": "observable_fact",
      "condition": null,
      "time": null,
      "scope": [
        "uav-alpha"
      ],
      "version": 1,
      "source_id": "fixture-source"
    },
    {
      "id": "alpha-battery-safety",
      "source": "alpha-battery",
      "target": "alpha-strategy",
      "relation": "INPUT_TO",
      "role": "safety_observation",
      "condition": "fixture-only reserve planning",
      "time": null,
      "scope": [
        "uav-alpha"
      ],
      "version": 1,
      "source_id": "fixture-source"
    }
  ],
  "relation_definitions": [
    {
      "id": "carried-by",
      "label": "Cargo carried-by relation",
      "type_id": "relation-definition-type",
      "subject_type_id": "parcel",
      "object_type_id": "ground-vehicle",
      "field_ids": [
        "custody"
      ],
      "description": "Declared entity-reference relation only; no inferred custody fact."
    }
  ],
  "parameter_definitions": [
    {
      "id": "reserve-threshold",
      "label": "Reserve threshold definition",
      "type_id": "parameter-definition-type",
      "value_type": "number",
      "unit": "%",
      "value": 20,
      "source_id": "fixture-source"
    }
  ],
  "spatial_zones": [],
  "lease_requests": [],
  "lease_approvals": [],
  "space_time_allocations": [],
  "delivery_receipts": []
};

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const list = value => Array.isArray(value) ? value : [];
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const derivedId = (...parts) => `derived-${encodeURIComponent(JSON.stringify(parts))}`;
const ns = value => typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value);
const copy = value => JSON.parse(JSON.stringify(value));
const UNITS = new Set(['1', 'ratio', '%', 'm', 'm/s', 'm/s²', 'm/s^2', 's', 'ms', 'ns', 'kg', 'W', 'kW', 'Wh', 'kWh', 'slot', 'core', 'MB', 'Mbps', 'dBm', 'Hz', 'GHz', 'deg']);
const FRAMES = new Set(['none', 'ENU', 'WGS84', 'body']);

/** Execute the JSON Schema subset used by the checked-in v1 contract in browser and Node. */
export function validateGraphSchema(value, schema = GRAPH_SCHEMA) {
  const errors = [];
  let visited = 0, exhausted = false;
  const add = (path, message) => errors.push({ code: 'E_SCHEMA', path, message, severity: 'error', node_id: null });
  const walk = (input, rule, path, ancestors = new Set(), depth = 0) => {
    if (++visited > 100000) { exhausted = true; return; }
    if (depth > 64) { add(path, 'Graph exceeds 64 levels of nesting.'); return; }
    if (rule.$ref) rule = schema.$defs[rule.$ref.split('/').pop()];
    if (!rule) { add(path, 'Unresolved contract schema reference.'); return; }
    if (rule.anyOf) {
      const ok = rule.anyOf.some(candidate => {
        const before = errors.length;
        walk(input, candidate, path, new Set(ancestors), depth + 1);
        const passed = errors.length === before;
        errors.splice(before);
        return passed;
      });
      if (!ok) add(path, 'Value does not match an allowed contract shape.');
      return;
    }
    if (rule.const !== undefined && input !== rule.const) add(path, `Expected ${JSON.stringify(rule.const)}.`);
    if (rule.enum && !rule.enum.includes(input)) add(path, `Expected one of ${rule.enum.join(', ')}.`);
    const matches = type => type === 'object' ? object(input) : type === 'array' ? Array.isArray(input) : type === 'null' ? input === null : type === 'integer' ? Number.isSafeInteger(input) : type === 'number' ? Number.isFinite(input) : typeof input === type;
    if (rule.type && !list(Array.isArray(rule.type) ? rule.type : [rule.type]).some(matches)) { add(path, `Expected ${list(Array.isArray(rule.type) ? rule.type : [rule.type]).join(' or ')}.`); return; }
    if (typeof input === 'number' && !Number.isFinite(input)) add(path, 'Numbers must be finite.');
    if (input === undefined || typeof input === 'bigint' || typeof input === 'function' || typeof input === 'symbol') { add(path, 'Only JSON values are supported.'); return; }
    if (typeof input === 'string') {
      if (rule.minLength !== undefined && input.length < rule.minLength) add(path, 'String must be nonempty.');
      if (rule.pattern && !new RegExp(rule.pattern).test(input)) add(path, `String does not match ${rule.pattern}.`);
    }
    if (typeof input === 'number') {
      if (rule.minimum !== undefined && input < rule.minimum) add(path, `Value must be at least ${rule.minimum}.`);
      if (rule.exclusiveMinimum !== undefined && input <= rule.exclusiveMinimum) add(path, `Value must exceed ${rule.exclusiveMinimum}.`);
    }
    if (object(input) || Array.isArray(input)) {
      if (ancestors.has(input)) { add(path, 'Graph must not contain circular references.'); return; }
      const next = new Set(ancestors); next.add(input);
      if (Array.isArray(input)) {
        if (rule.minItems !== undefined && input.length < rule.minItems) add(path, `Expected at least ${rule.minItems} item(s).`);
        if (rule.maxItems !== undefined && input.length > rule.maxItems) add(path, `Expected no more than ${rule.maxItems} items.`);
        if (rule.uniqueItems && new Set(input.filter(x => x === null || typeof x !== 'object').map(x => JSON.stringify(x))).size !== input.length) add(path, 'Array items must be unique.');
        input.forEach((item, i) => walk(item, rule.items || {}, `${path}[${i}]`, next, depth + 1));
      } else {
        for (const key of rule.required || []) if (!own(input, key)) add(path ? `${path}.${key}` : key, 'Required field is missing.');
        for (const [key, item] of Object.entries(input)) {
          const child = path ? `${path}.${key}` : key;
          if (rule.additionalProperties === false && !own(rule.properties || {}, key)) add(child, 'Field is not part of unified graph v1.');
          walk(item, rule.properties?.[key] || {}, child, next, depth + 1);
        }
      }
    }
  };
  walk(value, schema, '');
  if (exhausted) add('', 'Graph exceeds the 100,000-value validation budget.');
  return { valid: errors.length === 0, errors };
}

/** Nodes retain the original authored record; UI selection is not a second store. */
function authoredGraphNodes(graph) {
  return GRAPH_COLLECTIONS.flatMap(({ key }) => list(graph?.[key]).filter(object).map(node => ({ id: node.id, label: node.label || node.id, collection: key, node })));
}

export const GRAPH_DERIVED_COLLECTIONS = Object.freeze([
  { key: 'expression_types', label: 'Expression types', label_zh: '表达式类型', read_only: true },
  { key: 'expressions', label: 'Definition expressions', label_zh: '定义表达式', read_only: true },
]);

export function graphNodes(graph) {
  const authored = authoredGraphNodes(graph);
  const expressions = list(graph?.rules).filter(object).flatMap(rule => inspectDefinitionAST(graph, rule.id).nodes.map(expression => ({ id: expression.id, label: expression.label, collection: 'expressions', derived: true, read_only: true, owner_rule_id: rule.id, node: { ...expression, owner_rule_id: rule.id, rule_id: rule.id, type_id: `expression-${expression.kind}-type` } })));
  const kinds = ['state', 'param', 'const', 'predicate', 'event', 'unknown', 'op'];
  const types = kinds.map(kind => ({ id: `expression-${kind}-type`, label: `${kind} expression`, collection: 'expression_types', derived: true, read_only: true, node: { id: `expression-${kind}-type`, label: `${kind} expression`, kind, collection: 'expressions', type_id: 'derived-expression-type-definition' } }));
  return [...authored, ...types, ...expressions];
}

const REFERENCE_FIELDS = {
  zone_id: 'spatial_zones', request_id: 'lease_requests', approval_id: 'lease_approvals', authority_agent_id: 'agents', authority_agent_ids: 'agents', sender_entity_id: 'entities', receiver_entity_id: 'entities', command_id: 'commands', evidence_fact_ids: 'facts', evidence_receipt_ids: 'delivery_receipts', required_receipt_ids: 'delivery_receipts', required_allocation_ids: 'space_time_allocations', lease_request_ids: 'lease_requests', lease_approval_ids: 'lease_approvals', allocation_ids: 'space_time_allocations', delivery_receipt_ids: 'delivery_receipts',
  entity_id: 'entities', entity_ids: 'entities', target_entity_ids: 'entities', owner_entity_id: 'entities',
  field_id: 'fields', field_ids: 'fields', producer_module_id: 'modules', module_ids: 'modules',
  source_id: 'sources', strategy_id: 'strategies', observable_fact_ids: 'facts', input_fact_ids: 'facts', fact_ids: 'facts',
  request_ids: 'requests', objective_ids: 'objectives', constraint_ids: 'constraints', decision_ids: 'decisions',
  output_command_ids: 'commands', command_ids: 'commands', agent_id: 'agents', agent_ids: 'agents',
  executor_agent_id: 'agents', task_id: 'tasks', task_ids: 'tasks', resource_id: 'resources', predicate_ids: 'predicates',
  constraint_id: 'constraints', check_id: 'admission_checks', check_ids: 'admission_checks', updated_fact_ids: 'facts', behavior_id: 'behaviors', after_behavior_ids: 'behaviors', predicate_id: 'predicates', rule_ids: 'rules', state_field_ids: 'fields', relation_definition_ids: 'relation_definitions', parameter_ids: 'parameter_definitions', subpredicate_ids: 'predicates', output_predicate_id: 'predicates', output_event_id: 'events', subject_type_id: 'entity_types', object_type_id: 'entity_types', applicable_entity_type_ids: 'entity_types',
  event_ids: 'events', arbitration_id: 'arbitrations', behavior_ids: 'behaviors', capability_ids: 'capabilities',
};

function references(graph) {
  const out = [];
  for (const { id, collection, node } of authoredGraphNodes(graph)) {
    const index = graph[collection].indexOf(node);
    const walk = (value, path, relation, depth = 0) => {
      if (depth > 20 || !object(value)) return;
      for (const [key, target] of Object.entries(value)) {
        if (key === 'parameters' || key === 'value') continue;
        const to = key === 'type_id' ? collection === 'entities' ? 'entity_types' : 'node_types' : REFERENCE_FIELDS[key];
        const full = `${path}.${key}`;
        const rel = relation ? `${relation}.${key}` : key;
        if (to) {
          const targets = Array.isArray(target) ? target : target === null ? [] : [target];
          targets.forEach((value, i) => out.push({ source: id, target: value, collection: to, path: Array.isArray(target) ? `${full}[${i}]` : full, relation: rel }));
        } else if (Array.isArray(target)) target.forEach((child, i) => walk(child, `${full}[${i}]`, rel, depth + 1));
        else if (object(target)) walk(target, full, rel, depth + 1);
      }
    };
    walk(node, `${collection}[${index}]`, '');
    if (collection === 'facts' && node.value !== null && list(graph?.fields).find(field => field?.id === node.field_id)?.value_type === 'entity_ref') {
      out.push({ source: id, target: node.value, collection: 'entities', path: `${collection}[${index}].value`, relation: 'value.entity_ref' });
    }
  }
  return out;
}

export const GRAPH_EDGE_CONTRACTS = {
  INSTANCE_OF: { from: '*', to: ['node_types', 'entity_types', 'fields', 'expression_types'] },
  DECLARES: { from: ['entity_types'], to: ['fields'] },
  OWNS: { from: ['entities'], to: ['facts', 'fields', 'resources'] },
  CALLS: { from: ['agents'], to: ['strategies'] },
  INPUT_TO: { from: ['facts', 'objectives', 'constraints', 'requests'], to: ['strategies'], roles: ['observable_fact', 'safety_observation', 'task_goal', 'applicable_constraint', 'request'] },
  OUTPUT_MUST_SATISFY: { from: ['strategies'], to: ['constraints'] },
  PRODUCES: { from: ['strategies'], to: ['decisions'] },
  GENERATES: { from: ['decisions'], to: ['commands'] },
  COMPOSES: { from: ['commands'], to: ['behaviors'] },
  EXECUTED_BY: { from: ['behaviors'], to: ['modules'] },
  CONSTRAINED_BY: { from: ['behaviors', 'tasks', 'resources', 'rules', 'lease_approvals'], to: ['constraints'] },
  READS: { from: ['admission_checks', 'predicates', 'modules'], to: ['facts', 'fields'] },
  USES: { from: ['admission_checks', 'rules'], to: ['constraints', 'parameter_definitions'] },
  UPDATES: { from: ['modules'], to: ['facts', 'fields'] },
  EVIDENCED_BY: { from: ['events', 'facts', 'delivery_receipts', 'lease_approvals'], to: ['facts', 'admission_checks', 'sources', 'delivery_receipts'] },
  FEEDS_BACK: { from: ['events'], to: ['agents'] },
  BASED_ON: { from: ['constraints', 'predicate_results'], to: ['sources', 'facts'] },
  WAITS_FOR: { from: ['behaviors'], to: ['behaviors'] },
  DEPENDS_ON: { from: ['predicates', 'events'], to: ['fields', 'relation_definitions', 'predicates', 'events'] },
  USES_RULE: { from: ['predicates'], to: ['rules'] },
  READS_DEFINITION: { from: ['rules'], to: ['fields', 'relation_definitions'] },
  DEFINES: { from: ['rules', 'expressions'], to: ['predicates', 'events', 'rules'] },
  HAS_EXPRESSION: { from: ['rules'], to: ['expressions'] },
  STATE_INPUT: { from: ['fields'], to: ['expressions'] },
  PARAMETER_INPUT: { from: ['parameter_definitions'], to: ['expressions'] },
  ARGUMENT: { from: ['expressions'], to: ['expressions'] },
  TARGET_REFERENCE: { from: ['predicates', 'events'], to: ['expressions'] },
  BINDS_PARAMETER: { from: ['expressions'], to: ['expressions'] },
  CONTROL_INPUT: { from: ['expressions'], to: ['expressions'] },
  SCOPES: { from: ['expressions'], to: ['predicates', 'events', 'rules'] },
  REQUIRES: { from: ['behaviors'], to: ['capabilities', 'resources'] },
  ENTITY_REFERENCE: { from: ['facts'], to: ['entities'] },
  READ_SCOPE: { from: ['modules'], to: ['entities'] },
  WRITE_SCOPE: { from: ['modules'], to: ['entities'] },
  CONTROLS: { from: ['agents'], to: ['entities'] },
  OBSERVES: { from: ['agents'], to: ['facts'] },
  ACCEPTS_FIELD: { from: ['strategies'], to: ['fields'] },
  DECLARES_OUTPUT: { from: ['strategies'], to: ['commands'] },
  AVAILABLE_TO: { from: ['capabilities'], to: ['entities'] },
  PROVIDED_BY: { from: ['capabilities'], to: ['modules'] },
  ACTS_ON: { from: ['behaviors'], to: ['entities'] },
  SCOPED_TO: { from: ['requests', 'objectives', 'tasks', 'constraints', 'events', 'admission_checks', 'arbitrations', 'lease_requests', 'space_time_allocations'], to: ['entities'] },
  REQUESTS_TASK: { from: ['requests'], to: ['tasks'] },
  CITED_FROM: { from: ['requests', 'events', 'predicate_results', 'parameter_definitions', 'spatial_zones', 'lease_requests', 'lease_approvals', 'space_time_allocations', 'delivery_receipts'], to: ['sources'] },
  ISSUED_BY: { from: ['decisions', 'commands'], to: ['agents'] },
  IMPLEMENTS_TASK: { from: ['decisions', 'commands'], to: ['tasks'] },
  EXECUTOR: { from: ['tasks'], to: ['agents'] },
  DECLARES_COMMAND: { from: ['tasks', 'rules'], to: ['commands'] },
  PURSUES: { from: ['tasks'], to: ['objectives'] },
  ALLOCATES: { from: ['tasks'], to: ['resources'] },
  APPLIES_WHEN: { from: ['rules'], to: ['predicates'] },
  DECLARES_EVENT: { from: ['rules'], to: ['events'] },
  REFERENCES: { from: ['rules'], to: ['predicates'] },
  TARGETS: { from: ['commands'], to: ['entities'] },
  SCENARIO_INPUT: { from: ['scenarios'], to: ['facts'] },
  PROPOSES_DECISION: { from: ['scenarios'], to: ['decisions'] },
  SELECTS_TASK: { from: ['scenarios'], to: ['tasks'] },
  REPLAYS_EVENT: { from: ['scenarios'], to: ['events'] },
  REPLAYS_COMMAND: { from: ['scenarios'], to: ['commands'] },
  SCHEDULES_CHECK: { from: ['scenarios'], to: ['admission_checks'] },
  SCHEDULES_EVENT: { from: ['scenarios'], to: ['events'] },
  SCHEDULES_UPDATE: { from: ['scenarios'], to: ['facts'] },
  CHECKS: { from: ['admission_checks'], to: ['behaviors'] },
  HANDLES: { from: ['feedback_policies'], to: ['events'] },
  RESPONDS_AS: { from: ['feedback_policies'], to: ['agents'] },
  AFFECTS: { from: ['feedback_policies'], to: ['behaviors'] },
  PROPOSES_REPLAN: { from: ['feedback_policies'], to: ['decisions'] },
  RESULT_OF: { from: ['predicate_results'], to: ['predicates'] },
  SUBJECT_TYPE: { from: ['relation_definitions'], to: ['entity_types'] },
  OBJECT_TYPE: { from: ['relation_definitions'], to: ['entity_types'] },
  USES_FIELD: { from: ['relation_definitions'], to: ['fields'] },
  COVERS_TYPE: { from: ['sources'], to: ['entity_types'] },
  ARBITRATES: { from: ['arbitrations'], to: ['agents'] },
  ARBITRATED_BY: { from: ['agents'], to: ['arbitrations'] },
  CONDITIONED_ON: { from: ['commands'], to: ['predicates'] },
  CONDITIONS_BEHAVIOR: { from: ['commands'], to: ['behaviors'] },
  MANAGED_BY: { from: ['spatial_zones'], to: ['agents'] },
  REQUESTED_BY: { from: ['lease_requests'], to: ['agents'] },
  REQUESTS_ZONE: { from: ['lease_requests'], to: ['spatial_zones'] },
  DECIDES_REQUEST: { from: ['lease_approvals'], to: ['lease_requests'] },
  APPROVED_BY: { from: ['lease_approvals'], to: ['agents'] },
  ALLOCATED_UNDER: { from: ['space_time_allocations'], to: ['lease_approvals'] },
  ALLOCATES_ZONE: { from: ['space_time_allocations'], to: ['spatial_zones'] },
  REQUIRES_ALLOCATION: { from: ['behaviors'], to: ['space_time_allocations'] },
  REQUIRES_RECEIPT: { from: ['behaviors'], to: ['delivery_receipts'] },
  RECEIPT_FOR: { from: ['delivery_receipts'], to: ['commands', 'tasks'] },
  SENT_BY: { from: ['delivery_receipts'], to: ['entities'] },
  RECEIVED_BY: { from: ['delivery_receipts'], to: ['entities'] },
  SELECTS_LEASE_REQUEST: { from: ['scenarios'], to: ['lease_requests'] },
  SELECTS_LEASE_APPROVAL: { from: ['scenarios'], to: ['lease_approvals'] },
  SELECTS_ALLOCATION: { from: ['scenarios'], to: ['space_time_allocations'] },
  SELECTS_RECEIPT: { from: ['scenarios'], to: ['delivery_receipts'] },
};

export function graphEdges(graph) {
  const nodes = new Map(graphNodes(graph).map(node => [node.id, node]));
  const edges = [];
  const emit = (reference, relation, reverse = false, role = reference.relation) => {
    const record = nodes.get(reference.source)?.node || {};
    const scopeMatch = reference.path.match(/\.(reads|writes|controls)\[(\d+)\]/);
    const timelineMatch = reference.path.match(/\.admission_timeline\[(\d+)\]/);
    const timelineStep = timelineMatch ? list(record.admission_timeline)[Number(timelineMatch[1])] : null;
    const scoped = scopeMatch ? record[scopeMatch[1]]?.[Number(scopeMatch[2])] : null;
    const specificScope = list(scoped?.entity_ids).length ? scoped.entity_ids : record.entity_ids || record.target_entity_ids || (record.entity_id ? [record.entity_id] : []);
    edges.push({ id: derivedId('reference', reference.source, reference.path, relation), source: reverse ? reference.target : reference.source, target: reverse ? reference.source : reference.target, relation, role, condition: record.composition?.mode === 'conditional' ? 'predicate result must be known and match the authored condition' : null, time: timelineStep?.at_ns || record.valid_time_ns || null, scope: specificScope, version: graph?.schema_version || null, source_id: record.source_id || null, origin: 'typed_reference', path: reference.path, order: reference.relation === 'composition.behavior_ids' ? list(record.composition?.behavior_ids).indexOf(reference.target) : null });
  };
  for (const reference of references(graph)) {
    const kind = nodes.get(reference.source)?.collection, key = reference.relation;
    const fallback = {
      'spatial_zones:authority_agent_ids': 'MANAGED_BY', 'lease_requests:agent_id': 'REQUESTED_BY', 'lease_requests:zone_id': 'REQUESTS_ZONE',
      'lease_approvals:request_id': 'DECIDES_REQUEST', 'lease_approvals:authority_agent_id': 'APPROVED_BY', 'lease_approvals:evidence_receipt_ids': 'EVIDENCED_BY',
      'space_time_allocations:approval_id': 'ALLOCATED_UNDER', 'space_time_allocations:zone_id': 'ALLOCATES_ZONE',
      'behaviors:required_allocation_ids': 'REQUIRES_ALLOCATION', 'behaviors:required_receipt_ids': 'REQUIRES_RECEIPT',
      'delivery_receipts:command_id': 'RECEIPT_FOR', 'delivery_receipts:task_id': 'RECEIPT_FOR', 'delivery_receipts:sender_entity_id': 'SENT_BY', 'delivery_receipts:receiver_entity_id': 'RECEIVED_BY', 'delivery_receipts:evidence_fact_ids': 'EVIDENCED_BY',
      'scenarios:lease_request_ids': 'SELECTS_LEASE_REQUEST', 'scenarios:lease_approval_ids': 'SELECTS_LEASE_APPROVAL', 'scenarios:allocation_ids': 'SELECTS_ALLOCATION', 'scenarios:delivery_receipt_ids': 'SELECTS_RECEIPT',
      'facts:value.entity_ref': 'ENTITY_REFERENCE', 'modules:reads.entity_ids': 'READ_SCOPE', 'modules:writes.entity_ids': 'WRITE_SCOPE',
      'agents:controls.entity_ids': 'CONTROLS', 'agents:controls.arbitration_id': 'ARBITRATED_BY', 'agents:observable_fact_ids': 'OBSERVES', 'strategies:input_fields.field_id': 'ACCEPTS_FIELD', 'strategies:output_command_ids': 'DECLARES_OUTPUT',
      'capabilities:entity_ids': 'AVAILABLE_TO', 'capabilities:module_ids': 'PROVIDED_BY', 'behaviors:entity_ids': 'ACTS_ON',
      'requests:task_ids': 'REQUESTS_TASK', 'decisions:agent_id': 'ISSUED_BY', 'decisions:task_id': 'IMPLEMENTS_TASK', 'tasks:executor_agent_id': 'EXECUTOR',
      'tasks:command_ids': 'DECLARES_COMMAND', 'tasks:objective_ids': 'PURSUes', 'tasks:resource_claims.resource_id': 'ALLOCATES',
      'rules:predicate_ids': 'APPLIES_WHEN', 'rules:event_ids': 'DECLARES_EVENT', 'rules:command_ids': 'DECLARES_COMMAND', 'rules:subpredicate_ids': 'REFERENCES',
      'commands:agent_id': 'ISSUED_BY', 'commands:task_id': 'IMPLEMENTS_TASK', 'commands:target_entity_ids': 'TARGETS',
      'commands:composition.conditions.predicate_id': 'CONDITIONED_ON', 'commands:composition.conditions.behavior_id': 'CONDITIONS_BEHAVIOR',
      'scenarios:fact_ids': 'SCENARIO_INPUT', 'scenarios:decision_ids': 'PROPOSES_DECISION', 'scenarios:task_ids': 'SELECTS_TASK', 'scenarios:event_ids': 'REPLAYS_EVENT', 'scenarios:command_ids': 'REPLAYS_COMMAND',
      'scenarios:admission_timeline.check_ids': 'SCHEDULES_CHECK', 'scenarios:admission_timeline.event_ids': 'SCHEDULES_EVENT', 'scenarios:admission_timeline.updated_fact_ids': 'SCHEDULES_UPDATE',
      'admission_checks:behavior_ids': 'CHECKS', 'feedback_policies:event_ids': 'HANDLES', 'feedback_policies:agent_id': 'RESPONDS_AS', 'feedback_policies:behavior_ids': 'AFFECTS', 'feedback_policies:decision_ids': 'PROPOSES_REPLAN',
      'predicate_results:predicate_id': 'RESULT_OF', 'relation_definitions:subject_type_id': 'SUBJECT_TYPE', 'relation_definitions:object_type_id': 'OBJECT_TYPE', 'relation_definitions:field_ids': 'USES_FIELD', 'sources:applicable_entity_type_ids': 'COVERS_TYPE', 'arbitrations:agent_ids': 'ARBITRATES',
    };
    let relation = fallback[`${kind}:${key}`]?.toUpperCase() || (key === 'source_id' && ['requests','events','predicate_results','parameter_definitions','spatial_zones','lease_requests','lease_approvals','space_time_allocations','delivery_receipts'].includes(kind) ? 'CITED_FROM' : ['entity_id','entity_ids'].includes(key) && ['requests','objectives','tasks','constraints','events','admission_checks','arbitrations','lease_requests','space_time_allocations'].includes(kind) ? 'SCOPED_TO' : 'UNMAPPED_REFERENCE'), reverse = false, role = key;
    if (key === 'type_id') relation = 'INSTANCE_OF';
    else if (kind === 'entity_types' && key === 'field_ids') relation = 'DECLARES';
    else if (kind === 'facts' && key === 'entity_id') { relation = 'OWNS'; reverse = true; }
    else if (kind === 'facts' && key === 'field_id') relation = 'INSTANCE_OF';
    else if (kind === 'facts' && key === 'producer_module_id') { relation = 'UPDATES'; reverse = true; }
    else if (kind === 'facts' && key === 'source_id') relation = 'EVIDENCED_BY';
    else if (kind === 'agents' && key === 'strategy_id') relation = 'CALLS';
    else if (kind === 'strategies' && ['observable_fact_ids', 'objective_ids', 'constraint_ids', 'request_ids'].includes(key)) {
      relation = 'INPUT_TO'; reverse = true; role = { observable_fact_ids: 'observable_fact', objective_ids: 'task_goal', constraint_ids: 'applicable_constraint', request_ids: 'request' }[key];
      if (key === 'constraint_ids') emit(reference, 'OUTPUT_MUST_SATISFY', false, 'output_requirement');
    } else if (kind === 'strategies' && key === 'decision_ids') relation = 'PRODUCES';
    else if (kind === 'decisions' && key === 'command_ids') relation = 'GENERATES';
    else if (kind === 'commands' && key === 'composition.behavior_ids') { relation = 'COMPOSES'; role = nodes.get(reference.source)?.node.composition.mode; }
    else if (kind === 'behaviors' && key === 'module_ids') relation = 'EXECUTED_BY';
    else if (['behaviors', 'tasks', 'resources', 'rules', 'lease_approvals'].includes(kind) && key === 'constraint_ids') relation = 'CONSTRAINED_BY';
    else if (kind === 'behaviors' && key === 'after_behavior_ids') relation = 'WAITS_FOR';
    else if (kind === 'behaviors' && ['capability_ids', 'resource_claims.resource_id'].includes(key)) relation = 'REQUIRES';
    else if (kind === 'admission_checks' && key === 'field_id') { relation = 'READS'; role = 'scoped_field'; }
    else if (kind === 'admission_checks' && key === 'constraint_id') relation = 'USES';
    else if (kind === 'modules' && key === 'writes.field_ids') relation = 'UPDATES';
    else if (kind === 'modules' && key === 'reads.field_ids') relation = 'READS';
    else if (kind === 'predicates' && key === 'input_fact_ids') relation = 'READS';
    else if (kind === 'predicates' && key === 'rule_ids') relation = 'USES_RULE';
    else if (kind === 'predicates' && key === 'subpredicate_ids') relation = 'DEPENDS_ON';
    else if (kind === 'rules' && key === 'parameter_ids') relation = 'USES';
    else if (kind === 'rules' && ['state_field_ids', 'relation_definition_ids'].includes(key)) relation = 'READS_DEFINITION';
    else if (kind === 'rules' && ['output_predicate_id','output_event_id'].includes(key)) relation = 'DEFINES';
    else if (kind === 'events' && ['fact_ids', 'trigger.check_id'].includes(key)) relation = 'EVIDENCED_BY';
    else if (kind === 'constraints' && key === 'source_id') relation = 'BASED_ON';
    else if (kind === 'predicate_results' && key === 'input_fact_ids') relation = 'BASED_ON';
    if (kind === 'decisions' && key === 'strategy_id') { relation = 'PRODUCES'; reverse = true; }
    if (kind === 'resources' && key === 'owner_entity_id') { relation = 'OWNS'; reverse = true; }
    emit(reference, relation, reverse, role);
  }
  for (const entity of list(graph?.entities).filter(object)) for (const fieldId of list(nodes.get(entity.type_id)?.node.field_ids)) {
    edges.push({ id: `owns-${entity.id}-${fieldId}`, source: entity.id, target: fieldId, relation: 'OWNS', role: 'declared_state_scope', condition: null, time: null, scope: [entity.id], version: graph?.schema_version || null, source_id: null, origin: 'entity_type_binding', path: 'entities' });
  }
  for (const policy of list(graph?.feedback_policies).filter(object)) for (const eventId of list(policy.event_ids)) {
    edges.push({ id: `feedback-${policy.id}-${eventId}`, source: eventId, target: policy.agent_id, relation: 'FEEDS_BACK', role: policy.response, condition: 'only when declared event occurs', time: null, scope: [], version: graph?.schema_version || null, source_id: null, origin: 'feedback_policy', path: 'feedback_policies', policy_id: policy.id });
  }
  for (const rule of list(graph?.rules).filter(object)) if (rule.output_predicate_id || rule.output_event_id) for (const id of [...list(rule.state_field_ids), ...list(rule.relation_definition_ids), ...list(rule.subpredicate_ids)]) {
    edges.push({ id: `definition-${rule.id}-${id}`, source: rule.output_predicate_id || rule.output_event_id, target: id, relation: 'DEPENDS_ON', role: rule.operator, condition: null, time: null, scope: [], version: graph?.schema_version || null, source_id: null, origin: 'definition_rule', path: 'rules', rule_id: rule.id });
  }
  for (const rule of list(graph?.rules).filter(object)) {
    const ast = inspectDefinitionAST(graph, rule.id);
    edges.push(...ast.edges);
    for (const [branch, rootId] of Object.entries(ast.roots)) edges.push({ id: derivedId('rule-root', rule.id, branch), source: rule.id, target: rootId, relation: 'HAS_EXPRESSION', role: branch, condition: null, time: null, scope: [], version: graph?.schema_version || null, source_id: null, origin: 'definition_ast', path: `rules.${rule.id}.${branch}` });
    for (const node of ast.nodes) edges.push({ id: derivedId('expression-type', node.id), source: node.id, target: `expression-${node.kind}-type`, relation: 'INSTANCE_OF', role: 'expression_type', condition: null, time: null, scope: [], version: graph?.schema_version || null, source_id: null, origin: 'definition_ast', path: node.path });
  }
  return [...edges, ...list(graph?.edges).filter(object).map(edge => ({ ...edge, origin: 'authored_edge' }))];
}

export function graphNeighbors(graph, id) {
  const edges = graphEdges(graph);
  const incoming = edges.filter(edge => edge.target === id);
  const outgoing = edges.filter(edge => edge.source === id);
  const ids = new Set([...incoming.map(edge => edge.source), ...outgoing.map(edge => edge.target)]);
  return { incoming, outgoing, nodes: graphNodes(graph).filter(node => ids.has(node.id)) };
}

export function inspectGraphNode(graph, id) {
  const found = graphNodes(graph).find(node => node.id === id);
  if (!found) return null;
  const validation = validateGraph(graph);
  return { ...found, ...graphNeighbors(graph, id), issues: [...validation.errors, ...validation.warnings].filter(issue => issue.node_id === id) };
}

function mapsOf(graph) {
  return Object.fromEntries(GRAPH_COLLECTIONS.map(({ key }) => [key, new Map(list(graph?.[key]).filter(object).map(node => [node.id, node]))]));
}
function controls(agent) { return new Set(list(agent?.controls).flatMap(scope => list(scope.entity_ids))); }
function hasScope(scopes, entityId, fieldId) { return list(scopes).some(scope => list(scope.entity_ids).includes(entityId) && list(scope.field_ids).includes(fieldId)); }
function matchesValue(value, type) {
  if (value === null) return true; // Missing data is explicitly UNKNOWN, never a fabricated value.
  if (type === 'vector3') return Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
  if (type === 'entity_ref') return typeof value === 'string';
  if (type === 'integer') return Number.isSafeInteger(value);
  if (type === 'number') return Number.isFinite(value);
  return typeof value === type;
}

/** Peak demand: sequence takes maximum; parallel/conditional conservatively sum. */
export function commandResourceDemand(graph, commandOrId) {
  const maps = mapsOf(graph);
  const command = typeof commandOrId === 'string' ? maps.commands.get(commandOrId) : commandOrId;
  const demand = {};
  for (const id of list(command?.composition?.behavior_ids)) {
    const perBehavior = {};
    for (const claim of list(maps.behaviors.get(id)?.resource_claims)) perBehavior[claim.resource_id] = (perBehavior[claim.resource_id] || 0) + claim.amount;
    for (const [id, amount] of Object.entries(perBehavior)) demand[id] = command.composition.mode === 'sequence' ? Math.max(demand[id] || 0, amount) : (demand[id] || 0) + amount;
  }
  return demand;
}

/** Pure static contract checks. This never validates physics, legal permission or native truth. */
export function validateGraph(graph, config) {
  const shape = validateGraphSchema(graph);
  const errors = [...shape.errors], warnings = [];
  const nodes = authoredGraphNodes(graph);
  const counts = Object.fromEntries(GRAPH_COLLECTIONS.map(({ key }) => [key, list(graph?.[key]).length]));
  const maps = mapsOf(graph);
  const locate = path => nodes.find(({ collection, node }) => path.startsWith(`${collection}[${list(graph?.[collection]).indexOf(node)}]`))?.id || null;
  const add = (severity, code, path, message, nodeId) => (severity === 'error' ? errors : warnings).push({ severity, code, path, message, node_id: nodeId || locate(path) });
  const error = (code, path, message, id) => add('error', code, path, message, id);
  const warn = (code, path, message, id) => add('warning', code, path, message, id);
  for (const issue of errors) issue.node_id = locate(issue.path);
  const finish = () => ({ valid: errors.length === 0, errors, warnings, counts, status: errors.length ? 'declared' : 'static_checked', readiness: { declared: object(graph), static_checked: errors.length === 0, fixture_executed: false, real_connected: false } });
  // Shape-invalid input is never dereferenced as executable graph. Keep import/editor validation total.
  if (!shape.valid) return finish();
  const all = new Map();
  for (const item of nodes) {
    if (all.has(item.id)) error('E_DUPLICATE_ID', `${item.collection}[${graph[item.collection].indexOf(item.node)}].id`, `ID ${item.id} is already used in ${all.get(item.id).collection}; graph IDs must be globally unique.`, item.id);
    all.set(item.id, item);
  }
  for (const edge of references(graph)) {
    if (!maps[edge.collection]?.has(edge.target)) {
      const mistaken = all.get(edge.target);
      const confused = (edge.collection === 'commands' && mistaken?.collection === 'events') || (edge.collection === 'events' && mistaken?.collection === 'commands');
      error(confused ? 'E_EVENT_COMMAND_CONFUSION' : edge.relation === 'type_id' ? 'E_TYPE_REFERENCE' : 'E_UNRESOLVED_REFERENCE', edge.path, confused ? `${edge.target} is a ${mistaken.collection === 'events' ? 'event' : 'command'} definition, not a ${edge.collection === 'events' ? 'event' : 'command'}. Observations cannot stand in for actuator requests.` : `${edge.target} does not resolve to ${edge.collection}.`, edge.source);
    } else if (edge.relation === 'type_id' && edge.collection === 'node_types' && maps.node_types.get(edge.target).collection !== all.get(edge.source)?.collection) {
      error('E_TYPE_MISMATCH', edge.path, 'Node type belongs to a different graph collection.', edge.source);
    }
  }
  const each = (key, fn) => graph[key].forEach((node, index) => fn(node, `${key}[${index}]`));
  each('fields', (field, path) => {
    if (!UNITS.has(field.unit)) error('E_UNKNOWN_UNIT', `${path}.unit`, `Unsupported unit ${field.unit}; add a versioned unit contract rather than an implicit conversion.`);
    if (!FRAMES.has(field.frame)) error('E_UNKNOWN_FRAME', `${path}.frame`, `Unsupported coordinate frame ${field.frame}.`);
    if (field.value_type === 'entity_ref' && (field.unit !== '1' || field.frame !== 'none')) error('E_TYPE_MISMATCH', path, 'Entity-reference fields must be unitless in frame none.');
  });
  const scene = new Map();
  // Resolve only graph-linked IDs. Never expand a potentially enormous scene collection.
  if (config && Array.isArray(config.entities)) {
    const templates = new Map(config.entities.slice(0, 10000).filter(object).map(entity => [entity.id, entity]));
    for (const entity of graph.entities) {
      const id = entity.scene_entity_id;
      if (!id) continue;
      const exact = templates.get(id);
      if (exact?.count === 1) { scene.set(id, exact); continue; }
      const split = id.lastIndexOf('-'), suffix = id.slice(split + 1), template = templates.get(id.slice(0, split));
      if (template && /^[1-9][0-9]*$/.test(suffix) && Number.isSafeInteger(template.count) && template.count > 1 && Number(suffix) <= template.count) scene.set(id, template);
    }
  }
  each('entities', (entity, path) => {
    const type = maps.entity_types.get(entity.type_id);
    if (config && entity.scene_entity_id) {
      const linked = scene.get(entity.scene_entity_id);
      if (!linked) error('E_SCENE_ENTITY_REFERENCE', `${path}.scene_entity_id`, `Scene entity ${entity.scene_entity_id} is absent from the same configuration.`);
      else if (type && !type.compatible_config_types.includes(linked.type)) error('E_SCENE_ENTITY_TYPE', `${path}.scene_entity_id`, `Scene type ${linked.type} is incompatible with ${entity.type_id}.`);
    }
    for (const fieldId of type?.field_ids || []) if (maps.fields.get(fieldId)?.temporal === 'dynamic' && !graph.modules.some(module => hasScope(module.writes, entity.id, fieldId))) error('E_MISSING_STATE_PRODUCER', path, `Dynamic field ${fieldId} on ${entity.id} has no declared module producer.`);
  });
  each('modules', (module, path) => {
    for (const direction of ['reads', 'writes']) module[direction].forEach((scope, i) => {
      for (const entityId of scope.entity_ids) for (const fieldId of scope.field_ids) {
        const entity = maps.entities.get(entityId), type = maps.entity_types.get(entity?.type_id);
        if (type && !type.field_ids.includes(fieldId)) error('E_FIELD_SCOPE_TYPE', `${path}.${direction}[${i}]`, `${fieldId} is not defined for ${entityId}'s type.`);
        if (direction === 'reads' && !graph.modules.some(producer => hasScope(producer.writes, entityId, fieldId))) error('E_MISSING_STATE_PRODUCER', `${path}.reads[${i}]`, `Module reads ${entityId}.${fieldId} without a declared producer.`);
      }
    });
  });
  each('facts', (fact, path) => {
    const field = maps.fields.get(fact.field_id), entity = maps.entities.get(fact.entity_id), type = maps.entity_types.get(entity?.type_id);
    if (field) {
      if (!matchesValue(fact.value, field.value_type)) error('E_FACT_VALUE_TYPE', `${path}.value`, `Value must match declared ${field.value_type} field type.`);
      if (fact.unit !== field.unit) error('E_UNIT_MISMATCH', `${path}.unit`, `Fact unit ${fact.unit} differs from field unit ${field.unit}; no implicit conversion.`);
      if (fact.frame !== field.frame) error('E_FRAME_MISMATCH', `${path}.frame`, `Fact frame ${fact.frame} differs from field frame ${field.frame}.`);
    }
    if (type && !type.field_ids.includes(fact.field_id)) error('E_FIELD_SCOPE_TYPE', `${path}.field_id`, 'Fact field is not declared by the entity type.');
    if (BigInt(fact.available_time_ns) < BigInt(fact.valid_time_ns)) error('E_TIME_ORDER', `${path}.available_time_ns`, 'Observation cannot be available before its valid time.');
    if (fact.valid_until_ns !== null && BigInt(fact.valid_until_ns) < BigInt(fact.valid_time_ns)) error('E_TIME_ORDER', `${path}.valid_until_ns`, 'Validity end cannot precede valid time.');
    if (!hasScope(maps.modules.get(fact.producer_module_id)?.writes, fact.entity_id, fact.field_id)) error('E_MISSING_STATE_PRODUCER', `${path}.producer_module_id`, 'Fact producer does not declare write scope for this entity and field.');
    if (maps.sources.get(fact.source_id)?.kind !== 'authored_fixture') error('E_FACT_SOURCE_KIND', `${path}.source_id`, 'A legal-reference source cannot supply a fixture observation.');
    if (fact.value === null) warn('W_MISSING_FACT', `${path}.value`, 'Missing value remains UNKNOWN in fixture inspection.');
  });
  each('agents', (agent, path) => {
    const strategy = maps.strategies.get(agent.strategy_id);
    for (const factId of strategy?.observable_fact_ids || []) if (!agent.observable_fact_ids.includes(factId)) error('E_STRATEGY_INPUT_MISMATCH', `${path}.observable_fact_ids`, `Strategy requires ${factId}, absent from agent observations.`);
    for (const control of agent.controls) if (!control.entity_ids.length) error('E_EMPTY_CONTROL_SCOPE', `${path}.controls`, 'An agent needs at least one explicit controlled entity per scope.');
  });
  const scopes = graph.agents.flatMap(agent => agent.controls.map(scope => ({ agent, scope })));
  for (let i = 0; i < scopes.length; i++) for (let j = i + 1; j < scopes.length; j++) {
    const a = scopes[i], b = scopes[j];
    if (a.agent.id === b.agent.id || (a.scope.mode !== 'exclusive' && b.scope.mode !== 'exclusive')) continue;
    const overlap = a.scope.entity_ids.filter(id => b.scope.entity_ids.includes(id));
    if (!overlap.length) continue;
    const arbitration = a.scope.arbitration_id === b.scope.arbitration_id && maps.arbitrations.get(a.scope.arbitration_id);
    if (!arbitration || ![a.agent.id, b.agent.id].every(id => arbitration.agent_ids.includes(id)) || !overlap.every(id => arbitration.entity_ids.includes(id))) error('E_EXCLUSIVE_SCOPE_OVERLAP', `agents[${graph.agents.indexOf(b.agent)}].controls`, `Exclusive control overlaps on ${overlap.join(', ')} without a shared applicable arbitration.`, b.agent.id);
  }
  each('strategies', (strategy, path) => {
    const observed = strategy.observable_fact_ids.map(id => maps.facts.get(id)).filter(Boolean);
    for (const input of strategy.input_fields) {
      const field = maps.fields.get(input.field_id);
      if (field && ['value_type', 'unit', 'frame'].some(key => input[key] !== field[key])) error('E_STRATEGY_INPUT_MISMATCH', `${path}.input_fields`, `Input ${input.field_id} has a type/unit/frame mismatch.`);
      if (!observed.some(fact => fact.field_id === input.field_id)) error('E_STRATEGY_INPUT_MISMATCH', `${path}.input_fields`, `Input ${input.field_id} has no observable fact binding.`);
    }
    for (const fact of observed) if (!strategy.input_fields.some(input => input.field_id === fact.field_id)) error('E_STRATEGY_INPUT_MISMATCH', `${path}.observable_fact_ids`, `${fact.id} has no declared input field.`);
    for (const id of strategy.decision_ids) {
      const decision = maps.decisions.get(id);
      if (decision && decision.strategy_id !== strategy.id) error('E_STRATEGY_OUTPUT_MISMATCH', `${path}.decision_ids`, `Decision ${id} belongs to a different strategy.`);
    }
  });
  each('decisions', (decision, path) => {
    const strategy = maps.strategies.get(decision.strategy_id), agent = maps.agents.get(decision.agent_id), task = maps.tasks.get(decision.task_id);
    if (strategy && (!strategy.decision_ids.includes(decision.id) || decision.command_ids.some(id => !strategy.output_command_ids.includes(id)))) error('E_STRATEGY_OUTPUT_MISMATCH', path, 'Decision or its commands are outside the strategy output contract.');
    if (agent && agent.strategy_id !== decision.strategy_id) error('E_STRATEGY_OUTPUT_MISMATCH', `${path}.agent_id`, 'Decision strategy is not bound to its agent.');
    if (task && (task.executor_agent_id !== decision.agent_id || decision.command_ids.some(id => !task.command_ids.includes(id)))) error('E_DECISION_TASK_MISMATCH', `${path}.task_id`, 'Decision commands/executor do not match its task.');
    if (Object.values(decision.assessments).some(value => value !== 'UNKNOWN')) warn('W_AUTHORED_ASSESSMENT', `${path}.assessments`, 'Authored assessment is a fixture input only; execution will keep independent actual dimensions UNKNOWN.');
  });
  const claims = (record, path) => {
    const sums = new Map();
    for (const [i, claim] of record.resource_claims.entries()) {
      const resource = maps.resources.get(claim.resource_id);
      if (resource && resource.unit !== claim.unit) error('E_RESOURCE_UNIT_MISMATCH', `${path}.resource_claims[${i}].unit`, 'Resource claim unit differs from declared capacity unit.');
      sums.set(claim.resource_id, (sums.get(claim.resource_id) || 0) + claim.amount);
    }
    for (const [id, amount] of sums) if (amount > maps.resources.get(id)?.capacity) error('E_RESOURCE_OVERCOMMIT', `${path}.resource_claims`, `${id} claims ${amount}, exceeding capacity ${maps.resources.get(id).capacity}.`);
    if (!record.constraint_ids.length) error('E_MISSING_CONSTRAINT', `${path}.constraint_ids`, 'Tasks and behaviors require explicit applicable constraints.');
  };
  each('tasks', (task, path) => { claims(task, path); });
  each('resources', (resource, path) => {
    if (!resource.constraint_ids.length) error('E_MISSING_CONSTRAINT', `${path}.constraint_ids`, 'Resource capacity needs a declared constraint.');
    if (!UNITS.has(resource.unit)) error('E_UNKNOWN_UNIT', `${path}.unit`, `Unsupported capacity unit ${resource.unit}.`);
  });
  each('constraints', (constraint, path) => {
    const source = maps.sources.get(constraint.source_id);
    if (constraint.kind === 'legal') {
      if (source?.kind !== 'legal_reference') error('E_LEGAL_SOURCE_KIND', `${path}.source_id`, 'Legal constraint requires separately typed legal-reference metadata.');
      warn('W_LEGAL_UNVERIFIED', path, 'Legal metadata is declared only; it does not establish authority or permission.');
    }
  });
  each('behaviors', (behavior, path) => {
    claims(behavior, path);
    if (!behavior.capability_ids.length || !behavior.module_ids.length) error('E_BEHAVIOR_BINDING', path, 'Behavior requires an explicit capability and executing module.');
    for (const id of behavior.capability_ids) {
      const capability = maps.capabilities.get(id);
      if (capability && (!behavior.entity_ids.every(entityId => capability.entity_ids.includes(entityId)) || !behavior.module_ids.every(moduleId => capability.module_ids.includes(moduleId)))) error('E_CAPABILITY_SCOPE', `${path}.capability_ids`, 'Capability does not cover this behavior’s entities and modules.');
    }
  });
  each('commands', (command, path) => {
    const agent = maps.agents.get(command.agent_id), controlled = controls(agent), task = maps.tasks.get(command.task_id);
    if (agent && command.target_entity_ids.some(id => !controlled.has(id))) error('E_COMMAND_SCOPE', `${path}.target_entity_ids`, 'Command targets exceed its agent’s explicit control scope.');
    if (task && (task.executor_agent_id !== command.agent_id || !task.command_ids.includes(command.id))) error('E_COMMAND_TASK_MISMATCH', `${path}.task_id`, 'Command and task executor/output bindings do not agree.');
    for (const id of command.composition.behavior_ids) {
      const behavior = maps.behaviors.get(id);
      if (behavior && behavior.entity_ids.some(entityId => !controlled.has(entityId) || !command.target_entity_ids.includes(entityId))) error('E_BEHAVIOR_COMMAND_SCOPE', `${path}.composition`, 'Composed behavior exceeds command target or agent control scope.');
    }
    for (const [id, amount] of Object.entries(commandResourceDemand(graph, command))) {
      if (amount > maps.resources.get(id)?.capacity) error('E_RESOURCE_OVERCOMMIT', `${path}.composition`, `${command.composition.mode} composition claims ${amount} ${maps.resources.get(id).unit} of ${id}; capacity is ${maps.resources.get(id).capacity}.`);
      const taskCapacity = list(task?.resource_claims).filter(claim => claim.resource_id === id).reduce((sum, claim) => sum + claim.amount, 0);
      if (task && amount > taskCapacity) error('E_RESOURCE_ALLOCATION_MISMATCH', `${path}.composition`, `Behavior demand ${amount} exceeds task allocation ${taskCapacity} for ${id}.`);
    }
  });
  each('scenarios', (scenario, path) => {
    const totals = new Map();
    for (const id of scenario.task_ids) for (const claim of maps.tasks.get(id)?.resource_claims || []) totals.set(claim.resource_id, (totals.get(claim.resource_id) || 0) + claim.amount);
    for (const [id, amount] of totals) if (amount > maps.resources.get(id)?.capacity) error('E_RESOURCE_OVERCOMMIT', `${path}.task_ids`, `Concurrent fixture tasks claim ${amount} of ${id}, beyond capacity ${maps.resources.get(id).capacity}.`);
    for (const id of scenario.decision_ids) {
      const decision = maps.decisions.get(id);
      if (decision && (!scenario.task_ids.includes(decision.task_id) || decision.command_ids.some(commandId => !scenario.command_ids.includes(commandId)))) error('E_SCENARIO_BINDING', `${path}.decision_ids`, 'Scenario omits a selected decision’s task or command.');
    }
    for (const id of scenario.command_ids) {
      const command = maps.commands.get(id);
      if (command && !scenario.task_ids.includes(command.task_id)) error('E_SCENARIO_BINDING', `${path}.command_ids`, 'Scenario command has no selected task.');
    }
  });
  validateExtendedGraph(graph, maps, error, warn);
  validateSpatialContracts(graph, maps, error, warn);
  warn('W_FIXTURE_ONLY', 'provenance', 'Every binding is fixture/stub. Static checking is not fixture execution or a real backend connection.');
  return finish();
}

export const DEFINITION_TEMPORAL_CONTROLS = Object.freeze({
  duration_fraction: { arity: 1, controls: ['window_s','max_gap_s','scope'] },
  ordered_sequence: { arity: 2, controls: ['window_s','max_gap_s','scope'] },
  episode_active: { arity: 4, controls: ['enter_s','clear_s','max_gap_s','scope'] },
  ...Object.fromEntries(['stable_window','hold','holds','all_window','any_window','count_window','delta','rate','rise','fall','changed'].map(operator => [operator, { arity: 1, controls: ['window_s','duration_seconds','max_gap_s','scope'], window_active: !['rise','fall','changed'].includes(operator) }])),
});
export const SUPPORTED_DEFINITION_OPERATORS = Object.freeze(['literal', 'unknown', 'gt', 'gte', 'lt', 'lte', 'eq', 'ne', 'and', 'or', 'not', 'add', 'sub', 'abs', ...Object.keys(DEFINITION_TEMPORAL_CONTROLS)]);
const AST_CONTROLS = ['window_s', 'duration_seconds', 'max_gap_s', 'scope', 'enter_s', 'clear_s'];

/** Inspect supported definition syntax. Requirements and target dependencies are different sets. */
export function inspectDefinitionAST(graph, ruleId) {
  const maps = mapsOf(graph), rule = maps.rules.get(ruleId);
  const nodes = [], edges = [], errors = [], warnings = [], requirements = new Set(), dependencies = new Set();
  const issue = (code, path, message, warning = false) => (warning ? warnings : errors).push({ severity: warning ? 'warning' : 'error', code, path, message, node_id: ruleId });
  if (!rule) return { nodes, edges, requirements: [], dependencies: [], errors: [{ code: 'E_AST_RULE', message: 'Rule not found.', path: 'rules', node_id: ruleId, severity: 'error' }], warnings, roots: {} };
  const rootPath = `rules[${list(graph.rules).indexOf(rule)}]`;
  let visited = 0;
  const walk = (expression, path, branch, ancestors = new Set(), depth = 0) => {
    const id = derivedId('expression', ruleId, path);
    const node = { id, label: '', kind: 'unknown', collection: 'expressions', expression, branch, value_type: 'UNKNOWN', unit: 'UNKNOWN', path, status: 'definition_only', truth: 'UNKNOWN' };
    nodes.push(node);
    if (++visited > 2000 || depth > 32) { issue('E_AST_LIMIT', path, 'Definition AST exceeds its 2,000-node or 32-level inspection bound.'); return node; }
    if (expression === null) { node.label = 'UNKNOWN'; return node; }
    if (!object(expression)) {
      if (['number', 'boolean', 'string'].includes(typeof expression)) { Object.assign(node, { kind: 'const', label: JSON.stringify(expression), value_type: typeof expression, unit: '1' }); return node; }
      issue('E_AST_SHAPE', path, 'Expression must be a supported leaf or operator node.'); return node;
    }
    if (ancestors.has(expression)) { issue('E_AST_CYCLE', path, 'AST object is cyclic.'); return node; }
    const next = new Set(ancestors); next.add(expression);
    const tags = ['state', 'param', 'const', 'predicate', 'event', 'unknown', 'op'].filter(key => own(expression, key));
    if (tags.length !== 1) { issue('E_AST_SHAPE', path, 'Exactly one expression tag is required.'); return node; }
    const tag = tags[0]; node.kind = tag;
    const allowedKeys = tag === 'op' ? expression.op === 'literal' ? ['op','value','unit','value_type'] : expression.op === 'unknown' ? ['op','reason'] : ['op','args',...AST_CONTROLS] : tag === 'const' ? ['const','unit'] : ['predicate','event'].includes(tag) ? [tag,'parameters'] : [tag];
    for (const key of Object.keys(expression)) if (!allowedKeys.includes(key)) issue('E_AST_UNMAPPED_KEY', `${path}.${key}`, 'Unmapped expression property is unavailable; it is not silently ignored.');
    if (tag === 'op' && expression.op === 'unknown') { node.kind = 'unknown'; node.label = `UNKNOWN: ${expression.reason || 'unspecified'}`; return node; }
    if (tag === 'op' && expression.op === 'literal') {
      node.kind = 'const'; node.label = `literal: ${JSON.stringify(expression.value)}`; node.value_type = expression.value === null ? 'UNKNOWN' : typeof expression.value; node.unit = expression.unit || '1';
      if (expression.value !== null && !['number','boolean','string'].includes(node.value_type)) issue('E_AST_LITERAL', `${path}.value`, 'Literal must be a scalar or null/UNKNOWN.');
      if (expression.value_type && expression.value_type !== node.value_type) issue('E_AST_TYPE', `${path}.value_type`, 'Literal value differs from its declared type.');
      if (expression.unit && !UNITS.has(expression.unit)) issue('E_AST_UNIT', `${path}.unit`, 'Literal unit is outside the versioned contract.');
      return node;
    }
    const link = (source, target, relation, role) => edges.push({ id: derivedId('ast-edge', ruleId, path, source, relation, role, target), source, target, relation, role, condition: null, time: null, scope: [], version: graph?.schema_version || null, source_id: null, origin: 'definition_ast', path });
    if (tag === 'state' || tag === 'param') {
      const entry = maps[tag === 'state' ? 'fields' : 'parameter_definitions'].get(expression[tag]);
      node.label = `${tag}: ${expression[tag]}`;
      if (!entry) issue('E_AST_REFERENCE', `${path}.${tag}`, `Unresolved ${tag} definition ${expression[tag]}.`);
      else { node.value_type = entry.value_type; node.unit = entry.unit; }
      if (tag === 'state') requirements.add(expression[tag]);
      link(expression[tag], id, tag === 'state' ? 'STATE_INPUT' : 'PARAMETER_INPUT', tag);
    } else if (tag === 'const') {
      node.label = `const: ${JSON.stringify(expression.const)}`;
      node.value_type = expression.const === null ? 'UNKNOWN' : typeof expression.const;
      node.unit = expression.unit || '1';
      if (expression.const !== null && !['number', 'boolean', 'string'].includes(node.value_type)) issue('E_AST_LITERAL', `${path}.const`, 'Supported literal values are numbers, booleans, strings and null/UNKNOWN.');
      if (expression.unit && !UNITS.has(expression.unit)) issue('E_AST_UNIT', `${path}.unit`, 'Literal unit is outside the versioned unit contract.');
    } else if (tag === 'unknown') node.label = `UNKNOWN: ${expression.unknown}`;
    else if (tag === 'predicate' || tag === 'event') {
      node.label = `${tag}: ${expression[tag]}`; node.value_type = 'boolean'; node.unit = '1';
      if (tag === 'event' && !list(graph.rules).some(rule => rule?.output_event_id === expression.event && rule.definition_ast != null)) issue('E_AST_EVENT_UNAVAILABLE', `${path}.event`, 'Event target has no compatible Boolean definition rule; an authored occurrence/dispatch event is not predicate truth.');
      if (!maps[tag === 'predicate' ? 'predicates' : 'events'].has(expression[tag])) issue('E_AST_REFERENCE', `${path}.${tag}`, `Unresolved ${tag} target ${expression[tag]}.`);
      dependencies.add(expression[tag]);
      link(expression[tag], id, 'TARGET_REFERENCE', tag);
      for (const [key, value] of Object.entries(expression.parameters || {})) {
        if (!maps.parameter_definitions.has(key)) issue('E_AST_PARAMETER', `${path}.parameters.${key}`, 'Parameter binding does not resolve to a parameter definition.');
        const child = walk(value, `${path}.parameters.${key}`, branch, next, depth + 1);
        const parameter = maps.parameter_definitions.get(key);
        if (parameter && child.value_type !== 'UNKNOWN' && (child.value_type !== parameter.value_type || child.unit !== parameter.unit)) issue('E_AST_PARAMETER_TYPE', `${path}.parameters.${key}`, 'Bound parameter type/unit differs from its declared definition.');
        link(child.id, id, 'BINDS_PARAMETER', key);
      }
    } else {
      node.label = `op: ${expression.op}`;
      if (!SUPPORTED_DEFINITION_OPERATORS.includes(expression.op)) issue('E_AST_OPERATOR_UNSUPPORTED', `${path}.op`, `Operator ${expression.op} is unavailable in this bounded syntax inspector; no native catalog completeness is claimed.`);
      const args = list(expression.args).map((arg, i) => {
        const child = walk(arg, `${path}.args[${i}]`, branch, next, depth + 1); link(child.id, id, 'ARGUMENT', String(i)); return child;
      });
      const unary = ['not', 'abs'].includes(expression.op), variadic = ['and', 'or'].includes(expression.op);
      const temporal = DEFINITION_TEMPORAL_CONTROLS[expression.op];
      if (args.length !== (temporal?.arity ?? (unary ? 1 : 2)) && !(variadic && args.length >= 2)) issue('E_AST_ARITY', `${path}.args`, `${expression.op} has an unsupported operand count.`);
      const known = args.filter(arg => arg.value_type !== 'UNKNOWN');
      if (['and', 'or', 'not'].includes(expression.op)) {
        node.value_type = 'boolean'; node.unit = '1';
        if (known.some(arg => arg.value_type !== 'boolean')) issue('E_AST_TYPE', `${path}.args`, 'Logical operators require Boolean operands.');
      } else if (['gt', 'gte', 'lt', 'lte', 'eq', 'ne'].includes(expression.op)) {
        node.value_type = 'boolean'; node.unit = '1';
        if (!['eq', 'ne'].includes(expression.op) && known.some(arg => !['number', 'integer'].includes(arg.value_type))) issue('E_AST_TYPE', `${path}.args`, 'Ordered comparison requires numeric operands.');
        if (known.length > 1 && known.some(arg => (['number', 'integer'].includes(arg.value_type) ? 'number' : arg.value_type) !== (['number', 'integer'].includes(known[0].value_type) ? 'number' : known[0].value_type))) issue('E_AST_TYPE', `${path}.args`, 'Comparison operand types do not match.');
        if (known.length > 1 && known.some(arg => arg.unit !== known[0].unit)) issue('E_AST_UNIT', `${path}.args`, 'Comparison operand units do not match; no conversion is implicit.');
      } else if (['add', 'sub', 'abs'].includes(expression.op)) {
        node.value_type = 'number'; node.unit = known[0]?.unit || 'UNKNOWN';
        if (known.some(arg => !['number', 'integer'].includes(arg.value_type))) issue('E_AST_TYPE', `${path}.args`, 'Arithmetic operands must be numeric.');
        if (known.some(arg => arg.unit !== node.unit)) issue('E_AST_UNIT', `${path}.args`, 'Arithmetic operands require matching units.');
      }
      for (const control of AST_CONTROLS) if (own(expression, control)) {
        const allowed = temporal?.controls.includes(control);
        if (!allowed) issue('E_AST_CONTROL_UNSUPPORTED', `${path}.${control}`, `Control ${control} is unavailable for operator ${expression.op}; unsupported controls are never treated as effective.`);
        const values = control === 'scope' ? list(expression.scope) : [expression[control]];
        if (control === 'scope' && !Array.isArray(expression.scope)) issue('E_AST_SCOPE_SHAPE', `${path}.scope`, 'Native scope syntax is an array of expressions.');
        values.forEach((valueExpression, index) => {
          const childPath = control === 'scope' ? `${path}.scope[${index}]` : `${path}.${control}`;
          const child = walk(valueExpression, childPath, branch, next, depth + 1); link(child.id, id, 'CONTROL_INPUT', control === 'scope' ? `scope[${index}]` : control);
          const edge = edges[edges.length - 1];
          edge.status = !allowed ? 'unavailable_control' : control === 'duration_seconds' && own(expression, 'window_s') ? 'shadowed_alias' : ['window_s','duration_seconds'].includes(control) && temporal.window_active === false ? 'parsed_not_active_window' : 'declared_not_evaluated';
          edge.effective = allowed && edge.status === 'declared_not_evaluated';
          const value = object(valueExpression) && own(valueExpression, 'const') ? valueExpression.const : valueExpression;
          if (control !== 'scope' && typeof value === 'number' && (!Number.isFinite(value) || value < 0)) issue('E_AST_WINDOW', childPath, 'Temporal control must be a finite nonnegative duration.');
          if (control !== 'scope' && child.value_type !== 'UNKNOWN' && !['number', 'integer'].includes(child.value_type)) issue('E_AST_WINDOW', childPath, 'Temporal control must resolve to a numeric duration.');
          if (control !== 'scope' && !['UNKNOWN', '1', 's'].includes(child.unit)) issue('E_AST_WINDOW', childPath, 'Temporal control unit must be seconds.');
        });
      }
    }
    return node;
  };
  const roots = {};
  for (const branch of ['definition_ast', 'applicability_ast']) if (rule[branch] !== null && rule[branch] !== undefined) {
    const root = walk(rule[branch], `${rootPath}.${branch}`, branch); roots[branch] = root.id;
    edges.push({ id: derivedId('ast-root', ruleId, branch), source: root.id, target: rule.output_predicate_id || rule.output_event_id || rule.id, relation: branch === 'definition_ast' ? 'DEFINES' : 'SCOPES', role: branch === 'definition_ast' ? 'main_truth_definition' : 'independent_scope_status', condition: null, time: null, scope: [], version: graph?.schema_version || null, source_id: null, origin: 'definition_ast', path: `${rootPath}.${branch}` });
  }
  return { nodes, edges, requirements: [...requirements], dependencies: [...dependencies], errors, warnings, roots, execution: 'not_connected', native_truth: 'UNKNOWN', scope_status: 'UNKNOWN' };
}

function validateExtendedGraph(graph, maps, error, warn) {
  const canonicalNodes = graphNodes(graph), nodeMap = new Map();
  for (const node of canonicalNodes) {
    if (nodeMap.has(node.id)) error('E_DERIVED_NODE_ID_COLLISION', node.node.path || 'graph', `Canonical node identity ${node.id} collides.`, node.id);
    nodeMap.set(node.id, node);
  }
  const edgeIds = new Set();
  for (const edge of graphEdges(graph).filter(edge => edge.origin !== 'authored_edge')) {
    const from = nodeMap.get(edge.source), to = nodeMap.get(edge.target), contract = GRAPH_EDGE_CONTRACTS[edge.relation];
    if (edgeIds.has(edge.id)) error('E_DUPLICATE_EDGE', edge.path, 'Derived multigraph edge identity collided.');
    edgeIds.add(edge.id);
    if (!contract) warn('W_UNMAPPED_REFERENCE', edge.path, `Reference ${edge.role} has no implemented semantic edge relation; it is explicitly unavailable.`, edge.source);
    else if (from && contract.from !== '*' && !contract.from.includes(from.collection) || to && !contract.to.includes(to.collection)) error('E_EDGE_ENDPOINT_TYPE', edge.path, `Invalid generated endpoint types for ${edge.relation}.`, edge.source);
    else if (contract.roles && !contract.roles.includes(edge.role)) error('E_EDGE_ROLE', edge.path, 'Generated edge role is outside its relation contract.', edge.source);
  }
  for (const [i, edge] of graph.edges.entries()) {
    const path = `edges[${i}]`, from = nodeMap.get(edge.source), to = nodeMap.get(edge.target), contract = GRAPH_EDGE_CONTRACTS[edge.relation];
    if (edgeIds.has(edge.id)) error('E_DUPLICATE_EDGE', `${path}.id`, 'Edge IDs must be unique; parallel edges keep distinct identities.');
    edgeIds.add(edge.id);
    if (!from || !to) error('E_EDGE_REFERENCE', path, 'An edge endpoint is unresolved.');
    if (!contract) error('E_EDGE_RELATION', `${path}.relation`, 'Unsupported typed edge relation; it has no declared endpoint contract.');
    else {
      if (from && contract.from !== '*' && !contract.from.includes(from.collection) || to && !contract.to.includes(to.collection)) error('E_EDGE_ENDPOINT_TYPE', path, `Invalid endpoint types/direction for ${edge.relation}.`);
      if (contract.roles && !contract.roles.includes(edge.role)) error('E_EDGE_ROLE', `${path}.role`, `Invalid role for ${edge.relation}.`);
    }
    if (!maps.sources.has(edge.source_id)) error('E_EDGE_SOURCE', `${path}.source_id`, 'Edge source metadata does not resolve.');
    for (const id of edge.scope) if (!maps.entities.has(id)) error('E_EDGE_SCOPE', `${path}.scope`, 'Edge scope must resolve to explicit entities.');
    if (edge.time !== null && !ns(edge.time)) error('E_TIME_FORMAT', `${path}.time`, 'Edge time must be exact decimal-string nanoseconds.');
  }
  for (const [i, check] of graph.admission_checks.entries()) {
    const path = `admission_checks[${i}]`, constraint = maps.constraints.get(check.constraint_id), field = maps.fields.get(check.field_id);
    if (!constraint?.condition) error('E_ADMISSION_CONDITION', path, 'Bounded fixture admission requires a declared condition.');
    if (constraint?.kind === 'legal') error('E_LEGAL_ADMISSION', path, 'The local fixture adapter cannot evaluate legal authorization.');
    if (constraint?.condition && field) {
      const { operator, expected, expected_unit } = constraint.condition;
      if (expected_unit !== field.unit) error('E_ADMISSION_UNIT', path, 'Fixture threshold unit must match the declared field unit.');
      if (operator === 'boolean_equals' && (field.value_type !== 'boolean' || typeof expected !== 'boolean') || operator !== 'boolean_equals' && (!['number', 'integer'].includes(field.value_type) || !Number.isFinite(expected))) error('E_ADMISSION_TYPE', path, 'Fixture check operator/threshold does not match the declared field type.');
    }
    for (const id of check.behavior_ids) if (maps.behaviors.has(id) && !maps.behaviors.get(id).constraint_ids.includes(check.constraint_id)) error('E_ADMISSION_SCOPE', path, 'Admission constraint is not attached to the gated behavior.');
  }
  for (const [i, command] of graph.commands.entries()) {
    const ids = command.composition.behavior_ids;
    for (const condition of command.composition.conditions) if (!ids.includes(condition.behavior_id)) error('E_CONDITIONAL_SCOPE', `commands[${i}].composition.conditions`, 'Condition names a behavior outside this command.');
    if (command.composition.mode === 'conditional' && ids.some(id => !command.composition.conditions.some(condition => condition.behavior_id === id))) error('E_CONDITIONAL_BINDING', `commands[${i}].composition`, 'Each conditional behavior needs an explicit predicate condition.');
    if (command.composition.mode !== 'conditional' && command.composition.conditions.length) error('E_CONDITIONAL_BINDING', `commands[${i}].composition`, 'Conditions require conditional composition mode.');
    for (const [order, id] of ids.entries()) for (const previous of maps.behaviors.get(id)?.after_behavior_ids || []) {
      if (!ids.includes(previous) || command.composition.mode === 'sequence' && ids.indexOf(previous) >= order) error('E_BEHAVIOR_ORDER', `commands[${i}].composition`, 'Completion dependency is missing or violates sequence order.');
    }
  }
  for (const [i, scenario] of graph.scenarios.entries()) {
    let previous = -1n;
    for (const step of scenario.admission_timeline) {
      if (BigInt(step.at_ns) < previous) error('E_TIME_ORDER', `scenarios[${i}].admission_timeline`, 'Fixture timeline must be ordered by exact time.');
      previous = BigInt(step.at_ns);
      for (const id of step.updated_fact_ids) if (!scenario.fact_ids.includes(id)) error('E_SCENARIO_BINDING', `scenarios[${i}].admission_timeline`, 'Updated fact must belong to this scenario’s authored evidence.');
    }
  }
  for (const [i, parameter] of graph.parameter_definitions.entries()) {
    if (!matchesValue(parameter.value, parameter.value_type)) error('E_PARAMETER_VALUE_TYPE', `parameter_definitions[${i}].value`, 'Parameter default does not match its declared type.', parameter.id);
    if (!UNITS.has(parameter.unit)) error('E_UNKNOWN_UNIT', `parameter_definitions[${i}].unit`, 'Parameter unit is outside the versioned contract.', parameter.id);
  }
  const behaviorDeps = new Map(graph.behaviors.map(behavior => [behavior.id, behavior.after_behavior_ids]));
  const behaviorChecked = new Set();
  const behaviorCycle = (id, stack = new Set()) => {
    if (stack.has(id)) return true;
    if (behaviorChecked.has(id)) return false;
    behaviorChecked.add(id);
    return (behaviorDeps.get(id) || []).some(child => behaviorCycle(child, new Set([...stack, id])));
  };
  for (const id of behaviorDeps.keys()) if (behaviorCycle(id)) { error('E_BEHAVIOR_CYCLE', 'behaviors', `Completion dependency cycle includes ${id}.`, id); break; }
  const deps = new Map([...graph.predicates.map(predicate => [predicate.id, new Set(predicate.subpredicate_ids)]), ...graph.events.map(event => [event.id, new Set()])]);
  for (const rule of graph.rules) {
    const ast = inspectDefinitionAST(graph, rule.id);
    for (const issue of ast.errors) error(issue.code, issue.path, issue.message, rule.id);
    const targets = [rule.output_predicate_id, rule.output_event_id].filter(Boolean);
    if (rule.kind !== 'policy_rule' && targets.length !== 1) error('E_DEFINITION_TARGET', `rules[${graph.rules.indexOf(rule)}]`, 'A definition rule must define exactly one predicate or event.', rule.id);
    if (rule.kind === 'policy_rule' && targets.length) error('E_DEFINITION_TARGET', `rules[${graph.rules.indexOf(rule)}]`, 'Dispatch policy rules do not define semantic truth.', rule.id);
    if (rule.output_event_id && ast.roots.definition_ast) {
      const root = ast.nodes.find(node => node.id === ast.roots.definition_ast);
      if (root && !['boolean','UNKNOWN'].includes(root.value_type)) error('E_EVENT_DEFINITION_TYPE', root.path, 'Event definition root must be Boolean or UNKNOWN.', rule.id);
    }
    for (const target of targets) for (const id of [...rule.subpredicate_ids, ...ast.dependencies]) if (maps.predicates.has(id) || maps.events.has(id)) deps.get(target)?.add(id);
    if (ast.requirements.some(id => !rule.state_field_ids.includes(id))) error('E_AST_REQUIREMENTS', `rules[${graph.rules.indexOf(rule)}].state_field_ids`, 'Definition AST reads a state field absent from its explicit requirements.', rule.id);
  }
  const cycle = (id, visiting = new Set(), visited = new Set()) => {
    if (visiting.has(id)) return true;
    if (visited.has(id)) return false;
    visited.add(id); const next = new Set(visiting); next.add(id);
    return [...(deps.get(id) || [])].some(child => cycle(child, next, visited));
  };
  for (const id of deps.keys()) if (cycle(id)) { error('E_PREDICATE_CYCLE', 'predicates', `Predicate dependency cycle includes ${id}.`, id); break; }
  for (const [i, source] of graph.sources.entries()) if (source.kind === 'legal_reference' && (!source.uri || !source.issuer || !source.effective_from || !source.version || !source.clause)) warn('W_LEGAL_SOURCE_INCOMPLETE', `sources[${i}]`, 'Legal placeholder lacks verified issuer, effective period, version, clause or primary-source URI. Permission remains UNKNOWN.', source.id);
}

function validateSpatialContracts(graph, maps, error, warn) {
  const windowValid = (window, path) => {
    if (BigInt(window.start_ns) >= BigInt(window.end_ns)) error('E_SPATIAL_TIME_WINDOW', path, 'Space-time windows must have start < end; intervals are half-open [start, end).');
  };
  const altitudeValid = (altitude, path) => {
    if (altitude.min_m > altitude.max_m) error('E_ALTITUDE_ENVELOPE', path, 'Minimum altitude cannot exceed maximum altitude.');
  };
  const withinWindow = (inner, outer) => BigInt(inner.start_ns) >= BigInt(outer.start_ns) && BigInt(inner.end_ns) <= BigInt(outer.end_ns);
  const withinAltitude = (inner, outer) => inner.min_m >= outer.min_m && inner.max_m <= outer.max_m;
  graph.spatial_zones.forEach((zone, i) => {
    const path = `spatial_zones[${i}]`, geometry = zone.geometry;
    altitudeValid(zone.altitude, `${path}.altitude`);
    zone.time_windows.forEach((window, index) => windowValid(window, `${path}.time_windows[${index}]`));
    if (geometry.frame === 'ENU' && (geometry.unit !== 'm' || geometry.axis_order !== 'east_north') || geometry.frame === 'WGS84' && (geometry.unit !== 'deg' || geometry.axis_order !== 'longitude_latitude')) error('E_SPATIAL_FRAME_UNIT', `${path}.geometry`, 'Geometry frame, axis order and unit do not agree.');
    if (geometry.frame === 'WGS84' && geometry.coordinates.some(([longitude, latitude]) => Math.abs(longitude) > 180 || Math.abs(latitude) > 90)) error('E_SPATIAL_COORDINATE', `${path}.geometry.coordinates`, 'WGS84 coordinates exceed longitude/latitude bounds.');
    if (geometry.geometry_type === 'polygon' && (geometry.coordinates.length < 4 || geometry.coordinates[0].some((value, axis) => value !== geometry.coordinates.at(-1)[axis]))) error('E_SPATIAL_POLYGON', `${path}.geometry.coordinates`, 'A polygon needs at least four coordinates with an explicit closing point.');
    if (zone.kind === 'no_fly_zone' && (zone.access !== 'prohibited' || zone.capacity_slots !== 0)) error('E_NO_FLY_ACCESS', path, 'No-fly zones must remain prohibited with zero allocation capacity.');
    if (zone.kind === 'corridor' && zone.capacity_slots < 1) error('E_CORRIDOR_CAPACITY', `${path}.capacity_slots`, 'A corridor requires positive declared capacity.');
    if (zone.access === 'approval_required' && !zone.authority_agent_ids.length) error('E_ZONE_AUTHORITY', `${path}.authority_agent_ids`, 'Approval-required space must name a declared authority agent.');
    if (maps.sources.get(zone.source_id)?.kind !== 'authored_fixture') warn('W_SPATIAL_SOURCE', `${path}.source_id`, 'Zone source is a declaration, not verified observed geometry.');
  });
  graph.lease_requests.forEach((request, i) => {
    const path = `lease_requests[${i}]`;
    windowValid(request.requested_window, `${path}.requested_window`); altitudeValid(request.requested_altitude, `${path}.requested_altitude`);
    if (BigInt(request.issued_at_ns) > BigInt(request.requested_window.start_ns)) error('E_LEASE_TIME_ORDER', path, 'Lease request must be issued before its requested window starts.');
    if (!request.entity_ids.length) error('E_LEASE_SCOPE', `${path}.entity_ids`, 'Lease request must name explicit entity instances.');
    const controlled = controls(maps.agents.get(request.agent_id));
    if (request.entity_ids.some(id => !controlled.has(id))) error('E_LEASE_REQUEST_SCOPE', `${path}.entity_ids`, 'Lease requester does not control all requested entities.');
  });
  graph.lease_approvals.forEach((approval, i) => {
    const path = `lease_approvals[${i}]`, request = maps.lease_requests.get(approval.request_id), zone = maps.spatial_zones.get(request?.zone_id);
    if (request && BigInt(approval.issued_at_ns) < BigInt(request.issued_at_ns)) error('E_LEASE_TIME_ORDER', path, 'Approval record predates its lease request.');
    if (zone && !zone.authority_agent_ids.includes(approval.authority_agent_id)) error('E_LEASE_AUTHORITY', `${path}.authority_agent_id`, 'Approver is not a declared authority for the requested zone.');
    if (approval.decision === 'approved') {
      if (!approval.approved_window || !approval.approved_altitude) error('E_LEASE_APPROVAL_SHAPE', path, 'Authored approval requires an explicit time and altitude envelope.');
      else {
        windowValid(approval.approved_window, `${path}.approved_window`); altitudeValid(approval.approved_altitude, `${path}.approved_altitude`);
        if (BigInt(approval.issued_at_ns) > BigInt(approval.approved_window.start_ns)) error('E_LEASE_TIME_ORDER', path, 'Approval must precede the approved time window.');
        if (request && (!withinWindow(approval.approved_window, request.requested_window) || !withinAltitude(approval.approved_altitude, request.requested_altitude))) error('E_LEASE_APPROVAL_ENVELOPE', path, 'Approval cannot expand the requested time or altitude envelope.');
        if (zone && (!zone.time_windows.some(window => withinWindow(approval.approved_window, window)) || !withinAltitude(approval.approved_altitude, zone.altitude))) error('E_LEASE_ZONE_ENVELOPE', path, 'Approval is outside the zone’s declared time/altitude envelope.');
      }
      if (zone?.access === 'prohibited' || zone?.kind === 'no_fly_zone') error('E_NO_FLY_ALLOCATION', path, 'A prohibited/no-fly zone cannot receive authored allocation approval.');
    } else if (approval.approved_window !== null || approval.approved_altitude !== null) error('E_LEASE_APPROVAL_SHAPE', path, 'Rejected/pending records cannot contain an approved envelope.');
    for (const id of approval.evidence_receipt_ids) {
      const receipt = maps.delivery_receipts.get(id);
      if (receipt && (receipt.status !== 'acknowledged' || receipt.received_at_ns === null || BigInt(receipt.received_at_ns) > BigInt(approval.issued_at_ns))) error('E_APPROVAL_RECEIPT', `${path}.evidence_receipt_ids`, 'Approval evidence must be an acknowledged fixture receipt available before the approval.');
    }
  });
  graph.space_time_allocations.forEach((allocation, i) => {
    const path = `space_time_allocations[${i}]`, approval = maps.lease_approvals.get(allocation.approval_id), request = maps.lease_requests.get(approval?.request_id), zone = maps.spatial_zones.get(allocation.zone_id);
    windowValid(allocation.window, `${path}.window`); altitudeValid(allocation.altitude, `${path}.altitude`);
    if (!allocation.entity_ids.length || allocation.slots < allocation.entity_ids.length) error('E_ALLOCATION_SCOPE', path, 'Allocation must name entities and reserve at least one slot per entity.');
    if (allocation.status === 'cancelled') return;
    if (!approval || approval.decision !== 'approved' || !approval.approved_window || !approval.approved_altitude) error('E_ALLOCATION_APPROVAL', `${path}.approval_id`, 'Active declared allocation requires an explicitly authored approved lease.');
    else if (!withinWindow(allocation.window, approval.approved_window) || !withinAltitude(allocation.altitude, approval.approved_altitude)) error('E_ALLOCATION_ENVELOPE', path, 'Allocation exceeds its approved time/altitude envelope.');
    if (request && (allocation.zone_id !== request.zone_id || allocation.entity_ids.some(id => !request.entity_ids.includes(id)) || allocation.direction !== request.direction)) error('E_ALLOCATION_REQUEST_SCOPE', path, 'Allocation zone, entities or direction differ from its request.');
    if (zone && (zone.access === 'prohibited' || zone.kind === 'no_fly_zone')) error('E_NO_FLY_ALLOCATION', path, 'No-fly/prohibited zones cannot be allocated.');
    if (zone && zone.direction !== 'bidirectional' && zone.direction !== allocation.direction) error('E_ALLOCATION_DIRECTION', path, 'Allocation direction is incompatible with the corridor.');
  });
  for (const zone of graph.spatial_zones) {
    const changes = graph.space_time_allocations.filter(allocation => allocation.zone_id === zone.id && allocation.status === 'declared').flatMap(allocation => [{ time: BigInt(allocation.window.start_ns), delta: allocation.slots }, { time: BigInt(allocation.window.end_ns), delta: -allocation.slots }]).sort((a, b) => a.time < b.time ? -1 : a.time > b.time ? 1 : a.delta - b.delta);
    let demand = 0;
    for (const change of changes) { demand += change.delta; if (demand > zone.capacity_slots) { error('E_SPACE_TIME_OVERCOMMIT', 'space_time_allocations', `Overlapping time allocations exceed ${zone.id} capacity ${zone.capacity_slots}; no geometric separation is assumed.`, zone.id); break; } }
  }
  graph.delivery_receipts.forEach((receipt, i) => {
    const path = `delivery_receipts[${i}]`, command = maps.commands.get(receipt.command_id);
    if (command && command.task_id !== receipt.task_id) error('E_RECEIPT_BINDING', path, 'Receipt command and task must resolve to the same authored task.');
    if (receipt.status === 'acknowledged' && receipt.received_at_ns === null) error('E_RECEIPT_TIME', path, 'Acknowledged fixture receipt needs an exact received time.');
    if (receipt.received_at_ns !== null && BigInt(receipt.received_at_ns) < BigInt(receipt.emitted_at_ns)) error('E_RECEIPT_TIME', path, 'Receipt cannot arrive before its declared emission.');
    if (maps.sources.get(receipt.source_id)?.kind !== 'authored_fixture') error('E_RECEIPT_SOURCE', `${path}.source_id`, 'Receipt must explicitly use authored fixture provenance.');
  });
  graph.behaviors.forEach((behavior, i) => {
    for (const id of behavior.required_allocation_ids || []) {
      const allocation = maps.space_time_allocations.get(id);
      if (allocation && (allocation.status !== 'declared' || behavior.entity_ids.some(id => !allocation.entity_ids.includes(id)))) error('E_BEHAVIOR_ALLOCATION_SCOPE', `behaviors[${i}].required_allocation_ids`, 'Required allocation does not cover the active behavior entities.');
    }
  });
  if (graph.spatial_zones.length || graph.lease_approvals.length || graph.delivery_receipts.length) warn('W_SPATIAL_FIXTURE_ONLY', 'spatial_zones', 'Space-time contracts and receipts are authored fixtures. Geometry intersection, flight safety, actual corridor authority, delivery success and legal permission are not executed or verified.');
}

/** Canonical implemented inventory for coverage reports; not an external/private catalog. */
export function graphContractInventory(graph = DEFAULT_GRAPH) {
  const nodes = graphNodes(graph);
  return {
    schema_version: GRAPH_SCHEMA_VERSION,
    inventory_scope: 'confirmed_local_contract_only',
    collections: [...GRAPH_COLLECTIONS, ...GRAPH_DERIVED_COLLECTIONS].map(collection => ({ ...collection, support: 'supported', schema_definition: GRAPH_SCHEMA.properties[collection.key]?.items.$ref || '#/$defs/expression (derived readonly projection)', instance_ids: nodes.filter(node => node.collection === collection.key).map(node => node.id) })),
    node_types: list(graph?.node_types).filter(object).map(type => ({ id: type.id, collection: type.collection, support: 'supported' })),
    entity_types: list(graph?.entity_types).filter(object).map(type => ({ id: type.id, field_ids: [...list(type.field_ids)], support: 'supported' })),
    relations: Object.entries(GRAPH_EDGE_CONTRACTS).map(([relation, contract]) => ({ relation, ...copy(contract), support: 'supported', execution: 'definition_or_fixture_binding' })),
    definition_operators: SUPPORTED_DEFINITION_OPERATORS.map(operator => ({ operator, support: 'supported', execution: 'inspection_only' })),
    runtime: [
      { capability: 'schema_and_reference_validation', support: 'supported' },
      { capability: 'fact_time_source_resolution', support: 'supported' },
      { capability: 'bounded_fixture_admission', support: 'supported' },
      { capability: 'configured_fixture_feedback', support: 'supported' },
      { capability: 'native_predicate_execution', support: 'unrepresentable', reason: 'Native engine/catalog is not connected.' },
      { capability: 'real_module_or_actuator_execution', support: 'unrepresentable', reason: 'Only declared fixture bindings are present.' },
      { capability: 'spatial_collision_or_no_fly_intersection', support: 'unrepresentable', reason: 'Geometry is declarative; no spatial solver is implemented.' },
      { capability: 'legal_permission_or_real_lease_approval', support: 'unrepresentable', reason: 'Authoring metadata does not establish actual authority.' },
      { capability: 'real_network_delivery_or_cargo_success', support: 'unrepresentable', reason: 'Fixture receipt records are not observed delivery proof.' },
    ],
    coverage_policy: { statuses: ['supported', 'unrepresentable', 'not_applicable'], target: 'Every confirmed inventory item must have a concrete example reference or an explicit justified status.', private_catalog_completeness_claim: false },
  };
}
