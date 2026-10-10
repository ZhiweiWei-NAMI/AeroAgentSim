from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type

import aeroagentsim.task as task_pkg
from aeroagentsim.agent.drone import DroneAgent
from aeroagentsim.core.agent import AgentMeta
from aeroagentsim.core.enums import TriggerOperator, WorkflowStatus
from aeroagentsim.core.task import Task
from aeroagentsim.core.workflow import Workflow, WorkflowMeta

from .schemas import (
    ConfigValidationResult,
    CustomAgentDefinition,
    CustomTaskDefinition,
    CustomWorkflowDefinition,
    RegistryDefinitionMeta,
    RegistryKind,
    RegistryReference,
    TaskDefinition,
    ValidationIssue,
    WorkflowTypeDefinition,
    AgentDefinition,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_dump(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


def _sanitize_token(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "registry_item"


def _default_registry_dir() -> Path:
    registry_dir = os.getenv("AEROAGENTSIM_REGISTRY_DIR") or os.getenv("AIRFOGSIM_REGISTRY_DIR")
    if registry_dir:
        return Path(registry_dir)

    runtime_dir = os.getenv("AEROAGENTSIM_RUNTIME_DIR") or os.getenv("AIRFOGSIM_RUNTIME_DIR")
    if runtime_dir:
        runtime_path = Path(runtime_dir).expanduser().resolve()
        if runtime_path.name in {"workbench", "aeroagentsim"} and runtime_path.parent.name == "runtime":
            return runtime_path.parents[1] / "registry" / runtime_path.name
        return runtime_path.parent / "registry"

    db_path = os.getenv("AEROAGENTSIM_DB_PATH") or os.getenv("AIRFOGSIM_DB_PATH")
    if db_path:
        runtime_path = Path(db_path).expanduser().resolve().parent
        if runtime_path.name in {"workbench", "aeroagentsim"} and runtime_path.parent.name == "runtime":
            return runtime_path.parents[1] / "registry" / runtime_path.name
        return runtime_path.parent / "registry"

    project_root = Path(__file__).resolve().parents[3]
    preferred = project_root / "registry" / "aeroagentsim"
    legacy = project_root / "registry" / "workbench"
    return preferred if preferred.exists() or not legacy.exists() else legacy


def _default_runtime_dir() -> Path:
    runtime_dir = os.getenv("AEROAGENTSIM_RUNTIME_DIR") or os.getenv("AIRFOGSIM_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir)

    db_path = os.getenv("AEROAGENTSIM_DB_PATH") or os.getenv("AIRFOGSIM_DB_PATH")
    if db_path:
        return Path(db_path).expanduser().resolve().parent

    project_root = Path(__file__).resolve().parents[3]
    preferred = project_root / "runtime" / "aeroagentsim"
    legacy = project_root / "runtime" / "workbench"
    return preferred if preferred.exists() or not legacy.exists() else legacy


class RegistryService:
    MODEL_BY_KIND = {
        "agents": CustomAgentDefinition,
        "tasks": CustomTaskDefinition,
        "workflows": CustomWorkflowDefinition,
    }

    def __init__(
        self,
        base_dir: Optional[str] = None,
        runtime_dir: Optional[str] = None,
    ) -> None:
        self.base_dir = Path(base_dir) if base_dir else _default_registry_dir()
        self.runtime_dir = Path(runtime_dir) if runtime_dir else _default_runtime_dir()
        self._proxy_cache: Dict[Tuple[str, str, str], Type] = {}
        self._builtin_task_classes = {
            task_class.__name__: task_class for task_class in getattr(task_pkg, "_TASK_CLASSES", [])
        }
        self._builtin_task_aliases = {
            _sanitize_token(task_class.__name__).lower(): task_class.__name__
            for task_class in getattr(task_pkg, "_TASK_CLASSES", [])
        }
        for kind in self.MODEL_BY_KIND:
            self._kind_dir(kind).mkdir(parents=True, exist_ok=True)

    def list_definitions(self, kind: RegistryKind, definition_id: Optional[str] = None) -> List[RegistryDefinitionMeta]:
        definitions: List[RegistryDefinitionMeta] = []
        for path in sorted(self._kind_dir(kind).glob("*.json")):
            definition = self._load_path(kind, path)
            if definition_id and definition.id != definition_id:
                continue
            definitions.append(definition)
        definitions.sort(key=lambda item: (item.id, item.version), reverse=True)
        return definitions

    def get_definition(
        self,
        kind: RegistryKind,
        definition_id: str,
        version: Optional[str] = None,
    ) -> RegistryDefinitionMeta:
        candidates = [
            definition
            for definition in self.list_definitions(kind, definition_id=definition_id)
            if version in {None, "", definition.version}
        ]
        if not candidates:
            raise FileNotFoundError(f"Registry definition not found: {kind}/{definition_id}@{version or 'latest'}")
        candidates.sort(key=lambda item: item.updated_at or "", reverse=True)
        return candidates[0]

    def save_definition(self, kind: RegistryKind, payload: Dict[str, Any]) -> RegistryDefinitionMeta:
        model_type = self.MODEL_BY_KIND[kind]
        data = dict(payload)
        now = _utc_now()
        data["kind"] = kind
        data["source"] = "custom"
        data["created_at"] = data.get("created_at") or now
        data["updated_at"] = now
        definition = model_type(**data)
        path = self._definition_path(kind, definition.id, definition.version)
        path.write_text(json.dumps(_model_dump(definition), indent=2, ensure_ascii=False))
        self._proxy_cache.pop((kind, definition.id, definition.version), None)
        return definition

    def delete_definition(self, kind: RegistryKind, definition_id: str, version: Optional[str] = None) -> int:
        removed = 0
        if version:
            path = self._definition_path(kind, definition_id, version)
            if path.exists():
                path.unlink()
                removed += 1
        else:
            for path in self._kind_dir(kind).glob(f"{_sanitize_token(definition_id)}__*.json"):
                path.unlink()
                removed += 1
        return removed

    def validate_definition(self, kind: RegistryKind, payload: Any) -> ConfigValidationResult:
        issues: List[ValidationIssue] = []
        missing_dependencies: List[str] = []
        unresolved_proxies: List[str] = []

        try:
            payload_dict = _model_dump(payload) if not isinstance(payload, dict) else dict(payload)
            definition = self.MODEL_BY_KIND[kind](**{**payload_dict, "kind": kind, "source": "custom"})
        except Exception as exc:
            return ConfigValidationResult(
                is_valid=False,
                valid=False,
                errors=[str(exc)],
                issues=[ValidationIssue(level="error", category="schema", message=str(exc))],
            )

        if not definition.enabled:
            issues.append(
                ValidationIssue(
                    level="warning",
                    category="definition",
                    message=f"{kind}/{definition.id}@{definition.version} is disabled.",
                )
            )

        if kind == "agents":
            definition = definition  # type: ignore[assignment]
            if not getattr(definition, "state_templates", {}):
                issues.append(
                    ValidationIssue(
                        level="warning",
                        category="schema",
                        message=f"Custom agent {definition.id} defines no state templates.",
                    )
                )
        elif kind == "tasks":
            definition = definition  # type: ignore[assignment]
            if not getattr(definition, "adapter_type", ""):
                issues.append(
                    ValidationIssue(
                        level="error",
                        category="adapter",
                        message=f"Custom task {definition.id} is missing adapter_type.",
                    )
                )
            elif str(definition.adapter_type).lower() not in {"declarative", "metric_rate"} and definition.adapter_type not in self._builtin_task_classes:
                issues.append(
                    ValidationIssue(
                        level="warning",
                        category="adapter",
                        message=(
                            f"Custom task {definition.id} uses non-builtin adapter_type "
                            f"{definition.adapter_type}; runtime falls back to the declarative adapter."
                        ),
                    )
                )
            if not getattr(definition, "produced_states", []):
                issues.append(
                    ValidationIssue(
                        level="error",
                        category="schema",
                        message=f"Custom task {definition.id} must declare produced_states.",
                    )
                )
        elif kind == "workflows":
            definition = definition  # type: ignore[assignment]
            if not getattr(definition, "adapter_type", ""):
                issues.append(
                    ValidationIssue(
                        level="error",
                        category="adapter",
                        message=f"Custom workflow {definition.id} is missing adapter_type.",
                    )
                )
            if not getattr(definition, "states", []):
                issues.append(
                    ValidationIssue(
                        level="error",
                        category="schema",
                        message=f"Custom workflow {definition.id} must define states.",
                    )
                )
            if definition.start_state and definition.start_state not in definition.states:
                issues.append(
                    ValidationIssue(
                        level="error",
                        category="schema",
                        message=f"Custom workflow {definition.id} start_state must exist in states.",
                    )
                )
            for condition in getattr(definition, "trigger_conditions", []):
                if condition.source_state != "*" and condition.source_state not in definition.states:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            category="schema",
                            message=(
                                f"Workflow transition source state {condition.source_state} "
                                f"is not declared in {definition.id}."
                            ),
                        )
                    )
                if condition.target_state not in definition.states:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            category="schema",
                            message=(
                                f"Workflow transition target state {condition.target_state} "
                                f"is not declared in {definition.id}."
                            ),
                        )
                    )
            for binding in getattr(definition, "task_bindings", []):
                if binding.workflow_state not in definition.states:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            category="schema",
                            message=(
                                f"Workflow binding state {binding.workflow_state} "
                                f"is not declared in {definition.id}."
                            ),
                        )
                    )
                if not binding.component:
                    issues.append(
                        ValidationIssue(
                            level="warning",
                            category="binding",
                            message=(
                                f"Workflow binding {definition.id}:{binding.workflow_state} "
                                "does not declare a component."
                            ),
                        )
                    )
                if binding.task_ref:
                    try:
                        self.get_definition("tasks", binding.task_ref.definition_id, binding.task_ref.version)
                    except FileNotFoundError:
                        missing = f"tasks/{binding.task_ref.definition_id}@{binding.task_ref.version or 'latest'}"
                        missing_dependencies.append(missing)
                        issues.append(
                            ValidationIssue(
                                level="error",
                                category="dependency",
                                message=f"Missing task definition: {missing}.",
                            )
                        )
                elif not (binding.task_class or binding.task_name):
                    issues.append(
                        ValidationIssue(
                            level="error",
                            category="schema",
                            message=f"Workflow binding for state {binding.workflow_state} has no task reference.",
                        )
                    )

        try:
            self.compile_proxy(kind, definition.id, definition.version, payload=_model_dump(definition))
        except Exception as exc:
            unresolved_proxies.append(str(exc))
            issues.append(
                ValidationIssue(
                    level="error",
                    category="proxy",
                    message=f"Dynamic proxy compilation failed: {exc}",
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
            missing_dependencies=missing_dependencies,
            unresolved_proxies=unresolved_proxies,
        )

    def resolve_reference(self, reference: RegistryReference) -> RegistryDefinitionMeta:
        return self.get_definition(reference.kind, reference.definition_id, reference.version)

    def compile_proxy(
        self,
        kind: RegistryKind,
        definition_id: str,
        version: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Type:
        cache_key = (kind, definition_id, version or "latest")
        cached = self._proxy_cache.get(cache_key)
        if cached is not None:
            return cached

        definition_payload = payload or _model_dump(self.get_definition(kind, definition_id, version))
        model = self.MODEL_BY_KIND[kind](**definition_payload)
        if kind == "agents":
            proxy = self._compile_agent_proxy(model)
        elif kind == "tasks":
            proxy = self._compile_task_proxy(model)
        else:
            proxy = self._compile_workflow_proxy(model)
        self._proxy_cache[cache_key] = proxy
        return proxy

    def get_custom_agent_catalog(self) -> List[AgentDefinition]:
        catalog: List[AgentDefinition] = []
        for definition in self.list_definitions("agents"):
            definition = definition  # type: ignore[assignment]
            catalog.append(
                AgentDefinition(
                    id=definition.id,
                    name=definition.id,
                    description=definition.description.resolve("en-US", definition.id),
                    state_templates=definition.state_templates,
                    compatible_components=list(definition.allowed_components),
                    source="custom",
                    version=definition.version,
                    display_name=definition.display_name,
                    base_agent_type=definition.base_agent_type,
                )
            )
        return catalog

    def get_custom_task_catalog(self) -> List[TaskDefinition]:
        catalog: List[TaskDefinition] = []
        for definition in self.list_definitions("tasks"):
            definition = definition  # type: ignore[assignment]
            catalog.append(
                TaskDefinition(
                    id=definition.id,
                    name=definition.id,
                    description=definition.description.resolve("en-US", definition.id),
                    necessary_metrics=list(definition.necessary_metrics),
                    produced_states=list(definition.produced_states),
                    source="custom",
                    version=definition.version,
                    display_name=definition.display_name,
                    adapter_type=definition.adapter_type,
                    component=definition.component,
                    parameter_schema=definition.parameter_schema,
                    target_state_schema=definition.target_state_schema,
                )
            )
        return catalog

    def get_custom_workflow_catalog(self) -> List[WorkflowTypeDefinition]:
        catalog: List[WorkflowTypeDefinition] = []
        for definition in self.list_definitions("workflows"):
            definition = definition  # type: ignore[assignment]
            catalog.append(
                WorkflowTypeDefinition(
                    id=definition.id,
                    name=definition.id,
                    description=definition.description.resolve("en-US", definition.id),
                    property_templates=definition.property_templates,
                    states=list(definition.states),
                    start_state=definition.start_state,
                    trigger_conditions=list(definition.trigger_conditions),
                    task_bindings=list(definition.task_bindings),
                    critical_path=list(definition.critical_path),
                    source="custom",
                    version=definition.version,
                    display_name=definition.display_name,
                    adapter_type=definition.adapter_type,
                    supported_agent_types=list(definition.supported_agent_types),
                    registry_dependencies=list(definition.registry_dependencies),
                )
            )
        return catalog

    def iter_definition_refs(self, definitions: Iterable[RegistryDefinitionMeta]) -> List[RegistryReference]:
        refs: List[RegistryReference] = []
        for definition in definitions:
            refs.append(
                RegistryReference(
                    kind=getattr(definition, "kind", "workflows"),
                    definition_id=definition.id,
                    version=definition.version,
                    source="custom",
                )
            )
        return refs

    def get_usage(self, kind: RegistryKind, definition_id: str, version: Optional[str] = None) -> Dict[str, int]:
        target = {
            "kind": kind,
            "definition_id": definition_id,
            "version": version,
            "source": "custom",
        }
        config_count = 0
        run_count = 0
        config_dir = self.runtime_dir / "configs"
        runs_dir = self.runtime_dir / "runs"
        if config_dir.exists():
            for path in config_dir.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if self._payload_contains_reference(payload, target):
                    config_count += 1
        if runs_dir.exists():
            for path in runs_dir.glob("*/manifest.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if self._payload_contains_reference(payload, target):
                    run_count += 1
        return {"configs": config_count, "runs": run_count}

    def _compile_agent_proxy(self, definition: CustomAgentDefinition) -> Type:
        proxy_name = f"{_sanitize_token(definition.id)}_{_sanitize_token(definition.version)}_ProxyAgent"
        definition_payload = _model_dump(definition)

        def __init__(self, env, agent_name: str, properties=None, agent_id=None):
            merged_properties = {
                **definition_payload.get("default_properties", {}),
                **(properties or {}),
            }
            DroneAgent.__init__(self, env, agent_name, merged_properties, agent_id=agent_id)
            extra_states = {}
            for key, field in definition_payload.get("state_templates", {}).items():
                if key in self.state:
                    continue
                if isinstance(field, dict) and "default" in field:
                    extra_states[key] = field.get("default")
                elif key in merged_properties:
                    extra_states[key] = merged_properties.get(key)
            if extra_states:
                self.initialize_states(level_class=type(self), **extra_states)

        proxy = type(
            proxy_name,
            (DroneAgent,),
            {
                "__registry_definition__": definition,
                "__doc__": definition.description.resolve("en-US", definition.id),
                "__init__": __init__,
                "get_description": classmethod(
                    lambda cls: definition.description.resolve("en-US", definition.id)
                ),
            },
        )
        for key, field in definition.state_templates.items():
            AgentMeta.register_template(
                proxy,
                key,
                value_type=self._python_type_for_field(field.value_type),
                required=field.required,
                description=field.description,
            )
        return proxy

    def _compile_task_proxy(self, definition: CustomTaskDefinition) -> Type:
        adapter_name = str(definition.adapter_type or "").strip()
        adapter_class = self._builtin_task_classes.get(adapter_name)

        def _resolve_duration(instance) -> float:
            explicit = instance.properties.get("duration")
            if explicit is not None:
                try:
                    return max(float(explicit), 0.001)
                except (TypeError, ValueError):
                    return 10.0
            duration_field = definition.parameter_schema.get("duration")
            if duration_field and duration_field.default not in {None, ""}:
                try:
                    return max(float(duration_field.default), 0.001)
                except (TypeError, ValueError):
                    return 10.0
            return 10.0

        def estimate_remaining_time(self, performance_metrics: Dict[str, Any]) -> float:
            if self.progress >= 1.0:
                return 0.0
            adapter_mode = str(definition.adapter_type or "").lower()
            if adapter_mode == "metric_rate" and definition.necessary_metrics:
                metric_name = definition.necessary_metrics[0]
                metric_value = performance_metrics.get(metric_name, self.properties.get(metric_name, 0))
                try:
                    rate = max(float(metric_value), 1e-6)
                except (TypeError, ValueError):
                    rate = 1.0
                return max(0.0, 1.0 - self.progress) / rate
            duration = _resolve_duration(self)
            elapsed = max(self.env.now - (self.start_time or self.env.now), 0.0)
            return max(0.0, duration - elapsed)

        def _interpolate(current: Any, target: Any, progress: float):
            if isinstance(current, (int, float)) and isinstance(target, (int, float)):
                return current + (target - current) * progress
            if (
                isinstance(current, (list, tuple))
                and isinstance(target, (list, tuple))
                and len(current) == len(target)
                and all(isinstance(item, (int, float)) for item in current)
                and all(isinstance(item, (int, float)) for item in target)
            ):
                return [
                    current[index] + (target[index] - current[index]) * progress
                    for index in range(len(target))
                ]
            return target if progress >= 1.0 else current

        def _update_task_state(self, performance_metrics: Dict[str, Any]):
            adapter_mode = str(definition.adapter_type or "").lower()
            if adapter_mode == "metric_rate" and definition.necessary_metrics:
                metric_name = definition.necessary_metrics[0]
                metric_value = performance_metrics.get(metric_name, self.properties.get(metric_name, 0))
                try:
                    step = max(float(metric_value), 0.0) * max(self.env.now - self.last_update_time, 0.0)
                except (TypeError, ValueError):
                    step = 0.1
                self.progress = min(1.0, self.progress + step)
            else:
                duration = _resolve_duration(self)
                elapsed = max(self.env.now - (self.start_time or self.env.now), 0.0)
                self.progress = min(1.0, elapsed / duration)
            if self.progress >= 1.0:
                self.progress = 1.0

        def _get_task_specific_state_repr(self):
            result = {}
            for state_name in definition.produced_states:
                current = self.agent.get_state(state_name)
                target = self.target_state.get(state_name, self.properties.get(state_name, current))
                if target is None and state_name == "status":
                    target = "idle" if self.progress >= 1.0 else "active"
                elif target is None and state_name == "moving_status":
                    target = "hovering" if self.progress >= 1.0 else "flying"
                elif target is None:
                    target = current
                if current is None:
                    current = target
                result[state_name] = _interpolate(current, target, self.progress)
            return result

        attrs = {
            "__registry_definition__": definition,
            "__doc__": definition.description.resolve("en-US", definition.id),
            "NECESSARY_METRICS": list(definition.necessary_metrics),
            "PRODUCED_STATES": list(definition.produced_states),
            "estimate_remaining_time": estimate_remaining_time,
            "_update_task_state": _update_task_state,
            "_get_task_specific_state_repr": _get_task_specific_state_repr,
        }
        return type(
            f"{_sanitize_token(definition.id)}_{_sanitize_token(definition.version)}_ProxyTask",
            ((adapter_class or Task),),
            attrs,
        )

    def _compile_workflow_proxy(self, definition: CustomWorkflowDefinition) -> Type:
        proxy_name = f"{_sanitize_token(definition.id)}_{_sanitize_token(definition.version)}_ProxyWorkflow"

        def _resolve_binding_task_class(binding) -> Optional[str]:
            if binding.task_ref:
                proxy_class = self.compile_proxy(
                    "tasks",
                    binding.task_ref.definition_id,
                    binding.task_ref.version,
                )
                return proxy_class.__name__
            if binding.task_class in self._builtin_task_classes:
                return binding.task_class
            if binding.task_class:
                alias = self._builtin_task_aliases.get(_sanitize_token(binding.task_class).lower())
                if alias:
                    return alias
            return binding.task_class

        def _setup_transitions(self):
            start_state = definition.start_state or (definition.states[0] if definition.states else "idle")
            self.status_machine.set_start_transition(start_state)
            for condition in definition.trigger_conditions:
                description = condition.description
                if condition.trigger_type == "state":
                    operator = self._registry_operator(condition.operator or condition.config.get("operator"))
                    target_value = condition.target_value
                    if target_value is None:
                        target_value = condition.config.get("target_value")
                    self.status_machine.add_transition(
                        condition.source_state,
                        condition.target_state,
                        agent_state={
                            "agent_id": condition.config.get("agent_id") or self.owner.id,
                            "state_key": condition.config.get("state_key") or condition.source_ref,
                            "operator": operator,
                            "target_value": target_value,
                        },
                        description=description,
                    )
                elif condition.trigger_type == "event":
                    self.status_machine.add_transition(
                        condition.source_state,
                        condition.target_state,
                        event_trigger={
                            "source_id": condition.config.get("source_id") or self.id,
                            "event_name": condition.config.get("event_name") or condition.source_ref,
                            "value_key": condition.config.get("value_key"),
                            "operator": self._registry_operator(condition.operator or condition.config.get("operator")),
                            "target_value": condition.target_value if condition.target_value is not None else condition.config.get("target_value"),
                        },
                        description=description,
                    )
                elif condition.trigger_type == "time":
                    self.status_machine.add_transition(
                        condition.source_state,
                        condition.target_state,
                        time_trigger={
                            key: value
                            for key, value in {
                                "trigger_time": condition.config.get("trigger_time"),
                                "interval": condition.config.get("interval"),
                                "cron_expr": condition.config.get("cron_expr"),
                            }.items()
                            if value is not None
                        },
                        description=description,
                    )
                else:
                    raise ValueError(
                        f"Unsupported custom workflow trigger type: {condition.trigger_type}"
                    )

        def _get_current_suggested_task(self):
            if self.status != WorkflowStatus.RUNNING:
                return None
            current_state = getattr(self.status_machine, "current_status", None)
            for binding in definition.task_bindings:
                if binding.workflow_state == current_state:
                    task_class = _resolve_binding_task_class(binding)
                    return {
                        "component": binding.component,
                        "task_class": task_class,
                        "task_name": binding.task_name or task_class or "CustomTask",
                        "workflow_id": self.id,
                        "target_state": dict(binding.target_state),
                        "properties": dict(binding.properties),
                    }
            return None

        attrs = {
            "__registry_definition__": definition,
            "__doc__": definition.description.resolve("en-US", definition.id),
            "get_description": classmethod(
                lambda cls: definition.description.resolve("en-US", definition.id)
            ),
            "_setup_transitions": _setup_transitions,
            "get_current_suggested_task": _get_current_suggested_task,
            "_registry_operator": staticmethod(self._registry_operator),
        }
        proxy = WorkflowMeta(proxy_name, (Workflow,), attrs)
        for key, field in definition.property_templates.items():
            WorkflowMeta.register_template(
                proxy,
                key,
                value_type=self._python_type_for_field(field.value_type),
                required=field.required,
                description=field.description,
            )
        return proxy

    def _load_path(self, kind: RegistryKind, path: Path) -> RegistryDefinitionMeta:
        return self.MODEL_BY_KIND[kind](**json.loads(path.read_text()))

    def _definition_path(self, kind: RegistryKind, definition_id: str, version: str) -> Path:
        return self._kind_dir(kind) / f"{_sanitize_token(definition_id)}__{_sanitize_token(version)}.json"

    def _kind_dir(self, kind: RegistryKind) -> Path:
        return self.base_dir / kind

    def _payload_contains_reference(self, payload: Any, target: Dict[str, Any]) -> bool:
        if isinstance(payload, dict):
            if (
                payload.get("kind") == target["kind"]
                and payload.get("definition_id") == target["definition_id"]
                and payload.get("source") == target["source"]
            ):
                if target["version"] in {None, "", payload.get("version")}:
                    return True
            return any(self._payload_contains_reference(value, target) for value in payload.values())
        if isinstance(payload, list):
            return any(self._payload_contains_reference(value, target) for value in payload)
        return False

    def _python_type_for_field(self, value_type: str):
        normalized = str(value_type or "any").lower()
        if normalized in {"int", "integer"}:
            return int
        if normalized in {"float", "number"}:
            return float
        if normalized in {"bool", "boolean"}:
            return bool
        if normalized in {"list", "array"}:
            return list
        if normalized in {"dict", "object", "map"}:
            return dict
        if normalized in {"str", "string"}:
            return str
        return None

    def _registry_operator(self, value: Optional[str]):
        if not value:
            return TriggerOperator.EQUALS
        normalized = str(value).upper()
        return getattr(TriggerOperator, normalized, TriggerOperator.EQUALS)
