#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AirFogSim 包验证测试脚本

该脚本用于验证 AirFogSim 包的基本功能，包括：
1. 导入核心类
2. 运行示例测试

使用方法：
    python test_package.py

作者: Zhiwei Wei
邮箱: 2311769@tongji.edu.cn
"""

import sys
import traceback
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def test_core_imports():
    """测试核心类导入"""
    logger.info("测试核心类导入...")
    success = True

    # 分别测试每个导入，避免一个失败影响所有
    try:
        # 核心类导入 - 基础类
        from airfogsim.core import Agent
        logger.info("✓ 成功导入 Agent 类")
    except Exception as e:
        logger.error(f"✗ 导入 Agent 失败: {e}")
        success = False

    try:
        # 核心类导入 - Environment
        from airfogsim.core import Environment
        logger.info("✓ 成功导入 Environment 类")
    except Exception as e:
        logger.error(f"✗ 导入 Environment 失败: {e}")
        success = False

    try:
        # 核心类导入 - Task
        from airfogsim.core import Task
        logger.info("✓ 成功导入 Task 类")
    except Exception as e:
        logger.error(f"✗ 导入 Task 失败: {e}")
        success = False

    try:
        # 核心类导入 - Component
        from airfogsim.core import Component
        logger.info("✓ 成功导入 Component 类")
    except Exception as e:
        logger.error(f"✗ 导入 Component 失败: {e}")
        success = False

    try:
        # 核心类导入 - Workflow
        from airfogsim.core import Workflow
        logger.info("✓ 成功导入 Workflow 类")
    except Exception as e:
        logger.error(f"✗ 导入 Workflow 失败: {e}")
        success = False

    try:
        # 核心类导入 - 枚举
        from airfogsim.core import enums
        logger.info("✓ 成功导入 enums 模块")
    except Exception as e:
        logger.error(f"✗ 导入 enums 失败: {e}")
        success = False

    try:
        # 管理器导入
        from airfogsim.manager import AgentManager
        logger.info("✓ 成功导入 AgentManager 类")
    except Exception as e:
        logger.error(f"✗ 导入 AgentManager 失败: {e}")
        success = False

    try:
        # 代理导入
        from airfogsim.agent import DroneAgent
        logger.info("✓ 成功导入 DroneAgent 类")
    except Exception as e:
        logger.error(f"✗ 导入 DroneAgent 失败: {e}")
        success = False

    if success:
        logger.info("✅ 核心类导入测试完成，所有测试通过!")
    else:
        logger.error("❌ 核心类导入测试完成，部分测试失败")

    return success


def test_examples():
    """测试示例代码"""
    logger.info("\n测试示例代码...")
    try:
        from airfogsim.examples.test_examples import main

        # 运行示例测试，但仅列出可用示例而不实际运行
        logger.info("运行示例测试列表...")
        sys.argv = ["test_examples.py", "--list"]
        main()

        logger.info("✅ 示例测试列表成功!")
        return True
    except Exception as e:
        logger.error(f"❌ 示例测试失败: {e}")
        traceback.logger.info_exc()
        return False


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("AirFogSim 包验证测试")
    logger.info("=" * 60)

    # 测试导入
    import_success = test_core_imports()

    # 测试示例
    examples_success = test_examples()

    # 总结
    logger.info("\n" + "=" * 60)
    logger.info("测试结果摘要:")
    logger.info(f"核心类导入: {'✅ 成功' if import_success else '❌ 失败'}")
    logger.info(f"示例测试: {'✅ 成功' if examples_success else '❌ 失败'}")
    logger.info("=" * 60)

    # 返回状态码
    return 0 if import_success and examples_success else 1


if __name__ == "__main__":
    sys.exit(main())
