#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工作流可视化演示

此脚本演示如何使用工作流的UML活动图表示功能，
生成多种格式的图表并保存到文件。
"""

import os
import sys
from airfogsim.core.environment import Environment
from airfogsim.workflow.inspection import create_inspection_workflow
from airfogsim.agent.drone import DroneAgent
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def generate_workflow_diagrams(output_dir="./diagrams"):
    """生成工作流图表并保存到文件"""
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 创建环境和工作流
    env = Environment()
    
    # 创建无人机
    drone = DroneAgent(env, "inspection_drone", {
        "position": [0, 0, 0],
        "battery_level": 100,
        "status": "idle"
    })
    
    # 创建检查点列表 - 简单的3D点序列
    inspection_points = [
        [10, 10, 5],
        [20, 20, 5],
        [30, 30, 5],
        [40, 20, 5],
        [50, 10, 5]
    ]
    
    # 创建巡检工作流
    workflow = create_inspection_workflow(env, drone, inspection_points)
    
    # 生成PlantUML格式活动图
    plantuml_content = workflow.to_uml_activity_diagram()
    with open(os.path.join(output_dir, "inspection_workflow.puml"), "w", encoding="utf-8") as f:
        f.write(plantuml_content)
    logger.info(f"PlantUML活动图已保存到 {os.path.join(output_dir, 'inspection_workflow.puml')}")
    
    # 同时创建一个带有封装PlantUML代码的Markdown文件
    with open(os.path.join(output_dir, "inspection_workflow.md"), "w", encoding="utf-8") as f:
        f.write("# 巡检工作流活动图\n\n")
        f.write("## PlantUML活动图\n\n")
        f.write("```plantuml\n")
        f.write(plantuml_content)
        f.write("\n```\n\n")
        
        # 生成Mermaid格式图表
        mermaid_content = workflow.to_mermaid_diagram()
        f.write("## Mermaid状态图\n\n")
        f.write(mermaid_content)
    logger.info(f"综合Markdown文件已保存到 {os.path.join(output_dir, 'inspection_workflow.md')}")
    
    # 打印使用说明
    logger.info("\n要查看这些图表，您可以：")
    logger.info("1. 在VSCode中安装以下插件：")
    logger.info("   - PlantUML插件 (jebbs.plantuml)")
    logger.info("   - Markdown Preview Enhanced (shd101wyy.markdown-preview-enhanced)")
    logger.info("2. 使用在线工具：")
    logger.info("   - PlantUML: https://www.plantuml.com/plantuml/")
    logger.info("   - Mermaid Live Editor: https://mermaid.live/")

if __name__ == "__main__":
    # 设置输出目录
    output_dir = "./diagrams" if len(sys.argv) < 2 else sys.argv[1]
    generate_workflow_diagrams(output_dir)