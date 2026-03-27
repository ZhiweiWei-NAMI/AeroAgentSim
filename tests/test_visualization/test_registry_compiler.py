from airfogsim.visualization.catalog_service import CatalogService
from airfogsim.visualization.config_compiler import ConfigCompiler
from airfogsim.visualization.registry_service import RegistryService
from airfogsim.visualization.schemas import ConfigSnapshot


def test_custom_registry_definitions_are_collected_into_run_references(tmp_path):
    registry_service = RegistryService(
        base_dir=str(tmp_path / "registry" / "aeroagentsim"),
        runtime_dir=str(tmp_path / "runtime" / "aeroagentsim"),
    )

    registry_service.save_definition(
        "agents",
        {
            "id": "survey_proxy",
            "version": "1.0.0",
            "display_name": {"en_US": "Survey Proxy"},
            "description": {"en_US": "Custom survey agent"},
            "base_agent_type": "DroneAgent",
            "allowed_components": ["MoveToComponent"],
            "state_templates": {
                "target_reached": {
                    "key": "target_reached",
                    "value_type": "bool",
                    "required": False,
                    "default": False,
                }
            },
            "default_properties": {"target_reached": False},
        },
    )
    registry_service.save_definition(
        "tasks",
        {
            "id": "survey_move",
            "version": "1.0.0",
            "display_name": {"en_US": "Survey Move"},
            "description": {"en_US": "Custom move task"},
            "adapter_type": "declarative",
            "component": "MoveToComponent",
            "produced_states": ["target_reached"],
            "necessary_metrics": [],
            "parameter_schema": {},
            "target_state_schema": {},
        },
    )
    registry_service.save_definition(
        "workflows",
        {
            "id": "survey_flow",
            "version": "1.0.0",
            "display_name": {"en_US": "Survey Flow"},
            "description": {"en_US": "Custom survey workflow"},
            "adapter_type": "inspection",
            "states": ["idle", "moving"],
            "start_state": "idle",
            "property_templates": {},
            "trigger_conditions": [
                {
                    "source_state": "*",
                    "target_state": "moving",
                    "trigger_type": "event",
                    "source_ref": "start",
                }
            ],
            "task_bindings": [
                {
                    "workflow_state": "moving",
                    "component": "MoveToComponent",
                    "task_ref": {
                        "kind": "tasks",
                        "definition_id": "survey_move",
                        "version": "1.0.0",
                        "source": "custom",
                    },
                    "target_state": {"target_reached": True},
                }
            ],
            "critical_path": ["wfstate:survey_run:moving"],
            "supported_agent_types": ["survey_proxy"],
        },
    )

    compiler = ConfigCompiler(CatalogService(registry_service=registry_service))
    snapshot = ConfigSnapshot(
        config_id="demo",
        agents=[
            {
                "id": "agent_1",
                "name": "Agent 1",
                "type": "survey_proxy",
                "components": ["MoveToComponent"],
                "source": "custom",
                "definition_ref": {
                    "kind": "agents",
                    "definition_id": "survey_proxy",
                    "version": "1.0.0",
                    "source": "custom",
                },
            }
        ],
        workflows=[
            {
                "id": "survey_run",
                "name": "Survey Run",
                "type": "survey_flow",
                "agent_id": "agent_1",
                "source": "custom",
                "definition_ref": {
                    "kind": "workflows",
                    "definition_id": "survey_flow",
                    "version": "1.0.0",
                    "source": "custom",
                },
                "properties": {},
            }
        ],
    )

    validation = compiler.validate_snapshot(snapshot)
    references = compiler.collect_registry_references(snapshot)

    assert validation.is_valid is True
    assert {(item.kind, item.definition_id) for item in references} == {
        ("agents", "survey_proxy"),
        ("workflows", "survey_flow"),
        ("tasks", "survey_move"),
    }
