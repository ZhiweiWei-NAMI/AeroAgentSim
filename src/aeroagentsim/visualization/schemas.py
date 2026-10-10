from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


CoordinateMode = Literal["simulation_plane", "geo_osm"]
DefinitionSource = Literal["builtin", "custom"]
RegistryKind = Literal["agents", "tasks", "workflows"]


class LocalizedText(BaseModel):
    zh_CN: Optional[str] = None
    en_US: Optional[str] = None

    def resolve(self, locale: str = "en-US", fallback: str = "") -> str:
        normalized = str(locale or "en-US").lower()
        if normalized == "zh-cn":
            return self.zh_CN or self.en_US or fallback
        return self.en_US or self.zh_CN or fallback


class TemplateField(BaseModel):
    key: str
    value_type: str = "any"
    required: bool = False
    default: Any = None
    description: Optional[str] = None


class RegistryReference(BaseModel):
    kind: RegistryKind
    definition_id: str
    version: Optional[str] = None
    source: DefinitionSource = "custom"
    adapter_type: Optional[str] = None


class RegistryDefinitionMeta(BaseModel):
    id: str
    version: str = "1.0.0"
    schema_version: str = "2026-03-27"
    source: DefinitionSource = "custom"
    display_name: LocalizedText = Field(default_factory=LocalizedText)
    description: LocalizedText = Field(default_factory=LocalizedText)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    enabled: bool = True


class AgentDefinition(BaseModel):
    id: str
    name: str
    description: str
    state_templates: Dict[str, TemplateField] = Field(default_factory=dict)
    compatible_components: List[str] = Field(default_factory=list)
    source: DefinitionSource = "builtin"
    version: str = "builtin"
    display_name: Optional[LocalizedText] = None
    base_agent_type: Optional[str] = None


class ComponentDefinition(BaseModel):
    id: str
    name: str
    description: str
    produced_metrics: List[str] = Field(default_factory=list)
    monitored_states: List[str] = Field(default_factory=list)
    source: DefinitionSource = "builtin"
    version: str = "builtin"


class TaskDefinition(BaseModel):
    id: str
    name: str
    description: str
    necessary_metrics: List[str] = Field(default_factory=list)
    produced_states: List[str] = Field(default_factory=list)
    source: DefinitionSource = "builtin"
    version: str = "builtin"
    display_name: Optional[LocalizedText] = None
    adapter_type: Optional[str] = None
    component: Optional[str] = None
    parameter_schema: Dict[str, TemplateField] = Field(default_factory=dict)
    target_state_schema: Dict[str, TemplateField] = Field(default_factory=dict)


class TriggerCondition(BaseModel):
    source_state: str
    target_state: str
    trigger_type: str
    source_ref: Optional[str] = None
    operator: Optional[str] = None
    description: Optional[str] = None
    target_value: Any = None
    config: Dict[str, Any] = Field(default_factory=dict)


class TaskBinding(BaseModel):
    workflow_state: str
    component: Optional[str] = None
    task_class: Optional[str] = None
    task_name: Optional[str] = None
    task_ref: Optional[RegistryReference] = None
    produced_states: List[str] = Field(default_factory=list)
    target_state: Dict[str, Any] = Field(default_factory=dict)
    properties: Dict[str, Any] = Field(default_factory=dict)
    binding_status: Optional[str] = None


class WorkflowTypeDefinition(BaseModel):
    id: str
    name: str
    description: str
    property_templates: Dict[str, TemplateField] = Field(default_factory=dict)
    states: List[str] = Field(default_factory=list)
    start_state: Optional[str] = None
    trigger_conditions: List[TriggerCondition] = Field(default_factory=list)
    task_bindings: List[TaskBinding] = Field(default_factory=list)
    critical_path: List[str] = Field(default_factory=list)
    source: DefinitionSource = "builtin"
    version: str = "builtin"
    display_name: Optional[LocalizedText] = None
    adapter_type: Optional[str] = None
    supported_agent_types: List[str] = Field(default_factory=list)
    registry_dependencies: List[RegistryReference] = Field(default_factory=list)


class CustomAgentDefinition(RegistryDefinitionMeta):
    kind: RegistryKind = "agents"
    base_agent_type: str = "DroneAgent"
    state_templates: Dict[str, TemplateField] = Field(default_factory=dict)
    default_properties: Dict[str, Any] = Field(default_factory=dict)
    allowed_components: List[str] = Field(default_factory=list)
    initialization_schema: Dict[str, TemplateField] = Field(default_factory=dict)


class CustomTaskDefinition(RegistryDefinitionMeta):
    kind: RegistryKind = "tasks"
    necessary_metrics: List[str] = Field(default_factory=list)
    produced_states: List[str] = Field(default_factory=list)
    adapter_type: str = ""
    component: Optional[str] = None
    target_state_schema: Dict[str, TemplateField] = Field(default_factory=dict)
    parameter_schema: Dict[str, TemplateField] = Field(default_factory=dict)


