"""Typed, cutoff-aware input contracts; no raw sensor encoders or data scanning."""
from dataclasses import dataclass, field
import numpy as np

HISTORY, HORIZON, SAMPLES, DT = 21, 10, 8, 0.5
TIME_ATOL = 1e-6
SOURCE = {"unknown": 0, "measured": 1, "fused": 2, "predicted": 3}
MODALITIES = {"image": 0, "point": 1, "log": 2}
NODE_ENTITY, NODE_OBSERVER, NODE_OBSERVATION = 0, 1, 2
EDGE_SELF, EDGE_PHYSICAL, EDGE_TEMPORAL, EDGE_OWNS, EDGE_ASSOCIATION = range(5)

@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str = "continuous_unbounded"
    unit: str = "m"
    frame: str = "world"
    center: float = 0.0
    scale: float = 1.0
    min_std: float = 0.01  # fixture-specific numerical floor in this field's units
    def validate(self):
        if self.kind != "continuous_unbounded":
            raise NotImplementedError(f"Unsupported field domain {self.kind!r} for {self.name}; add a legal distribution head first")
        if not np.isfinite([self.center, self.scale, self.min_std]).all() or self.scale <= 0 or self.min_std <= 0:
            raise ValueError("Invalid field normalization/minimum standard deviation")
        if not self.unit or not self.frame:
            raise ValueError("Field units and coordinate frame are mandatory")

@dataclass(frozen=True)
class Observer:
    observer_id: str
    modality: str
    calibration_ref: str
    def validate(self):
        if self.modality not in MODALITIES or not self.observer_id or not self.calibration_ref:
            raise ValueError("Observer identity, supported modality, and calibration reference required")

@dataclass
class Observation:
    observation_id: str
    observer_id: str
    tick: int
    event_time: float
    available_time: float
    support_end: float  # latest raw frame/log used by encoder/tracker
    embedding_ref: str  # provenance, not interpreted as a fetch instruction
    tokens: np.ndarray  # [P,E], scene tokens stored once under this Observation
    pose: np.ndarray  # [6], world translation + rotation representation for fixture
    quality: float = 1.0

@dataclass
class Association:
    observation_id: str
    entity_id: str
    token_indices: tuple[int, ...]
    local_ref: str  # ROI/mask/point-index/log-span reference
    kind: str = "observed_by"
    score: float = 1.0
    available_time: float = 0.0
    support_end: float = 0.0

