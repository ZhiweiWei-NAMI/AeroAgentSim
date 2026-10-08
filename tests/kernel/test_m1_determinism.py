"""M1 determinism: literal canonical RNG fixtures, order/hash-seed independence.

Expected values are authored literals: the canonical seed byte string, its
SHA-256, the derived integer seed, first stream outputs, and the full toy
journal digest. Production ``canonical_json`` is never used to compute an
expectation; it only appears inside the kernel under test.

"""

from __future__ import annotations

import hashlib
import itertools
import os
import subprocess
import unittest.mock
from pathlib import Path

import pytest

from aerokernel import BindingManifest, Kernel
from aerokernel.rng import RNGStreams, derive_seed
from examples.two_engine_toy import MS, make_toy


def _find_repo() -> Path:
    """Walk up to the repository root holding the toy example (staging-safe)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "examples" / "two_engine_toy.py").is_file():
            return parent
    raise RuntimeError("repository root with examples/two_engine_toy.py not found")


REPO = _find_repo()
PYTHON = REPO / ".venv" / "bin" / "python"

# Literal canonical fixture: sha256(canonical_json([domain, 42, "toy", "p", "s"])[:-1]).
DOMAIN_SEED_42 = b'["aerokernel.rng/v1",42,"toy","p","s"]'
DOMAIN_SEED_42_SHA256 = (
    "1a7cfbd14afc6c22c06785f9947359ddbc66a6e2919777e5f78705112619659f"
)
DOMAIN_SEED_42_INT = (
    11980961080056293758601568902116667185823202855292399164253450250818135090591
)
# RNGStreams(42, "toy", "p", ("a", "b", "c")) seeds, authored as literals.
LITERAL_STREAM_SEEDS = {
    "a": 56962682778968447911473643639793967651632480027495150789375570742145031456723,
    "b": 93521091231102872448556674860258828431639688937509904283884096260135522122125,
    "c": 75200938285071381658843621219191308944516695784009333536828249217419153815577,
}
# First three uniform draws and first two 64-bit draws of each declared stream.
LITERAL_FIRST_RANDOM = {
    "a": [0.5838072053752666, 0.602983780387548, 0.954950396225883],
    "b": [0.9268398091130541, 0.20676759722974036, 0.198042253289752],
    "c": [0.36800616663503094, 0.6424896080195471, 0.08263266305219108],
}
LITERAL_FIRST_GETRENDBITS64 = {
    "a": [5514745618170961332, 13268169997033735704],
    "b": [17167956920280897917, 6674005087104440968],
    "c": [11480091421325811636, 12564448299957212986],
}
# sha256 of the complete committed toy journal (make_toy(); start; run_until(13ms)).
TOY_JOURNAL_SHA256 = "415bd04e584b14b1da01853f14ad2c43551a5583438a01e7b6495294d45ef164"

CHILD_JOURNAL_SHA = (
    "import hashlib\n"
    "from examples.two_engine_toy import MS, make_toy\n"
    "kernel = make_toy()\n"
    "kernel.start()\n"
    "kernel.run_until(13 * MS)\n"
    "print(hashlib.sha256(kernel.journal.bytes).hexdigest())\n"
)


def test_canonical_seed_bytes_and_digest_are_literal() -> None:
    """The derivation domain encoding is pinned byte for byte, not recomputed."""
    assert derive_seed(42, "toy", "p", "s") == DOMAIN_SEED_42_INT
    assert hashlib.sha256(DOMAIN_SEED_42).digest() == int(DOMAIN_SEED_42_INT).to_bytes(
        32, "big"
    )
    assert hashlib.sha256(DOMAIN_SEED_42).hexdigest() == DOMAIN_SEED_42_SHA256
    # The domain string is 38 canonical bytes without the trailing newline.
    assert len(DOMAIN_SEED_42) == 38 and DOMAIN_SEED_42.endswith(b"]")
    assert hashlib.sha256(DOMAIN_SEED_42 + b"\n").hexdigest() != DOMAIN_SEED_42_SHA256
    # A different root seed or name slot changes the domain, hence the seed.
    assert derive_seed(43, "toy", "p", "s") != DOMAIN_SEED_42_INT
    assert derive_seed(42, "toy", "p", "t") != DOMAIN_SEED_42_INT


def test_rng_streams_match_literal_first_outputs() -> None:
    """Declared streams are exclusive, literal, and selection-order independent."""
    assert RNGStreams(42, "toy", "p", ("a", "b", "c")).seeds == LITERAL_STREAM_SEEDS
    # Declaration order must not reshape seeds or any stream's sequence.
    assert RNGStreams(42, "toy", "p", ("c", "a", "b")).seeds == LITERAL_STREAM_SEEDS
    for name in ("a", "b", "c"):
        forward = RNGStreams(42, "toy", "p", ("a", "b", "c")).stream(name)
        backward = RNGStreams(42, "toy", "p", ("c", "b", "a")).stream(name)
        assert [forward.random() for _ in range(3)] == LITERAL_FIRST_RANDOM[name]
        assert [backward.random() for _ in range(2)] == LITERAL_FIRST_RANDOM[name][:2]
        assert [forward.getrandbits(64) for _ in range(2)] == (
            LITERAL_FIRST_GETRENDBITS64[name]
        )
        assert forward.random() != backward.random()  # interleaved draws diverge


def test_derived_seeds_are_pairwise_distinct() -> None:
    """Stream names select disjoint seeds even under a shared owner prefix."""
    seeds = [derive_seed(42, "toy", "p", name) for name in ("a", "b", "c")]
    assert len(set(seeds)) == len(seeds)
    assert seeds == [LITERAL_STREAM_SEEDS[name] for name in ("a", "b", "c")]


@pytest.mark.parametrize("hash_seed", ["0", "1", "71"])
def test_toy_journal_sha256_is_hash_seed_independent(hash_seed: str) -> None:
    """Full toy journal is byte-identical across PYTHONHASHSEED children."""
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [str(PYTHON), "-c", CHILD_JOURNAL_SHA],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    digest = completed.stdout.strip()
    assert len(digest) == 64
    assert digest == TOY_JOURNAL_SHA256


@pytest.mark.parametrize(
    "order",
    [(0, 1, 2, 3), (3, 2, 1, 0), (2, 0, 3, 1), (1, 3, 0, 2)],
)
def test_engine_registration_order_does_not_change_journal(
    order: tuple[int, ...],
) -> None:
    """Registration permutations produce the same committed journal bytes."""
    original = Kernel.bind

    def shuffled(self, registry, manifest, engines):
        return original(self, registry, manifest, tuple(engines[i] for i in order))

    with unittest.mock.patch.object(Kernel, "bind", shuffled):
        kernel = make_toy()
    kernel.start()
    kernel.run_until(13 * MS)
    digest = hashlib.sha256(kernel.journal.bytes).hexdigest()
    assert digest == TOY_JOURNAL_SHA256
    kernel.close()


@pytest.mark.parametrize(
    "declared",
    [
        (("c", "b"), ("b", "a")),
        (("b", "a"), ("c", "b")),
        (("a", "c"), ("a", "b")),
        (("b", "c"), ("a", "b")),
    ],
)
def test_manifest_cohorts_canonicalize_independently_of_declaration(declared):
    """Overlapping cohort declarations collapse to one canonical selector."""
    canonical = (("a", "b", "c"),)
    assert BindingManifest("run", "epoch", cohorts=declared).cohorts == canonical
    assert BindingManifest("run", "epoch", cohorts=(("a", "b"),)).cohorts == (
        ("a", "b"),
    )
    assert BindingManifest("run", "epoch", cohorts=(("b", "a"),)).cohorts == (
        ("a", "b"),
    )


def test_all_registration_permutations_share_one_journal_digest() -> None:
    """Every 4-engine registration permutation is journal-identical (in process)."""
    digests = set()
    for order in itertools.permutations(range(4)):
        original = Kernel.bind

        def shuffled(self, registry, manifest, engines, order=order, original=original):
            return original(self, registry, manifest, tuple(engines[i] for i in order))

        with unittest.mock.patch.object(Kernel, "bind", shuffled):
            kernel = make_toy()
        kernel.start()
        kernel.run_until(13 * MS)
        digests.add(hashlib.sha256(kernel.journal.bytes).hexdigest())
        kernel.close()
    assert digests == {TOY_JOURNAL_SHA256}
