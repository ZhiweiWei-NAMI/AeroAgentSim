"""Keep Harmonic's baseline systems when adding explicit world plugins."""

import xml.etree.ElementTree as ET


# Any world plugin suppresses the fallback server configuration in Gazebo 8:
# https://gazebosim.org/api/sim/8/server_config.html
_BASELINE_SYSTEMS = (
    ("gz::sim::systems::Physics", "gz-sim-physics-system"),
    ("gz::sim::systems::UserCommands", "gz-sim-user-commands-system"),
    ("gz::sim::systems::SceneBroadcaster", "gz-sim-scene-broadcaster-system"),
)


def ensure_baseline_systems(world):
    """Add missing baseline systems without replacing authored plugin settings."""
    existing = {plugin.get("name") for plugin in world.findall("plugin")}
    for name, filename in _BASELINE_SYSTEMS:
        if name not in existing:
            ET.SubElement(world, "plugin", filename=filename, name=name)
