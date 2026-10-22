"""Read only the configured physical joint from a paused native ECS snapshot."""


def component_type(name):
    # Gazebo's component factory uses FNV-1a wire type IDs, not version hashes.
    value = 0xCBF29CE484222325
    for byte in ("gz_sim_components." + name).encode("ascii"):
        value = ((value ^ byte) * 0x100000001B3) & ((1 << 64) - 1)
    return value


def attachment_snapshot(message, carrier_model, package_id, *, parent_link="base_link", child_link="parcel"):
    unavailable = {"status": "unavailable", "reason": "physical attachment not present in native state"}
    if not message.HasField("stats") or not message.stats.paused or not message.HasField("state"):
        return {**unavailable, "reason": "native attachment query requires a paused full snapshot"}
    stamp = message.stats.sim_time
    if not message.stats.HasField("sim_time") or stamp.sec < 0 or not 0 <= stamp.nsec < 1_000_000_000:
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

    parent = link_id(model_id(carrier_model), parent_link)
    child = link_id(model_id(package_id), child_link)
    if parent is None or child is None:
        return unavailable
    matches = [key for key, value in entities.items()
               if value.get("DetachableJoint", "").split() == [str(parent), str(child), "fixed"]]
    if len(matches) != 1:
        return unavailable
    return {"status": "attached", "native_source_time_ns": stamp.sec * 1_000_000_000 + stamp.nsec}
