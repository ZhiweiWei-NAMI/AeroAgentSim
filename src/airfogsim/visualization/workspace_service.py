from __future__ import annotations

from typing import Any, Dict, List

from .catalog_service import CatalogService
from .config_compiler import ConfigCompiler
from .config_repository import ConfigRepository
from .setup import preflight_runtime_config
from .schemas import (
    ConfigSnapshot,
    ConfigValidationResult,
    GraphEdge,
    GraphNode,
    PreflightResult,
    TaskBinding,
    TriggerCondition,
    ValidationIssue,
    WorkflowDefinition,
    WorkflowGraph,
)


def _model_dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


class WorkspaceService:
    def __init__(
        self,
        catalog_service: CatalogService,
        config_repository: ConfigRepository,
        config_compiler: ConfigCompiler,
    ) -> None:
        self.catalog_service = catalog_service
        self.config_repository = config_repository
        self.config_compiler = config_compiler

    def get_snapshot(self, config_id: str) -> ConfigSnapshot:
        if not self.config_repository.has_snapshots():
            return self._bootstrap_default_snapshot()
        try:
            return self.config_repository.get_snapshot(config_id)
        except FileNotFoundError:
            if config_id in {"current", "latest", "default"}:
                return self._bootstrap_default_snapshot()
            raise

    def save_snapshot(self, base_config_id: str, snapshot: ConfigSnapshot) -> ConfigSnapshot:
        if not snapshot.agents and not snapshot.workflows and base_config_id in {"current", "latest"}:
            current = self.get_snapshot(base_config_id)
            payload = _model_dump(current)
        else:
            payload = _model_dump(snapshot)

        payload["source_config_id"] = self._resolve_source_config_id(base_config_id, payload)
        candidate = ConfigSnapshot(**payload)
        candidate.registry_references = self.config_compiler.collect_registry_references(candidate)
        validation = self.validate_draft(candidate)
        candidate.validation_warnings = validation.warnings
        return self.config_repository.save_snapshot(candidate)

    def validate_snapshot(self, config_id: str) -> ConfigValidationResult:
        snapshot = self.get_snapshot(config_id)
        return self.validate_draft(snapshot)

    def validate_draft(self, snapshot: ConfigSnapshot) -> ConfigValidationResult:
        result = self.config_compiler.validate_snapshot(snapshot)
        graph = self.build_graph_for_snapshot(snapshot)
        result.graph_warnings = list(graph.warnings)
        for warning in graph.warnings:
            result.warnings.append(warning)
            result.issues.append(
                ValidationIssue(
                    level="warning",
                    category="graph",
                    message=warning,
                )
            )
        return result

    def compile_snapshot(self, config_id: str) -> Dict[str, Any]:
        snapshot = self.get_snapshot(config_id)
        return self.config_compiler.compile_snapshot(snapshot)

    def preflight_snapshot(self, config_id: str) -> PreflightResult:
        compiled = self.compile_snapshot(config_id)
        return preflight_runtime_config(compiled)

    def preflight_draft(self, snapshot: ConfigSnapshot) -> PreflightResult:
        compiled = self.config_compiler.compile_snapshot(snapshot)
        return preflight_runtime_config(compiled)

    def build_graph(self, config_id: str) -> WorkflowGraph:
        snapshot = self.get_snapshot(config_id)
        return self.build_graph_for_snapshot(snapshot)

    def build_graph_for_snapshot(self, snapshot: ConfigSnapshot) -> WorkflowGraph:
        component_catalog = {
            component.name: component for component in self.catalog_service.get_components(scope="builtin")
        }

        nodes: Dict[str, GraphNode] = {}
        edges: Dict[str, GraphEdge] = {}
        warnings: List[str] = []
        trigger_conditions: List[TriggerCondition] = []
        task_bindings: List[TaskBinding] = []
        critical_path: List[str] = []

        def add_node(node: GraphNode) -> None:
            nodes[node.id] = node

        def add_edge(edge: GraphEdge) -> None:
            edges[edge.id] = edge

        for agent in snapshot.agents:
            agent_node_id = f"agent:{agent.id}"
            add_node(
                GraphNode(
                    id=agent_node_id,
                    label=agent.name,
                    kind="agent",
                    group=agent.type,
                    metadata={
                        "agent_id": agent.id,
                        "agent_type": agent.type,
                        "source": agent.source,
                        "version": agent.version,
                    },
                )
            )

            agent_definition = self.catalog_service.get_agent_definition(
                agent.type,
                source=agent.definition_ref.source if agent.definition_ref else agent.source,
                version=agent.definition_ref.version if agent.definition_ref else agent.version,
            )
            if not agent_definition:
                warnings.append(f"Unknown agent type in graph build: {agent.type}")
                continue

            state_node_ids: Dict[str, str] = {}
            for state_name, template in agent_definition.state_templates.items():
                state_node_id = f"state:{agent.id}:{state_name}"
                state_node_ids[state_name] = state_node_id
                add_node(
                    GraphNode(
                        id=state_node_id,
                        label=state_name,
                        kind="agent_state",
                        group=agent.id,
                        metadata={
                            "agent_id": agent.id,
                            "required": template.required,
                            "value_type": template.value_type,
                            "description": template.description,
                            "source": agent_definition.source,
                            "version": agent_definition.version,
                        },
                    )
                )
                add_edge(
                    GraphEdge(
                        id=f"edge:{agent_node_id}:{state_node_id}:owns",
                        source=agent_node_id,
                        target=state_node_id,
                        kind="agent owns state",
                        label="agent owns state",
                    )
                )

            for component_name in agent.components:
                component = component_catalog.get(component_name)
                if not component:
                    warnings.append(f"Agent {agent.id} references unknown component {component_name}.")
                    continue
                component_node_id = f"component:{agent.id}:{component_name}"
                add_node(
                    GraphNode(
                        id=component_node_id,
                        label=component_name,
                        kind="component",
                        group=agent.id,
                        metadata={
                            "agent_id": agent.id,
                            "produced_metrics": component.produced_metrics,
                            "source": component.source,
                            "version": component.version,
                        },
                    )
                )
                for state_name in component.monitored_states:
                    if state_name not in state_node_ids:
                        continue
                    add_edge(
                        GraphEdge(
                            id=f"edge:{component_node_id}:{state_node_ids[state_name]}:monitors",
                            source=component_node_id,
                            target=state_node_ids[state_name],
                            kind="component monitors state",
                            label="component monitors state",
                        )
                    )

        agent_by_id = {agent.id: agent for agent in snapshot.agents}
        for workflow in snapshot.workflows:
            if not workflow.enabled:
                continue

            workflow_node_id = f"workflow:{workflow.id}"
            definition = self.catalog_service.get_workflow_definition(
                workflow.type,
                source=workflow.definition_ref.source if workflow.definition_ref else workflow.source,
                version=workflow.definition_ref.version if workflow.definition_ref else workflow.version,
            )
            add_node(
                GraphNode(
                    id=workflow_node_id,
                    label=workflow.name,
                    kind="workflow",
                    group=workflow.type,
                    metadata={
                        "workflow_id": workflow.id,
                        "agent_id": workflow.agent_id,
                        "source": workflow.source,
                        "version": workflow.version,
                        "binding_status": "resolved" if definition else "unresolved",
                    },
                )
            )

            if not definition:
                warnings.append(f"Workflow {workflow.id} has unknown type {workflow.type}.")
                continue

            agent = agent_by_id.get(workflow.agent_id)
            state_ids = {state: f"wfstate:{workflow.id}:{state}" for state in definition.states}

            for state_name, state_node_id in state_ids.items():
                add_node(
                    GraphNode(
                        id=state_node_id,
                        label=state_name,
                        kind="workflow_state",
                        group=workflow.id,
                        metadata={
                            "workflow_id": workflow.id,
                            "source": definition.source,
                            "version": definition.version,
                        },
                    )
                )

            for condition in definition.trigger_conditions:
                source_node = state_ids.get(condition.source_state)
                target_node = state_ids.get(condition.target_state)
                if condition.source_state == "*":
                    source_node = workflow_node_id
                if not source_node or not target_node:
                    warnings.append(
                        f"Workflow {workflow.id} has unresolved transition {condition.source_state} -> {condition.target_state}."
                    )
                    continue
                add_edge(
                    GraphEdge(
                        id=f"edge:{source_node}:{target_node}:{condition.trigger_type}",
                        source=source_node,
                        target=target_node,
                        kind="trigger causes transition",
                        label="trigger causes transition",
                        metadata={
                            "trigger_type": condition.trigger_type,
                            "source_ref": condition.source_ref,
                            "operator": condition.operator,
                            "description": condition.description,
                            "source": definition.source,
                            "version": definition.version,
                        },
                    )
                )
                trigger_conditions.append(
                    TriggerCondition(
                        source_state=source_node,
                        target_state=target_node,
                        trigger_type=condition.trigger_type,
                        source_ref=condition.source_ref,
                        operator=condition.operator,
                        description=condition.description,
                        target_value=condition.target_value,
                        config=dict(condition.config),
                    )
                )

            for binding in definition.task_bindings:
                binding_name = binding.task_ref.definition_id if binding.task_ref else (binding.task_class or binding.task_name or "task")
                task_node_id = f"task:{workflow.id}:{binding_name}"
                task_definition = self.catalog_service.get_task_definition(
                    binding_name,
                    source=binding.task_ref.source if binding.task_ref else None,
                    version=binding.task_ref.version if binding.task_ref else None,
                )
                add_node(
                    GraphNode(
                        id=task_node_id,
                        label=binding.task_name or binding_name,
                        kind="task",
                        group=workflow.id,
                        metadata={
                            "task_class": binding.task_class,
                            "component": binding.component,
                            "source": getattr(task_definition, "source", None),
                            "version": getattr(task_definition, "version", None),
                            "binding_status": "resolved" if task_definition else "unresolved",
                            "task_ref": _model_dump(binding.task_ref) if binding.task_ref else None,
                        },
                    )
                )
                add_edge(
                    GraphEdge(
                        id=f"edge:{workflow_node_id}:{task_node_id}:suggests",
                        source=workflow_node_id,
                        target=task_node_id,
                        kind="workflow suggests task",
                        label="workflow suggests task",
                        metadata={
                            "workflow_state": binding.workflow_state,
                            "binding_status": "resolved" if task_definition else "unresolved",
                            "source": getattr(task_definition, "source", None),
                            "version": getattr(task_definition, "version", None),
                        },
                    )
                )

                if agent:
                    produced_states = getattr(task_definition, "produced_states", binding.produced_states)
                    for produced_state in produced_states:
                        state_node_id = f"state:{agent.id}:{produced_state}"
                        if state_node_id not in nodes:
                            warnings.append(
                                f"Workflow {workflow.id} binding {binding_name} produces unresolved state {produced_state}."
                            )
                            continue
                        add_edge(
                            GraphEdge(
                                id=f"edge:{task_node_id}:{state_node_id}:produces",
                                source=task_node_id,
                                target=state_node_id,
                                kind="task produces state",
                                label="task produces state",
                                metadata={
                                    "binding_status": "resolved" if task_definition else "unresolved",
                                    "source": getattr(task_definition, "source", None),
                                    "version": getattr(task_definition, "version", None),
                                },
                            )
                        )

                task_bindings.append(
                    TaskBinding(
                        workflow_state=state_ids.get(binding.workflow_state, binding.workflow_state),
                        component=binding.component,
                        task_class=binding.task_class or getattr(task_definition, "name", None),
                        task_name=binding.task_name or binding_name,
                        task_ref=binding.task_ref,
                        produced_states=list(getattr(task_definition, "produced_states", binding.produced_states)),
                        target_state=dict(binding.target_state),
                        properties=dict(binding.properties),
                        binding_status="resolved" if task_definition else "unresolved",
                    )
                )

            critical_path.extend(
                state_ids[state_name]
                for state_name in definition.critical_path
                if state_name in state_ids
            )

        return WorkflowGraph(
            nodes=list(nodes.values()),
            edges=list(edges.values()),
            trigger_conditions=trigger_conditions,
            task_bindings=task_bindings,
            critical_path=critical_path,
            warnings=warnings,
        )

    def _bootstrap_default_snapshot(self) -> ConfigSnapshot:
        snapshot = ConfigSnapshot(
            name="AeroAgentSim Starter Config",
            coordinate_mode="simulation_plane",
            agents=[
                {
                    "id": "drone_alpha",
                    "name": "Drone Alpha",
                    "type": "DroneAgent",
                    "initial_position": [10, 10, 20],
                    "initial_battery": 92,
                    "components": ["MoveToComponent", "ChargingComponent"],
                    "properties": {},
                }
            ],
            workflows=[
                WorkflowDefinition(
                    id="inspection_alpha",
                    name="Inspection Alpha",
                    type="inspection",
                    agent_id="drone_alpha",
                    properties={
                        "inspection_points": [
                            [10, 10, 20],
                            [150, 60, 40],
                            [220, 180, 50],
                        ]
                    },
                )
            ],
        )
        snapshot.registry_references = self.config_compiler.collect_registry_references(snapshot)
        return self.config_repository.save_snapshot(snapshot)

    def _resolve_source_config_id(self, base_config_id: str, payload: Dict[str, Any]) -> str:
        if payload.get("source_config_id"):
            return payload["source_config_id"]
        try:
            return self.config_repository.resolve_config_id(base_config_id)
        except FileNotFoundError:
            return "bootstrap"
