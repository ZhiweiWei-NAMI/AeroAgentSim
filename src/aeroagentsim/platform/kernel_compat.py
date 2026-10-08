"""Temporary decoder registration repair for the in-flight kernel M2 release.

The kernel imports its relation records but omits that module from its closed
decoder table. Register only the actual kernel declarations, without changing
their encoding, binding, validation or execution. Remove when upstream includes
relations in codec._record_classes (tracked in docs/platform/p1.md).
"""

from __future__ import annotations

import dataclasses

from aerokernel import codec, relations


def register_relation_records() -> None:
    classes = codec._record_classes()
    for name, cls in vars(relations).items():
        if isinstance(cls, type) and dataclasses.is_dataclass(cls):
            registered = classes.get(name)
            if registered is not None and registered is not cls:
                raise RuntimeError(f"kernel relation codec name collision: {name}")
            classes[name] = cls
