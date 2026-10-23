"""Journal-only model capability for trusted, deterministic graph factories."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from copy import deepcopy
from typing import Any, cast

from .provider import OpenAIProvider, Provider, ProviderError
from .tools import strict_json


def json_copy(value: Any) -> Any:
    return strict_json(json.dumps(value, ensure_ascii=False, allow_nan=False))


def provider_record_metadata(
    provider: Provider | None,
    model: str,
    execution_mode: str,
    *,
    provider_mode: str | None = None,
    provider_profile: str | None = None,
) -> dict[str, Any]:
    """Read only credential-free policy attributes, never provider internals.

    Resolver metadata is supplied only when that resolver selected the provider;
    an injected wrapper may instead expose its own explicit mode/policy. Optional
    metadata properties must not turn an otherwise valid provider call into a fault.
    """

    def recorded_attribute(name: str) -> Any:
        try:
            return getattr(provider, name, None)
        except Exception:  # noqa: BLE001 -- optional recording cannot break providers
            return None

    mode = provider_mode
    if type(mode) is not str or mode not in {"stub", "scripted", "live"}:
        mode = recorded_attribute("mode")
    if type(mode) is not str or mode not in {"stub", "scripted", "live"}:
        mode = "live" if isinstance(provider, OpenAIProvider) else None
    scripted = mode in {"stub", "scripted"} or (
        mode is None
        and type(model) is str
        and model in {"stub", "scripted", "lowalt-scripted-allocation/v1"}
    )
    transformations = recorded_attribute("transport_transformations")
    return {
        "provider_mode": mode,
        "provider_profile": provider_profile if type(provider_profile) is str else None,
        "execution_mode": "scripted" if scripted else execution_mode,
        "transport_transformations": (
            list(transformations)
            if type(transformations) in (list, tuple)
            and all(type(item) is str for item in transformations)
            else None
        ),
    }


class ScriptedProvider:
    """Explicit per-call scripts; exhausted or missing scripts are real failures."""

    mode = "stub"

    def __init__(self, model: str, responses: dict[str, list[dict[str, Any]]]) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("scripted model name required")
        if not isinstance(responses, dict) or any(
            not isinstance(key, str) or not key or not isinstance(rows, list)
            for key, rows in responses.items()
        ):
            raise ValueError("scripted responses require named lists")
        self.model = model
        self.responses = deepcopy(responses)
        self.calls = 0

    def scripted(self, key: str) -> dict[str, Any]:
        self.calls += 1
        if key not in self.responses or not self.responses[key]:
            raise ProviderError("SCRIPT_EXHAUSTED", key)
        response = self.responses[key].pop(0)
        if "error" in response:
            raise ProviderError(response["error"]["code"], response["error"]["message"])
        return json_copy(response)  # type: ignore[no-any-return]

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        raise ProviderError("PROTOCOL", "scripted calls require a named graph key")


class JournalClient:
    """Nodes await this capability; they receive no kernel or mutable context.

    Calls are identified by (superstep, node, caller key, attempt), never by
    completion order. All buffers are confined to the graph event loop.
    """

    def __init__(
        self,
        provider: Provider | None,
        model: str,
        budget: dict[str, Any],
        *,
        replay: list[dict[str, Any]] | None = None,
        provider_mode: str | None = None,
        provider_profile: str | None = None,
    ) -> None:
        self._provider, self.model, self.budget = provider, model, budget
        self._provider_metadata = provider_record_metadata(
            provider,
            model,
            "structured_json",
            provider_mode=provider_mode,
            provider_profile=provider_profile,
        )
        self._deadline = time.monotonic() + budget["wall_timeout_s"]
        self._calls: dict[tuple[int, str, str, int], dict[str, Any]] = {}
        self._pending: set[tuple[int, str, str, int]] = set()
        self._replay = (
            None
            if replay is None
            else {tuple(row["identity"]): deepcopy(row) for row in replay}
        )
        if replay is not None and len(self._replay or {}) != len(replay):
            raise ValueError("duplicate replay call identity")
        self._used: set[tuple[int, str, str, int]] = set()
        self._spent_tokens = 0
        self._reserved_tokens = 0
        self._admission = asyncio.Lock()

    @property
    def records(self) -> list[dict[str, Any]]:
        return [json_copy(self._calls[key]) for key in sorted(self._calls)]

    async def _exchange(
        self,
        key: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        remaining: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        provider = self._provider
        if provider is None:
            raise ProviderError("REPLAY_MISMATCH", "replay cannot call a provider")
        if isinstance(provider, ScriptedProvider):
            return provider.scripted(key)
        # Daemon workers cannot delay loop shutdown or mutate journal buffers.
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        def deliver(result: dict[str, Any] | Exception) -> None:
            if future.done():
                return
            if isinstance(result, Exception):
                future.set_exception(result)
            else:
                future.set_result(result)

        def worker() -> None:
            try:
                result: dict[str, Any] | Exception = provider.complete(
                    messages,
                    tools,
                    timeout_s=remaining,
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 -- retain provider faults, never synthesize success
                result = exc
            try:
                loop.call_soon_threadsafe(deliver, result)
            except RuntimeError:
                pass  # Closed loop: late results have no publication capability.

        threading.Thread(target=worker, daemon=True, name="aas-graph-model").start()
        return await asyncio.wait_for(future, remaining)

    async def complete(
        self,
        key: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        attempt: int = 0,
    ) -> dict[str, Any]:
        from langgraph.config import get_config

        metadata = get_config()["metadata"]
        identity = (
            metadata["langgraph_step"],
            metadata["langgraph_node"],
            key,
            attempt,
        )
        if (
            not isinstance(key, str)
            or not key
            or identity in self._calls
            or identity in self._pending
        ):
            raise ProviderError("CALL_ID", "unique nonempty key per node/step required")
        # Retain identity before the cancellable wait, without reserving tokens.
        self._pending.add(identity)
        try:
            if self._replay is not None:
                return await self._complete(key, messages, tools, identity=identity)
            # The per-call cap is also the aggregate cap: only one full remaining
            # allowance fits at a time. Recheck bounds after the active call settles.
            try:
                await self._admission.acquire()
            except asyncio.CancelledError:
                # Journal the request and typed cancellation, but never dispatch,
                # settle tokens, or release the active call's admission lock.
                return await self._complete(
                    key, messages, tools, identity=identity, cancelled=True
                )
            try:
                return await self._complete(key, messages, tools, identity=identity)
            finally:
                self._admission.release()
        finally:
            self._pending.remove(identity)

    async def _complete(
        self,
        key: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        identity: tuple[int, str, str, int],
        cancelled: bool = False,
    ) -> dict[str, Any]:
        allowance = max(
            0, self.budget["max_tokens"] - self._spent_tokens - self._reserved_tokens
        )
        if self._replay is not None:
            if identity not in self._replay:
                raise ProviderError("REPLAY_MISMATCH", f"request changed: {identity}")
            # Admission/cancellation timing is not reproduced by zero-I/O replay.
            # New rows retain that input; legacy rows used the full per-call cap.
            # Compare the entire request, including max_tokens, below.
            allowance = self._replay[identity].get(
                "token_allowance", self.budget["max_tokens"]
            )
            if (
                type(allowance) is not int
                or not 0 <= allowance <= self.budget["max_tokens"]
            ):
                raise ProviderError("REPLAY_MISMATCH", "invalid token allowance")
        request = json_copy(
            {
                "messages": messages,
                "tools": [] if tools is None else tools,
                "model": self.model,
                "max_tokens": min(self.budget["max_tokens"], allowance),
                "stream": False,
            }
        )
        row: dict[str, Any] = {
            "identity": list(identity),
            "request": request,
            **self._provider_metadata,
            "execution_mode": (
                "scripted"
                if self._provider_metadata["execution_mode"] == "scripted"
                else "function_tools"
                if request["tools"]
                else "structured_json"
            ),
            "attempt": identity[3],
            "failure_code": None,
        }
        self._calls[identity] = row
        if self._replay is not None:
            if self._replay[identity]["request"] != request:
                raise ProviderError("REPLAY_MISMATCH", f"request changed: {identity}")
            recorded = self._replay[identity]
            self._used.add(identity)
            row.update(deepcopy(recorded))
        else:
            row["token_allowance"] = allowance
            reserved = 0
            started = time.monotonic()
            remaining = self._deadline - started
            row["timeout_s"] = (
                self.budget["wall_timeout_s"]
                if isinstance(self._provider, ScriptedProvider)
                else max(0.0, remaining)
            )
            try:
                if cancelled:
                    raise asyncio.CancelledError
                if len(self._calls) > self.budget["max_calls"]:
                    raise ProviderError("CALL_BUDGET", "graph call limit exceeded")
                if (
                    len(json.dumps(request, ensure_ascii=False).encode())
                    > self.budget["max_prompt_bytes"]
                ):
                    raise ProviderError(
                        "PROMPT_BUDGET", "graph prompt byte limit exceeded"
                    )
                if remaining <= 0:
                    raise ProviderError("WALL_TIMEOUT", "graph wall deadline expired")
                if allowance <= 0:
                    raise ProviderError(
                        "TOKEN_BUDGET", "aggregate graph token limit exhausted"
                    )
                # No await between admission and reservation: parallel nodes
                # cannot promise the same remaining tokens to two providers.
                reserved = request["max_tokens"]
                self._reserved_tokens += reserved
                response = await self._exchange(
                    key,
                    request["messages"],
                    request["tools"],
                    remaining,
                    request["max_tokens"],
                )
                row["response"] = json_copy(response)
                if time.monotonic() > self._deadline:
                    raise ProviderError("WALL_TIMEOUT", "late model response")
                if response["message"]["role"] != "assistant":
                    raise ProviderError("PROTOCOL", "assistant response required")
                used = response["usage"]["completion_tokens"]
                if type(used) is not int or used < 0:
                    raise ProviderError(
                        "USAGE_MISSING", "completion token count required"
                    )
                if used > request["max_tokens"]:
                    raise ProviderError(
                        "TOKEN_BUDGET", "completion token limit exceeded"
                    )
            except asyncio.CancelledError:
                row["error"] = {
                    "code": "WALL_TIMEOUT",
                    "message": "graph invocation canceled",
                }
                raise
            except (TimeoutError, asyncio.TimeoutError, ProviderError, KeyError, TypeError) as exc:
                code = (
                    exc.code
                    if isinstance(exc, ProviderError)
                    else "WALL_TIMEOUT"
                    if isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                    else "PROTOCOL"
                )
                row["error"] = {"code": code, "message": str(exc)}
            except Exception as exc:  # noqa: BLE001 -- retain provider faults, never synthesize success
                row["error"] = {"code": "PROVIDER_ERROR", "message": str(exc)}
            finally:
                if reserved:
                    recorded_response = row.get("response")
                    usage = (
                        recorded_response.get("usage")
                        if isinstance(recorded_response, dict)
                        else None
                    )
                    used = (
                        usage.get("completion_tokens") if isinstance(usage, dict) else None
                    )
                    if type(used) is int and used >= 0:
                        self._reserved_tokens -= reserved
                        self._spent_tokens += used
                    elif (
                        isinstance(self._provider, ScriptedProvider)
                        and "response" not in row
                    ):
                        # A script fault performs no inference. Preserve scripted
                        # transport retries without treating unknown live usage
                        # (or a scripted response with missing usage) as zero.
                        self._reserved_tokens -= reserved
                    # Unknown usage is not zero. Keep its reservation, including
                    # after cancellation: an I/O worker may still be running.
                row["latency_s"] = (
                    0.0
                    if isinstance(self._provider, ScriptedProvider)
                    else time.monotonic() - started
                )
                if "error" in row:
                    row["failure_code"] = row["error"]["code"]
        if "error" in row:
            row["failure_code"] = row["error"]["code"]
            raise ProviderError(row["error"]["code"], row["error"]["message"])
        if "rejection" in row:
            row["failure_code"] = row["rejection"]["code"]
        return cast(dict[str, Any], deepcopy(row["response"]))

    async def ask_json(
        self,
        key: str,
        system_prompt: str,
        observation: dict[str, Any],
        validator: Callable[[dict[str, Any]], None],
        *,
        user_prefix: str = "",
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": user_prefix
                + json.dumps(observation, ensure_ascii=False, allow_nan=False),
            },
        ]
        for attempt in range(self.budget["max_retries"] + 1):
            try:
                response = await self.complete(key, messages, attempt=attempt)
            except ProviderError as exc:
                if (
                    exc.code in {"HTTP_STATUS", "TRANSPORT"}
                    and attempt < self.budget["max_retries"]
                ):
                    continue
                raise
            from langgraph.config import get_config

            meta = get_config()["metadata"]
            row = self._calls[
                (meta["langgraph_step"], meta["langgraph_node"], key, attempt)
            ]
            try:
                value = strict_json(response["message"]["content"])
                if not isinstance(value, dict):
                    raise TypeError("model JSON must be an object")
                validator(value)
                row["validation"] = {"valid": True}
                return cast(dict[str, Any], value)
            except (ValueError, KeyError, TypeError) as exc:
                # Every attempt keeps its own response and typed rejection.
                row["rejection"] = {
                    "code": "TOOL_REJECTED",
                    "message": str(exc),
                }
                row["validation"] = {"valid": False, "code": "TOOL_REJECTED"}
                row["failure_code"] = "TOOL_REJECTED"
                if attempt == self.budget["max_retries"]:
                    raise ProviderError("TOOL_REJECTED", str(exc)) from exc
                messages.extend(
                    [
                        response["message"],
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "status": "rejected",
                                    "code": "TOOL_REJECTED",
                                    "message": str(exc),
                                }
                            ),
                        },
                    ]
                )
        raise AssertionError("bounded retry loop exhausted")

    def verify(self) -> None:
        if self._replay is not None and self._used != set(self._replay):
            raise ProviderError("REPLAY_MISMATCH", "unconsumed recorded model calls")
        tokens = sum(
            row["response"]["usage"]["completion_tokens"]
            for row in self.records
            if "response" in row and "error" not in row
        )
        if tokens > self.budget["max_tokens"]:
            raise ProviderError("TOKEN_BUDGET", "aggregate graph token limit exceeded")
