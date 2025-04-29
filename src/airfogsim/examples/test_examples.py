#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AirFogSim示例程序测试脚本

此脚本用于自动测试AirFogSim示例目录中的各个示例程序，
可以选择性地测试特定示例或运行所有示例。

使用方法:
  python test_examples.py          # 测试所有示例
  python test_examples.py --list   # 列出所有可用测试
  python test_examples.py --run example_workflow_diagram example_trigger_basic  # 测试指定示例
"""

import os
import sys
import subprocess
import argparse
import time
from typing import List, Dict, Tuple
from airfogsim.utils.logging_config import get_logger

# 配置日志
logger = get_logger("TestExamples")

# 示例配置：
# 'requires': 列出特殊要求（API密钥，特定软件等）
# 'timeout': 运行超时时间（秒）
# 'expected_exit_code': 预期的退出代码
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
        "timeout": 30,
        "expected_exit_code": 0
    },
    "example_weather_provider.py": {
        "description": "天气数据提供者示例，集成到仿真环境",
        "requires": ["OPENWEATHERMAP_API_KEY"],
        "timeout": 30,
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
        "expected_exit_code": 1  # 预期失败，因为默认没有配置SUMO
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
    }
}

def check_requirements(example: str) -> Tuple[bool, str]:
    """检查运行示例所需的特殊要求"""
    requirements = EXAMPLES[example].get("requires", [])
    missing = []

    for req in requirements:
        if req == "OPENWEATHERMAP_API_KEY":
            if not os.environ.get("OPENWEATHERMAP_API_KEY"):
                missing.append("OpenWeatherMap API密钥未设置")
        elif req == "SUMO":
            try:
                result = subprocess.run(["sumo", "--version"],
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       text=True)
                if result.returncode != 0:
                    missing.append("SUMO未安装或无法运行")
            except FileNotFoundError:
                missing.append("SUMO未安装")

    if missing:
        return False, "，".join(missing)
    return True, ""

def run_example(example: str) -> Tuple[bool, str, int]:
    """运行单个示例并返回结果"""
    # 首先检查要求
    req_met, reason = check_requirements(example)
    if not req_met:
        return False, f"跳过测试 {example}：{reason}", -1

    # 构建命令
    cmd = [sys.executable, example]
    timeout = EXAMPLES[example].get("timeout", 30)
    expected_code = EXAMPLES[example].get("expected_exit_code", 0)

    # 运行命令
    logger.info(f"开始运行 {example}，超时设置: {timeout}秒")
    start_time = time.time()

    try:
        process = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
        elapsed = time.time() - start_time

        # 检查返回代码
        if process.returncode == expected_code:
            return True, f"通过 (用时 {elapsed:.2f}秒，返回码: {process.returncode})", process.returncode
        else:
            return False, f"失败 (用时 {elapsed:.2f}秒，返回码: {process.returncode}，期望: {expected_code})\n错误: {process.stderr}", process.returncode

    except subprocess.TimeoutExpired:
        return False, f"超时 (超过 {timeout}秒)", -1
    except Exception as e:
        return False, f"错误: {str(e)}", -1

def list_examples():
    """列出所有可用的示例及其描述"""
    logger.info("可用的示例程序:")
    logger.info("-" * 70)
    for name, info in EXAMPLES.items():
        reqs = ""
        if info.get("requires"):
            reqs = f" [需要: {', '.join(info['requires'])}]"
        logger.info(f"{name:30} - {info['description']}{reqs}")
    logger.info("-" * 70)

def run_all_examples() -> Dict[str, Tuple[bool, str, int]]:
    """运行所有示例并返回结果字典"""
    results = {}
    for example in EXAMPLES.keys():
        success, message, code = run_example(example)
        results[example] = (success, message, code)
        status = "✅ 通过" if success else "❌ 失败"
        logger.info(f"{status} - {example}: {message}")
    return results

def main():
    """主函数，处理命令行参数并运行测试"""
    parser = argparse.ArgumentParser(description="AirFogSim示例程序测试工具")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="列出所有可用的示例")
    group.add_argument("--run", nargs="+", help="运行指定的示例（不带.py后缀）")

    args = parser.parse_args()

    # 切换到脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    if args.list:
        list_examples()
        return

    if args.run:
        # 运行指定的示例
        results = {}
        for example_name in args.run:
            # 添加.py后缀如果没有
            if not example_name.endswith(".py"):
                example_name += ".py"

            if example_name not in EXAMPLES:
                logger.error(f"未知示例: {example_name}")
                continue

            success, message, code = run_example(example_name)
            results[example_name] = (success, message, code)
            status = "✅ 通过" if success else "❌ 失败"
            logger.info(f"{status} - {example_name}: {message}")
    else:
        # 运行所有示例
        logger.info("开始测试所有示例...")
        results = run_all_examples()

    # 输出总结
    total = len(results)
    passed = sum(1 for success, _, _ in results.values() if success)

    logger.info("=" * 50)
    logger.info(f"测试总结: {passed}/{total} 通过")

    if passed < total:
        logger.info("失败的测试:")
        for name, (success, message, _) in results.items():
            if not success:
                logger.info(f"  - {name}: {message}")

if __name__ == "__main__":
    main()