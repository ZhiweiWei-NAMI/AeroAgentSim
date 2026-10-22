"""The documented product flows exist in the shipped CLI, router and catalogue."""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Any

import pytest

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.scenarios import TEMPLATES
from aeroagentsim.services import cli
from aeroagentsim.services.app import create_app

ROOT = Path(__file__).resolve().parents[2]
CLI_DOC = ROOT / "docs/reference/cli.md"
HTTP_DOC = ROOT / "docs/reference/http-api.md"

ROUTE_SECTIONS = {
    "Plugin catalog",
    "Run execution API",
    "Templates and comparisons",
    "Live ingress and watermarks",
    "Artifacts and agents",
    "Studio (authoring) API",
}

MANIFEST_FIELDS = {
    "format",
    "id",
    "title",
    "summary",
    "category",
    "difficulty",
    "duration_s",
    "scenario",
    "observe",
    "try",
    "knobs",
    "kpis",
}
CATEGORIES = {
    "logistics",
    "emergency",
    "inspection",
    "agriculture",
    "mobility",
    "security",
    "mixed",
}
DIFFICULTIES = {"beginner", "intermediate", "advanced"}


def _section_name(heading: str) -> str:
    text = heading.lstrip("#").strip()
    return text.split(" — ", 1)[0].strip()