@dataclass
class History:
    fields: tuple[FieldSpec, ...]
    entity_ids: tuple[str, ...]  # aligned stable slots across history; arbitrary IDs, no learned ID embeddings
    entity_types: np.ndarray  # [N]
    times: np.ndarray  # [21], actual seconds
    values: np.ndarray  # [21,N,D]
    valid: np.ndarray
    source: np.ndarray
    uncertainty: np.ndarray
    available_time: np.ndarray
    support_end: np.ndarray
    present: np.ndarray  # [21,N], existence/padding, not detection
    observers: tuple[Observer, ...] = ()
    observations: tuple[Observation, ...] = ()
    associations: tuple[Association, ...] = ()
    cutoff: float = 0.0
    embedding_width: int = 4
    def validate(self):
        for f in self.fields:
            f.validate()
        if tuple(f.name for f in self.fields) != ("x", "y"):
            raise NotImplementedError("This bounded fixture supports exactly ordered world-position fields (x, y); map new fields explicitly")
        if len({(f.unit, f.frame) for f in self.fields}) != 1:
            raise ValueError("Position x/y must use the same unit and coordinate frame")
        n, d = len(self.entity_ids), len(self.fields)
        if len(set(self.entity_ids)) != n or n < 1 or len({f.name for f in self.fields}) != d:
            raise ValueError("Duplicate/empty entity IDs or duplicate fields")
        if self.values.shape != (HISTORY, n, d) or self.entity_types.shape != (n,):
            raise ValueError("History must contain 21 samples and aligned entity/field axes")
        if self.times.shape != (HISTORY,) or not np.allclose(np.diff(self.times), DT, rtol=0, atol=TIME_ATOL) or not np.isclose(self.times[-1], self.cutoff, rtol=0, atol=TIME_ATOL) or np.any(self.times > self.cutoff + TIME_ATOL):
            raise ValueError("History requires cutoff-10s through cutoff in 0.5s steps")
        if not np.isfinite(self.times).all():
            raise ValueError("Nonfinite times")
        for name in ("valid", "source", "uncertainty", "available_time", "support_end"):
            if getattr(self, name).shape != self.values.shape:
                raise ValueError(f"{name} has wrong shape")
        if self.valid.dtype != bool or self.present.dtype != bool or self.present.shape != (HISTORY, n):
            raise ValueError("Masks must be explicit booleans")
        active = self.valid & self.present[..., None]
        if np.any(self.valid & ~self.present[..., None]):
            raise ValueError("Absent entity cannot have a valid field")
        if not np.isfinite(self.values[active]).all() or not np.isfinite(self.uncertainty[active]).all() or np.any(self.uncertainty[active] < 0):
            raise ValueError("Valid fields and uncertainties must be finite")
        if np.any(~np.isin(self.source[active], [1, 2, 3])):
            raise ValueError("Valid source must be measured/fused/predicted; hidden oracle truth is forbidden")
        for name in ("available_time", "support_end"):
            data = getattr(self, name)[active]
            if not np.isfinite(data).all() or np.any(data > self.cutoff):
                raise ValueError(f"Future leakage in numeric {name}")
        if np.any(self.support_end[active] > self.available_time[active]):
            raise ValueError("Numeric support cannot end after it became available")
        for obs in self.observers:
            obs.validate()
        owners = {o.observer_id: o for o in self.observers}
        if len(owners) != len(self.observers):
            raise ValueError("Duplicate observer IDs")
        observations = {}
        for o in self.observations:
            if o.observation_id in observations or o.observer_id not in owners:
                raise ValueError("Duplicate observation or missing Observer owner")
            if not (0 <= o.tick < HISTORY) or not np.isclose(o.event_time, self.times[o.tick], rtol=0, atol=TIME_ATOL):
                raise ValueError("Observation event time must match its history tick")
            if not np.isfinite([o.event_time, o.available_time, o.support_end, o.quality]).all() or not o.event_time <= o.support_end <= o.available_time <= self.cutoff:
                raise ValueError("Observation has future temporal support/arrival or invalid times")
            if o.tokens.ndim != 2 or o.tokens.shape[0] < 1 or o.tokens.shape[1] != self.embedding_width or not np.isfinite(o.tokens).all():
                raise ValueError("Observation token matrix is invalid")
            if o.pose.shape != (6,) or not np.isfinite(o.pose).all() or not 0 <= o.quality <= 1 or not o.embedding_ref:
                raise ValueError("Observation pose/quality/embedding provenance invalid")
            observations[o.observation_id] = o
        seen_association_tokens = set()
        for a in self.associations:
            if a.kind != "observed_by":
                raise NotImplementedError("Coverage candidates are not measured association edges; unsupported association kind")
            if a.observation_id not in observations or a.entity_id not in self.entity_ids or not a.local_ref:
                raise ValueError("Association must bind actual Observation and stable entity/local evidence")
            o = observations[a.observation_id]
            if not a.token_indices or len(set(a.token_indices)) != len(a.token_indices) or min(a.token_indices) < 0 or max(a.token_indices) >= len(o.tokens):
                raise ValueError("Association references invalid local token indices")
            if not np.isfinite([a.available_time, a.support_end, a.score]).all() or not 0 < a.score <= 1:
                raise ValueError("Association score/time invalid")
            if not o.available_time <= a.available_time <= self.cutoff or not o.support_end <= a.support_end <= a.available_time:
                raise ValueError("Association support/arrival leaks beyond cutoff or precedes evidence")
            for token_index in a.token_indices:
                key = (a.observation_id, a.entity_id, token_index)
                if key in seen_association_tokens:
                    raise ValueError("Duplicate local evidence association; overlap aggregation is not defined")
                seen_association_tokens.add(key)
            if not self.present[o.tick, self.entity_ids.index(a.entity_id)]:
                raise ValueError("Association to absent entity")
        return self

    def permute(self, order):
        """Reindex aligned entity slots while IDs and query bindings remain semantic."""
        from copy import deepcopy
        out = deepcopy(self)
        order = np.asarray(order)
        if sorted(order.tolist()) != list(range(len(self.entity_ids))):
            raise ValueError("Not a permutation")
        out.entity_ids = tuple(self.entity_ids[i] for i in order)
        out.entity_types = self.entity_types[order]
        for name in ("values", "valid", "source", "uncertainty", "available_time", "support_end", "present"):
            setattr(out, name, getattr(self, name)[:, order].copy())
        return out.validate()

@dataclass(frozen=True)
class Query:
    entity_a: str
    entity_b: str
    threshold: float
    position_fields: tuple[str, str] = ("x", "y")
    predicate: str = "distance_lt"
    def bind(self, history):
        if self.predicate != "distance_lt":
            raise NotImplementedError(f"Unsupported predicate: {self.predicate}; only memoryless distance_lt is implemented. Stateful rules need explicit cutoff-available initial rule memory, never an invented zero history")
        if not np.isfinite(self.threshold) or self.threshold < 0:
            raise ValueError("Distance threshold must be finite and nonnegative")
        ids = history.entity_ids
        if self.entity_a not in ids or self.entity_b not in ids:
            raise ValueError("Unknown query entity")
        lookup = {f.name: (i, f) for i, f in enumerate(history.fields)}
        try:
            fs = [lookup[name] for name in self.position_fields]
        except KeyError as e:
            raise ValueError("Missing query position field") from e
        if len(fs) != 2 or len(set(self.position_fields)) != 2 or len({(f.unit, f.frame) for _, f in fs}) != 1:
            raise ValueError("Distance predicate needs two distinct fields in the same unit/frame")
        return ids.index(self.entity_a), ids.index(self.entity_b), [i for i, _ in fs]