class CustomWorkflowDefinition(RegistryDefinitionMeta):
    kind: RegistryKind = "workflows"
    adapter_type: str = ""
    property_templates: Dict[str, TemplateField] = Field(default_factory=dict)
    states: List[str] = Field(default_factory=list)
    start_state: Optional[str] = None
    trigger_conditions: List[TriggerCondition] = Field(default_factory=list)
    task_bindings: List[TaskBinding] = Field(default_factory=list)
    critical_path: List[str] = Field(default_factory=list)
    supported_agent_types: List[str] = Field(default_factory=list)
    registry_dependencies: List[RegistryReference] = Field(default_factory=list)


class CatalogCompatibility(BaseModel):
    agent_components: Dict[str, List[str]] = Field(default_factory=dict)
    component_tasks: Dict[str, List[str]] = Field(default_factory=dict)
    workflow_agents: Dict[str, List[str]] = Field(default_factory=dict)
    workflow_tasks: Dict[str, List[str]] = Field(default_factory=dict)


class AgentInstance(BaseModel):
    id: str
    name: str
    type: str
    initial_position: List[float] = Field(default_factory=lambda: [10.0, 10.0, 0.0])
    initial_battery: float = 100.0
    components: List[str] = Field(default_factory=list)
    properties: Dict[str, Any] = Field(default_factory=dict)
    source: DefinitionSource = "builtin"
    version: Optional[str] = None
    definition_ref: Optional[RegistryReference] = None


class WorkflowDefinition(BaseModel):
    id: str
    name: str
    type: str
    agent_id: str
    properties: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    source: DefinitionSource = "builtin"
    version: Optional[str] = None
    definition_ref: Optional[RegistryReference] = None


class ConfigSnapshot(BaseModel):
    config_id: Optional[str] = None
    source_config_id: Optional[str] = None
    name: str = "AeroAgentSim Config"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    coordinate_mode: CoordinateMode = "simulation_plane"
    airspaces: List[Dict[str, Any]] = Field(default_factory=list)
    frequencies: List[Dict[str, Any]] = Field(default_factory=list)
    landing_spots: List[Dict[str, Any]] = Field(default_factory=list)
    traffic: Dict[str, Any] = Field(default_factory=dict)
    agents: List[AgentInstance] = Field(default_factory=list)
    workflows: List[WorkflowDefinition] = Field(default_factory=list)
    simulation_time: float = 0.0
    simulation_speed: float = 1.0
    validation_warnings: List[str] = Field(default_factory=list)
    registry_references: List[RegistryReference] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    level: Literal["error", "warning"] = "error"
    category: str = "validation"
    message: str
    path: Optional[str] = None


class ConfigValidationResult(BaseModel):
    is_valid: bool
    valid: Optional[bool] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    issues: List[ValidationIssue] = Field(default_factory=list)
    missing_dependencies: List[str] = Field(default_factory=list)
    compatibility_gaps: List[str] = Field(default_factory=list)
    unresolved_proxies: List[str] = Field(default_factory=list)
    version_conflicts: List[str] = Field(default_factory=list)
    graph_warnings: List[str] = Field(default_factory=list)


class PreflightCheck(BaseModel):
    name: str
    status: Literal["pass", "warning", "error", "skipped"] = "pass"
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class PreflightResult(BaseModel):
    is_ready: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    checks: List[PreflightCheck] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    label: str
    kind: str
    group: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    kind: str
    label: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowGraph(BaseModel):
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    trigger_conditions: List[TriggerCondition] = Field(default_factory=list)
    task_bindings: List[TaskBinding] = Field(default_factory=list)
    critical_path: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class SpatialAgentSnapshot(BaseModel):
    agent_id: str
    agent_type: str
    position: List[float]
    display_position: List[float]
    altitude: float = 0.0
    status: str = "unknown"
    current_workflow: Optional[str] = None
    current_task: Optional[str] = None
    current_task_id: Optional[str] = None
    color: str = "#2f6fed"
    recent_log: Optional[str] = None


class SpatialSnapshot(BaseModel):
    run_id: Optional[str] = None
    timestamp: float
    coordinate_mode: CoordinateMode = "simulation_plane"
    bounds: Dict[str, float] = Field(default_factory=dict)
    agents: List[SpatialAgentSnapshot] = Field(default_factory=list)


class RunManifest(BaseModel):
    run_id: str
    config_id: str
    config_name: str
    coordinate_mode: CoordinateMode = "simulation_plane"
    status: str = "created"
    created_at: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    updated_at: Optional[str] = None
    output_dir: str
    latest_sim_time: float = 0.0
    latest_speed: float = 1.0
    simulation_time: float = 0.0
    registry_references: List[RegistryReference] = Field(default_factory=list)


class RunStartRequest(BaseModel):
    config_id: Optional[str] = None


class HealthResponse(BaseModel):
    backend_available: bool = True
    db_writable: bool = False
    simulation_status: str = "STOPPED"
    active_run_id: Optional[str] = None
    recent_startup_error: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = None
