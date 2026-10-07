from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from aero_bench.runtime.contracts import (
    SCENE_STATE_ROOT_DIGEST,
    SceneState,
)
from aero_bench.serialization import canonical_json_bytes


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("scene state history JSON contains duplicate object keys")
        document[key] = value
    return document


def _reject_non_finite_json(value: str) -> object:
    raise ValueError(f"scene state history JSON contains non-finite value: {value}")


def _canonical_scene_state(state: SceneState) -> SceneState:
    if not isinstance(state, SceneState):
        raise ValueError("scene state history contains a non-SceneState value")
    try:
        canonical = SceneState.model_validate(state.model_dump(mode="json"))
    except (TypeError, ValueError) as error:
        raise ValueError("scene state history contains an invalid SceneState") from error
    if canonical != state:
        raise ValueError("scene state history contains a non-canonical SceneState")
    return canonical


@dataclass(frozen=True, slots=True)
class SceneStateHistoryEntry:
    """Byte index for one atomically appended canonical SceneState record."""

    tick: int
    sim_time_ns: int
    offset: int
    size_bytes: int
    scene_state_digest: str
    record_sha256: str


class IncrementalSceneStateHistory:
    """Append and validate SceneStates while a run is executing.

    The staging file is never exposed as a public artifact.  It is a crash
    diagnosable write-ahead history; callers seal it only after the terminal
    event and provider shutdown have closed the run.
    """

    def __init__(self, path: Path, *, run_id: str, scenario_digest: str) -> None:
        if not isinstance(path, Path):
            raise TypeError("SceneState history staging path must be a Path")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("SceneState history run_id is required")
        if not isinstance(scenario_digest, str) or not scenario_digest:
            raise ValueError("SceneState history scenario_digest is required")
        self.path = path
        self.run_id = run_id
        self.scenario_digest = scenario_digest
        self._stream = None
        self._previous: SceneState | None = None
        self._entries: list[SceneStateHistoryEntry] = []
        self._total_size_bytes = 0
        self._sealed_reader: BinaryIO | None = None
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._stream = self.path.open("xb")
            stat = os.fstat(self._stream.fileno())
            self._identity = (stat.st_dev, stat.st_ino)
        except FileExistsError as error:
            raise ValueError("SceneState history staging path already exists") from error
        except OSError as error:
            raise ValueError("SceneState history staging path is unavailable") from error

    @property
    def entries(self) -> tuple[SceneStateHistoryEntry, ...]:
        return tuple(self._entries)

    @property
    def latest(self) -> SceneState | None:
        return self._previous

    @property
    def count(self) -> int:
        """Number of durably appended SceneState records."""

        return len(self._entries)

    def _open_reader(self) -> BinaryIO:
        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        reader = os.fdopen(descriptor, "rb")
        stat = os.fstat(descriptor)
        if (stat.st_dev, stat.st_ino) != self._identity:
            reader.close()
            raise ValueError("SceneState history staging identity differs from append index")
        return reader

    def _read_bytes(self, offset: int, size_bytes: int) -> bytes:
        reader = self._sealed_reader
        owned = reader is None
        try:
            if reader is None:
                reader = self._open_reader()
            descriptor = reader.fileno()
            if os.fstat(descriptor).st_size != self._total_size_bytes:
                raise ValueError("SceneState history size differs from append index")
            payload = os.pread(descriptor, size_bytes, offset)
            if len(payload) != size_bytes:
                raise ValueError("SceneState history read differs from append index")
            return payload
        except OSError as error:
            raise ValueError("SceneState history cannot be read") from error
        finally:
            if owned and reader is not None:
                reader.close()

    def _validate_record(
        self, line: bytes, entry: SceneStateHistoryEntry, index: int
    ) -> SceneState:
        previous = self._entries[index - 1] if index else None
        expected_offset = previous.offset + previous.size_bytes if previous else 0
        if (
            entry.offset != expected_offset
            or entry.tick != index + 1
            or entry.size_bytes != len(line)
            or not line.endswith(b"\n")
            or hashlib.sha256(line).hexdigest() != entry.record_sha256
        ):
            raise ValueError("SceneState history record differs from append index")
        try:
            state = SceneState.model_validate(
                json.loads(
                    line.decode("utf-8"),
                    object_pairs_hook=_reject_duplicate_keys,
                    parse_constant=_reject_non_finite_json,
                )
            )
        except (UnicodeError, TypeError, ValueError) as error:
            raise ValueError("SceneState history contains invalid SceneState") from error
        if (
            state.at.tick != entry.tick
            or state.at.sim_time_ns != entry.sim_time_ns
            or state.scene_state_digest != entry.scene_state_digest
            or state.run_id != self.run_id
            or state.scenario_digest != self.scenario_digest
            or state.previous_scene_state_digest != (
                previous.scene_state_digest if previous else SCENE_STATE_ROOT_DIGEST
            )
            or (previous is not None and state.at.sim_time_ns <= previous.sim_time_ns)
            or canonical_json_bytes(state.model_dump(mode="json")) + b"\n" != line
        ):
            raise ValueError("SceneState history identity differs from append index")
        return state

    def _validate_content(
        self, content: bytes, *, aborted_before_first_motion: bool
    ) -> Iterator[SceneState]:
        if type(aborted_before_first_motion) is not bool:
            raise TypeError("aborted_before_first_motion must be a bool")
        if not self._entries and not aborted_before_first_motion:
            raise ValueError("SceneState history cannot be empty")
        if self._entries and aborted_before_first_motion:
            raise ValueError("aborted_before_first_motion requires empty history")
        offset = 0
        stream = io.BytesIO(content)
        for index, entry in enumerate(self._entries):
            line = stream.readline()
            yield self._validate_record(line, entry, index)
            offset += len(line)
        if stream.read(1) or offset != len(content) or offset != self._total_size_bytes:
            raise ValueError("SceneState history size differs from append index")

    def read_page(self, *, after_tick: int, limit: int) -> tuple[SceneState, ...]:
        """Read a bounded page, including after sealed staging is unlinked."""

        if (
            type(after_tick) is not int
            or after_tick < 0
            or after_tick > self.count
            or type(limit) is not int
            or not 1 <= limit <= 16
        ):
            raise ValueError("SceneState history page bounds are invalid")
        selected = self._entries[after_tick : after_tick + limit]
        if not selected:
            return ()
        payload = self._read_bytes(
            selected[0].offset, sum(entry.size_bytes for entry in selected)
        )
        stream = io.BytesIO(payload)
        return tuple(
            self._validate_record(stream.readline(), entry, after_tick + index)
            for index, entry in enumerate(selected)
        )

    def read_all(self, *, aborted_before_first_motion: bool = False) -> tuple[SceneState, ...]:
        """Read and validate every record against the append-time index."""

        return tuple(self._validate_content(
            self._read_bytes(0, self._total_size_bytes),
            aborted_before_first_motion=aborted_before_first_motion,
        ))

    def append(self, state: SceneState) -> SceneStateHistoryEntry:
        if self._closed or self._stream is None:
            raise ValueError("SceneState history writer is closed")
        canonical = _canonical_scene_state(state)
        if canonical.run_id != self.run_id or canonical.scenario_digest != self.scenario_digest:
            raise ValueError("SceneState history record has the wrong run or scenario")
        expected_tick = len(self._entries) + 1
        if canonical.at.tick != expected_tick:
            raise ValueError("SceneState history append tick is not contiguous")
        if self._previous is None:
            if canonical.previous_scene_state_digest != SCENE_STATE_ROOT_DIGEST:
                raise ValueError("first SceneState history record does not bind the chain root")
        else:
            if canonical.previous_scene_state_digest != self._previous.scene_state_digest:
                raise ValueError("SceneState history append predecessor is invalid")
            if canonical.at.sim_time_ns <= self._previous.at.sim_time_ns:
                raise ValueError("SceneState history append time is not advancing")
        line = canonical_json_bytes(canonical.model_dump(mode="json")) + b"\n"
        offset = self._stream.tell()
        if offset != self._total_size_bytes or os.fstat(self._stream.fileno()).st_size != offset:
            raise ValueError("SceneState history size differs from append index")
        try:
            self._stream.write(line)
            self._stream.flush()
            os.fsync(self._stream.fileno())
        except OSError as error:
            raise ValueError("SceneState history append failed") from error
        entry = SceneStateHistoryEntry(
            tick=canonical.at.tick,
            sim_time_ns=canonical.at.sim_time_ns,
            offset=offset,
            size_bytes=len(line),
            scene_state_digest=canonical.scene_state_digest,
            record_sha256=hashlib.sha256(line).hexdigest(),
        )
        self._entries.append(entry)
        self._total_size_bytes += entry.size_bytes
        self._previous = canonical
        return entry

    def seal(self, *, aborted_before_first_motion: bool = False) -> bytes:
        if type(aborted_before_first_motion) is not bool:
            raise TypeError("aborted_before_first_motion must be a bool")
        if self._stream is not None:
            try:
                self._stream.flush()
                os.fsync(self._stream.fileno())
                self._stream.close()
            except OSError as error:
                raise ValueError("SceneState history staging file cannot be closed") from error
            self._stream = None
            self._closed = True
        try:
            if self._sealed_reader is None:
                self._sealed_reader = self._open_reader()
            content = self._read_bytes(0, self._total_size_bytes)
            # Validate once without retaining a second complete object history.
            for _ in self._validate_content(
                content, aborted_before_first_motion=aborted_before_first_motion
            ):
                pass
        except (OSError, ValueError):
            if self._sealed_reader is not None:
                self._sealed_reader.close()
                self._sealed_reader = None
            raise
        return content

    def discard(self) -> None:
        """Unlink private staging; a sealed read-only descriptor stays readable."""

        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._closed = True
        try:
            stat = self.path.lstat()
            if (stat.st_dev, stat.st_ino) != self._identity:
                raise ValueError("SceneState history staging path was replaced; refusing removal")
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ValueError("SceneState history staging file cannot be removed") from error

    def close(self) -> None:
        """Release readers when the coordinator no longer serves projection pages."""

        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._sealed_reader is not None:
            self._sealed_reader.close()
            self._sealed_reader = None
        self._closed = True