@dataclass
class PackedGraph:
    features: np.ndarray
    node_types: np.ndarray
    edge_types: np.ndarray  # [V,V], destination/source; -1 means no edge
    edge_weights: np.ndarray
    last_entity_nodes: np.ndarray
    node_mask: np.ndarray
    metadata: dict = field(default_factory=dict)


def feature_width(d, embedding_width):
    return 5 * d + 12 + embedding_width


def build_history_graph(h: History, radius=5.0):
    """Dense reference graph; evidence tokens have one owner, local edges are explicit.

    A dense graph is deliberate for tiny fixtures only. No subgraph compression is
    implied; production sparse/ragged batching is unimplemented.
    """
    h.validate()
    t, n, d = h.values.shape
    width = feature_width(d, h.embedding_width)
    rows, types, masks, metadata, edges = [], [], [], [], []
    centers = np.array([f.center for f in h.fields])
    scales = np.array([f.scale for f in h.fields])
    for ti in range(t):
        for ni in range(n):
            mask = h.valid[ti, ni]
            row = np.zeros(width)
            row[:d] = np.where(mask, (h.values[ti, ni] - centers) / scales, 0)
            row[d:2*d] = mask
            row[2*d:3*d] = np.where(mask, h.source[ti, ni] / 3.0, 0)
            row[3*d:4*d] = np.where(mask, h.uncertainty[ti, ni] / scales, 0)
            row[4*d:5*d] = np.where(mask, h.cutoff - h.available_time[ti, ni], 0)
            row[5*d:5*d+4] = (h.times[ti] - h.cutoff, h.cutoff, h.entity_types[ni], float(h.present[ti, ni]))
            rows.append(row); types.append(NODE_ENTITY); masks.append(h.present[ti, ni]); metadata.append(("entity", h.entity_ids[ni], float(h.times[ti])))
            if ti and h.present[ti, ni] and h.present[ti-1, ni]:
                edges.append((ti*n+ni, (ti-1)*n+ni, EDGE_TEMPORAL, 1.0))
        # Geometry derives only from observed/estimated first two fields, no hidden truth.
        for a in range(n):
            for b in range(n):
                if a != b and h.present[ti, a] and h.present[ti, b] and h.valid[ti, [a,b], :2].all() and np.linalg.norm(h.values[ti,a,:2]-h.values[ti,b,:2]) < radius:
                    edges.append((ti*n+a, ti*n+b, EDGE_PHYSICAL, 1.0))
    owner_nodes = {}
    for o in h.observers:
        row = np.zeros(width); row[5*d+2] = MODALITIES[o.modality]
        owner_nodes[o.observer_id] = len(rows)
        rows.append(row); types.append(NODE_OBSERVER); masks.append(True); metadata.append(("observer", o.observer_id, o.calibration_ref))
    obs_nodes = {}
    for o in h.observations:
        obs_nodes[o.observation_id] = []
        for pi, token in enumerate(o.tokens):
            row = np.zeros(width)
            row[5*d:5*d+4] = (o.event_time-h.cutoff, o.available_time-h.cutoff, o.support_end-h.cutoff, o.quality)
            row[5*d+4:5*d+10] = o.pose
            row[-h.embedding_width:] = token
            idx = len(rows); obs_nodes[o.observation_id].append(idx)
            rows.append(row); types.append(NODE_OBSERVATION); masks.append(True)
            metadata.append(("observation", o.observation_id, pi, o.embedding_ref))
            edges.append((idx, owner_nodes[o.observer_id], EDGE_OWNS, 1.0))
    obs_lookup = {o.observation_id: o for o in h.observations}
    for a in h.associations:
        o = obs_lookup[a.observation_id]
        target = o.tick*n + h.entity_ids.index(a.entity_id)
        for pi in a.token_indices:
            edges.append((target, obs_nodes[a.observation_id][pi], EDGE_ASSOCIATION, a.score*o.quality))
    size = len(rows)
    edge_type = np.full((size, size), -1, dtype=np.int64)
    edge_weight = np.zeros((size, size))
    for i in range(size):
        edge_type[i, i] = EDGE_SELF; edge_weight[i, i] = 1
    for dst, src, kind, weight in edges:
        if weight > 0:
            edge_type[dst, src] = kind; edge_weight[dst, src] = weight
    return PackedGraph(np.stack(rows), np.asarray(types), edge_type, edge_weight,
                       np.arange((t-1)*n,t*n), np.asarray(masks),
                       {"nodes": metadata, "entity_ids": h.entity_ids, "observations": len(h.observations), "associations": len(h.associations)})
