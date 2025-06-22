#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AirFogSim统计数据收集器

该模块提供了统计数据收集功能，用于收集和分析仿真数据。
使用benchmark/collectors中的收集器实现，确保数据收集的一致性。
"""

import os
import time
import json
import csv
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

from airfogsim.statistics.collectors.agent_collector import AgentStateCollector
from airfogsim.statistics.collectors.workflow_collector import WorkflowStateCollector
from airfogsim.statistics.collectors.event_collector import EventCollector
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class StatsCollector:
    """
    统计数据收集器

    负责收集和分析仿真数据，包括代理状态、工作流状态和事件数据。
    该类使用benchmark/collectors中的收集器实现，确保数据收集的一致性。
    """

    def __init__(self, env, output_dir="./stats_data", **kwargs):
        """
        初始化统计数据收集器

        Args:
            env: 仿真环境
            output_dir: 输出目录
        """
        self.env = env
        self.output_dir = output_dir

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 创建子收集器
        self.agent_collector = AgentStateCollector(env, config=kwargs.get('agent_collector_config'))
        self.workflow_collector = WorkflowStateCollector(env)
        self.event_collector = EventCollector(env)

        # 记录开始时间
        self.start_time = time.time()

        logger.info(f"统计数据收集器初始化完成，输出目录: {output_dir}")

    @property
    def agent_states(self):
        """获取代理状态数据"""
        return self.agent_collector.agent_states

    @property
    def workflow_states(self):
        """获取工作流状态数据"""
        return self.workflow_collector.workflow_states

    @property
    def events(self):
        """获取事件数据"""
        return self.event_collector.events

    def export_data(self):
        """
        导出数据到文件

        Returns:
            Dict: 导出的文件路径
        """
        # 创建时间戳文件夹
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(self.output_dir, f"stats_{timestamp}")
        os.makedirs(output_dir, exist_ok=True)

        # 导出各个收集器的数据
        agent_files = self.agent_collector.export_data(output_dir)
        workflow_files = self.workflow_collector.export_data(output_dir)
        event_files = self.event_collector.export_data(output_dir)

        # 创建元数据文件
        metadata = {
            "timestamp": timestamp,
            "simulation_time": self.env.now,
            "real_time": time.time() - self.start_time,
            "num_agents": len(self.env.agents),
            "num_workflows": len(self.env.workflow_manager.workflows) if hasattr(self.env, 'workflow_manager') else 0,
            "num_events": len(self.event_collector.events)
        }

        metadata_file = os.path.join(output_dir, "metadata.json")
        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"\n统计数据已导出到: {output_dir}")

        return {
            "output_dir": output_dir,
            "metadata_file": metadata_file,
            **agent_files,
            **workflow_files,
            **event_files
        }
