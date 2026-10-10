"""Focused legacy regression tests; no SUMO, Torch, or package-level import."""

import ast
import configparser
import contextlib
import hashlib
import importlib
import io
import json
from pathlib import Path
import re
import sys
import types
import unittest
import warnings


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
NAMESPACE = "_preserved_airfogsim_test"


def load_legacy_module(name):
    # Real source modules retain their relative imports. Avoid executing the
    # legacy package __init__, which imports all simulation dependencies.
    for suffix in ("", ".entities", ".manager", ".scheduler"):
        package_name = NAMESPACE + suffix
        if package_name not in sys.modules:
            package = types.ModuleType(package_name)
            package.__path__ = [str(ROOT / "airfogsim" / suffix.lstrip("."))]
            sys.modules[package_name] = package
    return importlib.import_module(f"{NAMESPACE}.{name}")


class BlockchainRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Manager = load_legacy_module("manager.block_manager").BlockchainManager
        cls.Scheduler = load_legacy_module("scheduler.blockchain_sched").BlockchainScheduler
        cls.Constants = load_legacy_module("enum_const").EnumerateConstants

    def setUp(self):
        self.manager = self.Manager({})
        self.env = types.SimpleNamespace(blockchain_manager=self.manager)

    def add_batch(self, count=20):
        for index in range(count):
            self.manager.addTransaction(f"transaction-{index}")
        blocks = list(self.manager.generateToMineBlocks(1))
        for block in blocks:
            self.manager.addBlock(block, "test-miner")
        return blocks

    def test_two_blocks_from_one_batch_have_valid_final_hashes(self):
        blocks = self.add_batch()
        self.assertEqual(len(blocks), 2)
        self.assertTrue(self.manager.isValidChain())
        self.assertEqual(blocks[1].block_previous_hash, blocks[0].block_hash)
        self.assertEqual(blocks[1].block_hash, blocks[1].get_hash())
        self.assertEqual(self.manager.getTransactionsPerSecond(), 20.0)

    def test_tampered_transaction_is_detected(self):
        self.add_batch()
        self.manager.blockchain.chain[1].block_transactions.append("tampered")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(self.manager.isValidChain())

    def test_consensus_enum_is_accepted_and_example_uses_it(self):
        self.assertFalse(self.Scheduler.setBlockchainConsensus(self.env, "PoS"))
        self.assertTrue(self.Scheduler.setBlockchainConsensus(
            self.env, self.Constants.CONSENSUS_POS))
        tree = ast.parse((ROOT / "examples/example03_manage_Blockchain_example.py").read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "setBlockchainConsensus"]
        self.assertEqual(len(calls), 1)
        enum = calls[0].args[1]
        self.assertIsInstance(enum, ast.Attribute)
        self.assertEqual(enum.attr, "CONSENSUS_POS")
        self.assertEqual(enum.value.id, "EnumerateConstants")

    def test_example_reports_bytes_without_changing_scheduler_count_api(self):
        block = self.add_batch()[0]
        self.assertEqual(self.Scheduler.getBlockSizeByIndex(self.env, 1), 10)
        self.assertGreater(block.getBlockSize(), 10)
        self.assertEqual(self.manager.getBlockchainSize(),
                         sum(item.getBlockSize() for item in self.manager.blockchain.chain))
        tree = ast.parse((ROOT / "examples/example03_manage_Blockchain_example.py").read_text())
        values = [node.value for node in ast.walk(tree)
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "block_size"
                          for target in node.targets)
                  and isinstance(node.value, ast.Call)]
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].func.attr, "getBlockSize")


class PreservationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "SOURCE_MANIFEST.json").read_text())

    def test_all_original_python_sources_compile_and_parse_as_python310(self):
        sources = [row for row in self.manifest["files"] if row["path"].endswith(".py")]
        self.assertEqual(len(sources), 279)
        for row in sources:
            with self.subTest(path=row["path"]):
                source = (ROOT / row["destination"]).read_bytes()
                with warnings.catch_warnings():
                    warnings.simplefilter("error", SyntaxWarning)
                    compile(source, row["destination"], "exec")
                ast.parse(source, filename=row["destination"], feature_version=(3, 10))

    def test_original_blobs_preserved_except_three_documented_corrections(self):
        changed = []
        for row in self.manifest["files"]:
            with self.subTest(path=row["path"]):
                data = (ROOT / row["destination"]).read_bytes()
                if "correction" in row:
                    changed.append(row["path"])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), row["integrated_sha256"])
                else:
                    blob = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
                    self.assertEqual(hashlib.sha1(blob).hexdigest(), row["git_blob"])
        self.assertEqual(set(changed), {
            "airfogsim/manager/block_manager.py",
            "examples/example03_manage_Blockchain_example.py",
            "testAPI.py",
        })
        self.assertEqual(len(self.manifest["excluded"]), 85)
        for row in self.manifest["excluded"]:
            self.assertTrue(row["reason"])
            self.assertFalse((ROOT / row["path"]).exists(), row["path"])

    def test_api_sketch_allocation_has_separate_list_argument(self):
        tree = ast.parse((ROOT / "testAPI.py").read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "setCPUByNodeId"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0].args), 3)
        self.assertEqual(ast.literal_eval(calls[0].args[1]), "Fog-V_1")
        self.assertEqual(ast.literal_eval(calls[0].args[2]), [0.5, 0.5])

    def test_default_root_test_and_package_discovery_exclude_legacy(self):
        config = configparser.ConfigParser()
        config.read(REPOSITORY / "pytest.ini")
        self.assertEqual(config["pytest"]["testpaths"].split(), ["tests"])
        project = (REPOSITORY / "pyproject.toml").read_text()
        section = project.split("[tool.setuptools.packages.find]", 1)[1].split("\n[", 1)[0]
        where = re.search(r"^where\s*=\s*(\[[^\n]+\])", section, re.MULTILINE)
        self.assertIsNotNone(where)
        self.assertEqual(ast.literal_eval(where.group(1)), ["src"])
        self.assertNotIn("legacy", section)


if __name__ == "__main__":
    unittest.main()
