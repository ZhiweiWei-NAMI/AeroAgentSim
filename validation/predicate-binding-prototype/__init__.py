"""Fixture entity/state binding prototype, separate from Atlas's scalar evaluator."""
from .contracts import (Ref, Interval, Capability, ObjectRecord, QueryPoint, StateCell,
                        RelationRecord, ScopeCoverage, RoleRequirement, Selector, Binding)
from .adapter import (Snapshot, CompiledBinding, Prepared, Resolution, RelationAnswer,
                      Truth, exact_path, load_atlas)
