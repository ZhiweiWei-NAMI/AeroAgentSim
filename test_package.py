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


def test_core_imports():
    """测试核心类导入"""
    print("测试核心类导入...")
    success = True

    # 分别测试每个导入，避免一个失败影响所有
    try:
        # 核心类导入 - 基础类
        from airfogsim.core import Agent
        print("✓ 成功导入 Agent 类")
    except Exception as e:
        print(f"✗ 导入 Agent 失败: {e}")
        success = False

    try:
        # 核心类导入 - Environment
        from airfogsim.core import Environment
        print("✓ 成功导入 Environment 类")
    except Exception as e:
        print(f"✗ 导入 Environment 失败: {e}")
        success = False

    try:
        # 核心类导入 - Task
        from airfogsim.core import Task
        print("✓ 成功导入 Task 类")
    except Exception as e:
        print(f"✗ 导入 Task 失败: {e}")
        success = False

    try:
        # 核心类导入 - Component
        from airfogsim.core import Component
        print("✓ 成功导入 Component 类")
    except Exception as e:
        print(f"✗ 导入 Component 失败: {e}")
        success = False

    try:
        # 核心类导入 - Workflow
        from airfogsim.core import Workflow
        print("✓ 成功导入 Workflow 类")
    except Exception as e:
        print(f"✗ 导入 Workflow 失败: {e}")
        success = False

    try:
        # 核心类导入 - 枚举
        from airfogsim.core import enums
        print("✓ 成功导入 enums 模块")
    except Exception as e:
        print(f"✗ 导入 enums 失败: {e}")
        success = False

    try:
        # 管理器导入
        from airfogsim.manager import AgentManager
        print("✓ 成功导入 AgentManager 类")
    except Exception as e:
        print(f"✗ 导入 AgentManager 失败: {e}")
        success = False

    try:
        # 代理导入
        from airfogsim.agent import DroneAgent
        print("✓ 成功导入 DroneAgent 类")
    except Exception as e:
        print(f"✗ 导入 DroneAgent 失败: {e}")
        success = False

    if success:
        print("✅ 核心类导入测试完成，所有测试通过!")
    else:
        print("❌ 核心类导入测试完成，部分测试失败")

    return success


def test_examples():
    """测试示例代码"""
    print("\n测试示例代码...")
    try:
        from airfogsim.examples.test_examples import main

        # 运行示例测试，但仅列出可用示例而不实际运行
        print("运行示例测试列表...")
        sys.argv = ["test_examples.py", "--list"]
        main()

        print("✅ 示例测试列表成功!")
        return True
    except Exception as e:
        print(f"❌ 示例测试失败: {e}")
        traceback.print_exc()
        return False


def main():
    """主函数"""
    print("=" * 60)
    print("AirFogSim 包验证测试")
    print("=" * 60)

    # 测试导入
    import_success = test_core_imports()

    # 测试示例
    examples_success = test_examples()

    # 总结
    print("\n" + "=" * 60)
    print("测试结果摘要:")
    print(f"核心类导入: {'✅ 成功' if import_success else '❌ 失败'}")
    print(f"示例测试: {'✅ 成功' if examples_success else '❌ 失败'}")
    print("=" * 60)

    # 返回状态码
    return 0 if import_success and examples_success else 1


if __name__ == "__main__":
    sys.exit(main())