def validate_scene_state_history(
    states: tuple[SceneState, ...],
    *,
    aborted_before_first_motion: bool = False,
) -> tuple[SceneState, ...]:
    """Validate one contiguous, hash-chained SceneState history."""

    if type(aborted_before_first_motion) is not bool:
        raise TypeError("aborted_before_first_motion must be a bool")
    if not isinstance(states, tuple):
        raise ValueError("scene state history must be a tuple")
    if not states:
        if aborted_before_first_motion:
            return ()
        raise ValueError("scene state history cannot be empty")
    if aborted_before_first_motion:
        raise ValueError(
            "aborted_before_first_motion is only valid for an empty scene state history"
        )

    canonical_states = tuple(_canonical_scene_state(state) for state in states)
    first = canonical_states[0]
    run_id = first.run_id
    scenario_digest = first.scenario_digest
    previous: SceneState | None = None
    for expected_tick, state in enumerate(canonical_states, start=1):
        if state.run_id != run_id or state.scenario_digest != scenario_digest:
            raise ValueError("scene state history crosses a run or scenario boundary")
        if state.at.tick != expected_tick:
            raise ValueError("scene state history ticks must be contiguous from tick 1")
        if previous is None:
            if state.previous_scene_state_digest != SCENE_STATE_ROOT_DIGEST:
                raise ValueError("first scene state does not bind the chain root")
        else:
            if state.at.sim_time_ns <= previous.at.sim_time_ns:
                raise ValueError("scene state history simulation time must strictly advance")
            if state.previous_scene_state_digest != previous.scene_state_digest:
                raise ValueError("scene state history predecessor digest is invalid")
        previous = state
    return canonical_states


