"""
AirFogSim基准测试数据收集器

该模块提供了用于收集仿真数据的主收集器，用于创建基准测试数据集。
"""

import os
import time
import json
from datetime import datetime

from airfogsim.benchmark.collectors.agent_collector import AgentStateCollector
from airfogsim.benchmark.collectors.workflow_collector import WorkflowStateCollector
from airfogsim.benchmark.collectors.event_collector import EventCollector


class BenchmarkDataCollector:
    """
    基准测试数据收集器
    
    负责收集仿真数据，包括代理状态、工作流状态和事件数据，并将其导出为基准测试数据集。
    """
    
    def __init__(self, env, output_dir="./benchmark_data", **kwargs):
        """
        初始化基准测试数据收集器
        
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
        
        print(f"基准测试数据收集器初始化完成，输出目录: {output_dir}")
    
    def export_data(self):
        """
        导出数据到文件
        
        Returns:
            Dict: 导出的文件路径
        """
        # 创建时间戳文件夹
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(self.output_dir, f"benchmark_{timestamp}")
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
        
        print(f"\n基准测试数据已导出到: {output_dir}")
        
        return {
            "output_dir": output_dir,
            "metadata_file": metadata_file,
            **agent_files,
            **workflow_files,
            **event_files
        }
