"""Prefix-limited append storage and bounded copy-on-write candidate indexes."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from copy import deepcopy
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

    def fork(self) -> RecordLog:
        """Retain this exact prefix of the shared encoded log."""
        result = RecordLog(self.data, self.length, self.budget)
        result._decoded = self._decoded
        return result

    def freeze_tail(self, line: bytes) -> None:
        """Replace the newly acknowledged private tail with its compact bytes."""
        self.data[self.length - 1] = line

    def _raw(self, index: int) -> dict[str, Any]:
        from .values import parse_json

        value = super().__getitem__(index)
        if not isinstance(value, bytes):
            return cast(dict[str, Any], value)
        if index < 0:
            index += self.length
        key = (index, id(value))
        cached = self._decoded.get(key)
        if cached is not None:
            self._decoded.move_to_end(key)
            return cached[1]
        record = cast(dict[str, Any], parse_json(value, self.budget))
        self._decoded[key] = (value, record)
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
