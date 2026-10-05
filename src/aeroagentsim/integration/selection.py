"""Framework-neutral shared cursor/selection/evidence identity and race guards."""

from dataclasses import dataclass, field
from threading import RLock
from typing import Optional, Tuple

from .contracts import SELECTION_SCHEMA, VIEW_SCHEMA, ContractError, ViewKey
from .normalization import token
from .semantics import SemanticEvidence


@dataclass(frozen=True)
class Selection:
    view_key: ViewKey
    kind: str
    entity_ids: Tuple[str, ...]
    binding_id: Optional[str] = None
    state_id: Optional[str] = None
    target_id: Optional[str] = None
    graph_node_id: Optional[str] = None
    schema: str = field(default=SELECTION_SCHEMA, init=False)

    def __post_init__(self):
        if self.kind not in ("entity", "state", "predicate", "event", "graph_node"):
            raise ContractError("unsupported selection kind")
        if not self.entity_ids or len(set(self.entity_ids)) != len(self.entity_ids):
            raise ContractError("selection requires an ordered unique entity binding")
        object.__setattr__(self, "entity_ids", tuple(self.entity_ids))
        for entity_id in self.entity_ids:
            token(entity_id, "selected entity")
        for value in (self.binding_id, self.state_id, self.target_id, self.graph_node_id):
            if value is not None:
                token(value, "selection reference")
        if self.kind == "state" and (self.state_id is None or self.binding_id is None):
            raise ContractError("state selection requires state and binding IDs")
        if self.kind in ("predicate", "event") and (self.target_id is None or self.binding_id is None):
            raise ContractError("rule selection requires target and binding IDs")
        if self.kind == "graph_node" and self.graph_node_id is None:
            raise ContractError("graph selection requires a node ID")


@dataclass(frozen=True)
class ViewSnapshot:
    view_key: ViewKey
    mode: str
    selection: Optional[Selection] = None
    evidence: Tuple[SemanticEvidence, ...] = ()
    schema: str = field(default=VIEW_SCHEMA, init=False)


class SharedViewStore:
    """A reference reducer, not a renderer, replay engine, or control channel.

    Its owner calls activate when intentionally changing frame/cursor/revision.
    Asynchronous results and clicks may commit only against that complete key.
    """

    def __init__(self):
        self._snapshot = None
        self._lock = RLock()

    @property
    def snapshot(self) -> Optional[ViewSnapshot]:
        with self._lock:
            return self._snapshot

    def activate(self, view_key: ViewKey, mode: str) -> ViewSnapshot:
        if mode not in ("live_observation", "sealed_replay"):
            raise ContractError("view mode must distinguish live observation and sealed replay")
        with self._lock:
            self._snapshot = ViewSnapshot(view_key, mode)
            return self._snapshot

    def select(self, selection: Selection) -> bool:
        with self._lock:
            current = self._snapshot
            if current is None or selection.view_key != current.view_key:
                return False
            self._snapshot = ViewSnapshot(current.view_key, current.mode, selection, current.evidence)
            return True

    def apply_evidence(self, view_key: ViewKey, evidence: Tuple[SemanticEvidence, ...]) -> bool:
        evidence = tuple(evidence)
        with self._lock:
            current = self._snapshot
            if current is None or view_key != current.view_key or any(item.request.view_key != view_key for item in evidence):
                return False
            if len({item.request.binding.binding_id for item in evidence}) != len(evidence):
                raise ContractError("duplicate semantic binding in shared view evidence")
            self._snapshot = ViewSnapshot(current.view_key, current.mode, current.selection, evidence)
            return True
