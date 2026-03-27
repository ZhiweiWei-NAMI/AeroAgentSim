from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .catalog_service import CatalogService
from .schemas import (
    AgentDefinition,
    AgentInstance,
    ConfigSnapshot,
    ConfigValidationResult,
    RegistryReference,
    TaskBinding,
    TaskDefinition,
    ValidationIssue,
    WorkflowDefinition,
    WorkflowTypeDefinition,
)
from .type_utils import normalize_workflow_type


def _model_dump(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


class ConfigCompiler:
    def __init__(self, catalog_service: CatalogService) -> None:
        self.catalog_service = catalog_service

    def validate_snapshot(self, snapshot: ConfigSnapshot) -> ConfigValidationResult:
        issues: List[ValidationIssue] = []
        missing_dependencies: List[str] = []
        compatibility_gaps: List[str] = []
        unresolved_proxies: List[str] = []

        components = {component.name: component for component in self.catalog_service.get_components()}
        agent_ids: Set[str] = set()
        resolved_agents: Dict[str, AgentDefinition] = {}
        agent_components_by_id: Dict[str, Set[str]] = {}

        for index, agent in enumerate(snapshot.agents):
            path = f"agents[{index}]"
            if agent.id in agent_ids:
                issues.append(self._issue("error", "duplicate", f"Duplicate agent id: {agent.id}", path))
            agent_ids.add(agent.id)

            agent_definition = self._resolve_agent_definition(agent)
            if agent_definition is None:
                issues.append(
                    self._issue(
                        "error",
                        "registry",
                        f"Unknown agent definition: {agent.type}",
                        path,
                    )
                )
                continue
            resolved_agents[agent.id] = agent_definition
            agent_components_by_id[agent.id] = set(agent.components)

            compatible_components = set(agent_definition.compatible_components)
            for component_name in agent.components:
                if component_name not in components:
                    issues.append(
                        self._issue(
                            "error",
                            "compatibility",
                            f"Agent {agent.id} references unknown component {component_name}.",
                            f"{path}.components",
                        )
                    )
                    compatibility_gaps.append(f"{agent.id}:{component_name}")
                    continue
                if compatible_components and component_name not in compatible_components:
                    issues.append(
                        self._issue(
                            "warning",
                            "compatibility",
                            (
                                f"Agent {agent.id} selects component {component_name}, which is not "
                                f"declared compatible with {agent.type}."
                            ),
                            f"{path}.components",
                        )
                    )

        workflow_ids: Set[str] = set()
        for index, workflow in enumerate(snapshot.workflows):
            path = f"workflows[{index}]"
            if workflow.id in workflow_ids:
                issues.append(self._issue("error", "duplicate", f"Duplicate workflow id: {workflow.id}", path))
            workflow_ids.add(workflow.id)

            workflow_definition = self._resolve_workflow_definition(workflow)
            if workflow_definition is None:
                issues.append(
                    self._issue(
                        "error",
                        "registry",
                        f"Unknown workflow definition: {workflow.type}",
                        path,
                    )
                )
                continue

            agent_definition = resolved_agents.get(workflow.agent_id)
            if workflow.agent_id not in agent_ids:
                issues.append(
                    self._issue(
                        "error",
                        "binding",
                        f"Workflow {workflow.id} references missing agent {workflow.agent_id}.",
                        f"{path}.agent_id",
                    )
                )
                continue

            for key, template in workflow_definition.property_templates.items():
                if template.required and key not in workflow.properties:
                    issues.append(
                        self._issue(
                            "error",
                            "schema",
                            f"Workflow {workflow.id} is missing required property {key}.",
                            f"{path}.properties.{key}",
                        )
                    )

            try:
                self._validate_workflow_bindings(
                    workflow=workflow,
                    workflow_definition=workflow_definition,
                    agent_definition=agent_definition,
                    issues=issues,
                    missing_dependencies=missing_dependencies,
                    compatibility_gaps=compatibility_gaps,
                    unresolved_proxies=unresolved_proxies,
                    path=path,
                    components=components,
                    agent_components=agent_components_by_id.get(workflow.agent_id, set()),
                )
            except Exception as exc:  # pragma: no cover - defensive
                unresolved_proxies.append(str(exc))
                issues.append(
                    self._issue(
                        "error",
                        "proxy",
                        f"Workflow {workflow.id} failed during validation: {exc}",
                        path,
                    )
                )

        errors = [issue.message for issue in issues if issue.level == "error"]
        warnings = [issue.message for issue in issues if issue.level == "warning"]
        return ConfigValidationResult(
            is_valid=not errors,
            valid=not errors,
            errors=errors,
            warnings=warnings,
            issues=issues,
            missing_dependencies=sorted(set(missing_dependencies)),
            compatibility_gaps=sorted(set(compatibility_gaps)),
            unresolved_proxies=sorted(set(unresolved_proxies)),
            version_conflicts=[],
            graph_warnings=[],
        )

    def compile_snapshot(self, snapshot: ConfigSnapshot) -> Dict[str, Any]:
        registry_references: List[RegistryReference] = []
        compiled_agents = [self._compile_agent(agent, registry_references) for agent in snapshot.agents]
        compiled_workflows = [
            self._compile_workflow(workflow, registry_references)
            for workflow in snapshot.workflows
            if workflow.enabled
        ]

        deduped_refs = self._dedupe_references(snapshot.registry_references + registry_references)
        return {
            "airspaces": list(snapshot.airspaces),
            "frequencies": list(snapshot.frequencies),
            "landing_spots": list(snapshot.landing_spots),
            "traffic": dict(snapshot.traffic),
            "agents": compiled_agents,
            "workflows": compiled_workflows,
            "simulation_time": snapshot.simulation_time,
            "simulation_speed": snapshot.simulation_speed,
            "coordinate_mode": snapshot.coordinate_mode,
            "registry_references": [_model_dump(reference) for reference in deduped_refs],
        }

    def collect_registry_references(self, snapshot: ConfigSnapshot) -> List[RegistryReference]:
        compiled = self.compile_snapshot(snapshot)
        refs = compiled.get("registry_references", [])
        return [RegistryReference(**reference) for reference in refs]

    def _compile_agent(self, agent: AgentInstance, registry_references: List[RegistryReference]) -> Dict[str, Any]:
        definition = self._resolve_agent_definition(agent)
        runtime_type = definition.name if definition and definition.source == "builtin" else agent.type
        compiled = {
            "id": agent.id,
            "name": agent.name,
            "type": runtime_type,
            "position": list(agent.initial_position),
            "battery": agent.initial_battery,
            "components": list(agent.components),
            "properties": dict(agent.properties),
            "source": agent.source,
            "version": agent.version,
        }
        effective_ref = self._effective_reference("agents", agent.type, agent.source, agent.version, agent.definition_ref)
        if effective_ref and effective_ref.source == "custom":
            definition_payload = self.catalog_service.get_custom_definition(
                "agents",
                effective_ref.definition_id,
                effective_ref.version,
            )
            compiled["type"] = getattr(definition_payload, "base_agent_type", runtime_type) or runtime_type
            compiled["definition_ref"] = _model_dump(effective_ref)
            compiled["agent_definition"] = _model_dump(definition_payload)
            compiled["version"] = effective_ref.version
            registry_references.append(effective_ref)
        return compiled

    def _compile_workflow(
        self,
        workflow: WorkflowDefinition,
        registry_references: List[RegistryReference],
    ) -> Dict[str, Any]:
        properties = dict(workflow.properties)
        definition = self._resolve_workflow_definition(workflow)
        workflow_type = normalize_workflow_type(workflow.type)
        compiled = {
            "id": workflow.id,
            "name": workflow.name,
            "type": definition.name if definition and definition.source == "builtin" else workflow.type,
            "agent_id": workflow.agent_id,
            "properties": properties,
            "details": properties,
            "source": workflow.source,
            "version": workflow.version,
        }

        effective_ref = self._effective_reference(
            "workflows",
            workflow.type,
            workflow.source,
            workflow.version,
            workflow.definition_ref,
        )
        if effective_ref and effective_ref.source == "custom":
            custom_definition = self.catalog_service.get_custom_definition(
                "workflows",
                effective_ref.definition_id,
                effective_ref.version,
            )
            workflow_type = normalize_workflow_type(custom_definition.adapter_type or workflow.type)
            compiled["type"] = workflow_type
            compiled["definition_ref"] = _model_dump(effective_ref)
            compiled["workflow_definition"] = _model_dump(custom_definition)
            compiled["initial_status"] = custom_definition.start_state
            compiled["task_definitions"] = []
            registry_references.append(effective_ref)
            for binding in custom_definition.task_bindings:
                if not binding.task_ref or binding.task_ref.source != "custom":
                    continue
                task_ref = RegistryReference(
                    kind="tasks",
                    definition_id=binding.task_ref.definition_id,
                    version=binding.task_ref.version,
                    source="custom",
                )
                registry_references.append(task_ref)
                task_definition = self.catalog_service.get_custom_definition(
                    "tasks",
                    task_ref.definition_id,
                    task_ref.version,
                )
                compiled["task_definitions"].append(_model_dump(task_definition))
        if workflow_type == "inspection":
            compiled["waypoints"] = properties.get("inspection_points") or properties.get("waypoints") or []
        elif workflow_type == "charging":
            compiled["battery_threshold"] = properties.get("battery_threshold", 35)
            compiled["target_level"] = properties.get(
                "target_charge_level",
                properties.get("target_level", 90),
            )
            compiled["target_charge_level"] = compiled["target_level"]
        elif workflow_type == "logistics":
            compiled["pickup_location"] = properties.get("pickup_location", [0, 0, 0])
            compiled["delivery_location"] = properties.get("delivery_location", [0, 0, 0])
            compiled["payloads"] = self._normalize_payloads(properties.get("payloads", []))
            compiled["source_agent_id"] = properties.get("source_agent_id", workflow.agent_id)
            compiled["target_agent_id"] = properties.get("target_agent_id", workflow.agent_id)
        elif workflow_type == "imageprocessing":
            compiled["sensing_locations"] = properties.get("sensing_locations", [])
            compiled["image_resolution"] = properties.get("image_resolution", "1920x1080")
            compiled["image_format"] = properties.get("image_format", "jpeg")

        compiled.update(properties)
        return compiled

    def _validate_workflow_bindings(
        self,
        workflow: WorkflowDefinition,
        workflow_definition: WorkflowTypeDefinition,
        agent_definition: Optional[AgentDefinition],
        issues: List[ValidationIssue],
        missing_dependencies: List[str],
        compatibility_gaps: List[str],
        unresolved_proxies: List[str],
        path: str,
        components: Dict[str, Any],
        agent_components: Set[str],
    ) -> None:
        agent_states = set(agent_definition.state_templates.keys()) if agent_definition else set()
        workflow_ref = self._effective_reference(
            "workflows",
            workflow.type,
            workflow.source,
            workflow.version,
            workflow.definition_ref,
        )
        if workflow_ref and workflow_ref.source == "custom":
            registry_validation = self.catalog_service.registry_service.validate_definition(
                "workflows",
                _model_dump(
                    self.catalog_service.get_custom_definition(
                        "workflows",
                        workflow_ref.definition_id,
                        workflow_ref.version,
                    )
                ),
            )
            missing_dependencies.extend(registry_validation.missing_dependencies)
            unresolved_proxies.extend(registry_validation.unresolved_proxies)
            for issue in registry_validation.issues:
                issues.append(
                    self._issue(
                        issue.level,
                        issue.category,
                        issue.message,
                        path,
                    )
                )

        for binding_index, binding in enumerate(workflow_definition.task_bindings):
            binding_path = f"{path}.task_bindings[{binding_index}]"
            task_definition = self._resolve_binding_task_definition(binding)
            if task_definition is None:
                target = binding.task_ref.definition_id if binding.task_ref else binding.task_class
                issues.append(
                    self._issue(
                        "error",
                        "dependency",
                        f"Workflow {workflow.id} references unknown task definition {target}.",
                        binding_path,
                    )
                )
                if target:
                    missing_dependencies.append(str(target))
                continue

            component_name = binding.component or task_definition.component
            if not component_name:
                issues.append(
                    self._issue(
                        "warning",
                        "binding",
                        f"Workflow {workflow.id} binding {binding.workflow_state} has no component selected.",
                        binding_path,
                    )
                )
            elif component_name not in components:
                issues.append(
                    self._issue(
                        "error",
                        "compatibility",
                        f"Workflow {workflow.id} binding references unknown component {component_name}.",
                        binding_path,
                    )
                )
                compatibility_gaps.append(f"{workflow.id}:{component_name}")
            else:
                component = components[component_name]
                if set(task_definition.necessary_metrics).difference(component.produced_metrics):
                    issues.append(
                        self._issue(
                            "error",
                            "compatibility",
                            (
                                f"Task {task_definition.name} requires metrics {task_definition.necessary_metrics}, "
                                f"but component {component_name} exposes {component.produced_metrics}."
                            ),
                            binding_path,
                        )
                    )
                    compatibility_gaps.append(f"{task_definition.name}:{component_name}")

            if component_name and agent_components and component_name not in agent_components:
                issues.append(
                    self._issue(
                        "error",
                        "binding",
                        (
                            f"Workflow {workflow.id} binding {binding.workflow_state} requires component "
                            f"{component_name}, but agent {workflow.agent_id} only enables {sorted(agent_components)}."
                        ),
                        binding_path,
                    )
                )
                compatibility_gaps.append(f"{workflow.agent_id}:{component_name}")

            produced_states = set(task_definition.produced_states or binding.produced_states)
            if agent_states and not produced_states.issubset(agent_states):
                issues.append(
                    self._issue(
                        "error",
                        "compatibility",
                        (
                            f"Task {task_definition.name} produces states {sorted(produced_states)}, "
                            f"which are not all supported by agent {workflow.agent_id}."
                        ),
                        binding_path,
                    )
                )
                compatibility_gaps.append(f"{workflow.agent_id}:{task_definition.name}")

    def _resolve_agent_definition(self, agent: AgentInstance) -> Optional[AgentDefinition]:
        source = agent.definition_ref.source if agent.definition_ref else agent.source
        version = agent.definition_ref.version if agent.definition_ref else agent.version
        return self.catalog_service.get_agent_definition(agent.type, source=source, version=version)

    def _resolve_workflow_definition(self, workflow: WorkflowDefinition) -> Optional[WorkflowTypeDefinition]:
        source = workflow.definition_ref.source if workflow.definition_ref else workflow.source
        version = workflow.definition_ref.version if workflow.definition_ref else workflow.version
        return self.catalog_service.get_workflow_definition(workflow.type, source=source, version=version)

    def _resolve_binding_task_definition(self, binding: TaskBinding) -> Optional[TaskDefinition]:
        if binding.task_ref:
            return self.catalog_service.get_task_definition(
                binding.task_ref.definition_id,
                source=binding.task_ref.source,
                version=binding.task_ref.version,
            )
        return self.catalog_service.get_task_definition(binding.task_class)

    def _effective_reference(
        self,
        kind: str,
        definition_id: str,
        source: str,
        version: Optional[str],
        explicit_ref: Optional[RegistryReference],
    ) -> Optional[RegistryReference]:
        if explicit_ref:
            return explicit_ref
        if source != "custom":
            return None
        resolved = self.catalog_service.get_custom_definition(kind, definition_id, version)
        return RegistryReference(
            kind=kind,
            definition_id=resolved.id,
            version=resolved.version,
            source="custom",
        )

    def _normalize_payloads(self, payloads: List[Any]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for index, payload in enumerate(payloads):
            if isinstance(payload, dict):
                normalized.append(payload)
            else:
                normalized.append({"id": f"payload_{index}", "value": payload})
        return normalized

    def _dedupe_references(self, references: List[RegistryReference]) -> List[RegistryReference]:
        deduped: Dict[tuple, RegistryReference] = {}
        for reference in references:
            key = (reference.kind, reference.definition_id, reference.version, reference.source)
            deduped[key] = reference
        return list(deduped.values())

    def _issue(self, level: str, category: str, message: str, path: Optional[str] = None) -> ValidationIssue:
        return ValidationIssue(level=level, category=category, message=message, path=path)
