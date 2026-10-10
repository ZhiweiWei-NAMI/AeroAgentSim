"""Caller-side materialization of public authored fixtures into an authorized reader."""

import copy
import json
from pathlib import Path

import pytest

from aeroagentsim.integration import MappingArtifactReader, ReadOnlyReplay, canonical_bytes


@pytest.fixture
def bundle():
    return json.loads((Path(__file__).parent / "fixtures" / "synthetic_enu.json").read_bytes())


def replay_from(bundle):
    return ReadOnlyReplay(
        canonical_bytes(bundle["manifest"]),
        MappingArtifactReader({key: canonical_bytes(value) for key, value in bundle["artifacts"].items()}),
    )


@pytest.fixture
def replay(bundle):
    return replay_from(copy.deepcopy(bundle))
