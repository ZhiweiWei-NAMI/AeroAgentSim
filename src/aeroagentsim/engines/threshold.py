"""Retired module: thin alias for the pinned sampled threshold interpreter in behaviours.compat."""

from __future__ import annotations

from aeroagentsim.engines.behaviour import build_legacy_threshold

Threshold = build_legacy_threshold
build = build_legacy_threshold
