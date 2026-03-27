from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

import airfogsim.agent as agent_pkg
import airfogsim.task as task_pkg
from airfogsim.core.agent import Agent
from airfogsim.core.task import Task
from airfogsim.core.workflow import Workflow
from airfogsim.core.enums import TriggerOperator, WorkflowStatus

from .schemas import (
    CustomAgentDefinition,
    CustomTaskDefinition,
    CustomWorkflowDefinition,
    RegistryReference,
    TaskBinding,
    TriggerCondition,
)
from .type_utils import normalize_agent_type, normalize_workflow_type


class ProxyCompiler:
    def __init__(self) -> None:
        self._agent_cache: Dict[str, Type[Agent]] = {}
        self._task_cache: Dict[str, Type[Task]] = {}
        self._workflow_cache: Dict[str, Type[Workflow]] = {}
        self._builtin_agent_classes = {
            normalize_agent_type(agent_class.__name__): agent_class
            for agent_class in agent_pkg.get_all_agent_classes()
        }
        self._builtin_task_classes = {
            task_class.__name__: task_class
            for task_class in task_pkg._TASK_CLASSES
        }

    def compile_agent_class(self, definition: CustomAgentDefinition) -> Type[Agent]:
        cache_key = f"{definition.meta.id}@{definition.meta.version}"
        if cache_key in self._agent_cache:
            return self._agent_cache[cache_key]

        base_class = self._builtin_agent_classes.get(
            normalize_agent_type(definition.base_agent_type)
        )
        if base_class is None:
            raise ValueError(
                f"Unknown base agent type for custom agent {definition.meta.id}: {definition.base_agent_type}"
            )

        class_name = self._class_name("CustomAgent", definition.meta.id, definition.meta.version)
        proxy_class = type(class_name, (base_class,), {})
        for key, template in definition.state_templates.items():
            proxy_class.register_state_template(
                key,
                value_type=self._value_type(template.value_type),
                required=template.required,
                description=template.description,
            )
        proxy_class.get_description = classmethod(  # type: ignore[assignment]
            lambda cls: self._localized_text(
                definition.meta.description,
                fallback=f"Custom agent proxy for {definition.meta.id}",
            )
        )
        setattr(proxy_class, "REGISTRY_REFERENCE", self._reference("agents", definition))
        setattr(proxy_class, "CUSTOM_DEFAULT_PROPERTIES", dict(definition.default_properties))
        self._agent_cache[cache_key] = proxy_class
        return proxy_class

    def compile_task_class(self, definition: CustomTaskDefinition) -> Type[Task]:
        cache_key = f"{definition.meta.id}@{definition.meta.version}"
        if cache_key in self._task_cache:
            return self._task_cache[cache_key]

        base_class = self._builtin_task_classes.get(definition.adapter_task_type)
        if base_class is None:
            raise ValueError(
                f"Unknown adapter task type for custom task {definition.meta.id}: {definition.adapter_task_type}"
            )

        if list(base_class.NECESSARY_METRICS) != list(definition.necessary_metrics):
            raise ValueError(
                f"Custom task {definition.meta.id} must keep necessary_metrics aligned with adapter task {definition.adapter_task_type}."
            )
        if list(base_class.PRODUCED_STATES) != list(definition.produced_states):
            raise ValueError(
                f"Custom task {definition.meta.id} must keep produced_states aligned with adapter task {definition.adapter_task_type}."
            )

        class_name = self._class_name("CustomTask", definition.meta.id, definition.meta.version)
        proxy_class = type(
            class_name,
            (base_class,),
            {
                "NECESSARY_METRICS": list(definition.necessary_metrics),
                "PRODUCED_STATES": list(definition.produced_states),
                "__doc__": self._localized_text(
                    definition.meta.description,
                    fallback=f"Custom task proxy for {definition.meta.id}",
                ),
            },
        )
        setattr(proxy_class, "REGISTRY_REFERENCE", self._reference("tasks", definition))
        setattr(proxy_class, "COMPONENT_NAME", definition.component)
        setattr(proxy_class, "ADAPTER_TASK_TYPE", definition.adapter_task_type)
        self._task_cache[cache_key] = proxy_class
        return proxy_class

    def compile_workflow_class(self, definition: CustomWorkflowDefinition) -> Type[Workflow]:
        cache_key = f"{definition.meta.id}@{definition.meta.version}"
        if cache_key in self._workflow_cache:
            return self._workflow_cache[cache_key]

        compiler = self
        workflow_definition = definition

        class CustomWorkflowProxy(Workflow):
            @classmethod
            def get_description(cls):
                return compiler._localized_text(
                    workflow_definition.meta.description,
                    fallback=f"Custom workflow proxy for {workflow_definition.meta.id}",
                )

            def __init__(
                self,
                env,
                name,
                owner,
                timeout=None,
                event_names=None,
                initial_status="idle",
                callback=None,
                properties=None,
            ):
                self._custom_definition = workflow_definition
                self._workflow_bindings = {
                    binding.workflow_state: binding
                    for binding in workflow_definition.task_bindings
                }
                super().__init__(
                    env=env,
                    name=name,
                    owner=owner,
                    timeout=timeout,
                    event_names=event_names or [],
                    initial_status=initial_status,
                    callback=callback,
                    properties=properties or {},
                )

            def get_current_suggested_task(self):
                if not self.owner or self.status != WorkflowStatus.RUNNING:
                    return None
                binding = self._workflow_bindings.get(self.status_machine.state)
                if not binding:
                    return None
                task_class_name = compiler.resolve_task_runtime_name(binding)
                task_dict = {
                    "component": binding.component,
                    "task_class": task_class_name,
                    "task_name": binding.task_name or task_class_name,
                    "workflow_id": self.id,
                    "target_state": dict(binding.target_state),
                    "properties": {
                        **dict(binding.properties),
                        "registry_ref": _reference_dump(binding.registry_ref),
                    },
                }
                return self._add_priority_to_task(task_dict)

            def _setup_transitions(self):
                self.status_machine.set_start_transition(workflow_definition.start_state)
                for condition in workflow_definition.trigger_conditions:
                    self._add_condition(condition)

            def _add_condition(self, condition: TriggerCondition) -> None:
                config = dict(condition.config)
                if condition.trigger_type == "state":
                    agent_id = config.get("agent_id") or self.owner.id
                    state_key = config.get("state_key") or condition.source_ref
                    self.status_machine.add_transition(
                        condition.source_state,
                        condition.target_state,
                        agent_state={
                            "agent_id": agent_id,
                            "state_key": state_key,
                            "operator": compiler._trigger_operator(
                                condition.operator or config.get("operator")
                            ),
                            "target_value": config.get("target_value"),
                        },
                        description=condition.description,
                    )
                    return
                if condition.trigger_type == "event":
                    self.status_machine.add_transition(
                        condition.source_state,
                        condition.target_state,
                        event_trigger={
                            "source_id": config.get("source_id") or self.id,
                            "event_name": config.get("event_name") or condition.source_ref,
                            "value_key": config.get("value_key"),
                            "operator": compiler._trigger_operator(
                                condition.operator or config.get("operator")
                            ),
                            "target_value": config.get("target_value"),
                        },
                        description=condition.description,
                    )
                    return
                if condition.trigger_type == "time":
                    self.status_machine.add_transition(
                        condition.source_state,
                        condition.target_state,
                        time_trigger=config,
                        description=condition.description,
                    )
                    return
                raise ValueError(
                    f"Unsupported custom workflow trigger type: {condition.trigger_type}"
                )

        class_name = self._class_name("CustomWorkflow", definition.meta.id, definition.meta.version)
        CustomWorkflowProxy.__name__ = class_name
        CustomWorkflowProxy.__qualname__ = class_name
        for key, template in definition.property_templates.items():
            CustomWorkflowProxy.register_property_template(
                key,
                value_type=self._value_type(template.value_type),
                required=template.required,
                description=template.description,
            )
        setattr(
            CustomWorkflowProxy,
            "REGISTRY_REFERENCE",
            self._reference("workflows", definition),
        )
        setattr(CustomWorkflowProxy, "WORKFLOW_FAMILY", definition.workflow_family)
        self._workflow_cache[cache_key] = CustomWorkflowProxy
        return CustomWorkflowProxy

    def resolve_task_runtime_name(self, binding: TaskBinding) -> str:
        if binding.registry_ref:
            reference = binding.registry_ref
            cache_key = f"{reference.definition_id}@{reference.version}"
            task_class = self._task_cache.get(cache_key)
            if task_class is None:
                raise ValueError(
                    f"Custom task proxy not compiled for {reference.definition_id}@{reference.version}"
                )
            return task_class.__name__
        return str(binding.task_class or "")

    def resolve_builtin_task(self, task_name: str) -> Optional[Type[Task]]:
        return self._builtin_task_classes.get(task_name)

    def _class_name(self, prefix: str, definition_id: str, version: str) -> str:
        token = f"{prefix}_{definition_id}_{version}".replace(".", "_").replace("-", "_")
        return "".join(ch for ch in token if ch.isalnum() or ch == "_")

    def _localized_text(self, value, fallback: str) -> str:
        if getattr(value, "en_US", None):
            return value.en_US
        if getattr(value, "zh_CN", None):
            return value.zh_CN
        return fallback

    def _reference(self, kind: str, definition: Any) -> RegistryReference:
        return RegistryReference(
            kind=kind,
            definition_id=definition.meta.id,
            version=definition.meta.version,
            source="custom",
        )

    def _trigger_operator(self, value: Optional[str]):
        if not value:
            return TriggerOperator.EQUALS
        normalized = str(value).upper()
        return getattr(TriggerOperator, normalized, TriggerOperator.EQUALS)

    def _value_type(self, value_type: Optional[str]):
        mapping = {
            None: None,
            "any": None,
            "str": str,
            "string": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
        }
        return mapping.get(str(value_type).lower() if value_type is not None else None, None)


def _reference_dump(reference: Optional[RegistryReference]) -> Optional[Dict[str, Any]]:
    if reference is None:
        return None
    if hasattr(reference, "model_dump"):
        return reference.model_dump()
    return reference.dict()
