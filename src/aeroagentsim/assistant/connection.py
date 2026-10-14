"""Operator or user model connection, with credentials kept off the wire."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aeroagentsim.agents.provider import OpenAIProvider, Provider, ProviderError
from aeroagentsim.agents.tools import strict_json

PROFILE_LOCK = threading.RLock()
_PROFILE_API_KEY = "AEROAGENTSIM_ASSISTANT_PROFILE_API_KEY"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def provider_detail(error: ProviderError, timeout_s: int = 90) -> str:
    if error.code == "WALL_TIMEOUT":
        return f"The model did not answer within {timeout_s} s / 模型未在 {timeout_s} 秒内响应"
    if error.code == "HTTP_STATUS":
        match = re.search(r"HTTP (\d{3})", str(error))
        status = match.group(1) if match else "unavailable / 未提供"
        return f"The model endpoint refused the request (HTTP {status}); check the endpoint and API key / 模型端点拒绝请求（HTTP {status}）；请检查端点和 API 密钥"
    if error.code == "TRANSPORT":
        return "Cannot reach the model endpoint / 无法连接模型端点"
    return "The model returned an invalid completion / 模型返回了无效响应"


class ConnectionStore:
    def __init__(
        self,
        run_root: Path,
        *,
        provider_factory: Callable[..., Provider] = OpenAIProvider,
    ) -> None:
        self.root = run_root / "assistant"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "connection.json"
        self.key_path = self.root / "connection.key"
        self.factory = provider_factory
        self.lock = threading.RLock()
        with self.lock:
            self._refresh_profile()

    def _settings(self) -> tuple[dict[str, Any], str | None]:
        saved = strict_json(self.path.read_text()) if self.path.exists() else {}
        if saved.get("source") == "user":
            key = self.key_path.read_text() if self.key_path.exists() else None
            return saved, key
        base_url = os.environ.get("AEROAGENTSIM_ASSISTANT_BASE_URL")
        model = os.environ.get("AEROAGENTSIM_ASSISTANT_MODEL")
        key_env = os.environ.get("AEROAGENTSIM_ASSISTANT_API_KEY_ENV", "AEROAGENTSIM_ASSISTANT_API_KEY")
        key = os.environ.get(key_env)
        settings: dict[str, Any] = {
            "source": "operator" if base_url or model else None,
            "base_url": base_url,
            "model": model,
        }
        if (saved.get("base_url"), saved.get("model")) == (base_url, model):
            settings.update({k: saved[k] for k in ("status", "detail", "tested_at") if k in saved})
        return settings, key

    def _write(self, settings: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".pending.json")
        temporary.write_text(json.dumps(settings, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(self.path)

    def _profiles(self) -> dict[str, Any]:
        profiles = strict_json(os.environ.get("AEROAGENTSIM_PROVIDER_PROFILES", "{}"))
        if not isinstance(profiles, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in profiles.items()):
            raise ValueError("Provider profiles must map names to objects / 提供者配置必须是名称到对象的映射")
        return profiles

    def _refresh_profile(self) -> tuple[dict[str, Any], str | None]:
        settings, key = self._settings()
        if not settings.get("base_url") or not settings.get("model"):
            return settings, key
        OpenAIProvider(settings["base_url"], settings["model"], key)
        with PROFILE_LOCK:
            profiles = self._profiles()
            profiles["assistant"] = {
                "base_url": settings["base_url"].rstrip("/"),
                "model": settings["model"],
                "api_key_env": _PROFILE_API_KEY,
            }
            if key is None:
                os.environ.pop(_PROFILE_API_KEY, None)
            else:
                os.environ[_PROFILE_API_KEY] = key
            os.environ["AEROAGENTSIM_PROVIDER_PROFILES"] = json.dumps(profiles)
        return settings, key

    def get(self) -> dict[str, Any]:
        with self.lock:
            settings, key = self._settings()
            return {
                "configured": bool(settings.get("base_url") and settings.get("model")),
                "source": settings.get("source"),
                "base_url": settings.get("base_url"),
                "model": settings.get("model"),
                "has_key": bool(key),
                "status": settings.get("status", "untested"),
                "detail": settings.get("detail"),
                "tested_at": settings.get("tested_at"),
            }

    def set(self, base_url: str, model: str, api_key: str | None = None, *, clear_key: bool = False) -> dict[str, Any]:
        if not isinstance(base_url, str):
            raise ValueError("Model endpoint must be a string / 模型端点必须是字符串")
        OpenAIProvider(base_url, model, api_key)
        if api_key is not None and (not isinstance(api_key, str) or not api_key):
            raise ValueError("API key must be a nonempty string / API 密钥必须是非空字符串")
        if type(clear_key) is not bool or (clear_key and api_key is not None):
            raise ValueError("Choose a key or clear_key / 设置密钥和清除密钥不能同时使用")
        with self.lock, PROFILE_LOCK:
            self._profiles()
            _, previous_key = self._settings()
            key = None if clear_key else api_key if api_key is not None else previous_key
            if key is None:
                self.key_path.unlink(missing_ok=True)
            else:
                temporary = self.key_path.with_suffix(".pending")
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    os.fchmod(file.fileno(), 0o600)
                    file.write(key)
                temporary.replace(self.key_path)
            self._write({"source": "user", "base_url": base_url.rstrip("/"), "model": model, "status": "untested", "detail": None, "tested_at": None})
            self._refresh_profile()
        return self.get()

    def provider(self, session_id: str | None = None) -> Provider:
        with self.lock:
            settings, key = self._refresh_profile()
            if not settings.get("base_url") or not settings.get("model"):
                raise ValueError("Connect a model first / 请先连接模型")
            provider = self.factory(settings["base_url"], settings["model"], key)
            if isinstance(provider, OpenAIProvider):
                provider.bind_session("assistant:" + session_id if session_id else "connection-check:" + str(self.root.resolve()))
            return provider

    def test(self) -> dict[str, Any]:
        with self.lock:
            settings, _ = self._settings()
            try:
                self.provider().complete(
                    [{"role": "user", "content": "Reply OK."}], [], timeout_s=20, max_tokens=8
                )
            except ProviderError as error:
                settings.update(status="error", detail=f"{error.code}: {provider_detail(error, 20)}", tested_at=now())
            except ValueError:
                settings.update(status="error", detail="CONFIGURATION: Connect a valid model endpoint / 请配置有效模型端点", tested_at=now())
            else:
                settings.update(status="ok", detail=None, tested_at=now())
            self._write(settings)
            return self.get()
