"""
Comprehensive integration tests for AirFogSim examples.

This module provides complete pytest-based testing for all AirFogSim examples,
replacing the standalone test_examples.py script in the examples directory.
"""

import pytest
import sys
import subprocess
import os
import time
from pathlib import Path
from typing import Dict, Tuple


# Example configurations migrated from examples/test_examples.py
EXAMPLES = {
    "example_trigger_basic.py": {
        "description": "触发器系统示例，展示如何使用不同类型的触发器",
        "requires": [],
        "timeout": 20,
        "expected_exit_code": 0
    },
    "example_workflow_diagram.py": {
        "description": "工作流图表生成示例，生成PlantUML和Mermaid格式图表",
        "requires": [],
        "timeout": 10,
        "expected_exit_code": 0
    },
    "test_workflow_diagram.py": {
        "description": "工作流图表生成辅助工具，输出到控制台",
        "requires": [],
        "timeout": 10,
        "expected_exit_code": 0
    },
    "example_weather_openweathermap.py": {
        "description": "OpenWeatherMap API适配器示例",
        "requires": ["OPENWEATHERMAP_API_KEY"],
        "timeout": 60,
        "expected_exit_code": 0
    },
    "example_weather_provider.py": {
        "description": "天气数据提供者示例，集成到仿真环境",
        "requires": ["OPENWEATHERMAP_API_KEY"],
        "timeout": 60,
        "expected_exit_code": 0
    },
    "example_workflow_image_processing.py": {
        "description": "图像感知处理工作流示例",
        "requires": [],
        "timeout": 30,
        "expected_exit_code": 0
    },
    "example_workflow_contract.py": {
        "description": "多任务合约示例",
        "requires": [],
        "timeout": 20,
        "expected_exit_code": 0
    },
    "example_workflow_inspection.py": {
        "description": "无人机巡检工作流示例",
        "requires": [],
        "timeout": 60,
        "expected_exit_code": 0
    },
    "example_workflow_logistics.py": {
        "description": "物流工作流示例，较为复杂，耗时较长",
        "requires": [],
        "timeout": 180,
        "expected_exit_code": 0
    },
    "example_simulation_traffic.py": {
        "description": "SUMO交通仿真集成示例（需要安装SUMO）",
        "requires": ["SUMO"],
        "timeout": 60,
        "expected_exit_code": 0
    },
    "example_task_priority.py": {
        "description": "任务优先级和抢占机制示例",
        "requires": [],
        "timeout": 20,
        "expected_exit_code": 0
    },
    "example_task_duplicate_check.py": {
        "description": "任务重复检查示例",
        "requires": [],
        "timeout": 20,
        "expected_exit_code": 0
    },
    "example_task_queue_sort.py": {
        "description": "任务队列排序示例",
        "requires": [],
        "timeout": 20,
        "expected_exit_code": 0
    },
    "example_workflow_priority.py": {
        "description": "工作流优先级示例",
        "requires": [],
        "timeout": 20,
        "expected_exit_code": 0
    },
    "example_benchmark_multi_workflow.py": {
        "description": "JOSS论文多工作流基准测试示例",
        "requires": [],
        "timeout": 120,
        "expected_exit_code": 0
    },
    "example_frequency_signal_integration.py": {
        "description": "频率信号集成示例，展示频率管理和信号传播",
        "requires": [],
        "timeout": 60,
        "expected_exit_code": 0
    },
    "example_object_sensor.py": {
        "description": "物体传感器示例，展示如何使用物体传感器组件",
        "requires": [],
        "timeout": 30,
        "expected_exit_code": 0
    },
    "example_signal_sensing.py": {
        "description": "信号感知示例，展示电磁信号感知功能",
        "requires": [],
        "timeout": 60,
        "expected_exit_code": 0
    },
    "example_weather_at_position.py": {
        "description": "位置天气数据示例，展示如何获取特定位置的天气信息",
        "requires": ["OPENWEATHERMAP_API_KEY"],
        "timeout": 60,
        "expected_exit_code": 0
    }
}


