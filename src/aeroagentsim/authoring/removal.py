"""Plan entity-reference removal in memory; the store validates and commits once."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.scenario.loader import UniqueLoader

_DROP = object()
_DESCRIPTIVE = {"label", "label_zh", "title", "title_zh", "description", "description_zh", "type", "typeId", "plugin", "field", "schema", "initial", "state", "status", "from", "to"}
_MAPS = {"profiles", "subjects", "bypass_routes", "zone_exemptions", "entities", "resources"}
_STRING_ENTITY_FACTS = {"lowalt.task_origin", "lowalt.task_destination", "lowalt.task_vehicle", "lowalt.membership", "lowalt.conflicts"}


def generated_member(declaration: Any, entity_id: str) -> bool:
    if not isinstance(declaration, dict):
        return False
    prefix, count = declaration.get("prefix"), declaration.get("count")
    if not isinstance(prefix, str) or not prefix or type(count) is not int or not entity_id.startswith(prefix):
        return False
    suffix = entity_id[len(prefix):]
    return suffix.isdigit() and str(int(suffix)) == suffix and 1 <= int(suffix) <= count


def pointer(path: tuple[str, ...]) -> str:
    return "".join("/" + part.replace("~", "~0").replace("/", "~1") for part in path)


def references(value: Any, names: set[str], *, map_keys: bool = False) -> bool:
    if isinstance(value, str):
        return value in names
    if isinstance(value, list):
        return any(references(child, names) for child in value)
    if isinstance(value, dict):
        return any(
            (map_keys and key in names) or references(child, names, map_keys=key in _MAPS or key == "predicate_bindings")
            for key, child in value.items() if key not in _DESCRIPTIVE and not (key == "states" and isinstance(child, list))
        )
    return False


class RemovalPlan:
    def __init__(self, document: dict[str, Any], entity_id: str, root: Path) -> None:
        self.original = copy.deepcopy(document)
        self.entity_id, self.root = entity_id, root.resolve()
        self.fact_schemas = {field["id"]: field["schema"] for field in document.get("registry", {}).get("fields", [])}
        if "generators" in self.original and not isinstance(self.original["generators"], dict):
            raise TypeError("generators: mapping required / 生成器须为映射")
        if generated_member(self.original.get("generators", {}).get("fleet"), entity_id):
            from aeroagentsim.packs.lowalt.template import expand

            # Materialize the surviving fleet before removing its regeneration
            # rule, so a later load neither resurrects this id nor loses siblings.
            declaration = self.original["generators"]["fleet"]
            members = {declaration["prefix"] + str(i + 1) for i in range(declaration["count"])}
            missing = members - {entity["id"] for entity in self.original["entities"]}
            if missing:
                expanded = copy.deepcopy(self.original)
                expand(expanded)
                self.original["entities"].extend(entity for entity in expanded["entities"] if entity["id"] in missing)
        self.dependencies: dict[str, dict[str, str]] = {}
        self.external: dict[tuple[str, ...], str] = {}
        self.wrappers: set[tuple[str, ...]] = set()
        self.packages: list[tuple[tuple[str, ...], Any, Any]] = []
        specs = self.original.get("behaviours", [])
        for i, spec in enumerate(specs):
            path = ("behaviours", str(i))
            expanded = self.package(spec, self.root, path, set())
            self.packages.append((path, copy.deepcopy(spec), copy.deepcopy(expanded)))
            specs[i] = expanded
        for engine_id, engine in self.original.get("engines", {}).items():
            if engine.get("plugin") != "behaviour":
                continue
            specs = engine.get("config", {}).get("packages", [])
            for i, spec in enumerate(specs):
                path = ("engines", engine_id, "config", "packages", str(i))
                expanded = self.package(spec, self.root, path, set())
                self.packages.append((path, copy.deepcopy(spec), copy.deepcopy(expanded)))
                specs[i] = expanded

    def package(self, spec: Any, base: Path, path: tuple[str, ...], active: set[Path]) -> dict[str, Any]:
        if not isinstance(spec, dict):
            raise TypeError("behaviours: package mapping required / 行为包须为映射")
        source: Path | None = None
        if "path" in spec:
            source = (base / spec["path"]).resolve()
            if not source.is_relative_to(self.root):
                raise ValueError("behaviours.path: must stay inside workspace / 行为包路径须在工作区内")
            if source in active:
                raise ValueError("behaviours.imports: cyclic package references / 行为包循环引用")
            active.add(source)
            document = yaml.load(source.read_bytes(), Loader=UniqueLoader)
            self.external[path] = source.relative_to(self.root).as_posix()
            base = source.parent
        else:
            document = spec.get("document", spec)
            if "document" in spec:
                self.wrappers.add(path)
        if not isinstance(document, dict):
            raise TypeError("behaviours: package mapping required / 行为包须为映射")
        document = copy.deepcopy(document)
        for i, imported in enumerate(document.get("imports", [])):
            document["imports"][i] = self.package(imported, base, (*path, "imports", str(i)), active)
        if "snapshot" in document.get("registry", {}):
            snapshot = (base / document["registry"]["snapshot"]).resolve()
            if not snapshot.is_relative_to(self.root):
                raise ValueError("behaviours.registry.snapshot: must stay inside workspace / 行为包快照须在工作区内")
            document["registry"]["snapshot"] = str(snapshot)
        if source is not None:
            active.remove(source)
        return document

    def add(self, path: tuple[str, ...], kind: str, names: set[str], *, reason: str | None = None) -> None:
        location = pointer(path)
        authored = location
        ancestors = sorted((row for row in self.external.items() if path[:len(row[0])] == row[0]), key=lambda row: len(row[0]))
        visible_path = ancestors[0][0] if ancestors else path
        for wrapper in sorted(self.wrappers, key=len, reverse=True):
            if visible_path[:len(wrapper)] == wrapper:
                visible_path = (*visible_path[:len(wrapper)], "document", *visible_path[len(wrapper):])
        location = pointer(visible_path)
        if ancestors:
            root, source = ancestors[-1]
            authored = source + "#" + pointer(path[len(root):])
        node: Any = self.original
        for part in path:
            node = node[int(part)] if isinstance(node, list) else node[part]
        actual = {name for name in names if references(node, {name}) or name == path[-1]}
        if reason is None:
            if kind == "generator" and generated_member(node, self.entity_id):
                reason = f"generates {self.entity_id} / 会生成 {self.entity_id}"
            else:
                reason = f"references {', '.join(sorted(actual))} / 引用 {', '.join(sorted(actual))}"
        self.dependencies[authored] = {
            "path": location, "kind": kind,
            "detail": f"Remove {authored}; {reason} / 删除 {authored}",
        }

    def rows(self, rows: list[Any], path: tuple[str, ...], kind: str, names: set[str]) -> list[Any]:
        kept = []
        for i, row in enumerate(rows):
            if references(row, names):
                self.add((*path, str(i)), kind, names)
            else:
                kept.append(row)
        return kept

    def workloads(self, rows: list[Any], path: tuple[str, ...], names: set[str]) -> list[Any]:
        """A workload shares endpoints and release settings across task members."""
        kept = []
        for i, row in enumerate(rows):
            location = (*path, str(i))
            if not isinstance(row, dict) or not isinstance(row.get("tasks"), list):
                raise ValueError(f"{pointer(location)}.tasks: task list required / 须提供任务列表")
            indices = [j for j, task in enumerate(row["tasks"]) if task in names]
            if indices and len(indices) == len(row["tasks"]):
                self.add(location, "generator", names)
                continue
            cleaned = copy.deepcopy(row)
            if indices:
                cleaned["tasks"] = self.rows(row["tasks"], (*location, "tasks"), "generator", names)
                arrivals = row.get("arrivals")
                if not isinstance(arrivals, dict):
                    raise ValueError(f"{pointer(location)}.arrivals: release configuration required")
                if arrivals.get("mode") == "scheduled":
                    times = arrivals.get("times_ns")
                    if not isinstance(times, list) or len(times) != len(row["tasks"]):
                        raise ValueError(f"{pointer(location)}.arrivals: one scheduled time per task required / 每个任务须对应一个释放时间")
                    cleaned["arrivals"]["times_ns"] = [time for j, time in enumerate(times) if j not in indices]
                    for j in indices:
                        self.add((*location, "arrivals", "times_ns", str(j)), "generator", names,
                                 reason=f"scheduled release for {row['tasks'][j]} / 对应 {row['tasks'][j]} 的定时释放")
            for key in ("origins", "destinations"):
                if key in row:
                    cleaned[key] = self.rows(row[key], (*location, key), "generator", names)
            kept.append(cleaned)
        return kept

    def binding_rows(self, section: str, rows: list[Any], engines: set[str]) -> list[Any]:
        kept = []
        for i, row in enumerate(rows):
            if section in {"exact", "rules", "lifecycle", "relations", "obligations"} and isinstance(row, dict):
                dependent = row.get("entity") == self.entity_id or row.get("ids") == self.entity_id or any(row.get(key) in engines for key in ("writer", "controller"))
            elif section == "commands" and isinstance(row, dict):
                dependent = row.get("target") in engines or references(row.get("payload"), {self.entity_id})
            elif section == "samples" and isinstance(row, dict):
                dependent = references(row.get("bindings"), {self.entity_id}) or row.get("partition") in engines or references(row.get("upstream"), engines)
            elif section in {"cohorts", "cancel_sources"}:
                dependent = references(row, engines)
            else:
                dependent = references(row, {self.entity_id} | engines)
            if dependent:
                self.add(("bindings", section, str(i)), "binding", {self.entity_id} | engines)
            else:
                kept.append(row)
        return kept

    def config(self, value: Any, path: tuple[str, ...], names: set[str]) -> Any:
        if isinstance(value, str):
            return _DROP if value in names else value
        if isinstance(value, list):
            if len(path) == 4 and path[0] == "engines" and path[-2:] == ("config", "generators") and self.original["engines"][path[1]].get("plugin") == "lowalt_tasks":
                return self.workloads(value, path, names)
            return self.rows(value, path, "engine", names)
        if not isinstance(value, dict):
            return value
        if "$ref" in value and references(value["$ref"], names):
            return _DROP
        result = {}
        for key, child in value.items():
            child_path = (*path, key)
            if key in names and path[-1] in _MAPS:
                self.add(child_path, "engine", names)
                continue
            cleaned = child if key in _DESCRIPTIVE else self.config(child, child_path, names)
            if cleaned is _DROP:
                if path[-1] in _MAPS:
                    self.add(child_path, "engine", names)
                    continue
                return _DROP
            result[key] = cleaned
        return result

    def behaviour(self, package: dict[str, Any], path: tuple[str, ...], names: set[str], symbols: set[str]) -> None:
        for i, imported in enumerate(package.get("imports", [])):
            self.behaviour(imported, (*path, "imports", str(i)), names, symbols)
        for section in ("predicates", "chains"):
            for key, declaration in list(package.get(section, {}).items()):
                if references(declaration, names | symbols):
                    self.add((*path, section, key), "behaviour", names | symbols)
                    del package[section][key]
                    symbols.add(key)
        bootstrap = package.get("bootstrap_relations")
        if isinstance(bootstrap, dict):
            if references(bootstrap.get("owner"), names):
                self.add((*path, "bootstrap_relations"), "behaviour", names)
                del package["bootstrap_relations"]
            else:
                bootstrap["assertions"] = self.rows(bootstrap["assertions"], (*path, "bootstrap_relations", "assertions"), "relation", names)
        for section in ("bindings", "conflicts", "sampled_contexts", "injection_points"):
            if section in package:
                package[section] = self.rows(package[section], (*path, section), "behaviour", names | symbols)

    def build(self) -> tuple[dict[str, Any], list[dict[str, str]]]:
        removed_engines: set[str] = set()
        removed_symbols: set[str] = set()
        # Rebuild from the original tree on each closure pass so preview pointers
        # retain authored indices, including after removing adjacent list rows.
        while True:
            before = (len(removed_engines), len(removed_symbols))
            self.dependencies.clear()
            result = copy.deepcopy(self.original)
            names = {self.entity_id} | removed_engines
            entities = []
            for i, entity in enumerate(result["entities"]):
                if entity["id"] == self.entity_id:
                    continue
                for field, value in list(entity["facts"].items()):
                    path = ("entities", str(i), "facts", field)
                    schema = self.fact_schemas.get(field, {})
                    if isinstance(schema, dict) and schema.get("type") == "string" and field not in _STRING_ENTITY_FACTS:
                        continue
                    if references(value, {self.entity_id}):
                        if isinstance(value, list):
                            entity["facts"][field] = self.rows(value, path, "relation", {self.entity_id})
                        else:
                            self.add(path, "relation", {self.entity_id})
                            del entity["facts"][field]
                entities.append(entity)
            result["entities"] = entities
            for key, rows in result.get("bindings", {}).items():
                if isinstance(rows, list):
                    result["bindings"][key] = self.binding_rows(key, rows, removed_engines)
                elif isinstance(rows, dict):
                    for name, value in list(rows.items()):
                        if references(value, removed_engines if key == "lifecycle_participants" else names):
                            self.add(("bindings", key, name), "binding", names)
                            del rows[name]
            for key, engine in list(result.get("engines", {}).items()):
                if engine.get("plugin") == "behaviour":
                    capabilities = engine.get("config", {}).get("capabilities", {})
                    for capability, declaration in list(capabilities.items()):
                        if references(declaration, names):
                            self.add(("engines", key, "config", "capabilities", capability), "engine", names)
                            del capabilities[capability]
                            removed_symbols.add(capability)
                    for i, package in enumerate(engine.get("config", {}).get("packages", [])):
                        self.behaviour(package, ("engines", key, "config", "packages", str(i)), names, removed_symbols)
                    other = {name: value for name, value in engine["config"].items() if name not in {"packages", "capabilities"}}
                    cleaned = self.config(other, ("engines", key, "config"), names)
                    if cleaned is _DROP or key in removed_engines:
                        self.add(("engines", key), "engine", names)
                        del result["engines"][key]
                        removed_engines.add(key)
                    else:
                        engine["config"].update(cleaned)
                    continue
                cleaned = self.config(engine["config"], ("engines", key, "config"), names)
                profiles = engine["config"].get("profiles")
                empty_environment = engine.get("plugin") == "environment" and cleaned is not _DROP and isinstance(profiles, dict) and len(profiles) > 0 and cleaned.get("profiles") == {}
                if cleaned is _DROP or empty_environment or key in removed_engines:
                    self.add(("engines", key), "engine", names)
                    del result["engines"][key]
                    removed_engines.add(key)
                else:
                    engine["config"] = cleaned
            for i, package in enumerate(result.get("behaviours", [])):
                self.behaviour(package, ("behaviours", str(i)), names, removed_symbols)
            if "presentation" in result:
                result["presentation"] = self.rows(result["presentation"], ("presentation",), "presentation", names)
            for section in ("generators", "relations", "resources", "tasks", "agents", "decisions", "ingress_streams"):
                declarations = result.get(section)
                kind = "generator" if section == "generators" else "relation" if section in {"relations", "resources"} else "other"
                if isinstance(declarations, list):
                    result[section] = self.rows(declarations, (section,), kind, names)
                elif isinstance(declarations, dict):
                    for key, declaration in list(declarations.items()):
                        generated = section == "generators" and generated_member(declaration, self.entity_id)
                        if key in names or generated or references(declaration, names):
                            self.add((section, key), kind, names)
                            del declarations[key]
                    if section == "generators" and not declarations:
                        del result[section]
            if before == (len(removed_engines), len(removed_symbols)):
                break
        for path, original, expanded in self.packages:
            node: Any = result
            for token in path[:-1]:
                if isinstance(node, dict) and token not in node:
                    node = None
                    break
                node = node[int(token)] if isinstance(node, list) else node[token]
            if node is not None:
                token: Any = int(path[-1]) if isinstance(node, list) else path[-1]
                if node[token] == expanded:
                    node[token] = original
        return result, sorted(self.dependencies.values(), key=lambda row: (row["path"], row["detail"]))
