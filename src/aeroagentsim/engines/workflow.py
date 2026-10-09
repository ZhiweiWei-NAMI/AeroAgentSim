"""Retired module: thin alias for the pinned workflow interpreter in behaviours.compat."""

from __future__ import annotations

from aeroagentsim.engines.behaviour import build_legacy_workflow

Workflow = build_legacy_workflow
build = build_legacy_workflow