class TestExamplesIntegration:
    """Comprehensive test cases for all example programs."""

    @pytest.fixture(scope="class")
    def examples_dir(self):
        """Get the examples directory path."""
        src_path = Path(__file__).parent.parent.parent / "src" / "aeroagentsim" / "examples"
        return src_path

    def test_examples_directory_exists(self, examples_dir):
        """Test that examples directory exists."""
        assert examples_dir.exists()
        assert examples_dir.is_dir()

    def _check_requirements(self, example: str) -> Tuple[bool, str]:
        """Check if requirements for running an example are met."""
        requirements = EXAMPLES[example].get("requires", [])
        missing = []

        for req in requirements:
            if req == "OPENWEATHERMAP_API_KEY":
                if not os.environ.get("OPENWEATHERMAP_API_KEY"):
                    missing.append("OpenWeatherMap API key not set")
            elif req == "SUMO":
                try:
                    result = subprocess.run(["sumo", "--version"],
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE,
                                          text=True)
                    if result.returncode != 0:
                        missing.append("SUMO not installed or cannot run")
                except FileNotFoundError:
                    missing.append("SUMO not installed")

        if missing:
            return False, ", ".join(missing)
        return True, ""

    def _run_example(self, examples_dir: Path, example: str) -> Tuple[bool, str, int]:
        """Run a single example and return results."""
        # Check requirements first
        req_met, reason = self._check_requirements(example)
        if not req_met:
            return False, f"Skipped: {reason}", -1

        # Build command
        example_file = examples_dir / example
        if not example_file.exists():
            return False, f"Example file not found: {example}", -1

        cmd = [sys.executable, str(example_file)]
        timeout = EXAMPLES[example].get("timeout", 30)
        expected_code = EXAMPLES[example].get("expected_exit_code", 0)

        # Run command
        start_time = time.time()

        try:
            process = subprocess.run(
                cmd,
                cwd=str(examples_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout
            )
            elapsed = time.time() - start_time

            # Check return code
            if process.returncode == expected_code:
                return True, f"Passed (time: {elapsed:.2f}s, code: {process.returncode})", process.returncode
            else:
                return False, f"Failed (time: {elapsed:.2f}s, code: {process.returncode}, expected: {expected_code})", process.returncode

        except subprocess.TimeoutExpired:
            return False, f"Timeout (exceeded {timeout}s)", -1
        except Exception as e:
            return False, f"Error: {str(e)}", -1

    # Individual test methods for each example
    @pytest.mark.parametrize("example_name", [
        "example_trigger_basic.py",
        "example_workflow_diagram.py",
        "test_workflow_diagram.py"
    ])
    def test_basic_examples(self, examples_dir, example_name):
        """Test basic examples that should always work."""
        success, message, code = self._run_example(examples_dir, example_name)
        assert success, f"{example_name} failed: {message}"

    @pytest.mark.parametrize("example_name", [
        "example_workflow_image_processing.py",
        "example_workflow_contract.py",
        "example_task_priority.py",
        "example_task_duplicate_check.py",
        "example_task_queue_sort.py",
        "example_workflow_priority.py"
    ])
    def test_workflow_and_task_examples(self, examples_dir, example_name):
        """Test workflow and task management examples."""
        success, message, code = self._run_example(examples_dir, example_name)
        assert success, f"{example_name} failed: {message}"

    @pytest.mark.parametrize("example_name", [
        "example_object_sensor.py",
        "example_signal_sensing.py",
        "example_frequency_signal_integration.py"
    ])
    def test_sensor_and_signal_examples(self, examples_dir, example_name):
        """Test sensor and signal processing examples."""
        success, message, code = self._run_example(examples_dir, example_name)
        assert success, f"{example_name} failed: {message}"

    @pytest.mark.slow
    def test_workflow_inspection_example(self, examples_dir):
        """Test the inspection workflow example (marked as slow)."""
        success, message, code = self._run_example(examples_dir, "example_workflow_inspection.py")
        assert success, f"Inspection workflow failed: {message}"

    @pytest.mark.slow
    def test_workflow_logistics_example(self, examples_dir):
        """Test the logistics workflow example (marked as slow)."""
        success, message, code = self._run_example(examples_dir, "example_workflow_logistics.py")
        assert success, f"Logistics workflow failed: {message}"

    @pytest.mark.slow
    def test_benchmark_multi_workflow_example(self, examples_dir):
        """Test the JOSS benchmark example (marked as slow)."""
        success, message, code = self._run_example(examples_dir, "example_benchmark_multi_workflow.py")
        assert success, f"Benchmark multi-workflow failed: {message}"

    def test_weather_examples_with_api_key(self, examples_dir):
        """Test weather examples if API key is available."""
        weather_examples = [
            "example_weather_openweathermap.py",
            "example_weather_provider.py",
            "example_weather_at_position.py"
        ]

        for example_name in weather_examples:
            success, message, code = self._run_example(examples_dir, example_name)

            if "API key not set" in message:
                pytest.skip(f"Skipping {example_name}: {message}")
            else:
                assert success, f"{example_name} failed: {message}"

    def test_sumo_traffic_example(self, examples_dir):
        """Test SUMO traffic example (expected to fail if SUMO not installed)."""
        success, message, code = self._run_example(examples_dir, "example_simulation_traffic.py")

        if "SUMO not installed" in message:
            pytest.skip(f"Skipping SUMO example: {message}")
        else:
            # This example is expected to fail with exit code 1 if SUMO is not properly configured
            # but installed, so we check for the expected exit code
            expected_code = EXAMPLES["example_simulation_traffic.py"]["expected_exit_code"]
            assert code == expected_code, f"SUMO example returned unexpected code: {code}, expected: {expected_code}"

    def test_all_examples_exist(self, examples_dir):
        """Test that all configured examples actually exist as files."""
        missing_examples = []
        for example_name in EXAMPLES.keys():
            example_file = examples_dir / example_name
            if not example_file.exists():
                missing_examples.append(example_name)

        assert not missing_examples, f"Missing example files: {missing_examples}"

    def test_examples_configuration_completeness(self):
        """Test that all examples have proper configuration."""
        for example_name, config in EXAMPLES.items():
            assert "description" in config, f"{example_name} missing description"
            assert "timeout" in config, f"{example_name} missing timeout"
            assert "expected_exit_code" in config, f"{example_name} missing expected_exit_code"
            assert isinstance(config["requires"], list), f"{example_name} requires should be a list"