def _route_rows(text: str) -> list[tuple[str, set[str]]]:
    """Route paths and methods from the API tables in http-api.md."""
    section = ""
    rows: list[tuple[str, set[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            section = _section_name(line)
            continue
        if section not in ROUTE_SECTIONS or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        methods = set(re.findall(r"\b(?:GET|POST|PUT|PATCH|DELETE)\b", cells[1]))
        for token in re.findall(r"`([^`]+)`", cells[0]):
            token = token.strip()
            if not token.startswith("/"):
                continue
            path = token.split("?", 1)[0]
            if section == "Studio (authoring) API" and not path.startswith("/v1/"):
                path = "/v1/studio" + path
            rows.append((path, methods))
    return rows


def _normalise_route(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.split("?", 1)[0]).rstrip("/") or "/"


def _walk_routes(routes: Any, prefix: str = "") -> Any:
    """Flatten routes, including routers that newer FastAPI keeps nested."""
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            context = getattr(route, "include_context", None)
            inner = prefix + (getattr(context, "prefix", "") or "")
            yield from _walk_routes(nested.routes, inner)
        else:
            yield prefix + (getattr(route, "path", "") or ""), getattr(route, "methods", None)


def _available_routes(app: Any) -> dict[str, set[str]]:
    rows: dict[str, set[str]] = {}
    for path, methods in _walk_routes(app.routes):
        if path.startswith("/v1/") and methods:
            key = _normalise_route(path)
            rows.setdefault(key, set()).update(
                method.upper() for method in methods
            )
    return rows


def _cli_parser(monkeypatch: pytest.MonkeyPatch) -> argparse.ArgumentParser:
    """Build the real parser, then stop before dispatching a command."""
    captured: dict[str, argparse.ArgumentParser] = {}

    class _Captured(Exception):
        pass

    def capture(parser: argparse.ArgumentParser, args: Any = None, namespace: Any = None) -> None:
        captured["parser"] = parser
        raise _Captured

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(_Captured):
        cli.main()
    return captured["parser"]


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _documented_cli_commands(text: str) -> set[str]:
    return set(re.findall(r"^## aeroagentsim ([a-z][a-z-]*)$", text, re.MULTILINE))


def _documented_cli_flags(text: str) -> dict[str, set[str]]:
    section = ""
    flags: dict[str, set[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            match = re.fullmatch(r"## aeroagentsim ([a-z][a-z-]*)", line)
            section = match.group(1) if match else ""
            continue
        if not section or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells:
            continue
        for flag in re.findall(r"`(--[a-z][a-z-]*)`", cells[0]):
            flags.setdefault(section, set()).add(flag)
    return flags


def _documented_asset_flags(text: str) -> dict[str, set[str]]:
    section = ""
    rows: dict[str, set[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section != "aeroagentsim assets" or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("`"):
            continue
        name = cells[0].strip("`").strip()
        if name not in {"list", "fetch", "install", "remove"}:
            continue
        rows.setdefault(name, set()).update(re.findall(r"`(--[a-z][a-z-]*)`", cells[1]))
    return rows


def test_documented_cli_subcommands_and_flags_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    text = CLI_DOC.read_text(encoding="utf-8")
    parser = _cli_parser(monkeypatch)
    commands = _subcommands(parser)

    documented = _documented_cli_commands(text)
    assert documented, "cli.md must document at least one `aeroagentsim <command>` heading"
    assert documented <= set(commands), (
        f"cli.md documents unknown subcommands: {sorted(documented - set(commands))}"
    )
    assert set(commands) <= documented, (
        f"cli.md is missing actual subcommands: {sorted(set(commands) - documented)}"
    )

    assets = _subcommands(commands["assets"])
    documented_assets = set(re.findall(r"aeroagentsim assets ([a-z]+)", text))
    assert documented_assets == set(assets), (
        f"cli.md asset subcommands {sorted(documented_assets)} do not match {sorted(assets)}"
    )
    for name, flags in _documented_asset_flags(text).items():
        available = set(assets[name]._option_string_actions)
        assert flags <= available, (name, sorted(flags - available))

    for command, flags in _documented_cli_flags(text).items():
        available = set(commands[command]._option_string_actions)
        assert flags <= available, (command, sorted(flags - available))


def test_documented_http_routes_exist(tmp_path: Path) -> None:
    app = create_app(tmp_path / "runs", studio_root=tmp_path / "studio")
    available = _available_routes(app)
    missing: list[tuple[str, list[str]]] = []
    for path, methods in _route_rows(HTTP_DOC.read_text(encoding="utf-8")):
        wanted = _normalise_route(path)
        if not methods <= available.get(wanted, set()):
            missing.append((path, sorted(methods)))
    assert not missing, f"http-api.md documents routes not present in the app: {missing}"


def test_builtin_catalogue_criteria_hold_without_fixed_count() -> None:
    registry = TemplateRegistry()
    rows = registry.list()
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = row["id"]
        assert identifier not in by_id, f"duplicate catalogue id {identifier!r}"
        by_id[identifier] = row

    assert set(TEMPLATES) <= set(by_id), (
        f"builtin templates missing from the catalogue: {sorted(set(TEMPLATES) - set(by_id))}"
    )

    for identifier in TEMPLATES:
        row = by_id[identifier]
        assert MANIFEST_FIELDS <= set(row), (identifier, sorted(MANIFEST_FIELDS - set(row)))
        assert row["format"] == "aeroagentsim.template/v1", identifier
        assert row["id"] == identifier
        assert isinstance(row["title"], str) and row["title"].strip(), identifier
        assert isinstance(row["summary"], str) and row["summary"].strip(), identifier
        assert row["category"] in CATEGORIES, identifier
        assert row["difficulty"] in DIFFICULTIES, identifier
        assert (
            type(row["duration_s"]) in (int, float)
            and math.isfinite(row["duration_s"])
            and row["duration_s"] > 0
        ), identifier
        assert isinstance(row["scenario"], str) and row["scenario"].strip(), identifier
        for key in ("observe", "try"):
            assert isinstance(row[key], list) and 2 <= len(row[key]) <= 4, identifier
            assert all(isinstance(item, str) and item.strip() for item in row[key])
        assert isinstance(row["knobs"], list), identifier
        for knob in row["knobs"]:
            assert {"id", "label", "type", "default"} <= set(knob), (identifier, knob)
        assert isinstance(row["kpis"], list), identifier
        assert all(isinstance(item, str) and item for item in row["kpis"]), identifier

        # Applying the declared defaults must resolve the scenario resource and
        # pass configuration loading; discovery alone is not enough.
        prepared = registry.apply(identifier, {})
        assert prepared.metadata["id"] == identifier
        assert isinstance(prepared.document, dict) and prepared.document
