"""Prefix-limited append storage and bounded copy-on-write candidate indexes."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Generic, TypeVar, cast, overload

K = TypeVar("K")
V = TypeVar("V")
T = TypeVar("T")
D = TypeVar("D")
_MISSING = object()


class Overlay(MutableMapping[K, V]):
    """A private write overlay; committed ancestors are never mutated.

    Flattening every 16 generations bounds lookup cost without copying an index
    for each intent/control/candidate. Values must be immutable or replaced.
    """

    def __init__(self, base: Mapping[K, V] | None = None) -> None:
        self.base: Mapping[K, V] = {} if base is None else base
        self.writes: dict[K, V] = {}
        self.deleted: set[K] = set()
        self.depth: int = self.base.depth + 1 if isinstance(self.base, Overlay) else 0
        if self.depth >= 16:
            self.base = dict(self.base)
            self.depth = 0

    def fork(self) -> Overlay[K, V]:
        """Open a fresh candidate without copying the committed index."""
        if not self.writes and not self.deleted:
            return Overlay(self.base)
        return Overlay(self)

    def _lookup(self, key: K) -> object:
        layer = self
        while True:
            if layer.deleted and key in layer.deleted:
                return _MISSING
            value = layer.writes.get(key, _MISSING)
            if value is not _MISSING:
                return value
            base = layer.base
            if not isinstance(base, Overlay):
                return base.get(key, _MISSING)
            layer = base

    def __getitem__(self, key: K) -> V:
        value = self._lookup(key)
        if value is _MISSING:
            raise KeyError(key)
        return cast(V, value)

    @overload
    def get(self, key: K) -> V | None: ...

    @overload
    def get(self, key: K, default: D) -> V | D: ...

    def get(self, key: K, default: D | None = None) -> V | D | None:
        value = self._lookup(key)
        return default if value is _MISSING else cast(V, value)

    def __setitem__(self, key: K, value: V) -> None:
        self.deleted.discard(key)
        self.writes[key] = value

    def __delitem__(self, key: K) -> None:
        self[key]
        self.writes.pop(key, None)
        self.deleted.add(key)

    def __iter__(self) -> Iterator[K]:
        yield from self.writes
        for key in self.base:
            if key not in self.writes and key not in self.deleted:
                yield key

    def __len__(self) -> int:
        return sum(1 for _ in self)


class AppendList(Sequence[T], Generic[T]):
    """A stable prefix of shared append storage, including failed candidates.

    Appending to a fork cannot change an older prefix's length. A divergent fork
    copies only when abandoned uncommitted storage already occupies its suffix.
    """

    def __init__(self, values: list[T] | None = None, count: int | None = None) -> None:
        self.data = [] if values is None else values
        self.length = len(self.data) if count is None else count

    def fork(self) -> AppendList[T]:
        """Share storage while retaining this exact prefix."""
        return AppendList(self.data, self.length)

    def append(self, value: T) -> None:
        """Append a private suffix; previously issued prefixes stay fixed."""
        if len(self.data) != self.length:
            self.data = self.data[: self.length]
        self.data.append(value)
        self.length += 1

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> list[T]: ...

    def __getitem__(self, index: int | slice) -> T | list[T]:
        if isinstance(index, slice):
            return [self.data[i] for i in range(*index.indices(self.length))]
        if index < 0:
            index += self.length
        if not 0 <= index < self.length:
            raise IndexError(index)
        return self.data[index]

    def __len__(self) -> int:
        return self.length

    def __iter__(self) -> Iterator[T]:
        for i in range(self.length):
            yield self.data[i]


@dataclass(frozen=True)
class FileFrame:
    """Immutable acknowledged byte range; hash detects changes to its source."""

    path: Path
    offset: int
    length: int
    digest: bytes

    def read(self) -> bytes:
        """Read exactly the committed frame, never a growing file tail."""
        from .errors import KernelError

        with self.path.open("rb") as source:
            source.seek(self.offset)
            line = source.read(self.length)
        if sha256(line).digest() != self.digest:
            raise KernelError("JOURNAL_CHANGED", "committed file frame changed")
        return line


@dataclass(frozen=True, slots=True)
class FactAuthority:
    """Indexed fact authority without its value, timestamps or causes."""

    partition: str
    key: tuple[Any, str]


class RecordLog(AppendList[Any]):
    """Encoded prefixes with eight decoded records shared safely across forks.

    Cache keys include the actual immutable line identity, so divergent suffixes
    at the same index cannot alias. Only private raw trees are cached; diagnostic
    reads and single-item reads always detach their returned trees.
    """

    def __init__(
        self,
        values: list[dict[str, Any] | bytes] | None = None,
        count: int | None = None,
        budget: Any = None,
    ) -> None:
        super().__init__(values, count)
        self.budget = budget
        self._decoded: OrderedDict[tuple[int, int], tuple[bytes, dict[str, Any]]] = (
            OrderedDict()
        )
        self._cause_indexes: AppendList[
            tuple[object, list[dict[str, Any] | FactAuthority] | None]
        ] = AppendList()
        self.provenance = "full"
        self._entities: dict[tuple[Any, ...], Any] = {}
        self._fact_authorities: dict[tuple[str, Any, str], FactAuthority] = {}

    def fork(self) -> RecordLog:
        """Retain this exact prefix of the shared encoded log."""
        result = RecordLog(self.data, self.length, self.budget)
        result.provenance = self.provenance
        result._decoded = self._decoded
        result._cause_indexes = self._cause_indexes.fork()
        result._entities = self._entities
        result._fact_authorities = self._fact_authorities
        return result

    def freeze_tail(self, line: bytes) -> None:
        """Replace the newly acknowledged private tail with its compact bytes."""
        record = self._raw(self.length - 1)
        items = record.get("items", [])
        metadata = (
            None
            if self.provenance == "lean"
            or items
            and all(
                "$fact" in item or "$retract" in item or item.get("kind") == "return"
                for item in items
            )
            else self._cause_metadata(record)
        )
        self._cause_indexes.append((line, metadata))
        self.data[self.length - 1] = line

    def file_tail(self, path: Path, offset: int, line: bytes) -> None:
        """Replace an acknowledged tail with a hash-pinned file byte range."""
        frame = FileFrame(path.resolve(), offset, len(line), sha256(line).digest())
        _, metadata = self._cause_indexes[-1]
        self._cause_indexes.data[self.length - 1] = (frame, metadata)
        self.data[self.length - 1] = frame

    def _cause_metadata(
        self, record: dict[str, Any]
    ) -> list[dict[str, Any] | FactAuthority]:
        """Keep only authority data, independently of the decoded-payload LRU.

        These summaries are internal validation indexes, never diagnostic records.
        Payloads and cause lists have already been checked at publication; cause
        authority depends on kind, ownership, coordinates and declaration keys.
        """
        from .ids import EntityRef

        def summary(value: Any) -> Any:
            if isinstance(value, list):
                return [summary(v) for v in value]
            if not isinstance(value, dict):
                return value
            return {
                key: []
                if key in {"causes", "cleanup_refs"}
                else None
                if key in {"value", "payload", "result"}
                else {}
                if key in {"bindings", "sources", "clocks"}
                else summary(v)
                for key, v in value.items()
            }

        result: list[dict[str, Any] | FactAuthority] = []
        entities: dict[int, Any] = {}
        for item in record.get("items", []):
            if "$fact" in item or "$retract" in item:
                # Facts need only ownership and their declared read key. Neither
                # payloads nor stamps/intervals participate in cause authority.
                tag = "$fact" if "$fact" in item else "$retract"
                row = item[tag]
                entity = entities.get(row[1])
                if entity is None:
                    identity = tuple(record["fact_tables"]["entities"][row[1]])
                    entity = self._entities.get(identity)
                    if entity is None:
                        entity = EntityRef(*identity)
                        self._entities[identity] = entity
                    entities[row[1]] = entity
                field = record["fact_tables"]["fields"][row[2]]
                authority_key = (row[0], entity, field)
                authority = self._fact_authorities.get(authority_key)
                if authority is None:
                    authority = FactAuthority(row[0], (entity, field))
                    self._fact_authorities[authority_key] = authority
                result.append(authority)
                continue
            result.append(
                summary(
                    {
                        k: None if k == "version" else v
                        for k, v in item.items()
                        if k
                        in {
                            "kind",
                            "partition",
                            "proposal",
                            "version",
                            "message",
                            "delivery",
                            "edge",
                            "obligation",
                            "command_id",
                            "status",
                        }
                    }
                )
            )
        return result

    def cause_item(self, record_index: int, item_index: int) -> dict[str, Any]:
        """Internal immutable authority summary; callers must only read it."""
        value = super().__getitem__(record_index)
        if record_index < 0:
            record_index += self.length
        if record_index < len(self._cause_indexes):
            identity, items = self._cause_indexes[record_index]
            if identity is value:
                if items is None:
                    items = self._cause_metadata(self._raw(record_index))
                    self._cause_indexes.data[record_index] = (value, items)
                if not 0 <= item_index < len(items):
                    raise IndexError(item_index)
                return self._authority(items[item_index])
        items = self._cause_metadata(self._raw(record_index))
        if not 0 <= item_index < len(items):
            raise IndexError(item_index)
        return self._authority(items[item_index])

    @staticmethod
    def _authority(item: dict[str, Any] | FactAuthority) -> dict[str, Any]:
        if isinstance(item, FactAuthority):
            return {
                "kind": "operation",
                "partition": item.partition,
                "version": None,
                "_key": item.key,
            }
        return item

    def _raw(self, index: int) -> dict[str, Any]:
        from .values import parse_json

        value = super().__getitem__(index)
        if not isinstance(value, (bytes, FileFrame)):
            return cast(dict[str, Any], value)
        if index < 0:
            index += self.length
        key = (index, id(value))
        cached = self._decoded.get(key)
        if cached is not None:
            self._decoded.move_to_end(key)
            return cached[1]
        line = value.read() if isinstance(value, FileFrame) else value
        budget = self.budget
        if self.provenance == "lean":
            from .values import ResourceBudget

            budget = ResourceBudget(
                budget.integer_digits, budget.frame_bytes, budget.nesting_depth * 2 + 2
            )
        record = cast(dict[str, Any], parse_json(line, budget))
        if "data" in record:
            from .journal_codec import decode_frame

            record = decode_frame(record, self.budget, lean=self.provenance == "lean")
        self._decoded[key] = (line, record)
        if len(self._decoded) > 8:
            self._decoded.popitem(last=False)
        return record

    def item(self, record_index: int, item_index: int) -> dict[str, Any]:
        """Resolve one item without expanding other facts or returned batches."""
        from .compact import expand_item

        record = self._raw(record_index)
        items = record.get("items", [])
        if not 0 <= item_index < len(items):
            raise IndexError(item_index)
        return deepcopy(expand_item(record, item_index))

    @overload
    def __getitem__(self, index: int) -> dict[str, Any]: ...

    @overload
    def __getitem__(self, index: slice) -> list[dict[str, Any]]: ...

    def __getitem__(self, index: int | slice) -> dict[str, Any] | list[dict[str, Any]]:
        from .compact import expand_record

        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(self.length))]
        return deepcopy(expand_record(self._raw(index)))

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for index in range(self.length):
            yield self[index]
