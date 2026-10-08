"""Identity, actual inheritance, browsing navigation and cross-artifact claims."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .core import Audit, escape


def run(audit: Audit) -> None:
    roots = []
    names = defaultdict(list)
    detached = []
    categories = defaultdict(list)
    cycles = set()
    for identity, row in sorted(audit.entities.items()):
        data = row.data
        parent = data.get("parent")
        suggested = data.get("parentStatus") == "suggested"
        if parent is None or suggested:
            roots.append(identity)
        elif parent not in audit.entities:
            audit.add(
                "entity.dangling_parent",
                "blocker",
                row,
                f"Actual parent {parent!r} is missing",
                pointer=row.pointer + "/parent",
            )
        trail = []
        current = identity
        while current in audit.entities and current not in trail:
            trail.append(current)
            ancestor = audit.entities[current].data
            current = (
                None
                if ancestor.get("parentStatus") == "suggested"
                else ancestor.get("parent")
            )
        if current in trail:
            cycle = tuple(sorted(trail[trail.index(current) :]))
            if cycle not in cycles:
                cycles.add(cycle)
                audit.add(
                    "entity.inheritance_cycle",
                    "blocker",
                    row,
                    "Actual is-a cycle",
                    details={"ids": list(cycle)},
                )
        if type(data.get("abstract")) is not bool:
            audit.add(
                "entity.abstract_flag",
                "blocker",
                row,
                f"Abstract flag must be boolean, got {data.get('abstract')!r}",
                pointer=row.pointer + "/abstract",
            )
        if (
            data.get("classification") == "unresolved_profile"
            or parent is None
            and identity != "oo:ModelObject"
        ):
            detached.append(identity)
        if "oo:ModelObject" not in audit.ancestors(identity):
            audit.add(
                "entity.unreachable_root",
                "major" if identity in detached or suggested else "blocker",
                row,
                "No adopted is-a path to oo:ModelObject; original/suggested parent is not promoted",
            )
        name = data.get("name")
        if isinstance(name, str):
            names[name].append(identity)
        navigation = data.get("navigation")
        if not isinstance(navigation, dict) or not navigation.get("view"):
            audit.add(
                "entity.navigation_missing", "major", row, "Primary directory missing"
            )
        else:
            view = navigation["view"]
            categories[view].append(identity)
            if parent in audit.entities and not suggested:
                parent_nav = audit.entities[parent].data.get("navigation", {})
                if parent_nav.get("view") != view and parent != "oo:ModelObject":
                    audit.add(
                        "entity.cross_directory_inheritance",
                        "info",
                        row,
                        f"Browsing directory {view!r} differs from actual parent's {parent_nav.get('view')!r}; categories are not is-a edges",
                    )
        for key in ("ownFieldIds", "originalOwnFieldIds"):
            for i, field in enumerate(data.get(key, [])):
                if field not in audit.fields:
                    audit.add(
                        "entity.field_reference",
                        "blocker",
                        row,
                        f"{key} points to absent field {field!r}",
                        pointer=row.pointer + f"/{key}/{i}",
                    )
        for i, association in enumerate(data.get("objectAssociations", [])):
            if association.get("relationId") not in audit.relations:
                audit.add(
                    "entity.relation_reference",
                    "blocker",
                    row,
                    f"Association relation {association.get('relationId')!r} is absent",
                    pointer=row.pointer + f"/objectAssociations/{i}",
                )
    if "oo:ModelObject" not in audit.entities:
        audit.add(
            "entity.declared_root",
            "blocker",
            "entity-directory/data/concepts.json",
            "Declared root oo:ModelObject missing",
        )
    elif audit.entities["oo:ModelObject"].data.get("parent") is not None:
        audit.add(
            "entity.declared_root",
            "blocker",
            audit.entities["oo:ModelObject"],
            "Declared root has an actual parent",
        )
    if len(roots) != 1 or roots != ["oo:ModelObject"]:
        audit.add(
            "entity.multiple_roots",
            "major",
            "entity-directory/data/concepts.json",
            f"{len(roots)} roots/detached identities; adopted root is oo:ModelObject",
            details={"ids": roots},
            count=len(roots),
        )
    for name, identities in sorted(names.items()):
        if len(identities) > 1:
            audit.add(
                "entity.duplicate_name",
                "minor",
                audit.entities[identities[0]],
                f"Display name {name!r} shared by distinct IDs",
                count=len(identities),
                details={"ids": identities},
            )
    if detached:
        audit.add(
            "profile.unattached",
            "major",
            "entity-directory/data/concepts.json",
            f"{len(detached)} unattached identities require reviewed identity/owner bindings; no implicit parent repair",
            count=len(detached),
            details={"ids": detached},
        )
    audit.metrics["hierarchy"] = {
        "roots": roots,
        "detached_profile_ids": detached,
        "abstract_counts": dict(
            Counter(str(r.data.get("abstract")) for r in audit.entity_rows)
        ),
        "cycles": len(cycles),
    }
    audit.metrics["directories"] = dict(sorted(categories.items()))
    nav_path = "entity-directory/data/navigation.json"
    nav = audit.documents.get(nav_path, {})
    primary = defaultdict(list)
    visible_counts = {}
    labels = {e["key"]: e.get("label", e["key"]) for e in nav.get("homepage", [])}
    audit.metrics["directory_labels"] = labels
    rows = nav.get("homepage", []) + nav.get("extraViews", [])
    for i, entry in enumerate(rows):
        key = entry.get("key")
        ids = entry.get("ids", [])
        visible_counts[key] = len(set(ids))
        for identity in ids:
            if identity not in audit.entities:
                audit.add(
                    "navigation.dangling_id",
                    "blocker",
                    nav_path,
                    f"View {key} references absent type {identity}",
                    pointer=f"/homepage/{i}/ids",
                )
        if entry.get("count") != len(set(ids)):
            audit.add(
                "cross.navigation_count",
                "minor",
                nav_path,
                f"View {key}: claimed count {entry.get('count')}, actual {len(set(ids))}",
            )
        actual_primary = set(categories.get(key, []))
        if entry.get("primaryCount") is not None and entry["primaryCount"] != len(
            actual_primary
        ):
            audit.add(
                "cross.navigation_primary_count",
                "major",
                nav_path,
                f"View {key}: claimed primary {entry['primaryCount']}, actual {len(actual_primary)}",
            )
        if actual_primary - set(ids):
            audit.add(
                "navigation.primary_coverage",
                "major",
                nav_path,
                f"View {key} omits primary IDs",
                details={"ids": sorted(actual_primary - set(ids))},
            )

        def topics(nodes, pointer, view_key=key):
            for j, topic in enumerate(nodes):
                here = pointer + f"/{j}"
                descendants = set(topic.get("ownIds", []))
                descendants.update(
                    topics(topic.get("children", []), here + "/children")
                )
                for identity in topic.get("ownIds", []):
                    if (
                        identity in audit.entities
                        and audit.entities[identity]
                        .data.get("navigation", {})
                        .get("view")
                        == view_key
                    ):
                        primary[identity].append(view_key)
                if set(topic.get("ids", [])) != descendants:
                    audit.add(
                        "navigation.topic_closure",
                        "major",
                        nav_path,
                        "Topic ids disagree with ownIds + descendant closure",
                        pointer=here,
                    )
                if topic.get("count") != len(set(topic.get("ids", []))):
                    audit.add(
                        "cross.topic_count",
                        "minor",
                        nav_path,
                        "Topic count disagrees with distinct IDs",
                        pointer=here,
                    )
                yield from descendants

        for identity in entry.get("baseTypeIds", []):
            if (
                identity in audit.entities
                and audit.entities[identity].data.get("navigation", {}).get("view")
                == key
            ):
                primary[identity].append(key)
        list(
            topics(
                entry.get("topics", []),
                ("/homepage/" if i < len(nav.get("homepage", [])) else "/extraViews/")
                + str(i)
                + "/topics",
            )
        )
    for identity, entries in primary.items():
        if len(entries) != 1:
            audit.add(
                "navigation.primary_duplicate",
                "major",
                audit.entities.get(identity, nav_path),
                f"Identity occurs {len(entries)} times in primary topic ownIds",
                count=len(entries),
            )
    for identity in audit.entities.keys() - primary.keys():
        audit.add(
            "navigation.primary_unclassified",
            "major",
            audit.entities[identity],
            "Identity absent from all primary topic ownIds",
        )
    audit.metrics["navigation_visible_counts"] = visible_counts
    totals = audit.documents.get("entity-directory/data/concepts.json", {}).get(
        "totals", {}
    )
    observed = {
        "unique": len(audit.entities),
        "original950": sum(
            r.data.get("origin") == "original950" for r in audit.entity_rows
        ),
        "restored14": sum(
            r.data.get("origin") == "restored14" for r in audit.entity_rows
        ),
        "newDesign6": sum(
            r.data.get("origin") == "newDesign6" for r in audit.entity_rows
        ),
        "informationRecords": sum(
            r.data.get("informationRecord") is True for r in audit.entity_rows
        ),
        "objectAssociations": sum(
            len(r.data.get("objectAssociations", [])) for r in audit.entity_rows
        ),
        "crossIndexLinks": sum(
            len(r.data.get("navigation", {}).get("cross", []))
            for r in audit.entity_rows
        ),
    }
    for key, actual in observed.items():
        if key in totals and totals[key] != actual:
            audit.add(
                "cross.entity_counts",
                "major",
                "entity-directory/data/concepts.json",
                f"{key}: claimed {totals[key]}, actual {actual}",
                pointer="/totals/" + key,
            )
    audit.metrics["entity_counts"] = observed
    for name in ("entity-directory/README.md", "semantic-directory/README.md"):
        path = audit.root / name
        if path.is_file():
            text = audit.read_text(path)
            for match in re.finditer(r"([\d,]+)\s*个(?:唯一ID|身份)", text):
                claim = int(match[1].replace(",", ""))
                if claim != len(audit.entities):
                    audit.add(
                        "cross.readme_identity_count",
                        "minor",
                        name,
                        f"README claims {claim} identities; actual {len(audit.entities)}",
                        pointer=f"line:{text[: match.start()].count(chr(10)) + 1}",
                    )
            if (
                "265" in text
                and "327" in text
                and any(labels.get(k, k) == "信息与记录" for k in visible_counts)
            ):
                info_key = next(
                    k for k in visible_counts if labels.get(k, k) == "信息与记录"
                )
                info_primary = len(categories.get(info_key, []))
                actual = visible_counts[info_key]
                audit.metrics["information_identities"] = {
                    "primary": info_primary,
                    "associated": actual - info_primary,
                    "visible": actual,
                }
                if (info_primary, actual - info_primary, actual) != (265, 62, 327):
                    audit.add(
                        "cross.readme_information_count",
                        "minor",
                        name,
                        f"Information claim 265+62=327; actual {info_primary}+{actual - info_primary}={actual}",
                    )
            for label, actual in (
                ("源字段", audit.metrics.get("source_field_count")),
                ("源关系", len(audit.relations)),
            ):
                for match in re.finditer(r"([\d,]+)\s*(?:个|条)" + label, text):
                    claim = int(match[1].replace(",", ""))
                    if actual is not None and claim != actual:
                        audit.add(
                            "cross.readme_semantic_count",
                            "minor",
                            name,
                            f"{label}: claimed {claim}, actual {actual}",
                        )
            for line in text.splitlines():
                cells = [v.strip() for v in line.split("|")]
                if (
                    len(cells) > 3
                    and cells[1] in {labels.get(k, k) for k in categories}
                    and cells[2].isdigit()
                    and int(cells[2])
                    != len(
                        categories[
                            next(k for k in categories if labels.get(k, k) == cells[1])
                        ]
                    )
                ):
                    audit.add(
                        "cross.readme_directory_count",
                        "minor",
                        name,
                        f"{cells[1]} primary claimed {cells[2]}, actual {len(categories[next(k for k in categories if labels.get(k, k) == cells[1])])}",
                    )
    # Current semantic data uses the shared entity catalog as input; do not invent a second materialized identity set.
    semantic_docs = [
        (p, d)
        for p, d in audit.documents.items()
        if p.startswith("semantic-directory/")
        and isinstance(d, dict)
        and isinstance(d.get("entities"), list)
    ]
    for path, doc in semantic_docs:
        ids = {e.get("id") for e in doc["entities"] if isinstance(e, dict)}
        if ids != audit.entities.keys():
            audit.add(
                "cross.identity_set",
                "blocker",
                path,
                "Semantic and entity identity sets disagree",
                details={
                    "semantic_only": sorted(ids - audit.entities.keys()),
                    "entity_only": sorted(audit.entities.keys() - ids),
                },
            )
    if not semantic_docs:
        audit.add(
            "cross.semantic_materialization",
            "info",
            "semantic-directory/src/build_semantics.py",
            "No separate materialized semantic entity JSON; generator reads entity-directory/data/concepts.json. Referenced classes are checked against this catalog; second identity set cannot be independently compared.",
        )
    for row in [*audit.field_rows, *audit.relation_rows, *audit.semantic_rows]:
        for i, source in enumerate(row.data.get("sources", [])):
            if source not in audit.sources:
                audit.add(
                    "cross.source_reference",
                    "blocker",
                    row,
                    f"Source ID {source!r} is absent",
                    pointer=row.pointer + f"/sources/{i}",
                )
    for row in audit.sources.values():
        path = row.data.get("path")
        if isinstance(path, str) and not re.match(r"^\w+://", path):
            candidates = [audit.root / path, audit.root / "semantic-directory" / path]
            if not any(p.is_file() for p in candidates):
                historical = bool(
                    row.data.get("revision")
                    or row.data.get("sha256")
                    or row.data.get("integrationProvenance")
                )
                audit.add(
                    "cross.source_path",
                    "info" if historical else "major",
                    row,
                    f"Source snapshot path {path!r} not present locally; "
                    + (
                        "historical citation, not a current input"
                        if historical
                        else "unresolved local provenance"
                    ),
                )
    for path in ("semantic-directory/data/provenance.json",):
        doc = audit.documents.get(path, {})
        for i, source in enumerate(doc.get("sourceFiles", [])):
            local = source.get("path")
            if (
                local
                and not (audit.root / local).is_file()
                and not (audit.root / "semantic-directory" / local).is_file()
            ):
                audit.add(
                    "cross.historical_provenance_path",
                    "info",
                    path,
                    f"Historical source snapshot {local!r} not a current checkout file",
                    pointer=f"/sourceFiles/{i}",
                )
    for path, doc in audit.documents.items():
        if path.startswith("entity-directory/config/hierarchy-"):

            def inspect(node, pointer="", artifact=path):
                if isinstance(node, dict):
                    for key, value in node.items():
                        if key in ("baseIds", "typeIds"):
                            for i, identity in enumerate(value):
                                if identity not in audit.entities:
                                    audit.add(
                                        "navigation.config_reference",
                                        "blocker",
                                        artifact,
                                        f"Hierarchy config references absent type {identity!r}",
                                        pointer=pointer + "/" + key + f"/{i}",
                                    )
                        else:
                            inspect(value, pointer + "/" + escape(key))
                elif isinstance(node, list):
                    for i, value in enumerate(node):
                        inspect(value, pointer + f"/{i}")

            inspect(doc)
    original = audit.metrics.get("original", {})
    for name in ("semantic-directory/README.md", "semantic-directory/HANDOFF.md"):
        path = audit.root / name
        if not path.is_file():
            continue
        text = audit.read_text(path)
        for label, key in (
            ("原谓词", "predicates"),
            ("原事件", "events"),
            ("原根规则", "root_rules"),
        ):
            for match in re.finditer(r"([\d,]+)\s*个" + label, text):
                claim = int(match[1].replace(",", ""))
                if key in original and claim != original[key]:
                    audit.add(
                        "cross.readme_original_count",
                        "minor",
                        name,
                        f"{label}: claimed {claim}, actual {original[key]}",
                    )
    provenance = audit.documents.get("semantic-directory/data/provenance.json", {})
    claims = provenance.get("candidateRestoration", {}).get("counts", {})
    source_fields = {r.id for r in audit.field_rows if "/definitions/" in r.artifact}
    own = {f for r in audit.entity_rows for f in r.data.get("ownFieldIds", [])}
    proposed = {
        f for r in audit.entity_rows for f in r.data.get("originalOwnFieldIds", [])
    } - own
    observed_counts = {
        "concepts": len(audit.entities),
        "restoredFields": len(source_fields),
        "currentOwnFieldIds": len(own),
        "proposedOnlyFieldIds": len(proposed),
        "restoredRelations": len(audit.relations),
        "currentFieldGap": len(own - audit.fields.keys()),
        "proposedFieldGap": len(proposed - audit.fields.keys()),
        "sourceRecords": len(
            audit.documents.get("semantic-directory/data/sources.json", [])
        ),
    }
    audit.metrics["restoration_recomputed_counts"] = observed_counts
    for key, actual in observed_counts.items():
        if key in claims and claims[key] != actual:
            audit.add(
                "cross.restoration_counts",
                "major",
                "semantic-directory/data/provenance.json",
                f"{key}: claimed {claims[key]}, actual {actual}",
                pointer="/candidateRestoration/counts/" + key,
            )
    registry = provenance.get("sourceRegistry", {})
    source_name = "semantic-directory/data/" + registry.get("path", "sources.json")
    if source_name in audit.inventory:
        for key, actual in {
            **audit.inventory[source_name],
            "recordCount": observed_counts["sourceRecords"],
        }.items():
            if key in registry and registry[key] != actual:
                audit.add(
                    "cross.source_registry_integrity",
                    "blocker",
                    "semantic-directory/data/provenance.json",
                    f"Source registry {key} differs from current file: {registry[key]} vs {actual}",
                    pointer="/sourceRegistry/" + key,
                )
    catalog = audit.documents.get("entity-directory/data/catalog-source.json", {}).get(
        "concepts", []
    )
    catalog_ids = {r.get("id") for r in catalog if isinstance(r, dict)}
    original_ids = {
        r.id for r in audit.entity_rows if r.data.get("origin") == "original950"
    }
    if catalog_ids != original_ids:
        audit.add(
            "cross.catalog_identity_set",
            "blocker",
            "entity-directory/data/catalog-source.json",
            "Original catalog and original950 identity sets disagree",
            details={
                "catalog_only": sorted(catalog_ids - original_ids),
                "directory_only": sorted(original_ids - catalog_ids),
            },
        )
    check_profiles(audit)


def check_profiles(audit: Audit) -> None:
    for row in audit.profiles.values():
        profile = row.data
        for key, registry in (
            ("fieldIds", audit.fields),
            ("capabilityFieldIds", audit.fields),
            ("predicateIds", audit.predicates),
            ("sourcePredicateIds", audit.predicates),
            ("eventIds", audit.events),
            ("sourceEventIds", audit.events),
            ("ruleIds", audit.rules),
            ("sourceTargetIds", {**audit.predicates, **audit.events}),
            ("compatibleTypeIds", audit.entities),
            ("baseTypeIds", audit.entities),
        ):
            for i, identity in enumerate(profile.get(key, [])):
                if identity not in registry:
                    audit.add(
                        "profile.reference",
                        "blocker",
                        row,
                        f"{key} references missing {identity!r}",
                        pointer=row.pointer + f"/{key}/{i}",
                    )
        if profile.get("kind") == "source_hierarchy_reuse":
            bases = profile.get("baseTypeIds", [])
            for identity in profile.get("compatibleTypeIds", []):
                if not any(b in audit.ancestors(identity) for b in bases):
                    audit.add(
                        "profile.inheritance",
                        "blocker",
                        row,
                        f"Type {identity!r} does not inherit the advertised source base",
                    )
        elif profile.get("kind") in (
            "record_subject_adapter",
            "explicit_configuration_capability",
            "explicit_protocol_capability",
            "explicit_document_capability",
        ):
            audit.add(
                "profile.subject_projection",
                "info",
                row,
                "Explicit subject capability application; source producer record fields remain separate from effective inheritance",
            )
