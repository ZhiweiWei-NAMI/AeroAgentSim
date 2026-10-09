"""The general kernel does not carry retired ontology audit tooling."""

from pathlib import Path


def test_removed_aerograph_audit_tooling_is_absent() -> None:
    root = Path(__file__).resolve().parents[2]
    for retired in (
        "tools/aerograph_audit",
        "tests/test_aerograph_audit.py",
        "docs/audit",
    ):
        assert not (root / retired).exists()
    # Keep the development namespace used alongside platform tools.
    assert (root / "tools/__init__.py").is_file()
