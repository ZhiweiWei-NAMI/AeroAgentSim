from __future__ import annotations

import inspect
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Type

import airfogsim.agent as agent_pkg
import airfogsim.component as component_pkg
import airfogsim.workflow as workflow_pkg
from airfogsim.agent.drone import DroneAgent
from airfogsim.core.environment import Environment
from airfogsim.core.enums import WorkflowStatus

from .registry_service import RegistryService
from .schemas import (
    AgentDefinition,
    CatalogCompatibility,
    ComponentDefinition,
    CustomAgentDefinition,
    CustomTaskDefinition,
    CustomWorkflowDefinition,
    RegistryReference,
    TaskBinding,
    TaskDefinition,
    TemplateField,
    TriggerCondition,
    WorkflowTypeDefinition,
)
from .setup import setup_environment_resources
from .type_utils import (
    is_supported_workbench_workflow_type,
    normalize_agent_type,
    normalize_component_name,
    normalize_workflow_type,
)


def _model_dump(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


def _normalize_name(name: str) -> str:
    return normalize_workflow_type(normalize_agent_type(name))


def _sanitize_catalog_value(value: Any) -> Any:
    if callable(value):
        name = getattr(value, "__name__", None) or value.__class__.__name__
        return f"<callable:{name}>"
    if isinstance(value, dict):
        return {key: _sanitize_catalog_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_catalog_value(item) for item in value]
    return value


class CatalogService:
    """Expose merged builtin/custom metadata for the workbench."""

    def __init__(self, registry_service: Optional[RegistryService] = None) -> None:
        self.registry_service = registry_service or RegistryService()
        self._workflow_classes = {
            _normalize_name(workflow_class.__name__): workflow_class
            for workflow_class in workflow_pkg.get_all_workflow_classes()
        }

    def get_workflow_class(self, workflow_type: str):
        return self._workflow_classes.get(_normalize_name(workflow_type))

    def get_agent_definition(
        self,
        agent_type: Optional[str],
        source: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Optional[AgentDefinition]:
        if isinstance(source, RegistryReference):
            ref = source
            version = ref.version
            source = ref.source
            agent_type = ref.definition_id
        normalized = normalize_agent_type(agent_type)
        catalog = self.get_agents(scope="custom" if source == "custom" else "all")
        matches = []
        for definition in catalog:
            if version and definition.version != version:
                continue
            if source and definition.source != source:
                continue
            tokens = {
                definition.id,
                definition.name,
                normalize_agent_type(definition.id),
                normalize_agent_type(definition.name),
            }
            if agent_type in tokens or normalized in tokens:
                matches.append(definition)
        if not matches:
            return None
        matches.sort(key=lambda item: (item.source == "custom", item.version or ""), reverse=True)
        return matches[0]

    def get_task_definition(
        self,
        task_type: Optional[str],
        source: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Optional[TaskDefinition]:
        if isinstance(source, RegistryReference):
            ref = source
            version = ref.version
            source = ref.source
            task_type = ref.definition_id
        normalized = _normalize_name(task_type or "")
        catalog = self.get_tasks(scope="custom" if source == "custom" else "all")
        matches = []
        for definition in catalog:
            if version and definition.version != version:
                continue
            if source and definition.source != source:
                continue
            tokens = {
                definition.id,
                definition.name,
                _normalize_name(definition.id),
                _normalize_name(definition.name),
            }
            if task_type in tokens or normalized in tokens:
                matches.append(definition)
        if not matches:
            return None
        matches.sort(key=lambda item: (item.source == "custom", item.version or ""), reverse=True)
        return matches[0]

    def get_workflow_definition(
        self,
        workflow_type: Optional[str],
        source: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Optional[WorkflowTypeDefinition]:
        if isinstance(source, RegistryReference):
            ref = source
            version = ref.version
            source = ref.source
            workflow_type = ref.definition_id
        normalized = normalize_workflow_type(workflow_type)
        catalog = self.get_workflows(scope="custom" if source == "custom" else "all")
        matches = []
        for definition in catalog:
            if version and definition.version != version:
                continue
            if source and definition.source != source:
                continue
            tokens = {
                definition.id,
                definition.name,
                normalize_workflow_type(definition.id),
                normalize_workflow_type(definition.name),
            }
            if workflow_type in tokens or normalized in tokens:
                matches.append(definition)
        if not matches:
            return None
        matches.sort(key=lambda item: (item.source == "custom", item.version or ""), reverse=True)
        return matches[0]

    def get_custom_definition(self, kind: str, definition_id: str, version: Optional[str] = None):
        return self.registry_service.get_definition(kind, definition_id, version)

    @lru_cache(maxsize=1)
    def get_builtin_agents(self) -> Tuple[AgentDefinition, ...]:
        component_index = {component.name: component for component in self.get_components()}
        definitions: List[AgentDefinition] = []

        for agent_class in agent_pkg.get_all_agent_classes():
            state_templates = self._serialize_templates(agent_class.get_state_templates())
            compatible_components = sorted(
                component.name
                for component in component_index.values()
                if set(component.monitored_states).issubset(set(state_templates))
            )
            definitions.append(
                AgentDefinition(
                    id=_normalize_name(agent_class.__name__),
                    name=agent_class.__name__,
                    description=agent_class.get_description(),
                    state_templates=state_templates,
                    compatible_components=compatible_components,
                    source="builtin",
                    version="builtin",
                )
            )

        definitions.sort(key=lambda item: item.name)
        return tuple(definitions)

    def get_agents(self, scope: str = "all") -> Tuple[AgentDefinition, ...]:
        builtin = list(self.get_builtin_agents()) if scope in {"all", "builtin"} else []
        custom = (
            self.registry_service.get_custom_agent_catalog()
            if scope in {"all", "custom"}
            else []
        )
        items = builtin + custom
        items.sort(key=lambda item: (item.source, item.name, item.version or ""))
        return tuple(items)

    @lru_cache(maxsize=1)
    def get_components(self, scope: str = "all") -> Tuple[ComponentDefinition, ...]:
        if scope == "custom":
            return tuple()
        definitions = [
            ComponentDefinition(
                id=_normalize_name(component_class.__name__),
                name=component_class.__name__,
                description=component_class.__doc__ or f"{component_class.__name__} component",
                produced_metrics=list(getattr(component_class, "PRODUCED_METRICS", [])),
                monitored_states=list(getattr(component_class, "MONITORED_STATES", [])),
                source="builtin",
                version="builtin",
            )
            for component_class in component_pkg.get_all_component_classes()
        ]
        definitions.sort(key=lambda item: item.name)
        return tuple(definitions)

    @lru_cache(maxsize=1)
    def get_builtin_tasks(self) -> Tuple[TaskDefinition, ...]:
        env = self._create_preview_env()
        definitions = [
            TaskDefinition(
                id=_normalize_name(task_class.__name__),
                name=task_class.__name__,
                description=task_class.__doc__ or f"{task_class.__name__} task",
                necessary_metrics=list(getattr(task_class, "NECESSARY_METRICS", [])),
                produced_states=list(getattr(task_class, "PRODUCED_STATES", [])),
                source="builtin",
                version="builtin",
            )
            for task_class in env.task_manager.get_all_task_classes()
        ]
        definitions.sort(key=lambda item: item.name)
        return tuple(definitions)

    def get_tasks(self, scope: str = "all") -> Tuple[TaskDefinition, ...]:
        builtin = list(self.get_builtin_tasks()) if scope in {"all", "builtin"} else []
        custom = (
            self.registry_service.get_custom_task_catalog()
            if scope in {"all", "custom"}
            else []
        )
        items = builtin + custom
        items.sort(key=lambda item: (item.source, item.name, item.version or ""))
        return tuple(items)

    @lru_cache(maxsize=1)
    def get_builtin_workflows(self) -> Tuple[WorkflowTypeDefinition, ...]:
        definitions: List[WorkflowTypeDefinition] = []
        for workflow_class in workflow_pkg.get_all_workflow_classes():
            if not is_supported_workbench_workflow_type(workflow_class.__name__):
                continue
            try:
                definitions.append(self._preview_workflow_definition(workflow_class))
            except Exception:
                continue
        definitions.sort(key=lambda item: item.name)
        return tuple(definitions)

    def get_workflows(self, scope: str = "all") -> Tuple[WorkflowTypeDefinition, ...]:
        builtin = list(self.get_builtin_workflows()) if scope in {"all", "builtin"} else []
        custom = (
            self.registry_service.get_custom_workflow_catalog()
            if scope in {"all", "custom"}
            else []
        )
        items = builtin + custom
        items.sort(key=lambda item: (item.source, item.name, item.version or ""))
        return tuple(items)

    def get_compatibility(self, scope: str = "all") -> CatalogCompatibility:
        agent_components: Dict[str, List[str]] = {}
        component_tasks: Dict[str, List[str]] = {}
        workflow_agents: Dict[str, List[str]] = {}
        workflow_tasks: Dict[str, List[str]] = {}

        agents = {self._catalog_key(agent): agent for agent in self.get_agents(scope=scope)}
        components = {component.name: component for component in self.get_components()}
        tasks = {self._catalog_key(task): task for task in self.get_tasks(scope=scope)}

        for agent_key, agent in agents.items():
            if agent.source == "custom" and agent.compatible_components:
                agent_components[agent_key] = sorted(agent.compatible_components)
            else:
                agent_components[agent_key] = sorted(
                    component.name
                    for component in components.values()
                    if set(component.monitored_states).issubset(set(agent.state_templates.keys()))
                )

        for component_name, component in components.items():
            available_metrics = set(component.produced_metrics)
            compatible_tasks = []
            for task_key, task in tasks.items():
                if task.component and task.component != component_name:
                    continue
                if set(task.necessary_metrics).issubset(available_metrics):
                    compatible_tasks.append(task_key)
            component_tasks[component_name] = sorted(compatible_tasks)

        for workflow in self.get_workflows(scope=scope):
            workflow_key = self._catalog_key(workflow)
            workflow_task_names = sorted(
                {
                    self._binding_task_key(binding)
                    for binding in workflow.task_bindings
                    if self._binding_task_key(binding)
                }
            )
            workflow_tasks[workflow_key] = workflow_task_names

            required_states = self._required_states_for_workflow(workflow, tasks)
            compatible_agents = []
            supported = {
                normalize_agent_type(value)
                for value in getattr(workflow, "supported_agent_types", [])
            }
            for agent_key, agent in agents.items():
                agent_tokens = {
                    normalize_agent_type(agent.id),
                    normalize_agent_type(agent.name),
                }
                if supported and supported.isdisjoint(agent_tokens):
                    continue
                if required_states.issubset(set(agent.state_templates.keys())):
                    compatible_agents.append(agent_key)
            workflow_agents[workflow_key] = sorted(compatible_agents)

        return CatalogCompatibility(
            agent_components=agent_components,
            component_tasks=component_tasks,
            workflow_agents=workflow_agents,
            workflow_tasks=workflow_tasks,
        )

    def _preview_workflow_definition(self, workflow_class: Type) -> WorkflowTypeDefinition:
        workflow, env = self._build_workflow_preview(workflow_class)

        states: Set[str] = {workflow.status_machine.start_status}
        trigger_conditions: List[TriggerCondition] = []
        task_bindings: List[TaskBinding] = []

        for state, transitions in workflow.status_machine.state_transitions.items():
            states.add(state)
            for trigger, next_state, description in transitions:
                states.add(next_state)
                trigger_conditions.append(
                    self._build_trigger_condition(state, next_state, trigger, description)
                )

        for trigger, next_state, description in workflow.status_machine.wildcard_transitions:
            states.add(next_state)
            trigger_conditions.append(
                self._build_trigger_condition("*", next_state, trigger, description)
            )

        workflow.status = WorkflowStatus.RUNNING
        task_class_index = {
            task_class.__name__: task_class for task_class in env.task_manager.get_all_task_classes()
        }

        for state in sorted(states):
            workflow.status_machine.current_status = state
            try:
                task = workflow.get_current_suggested_task()
            except Exception:
                task = None
            if not task:
                continue
            task_class_name = task.get("task_class")
            produced_states = list(
                getattr(task_class_index.get(task_class_name), "PRODUCED_STATES", [])
            )
            task_bindings.append(
                TaskBinding(
                    workflow_state=state,
                    component=self._normalize_component_name(task.get("component")),
                    task_class=task_class_name,
                    task_name=task.get("task_name"),
                    produced_states=produced_states,
                    target_state=task.get("target_state") or {},
                    properties=task.get("properties") or {},
                )
            )

        return WorkflowTypeDefinition(
            id=_normalize_name(workflow_class.__name__),
            name=workflow_class.__name__,
            description=workflow_class.get_description(),
            property_templates=self._serialize_templates(workflow_class.get_property_templates()),
            states=sorted(states),
            start_state=workflow.status_machine.start_status,
            trigger_conditions=trigger_conditions,
            task_bindings=task_bindings,
            critical_path=self._compute_critical_path(
                workflow.status_machine.start_status, trigger_conditions
            ),
            source="builtin",
            version="builtin",
        )

    def _build_workflow_preview(self, workflow_class: Type):
        env = self._create_preview_env()
        preview_agent = env.create_agent(
            DroneAgent,
            "preview_owner",
            agent_id="preview_owner",
            properties={
                "position": [10, 10, 25],
                "battery_level": 80,
                "status": "idle",
                "moving_status": "idle",
                "altitude": 25,
            },
        )
        properties = self._sample_properties(workflow_class)
        kwargs = {
            "env": env,
            "name": workflow_class.__name__,
            "owner": preview_agent,
            "properties": properties,
        }
        signature = inspect.signature(workflow_class.__init__)
        if "executor_agent_id" in signature.parameters:
            kwargs["executor_agent_id"] = getattr(preview_agent, "id", "preview_owner")
        workflow = workflow_class(**kwargs)
        return workflow, env

    def _build_trigger_condition(
        self,
        source_state: str,
        target_state: str,
        trigger: Any,
        description: Optional[str],
    ) -> TriggerCondition:
        trigger_type = getattr(getattr(trigger, "type", None), "value", "unknown")
        payload = {
            "source_state": source_state,
            "target_state": target_state,
            "trigger_type": trigger_type,
            "description": description,
            "config": {},
        }
        for attr in ("state_key", "event_name", "value_key", "trigger_time", "interval"):
            if hasattr(trigger, attr):
                payload["config"][attr] = _sanitize_catalog_value(getattr(trigger, attr))
        if hasattr(trigger, "state_key"):
            payload["source_ref"] = getattr(trigger, "state_key")
        elif hasattr(trigger, "event_name"):
            payload["source_ref"] = getattr(trigger, "event_name")
        if hasattr(trigger, "operator"):
            operator = getattr(trigger, "operator")
            payload["operator"] = getattr(operator, "value", str(operator))
        if hasattr(trigger, "target_value"):
            payload["target_value"] = _sanitize_catalog_value(getattr(trigger, "target_value"))
        return TriggerCondition(**payload)

    def _compute_critical_path(
        self,
        start_state: Optional[str],
        trigger_conditions: Iterable[TriggerCondition],
    ) -> List[str]:
        if not start_state:
            return []
        edges_by_source: Dict[str, List[str]] = {}
        for condition in trigger_conditions:
            edges_by_source.setdefault(condition.source_state, []).append(condition.target_state)
        path = [start_state]
        current = start_state
        visited = {start_state}
        while True:
            next_states = [state for state in edges_by_source.get(current, []) if state not in visited]
            if not next_states:
                break
            current = next_states[0]
            visited.add(current)
            path.append(current)
        return path

    def _required_states_for_workflow(
        self,
        workflow: WorkflowTypeDefinition,
        tasks: Dict[str, TaskDefinition],
    ) -> Set[str]:
        required_states: Set[str] = set()
        for binding in workflow.task_bindings:
            task_key = self._binding_task_key(binding)
            task = tasks.get(task_key)
            if task:
                required_states.update(task.produced_states)
            required_states.update(binding.produced_states)
            required_states.update(binding.target_state.keys())
        for trigger in workflow.trigger_conditions:
            if trigger.trigger_type == "state" and trigger.source_ref:
                required_states.add(trigger.source_ref)
        return required_states

    def _binding_task_key(self, binding: TaskBinding) -> Optional[str]:
        if binding.task_ref:
            return binding.task_ref.definition_id
        if binding.task_class:
            return _normalize_name(binding.task_class)
        return None

    def _catalog_key(self, item: Any) -> str:
        if getattr(item, "source", "builtin") == "custom":
            return f"{item.id}@{item.version}"
        return item.name

    def _serialize_templates(self, templates: Dict[str, Any]) -> Dict[str, TemplateField]:
        serialized: Dict[str, TemplateField] = {}
        for key, template in templates.items():
            value_type = getattr(template, "value_type", None)
            if isinstance(value_type, tuple):
                value_type_name = "|".join(sorted(item.__name__ for item in value_type if hasattr(item, "__name__")))
            elif hasattr(value_type, "__name__"):
                value_type_name = value_type.__name__
            elif value_type is None:
                value_type_name = "any"
            else:
                value_type_name = str(value_type)
            serialized[key] = TemplateField(
                key=key,
                value_type=value_type_name,
                required=bool(getattr(template, "required", False)),
                default=getattr(template, "default", None),
                description=getattr(template, "description", None),
            )
        return serialized

    def _sample_properties(self, workflow_class: Type) -> Dict[str, Any]:
        sample: Dict[str, Any] = {}
        for key, template in workflow_class.get_property_templates().items():
            if getattr(template, "default", None) is not None:
                sample[key] = getattr(template, "default")
                continue
            value_type = getattr(template, "value_type", None)
            key_hint = key.lower()
            if key_hint == "pickup_location":
                sample[key] = [10, 10, 20]
                continue
            if key_hint == "delivery_location":
                sample[key] = [20, 20, 30]
                continue
            if key_hint == "payloads":
                sample[key] = [{"id": "payload_1", "weight": 1.0}]
                continue
            if key_hint == "payload_properties":
                sample[key] = {"fragile": False}
                continue
            if value_type in {int, float} or key_hint.endswith(("threshold", "level")):
                sample[key] = 1
            elif value_type in {list, tuple} or "point" in key_hint or "location" in key_hint:
                sample[key] = [[10, 10, 20], [20, 20, 30]]
            elif value_type is bool:
                sample[key] = False
            else:
                sample[key] = "sample"
        return sample

    def _create_preview_env(self) -> Environment:
        env = Environment(initial_time=0, visual_interval=0)
        setup_environment_resources(env, {}, None)
        return env

    def _normalize_component_name(self, component_name: Optional[str]) -> Optional[str]:
        return normalize_component_name(component_name)
