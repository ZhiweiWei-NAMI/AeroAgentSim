"""Read only the configured payload joint from Gazebo's native ECS snapshot.

DetachableJoint publishes transitions without transport latching. A subscriber
which starts after the initial paused PreUpdate therefore needs this read-only
bootstrap. Full world state stays in the request-only native child process.
"""


def component_type(name):
    # Gazebo components::Factory uses common::hash64 (FNV-1a) for wire type IDs.
    # This is protocol decoding, not a software version/integrity gate.
    value = 0xcbf29ce484222325
    for byte in ("gz_sim_components." + name).encode("ascii"):
        value = ((value ^ byte) * 0x100000001b3) & ((1 << 64) - 1)
    return value


def attachment_snapshot(message, carrier_model, package_id):
    """Return attached only when the actual matching fixed joint is present."""
    unavailable = {"status": "unavailable", "reason": "physical attachment not present in native state"}
    if not message.HasField("stats") or not message.stats.paused or not message.HasField("state"):
        return {**unavailable, "reason": "native attachment query requires a paused full snapshot"}
    stamp = message.stats.sim_time
    if stamp.sec < 0 or not 0 <= stamp.nsec < 1_000_000_000:
        return {**unavailable, "reason": "native attachment snapshot has invalid time"}
    wanted = {component_type(name): name for name in
              ("Name", "ParentEntity", "Model", "Link", "DetachableJoint")}
    entities = {}
    for entity_id, entity in message.state.entities.items():
        if entity.remove:
            continue
        components = {}
        for component in entity.components.values():
            name = wanted.get(component.type)
            if name and not component.remove:
                components[name] = component.component.decode("utf-8")
        entities[entity_id] = components

    def model_id(name):
        matches = [key for key, value in entities.items()
                   if "Model" in value and value.get("Name") == name]
        return matches[0] if len(matches) == 1 else None

    def link_id(model, name):
        matches = [key for key, value in entities.items() if "Link" in value
                   and value.get("Name") == name and value.get("ParentEntity") == str(model)]
        return matches[0] if model is not None and len(matches) == 1 else None

    parent = link_id(model_id(carrier_model), "base_link")
    child = link_id(model_id(package_id), "parcel")
    if parent is None or child is None:
        return unavailable
    matches = [key for key, value in entities.items()
               if value.get("DetachableJoint", "").split() == [str(parent), str(child), "fixed"]]
    if len(matches) != 1:
        return unavailable
    return {"status": "attached", "released": False,
            "source": "gazebo_detachable_joint_component_query",
            "source_clock": "gazebo_sim", "native_source_time_ns": stamp.sec * 1_000_000_000 + stamp.nsec,
            "joint_entity": matches[0], "parent_link_entity": parent, "child_link_entity": child}
