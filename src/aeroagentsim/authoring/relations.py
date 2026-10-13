"""Concrete initial links use the existing source-scope writer permission."""

from __future__ import annotations

import copy
from fnmatch import fnmatchcase
from typing import Any

import yaml

from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import UniqueLoader
from aeroagentsim.scenario.paths import source_path

from .workspace import WorkspaceOperationError, WorkspaceStore, validate_configuration


def _literal_scope(identifier: str) -> str:
    return "".join("[[]" if char == "[" else f"[{char}]" if char in "*?" else char for char in identifier)


def add_initial_relation(store: WorkspaceStore, identifier: str, body: dict[str, Any]) -> dict[str, Any]:
    """Check real endpoints and permissions, then atomically persist a bootstrap assertion.

    A covering rule is reused verbatim, including inherited source types and glob
    scopes. An existing non-covering rule must be edited explicitly; broadening it
    here would silently grant ownership over other research objects.
    """
    required = {"relation", "writer", "source", "target", "revision"}
    if set(body) != required or any(not isinstance(body.get(key), str) or not body[key].strip() for key in required - {"revision"}):
        raise WorkspaceOperationError(None, "relations", "Choose a relation, writer, source and target and supply the draft revision. / 请选择关系、写入者、源与目标，并提供草稿修订号。")
    if type(body["revision"]) is not int or body["revision"] < 0:
        raise WorkspaceOperationError(None, "revision", "The draft revision must be a nonnegative integer. / 草稿修订号须为非负整数。")
    with store.lock:
        draft = store.get(identifier)
        store.check_revision(draft, body["revision"])
        document = copy.deepcopy(draft["scenario"])
        base = store._base(document, identifier)
        loaded = load_scenario(document, base=base)
        relation_id, writer = body["relation"], body["writer"]
        try:
            relation = loaded.registry.relation(relation_id)
        except Exception as error:
            raise WorkspaceOperationError(None, "relation", f"Relation {relation_id}: {error} / 关系 {relation_id}：{error}") from error
        refs = {ref.id: ref for ref in loaded.manifest.entities}
        for field, required_type in (("source", relation.source_type), ("target", relation.target_type)):
            ref = refs.get(body[field])
            if ref is None or not loaded.registry.is_a(ref.type_id, required_type):
                raise WorkspaceOperationError(None, field, f"Choose an existing {required_type} instance for {field}. / 请为 {field} 选择已有的 {required_type} 实例。")
        source, target = refs[body["source"]], refs[body["target"]]
        writers = store.relation_writers(identifier).get(relation_id, [])
        option = next((item for item in writers if item["writer"] == writer), None)
        behaviours = [(key, spec) for key, spec in document["engines"].items() if spec["plugin"] == "behaviour"]
        if len(behaviours) != 1 or option is None or option["plugin"] != "behaviour" or option["engine_id"] != writer:
            raise WorkspaceOperationError(None, "writer", f"Configure a behaviour engine that writes {relation_id} in Execution components. / 请在“执行组件”配置可写入 {relation_id} 的行为引擎。")

        packages = document.get("behaviours", behaviours[0][1].get("config", {}).get("packages", []))
        if not isinstance(packages, list):
            raise WorkspaceOperationError(None, "behaviours", "Behaviour packages must be a list. / 行为包须为列表。")
        resolved = []
        for spec in packages:
            if "path" in spec:
                spec = yaml.load(source_path(spec["path"], base).read_bytes(), Loader=UniqueLoader)
            resolved.append(spec.get("document", spec))
        links = [edge for spec in resolved for edge in spec.get("bootstrap_relations", {}).get("assertions", [])]
        related = [edge for edge in links if edge["relation"] == relation_id]
        if any(edge["source"]["$ref"]["id"] == source.id and edge["target"]["$ref"]["id"] == target.id for edge in related):
            raise WorkspaceOperationError(None, "relation", "This initial link already exists. / 此初始连接已存在。")
        for field, limit, ref in (("source", relation.targets_per_source.maximum, source), ("target", relation.sources_per_target.maximum, target)):
            if limit is not None and sum(edge[field]["$ref"]["id"] == ref.id for edge in related) >= limit:
                raise WorkspaceOperationError(None, field, "This link exceeds the relation's endpoint limit. / 此连接超出了关系允许的端点数量。")

        rules = document.setdefault("bindings", {}).setdefault("relations", [])
        own = [rule for rule in rules if rule["writer"] == writer and rule["relation"] == relation_id]
        covers = lambda rule: loaded.registry.is_a(source.type_id, rule["type"]) and fnmatchcase(source.id, rule.get("ids", "*"))
        covering = [rule for rule in rules if rule["relation"] == relation_id and covers(rule)]
        if len(covering) > 1 or covering and covering[0]["writer"] != writer:
            raise WorkspaceOperationError(None, "writer", f"Resolve the competing write permissions for {relation_id} from {source.id} in Relations & resources. / 请在“关系与资源”解决 {source.id} 写入 {relation_id} 的冲突权限。")
        if not covering:
            if own:
                raise WorkspaceOperationError(None, "source", f"The existing {writer} permission for {relation_id} does not cover {source.id}. Edit its source scope in File (YAML) before adding this link. / {writer} 写入 {relation_id} 的现有权限不覆盖 {source.id}。请先在“文件”（YAML）修改其源范围，再添加连接。")
            rules.append({"writer": writer, "relation": relation_id, "type": relation.source_type, "ids": _literal_scope(source.id)})
        occupied = {spec["id"] for spec in resolved} | {edge["id"] for edge in links}
        index = 1
        while f"workbench-link-{index}" in occupied:
            index += 1
        link_id = f"workbench-link-{index}"
        package = {
            "format": "aeroagentsim.behaviour-package/v1", "id": link_id, "revision": 1,
            "predicates": {}, "chains": {}, "bindings": [],
            "budgets": {"max_transitions_per_instance_per_ns": 64, "max_instances": 1},
            "bootstrap_relations": {"owner": writer, "assertions": [{"id": link_id, "relation": relation_id,
                "source": {"$ref": source.to_data()}, "target": {"$ref": target.to_data()}}]},
        }
        document["behaviours"] = [*packages, package]
        try:
            validate_configuration(document, base=base, directory=store.directory(identifier))
        except Exception as error:
            raise WorkspaceOperationError(None, "relation", f"Initial link {relation_id}: {error} / 初始连接 {relation_id} 无效：{error}") from error
        store.save(identifier, {"scenario": document, "revision": draft["revision"]})
        return store.summary(identifier)