def scene_state_jsonl_bytes(
    states: tuple[SceneState, ...],
    *,
    aborted_before_first_motion: bool = False,
) -> bytes:
    """Return the canonical JSONL encoding of a validated SceneState history."""

    history = validate_scene_state_history(
        states,
        aborted_before_first_motion=aborted_before_first_motion,
    )
    return b"".join(
        canonical_json_bytes(state.model_dump(mode="json")) + b"\n"
        for state in history
    )


def scene_state_history_from_jsonl_bytes(
    content: bytes,
    *,
    aborted_before_first_motion: bool = False,
) -> tuple[SceneState, ...]:
    """Parse and strictly validate canonical SceneState-history JSONL bytes."""

    if type(aborted_before_first_motion) is not bool:
        raise TypeError("aborted_before_first_motion must be a bool")
    if not isinstance(content, bytes):
        raise ValueError("scene state history content must be bytes")
    if not content:
        return validate_scene_state_history(
            (),
            aborted_before_first_motion=aborted_before_first_motion,
        )
    if aborted_before_first_motion:
        raise ValueError(
            "aborted_before_first_motion is only valid for an empty scene state history"
        )
    if not content.endswith(b"\n"):
        raise ValueError("scene state history must use newline-terminated canonical JSONL")
    raw_lines = content[:-1].split(b"\n")
    if not raw_lines or any(not raw_line for raw_line in raw_lines):
        raise ValueError("scene state history contains an empty record")

    states: list[SceneState] = []
    for line_number, raw_line in enumerate(raw_lines, start=1):
        try:
            decoded = raw_line.decode("utf-8")
            document = json.loads(
                decoded,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_json,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError(
                f"scene state history record {line_number} contains invalid JSON"
            ) from error
        if not isinstance(document, dict):
            raise ValueError(
                f"scene state history record {line_number} must be a JSON object"
            )
        try:
            state = SceneState.model_validate(document)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"scene state history record {line_number} is not a valid SceneState"
            ) from error
        states.append(state)
    return validate_scene_state_history(tuple(states))


def read_scene_state_history_jsonl(
    source: Path,
    *,
    aborted_before_first_motion: bool = False,
) -> tuple[SceneState, ...]:
    """Read canonical SceneState-history JSONL from an authoritative artifact."""

    try:
        content = source.resolve(strict=True).read_bytes()
    except (OSError, RuntimeError) as error:
        raise ValueError("scene state history artifact is unavailable") from error
    return scene_state_history_from_jsonl_bytes(
        content,
        aborted_before_first_motion=aborted_before_first_motion,
    )


__all__ = [
    "IncrementalSceneStateHistory",
    "SceneStateHistoryEntry",
    "read_scene_state_history_jsonl",
    "scene_state_history_from_jsonl_bytes",
    "scene_state_jsonl_bytes",
    "validate_scene_state_history",
]
