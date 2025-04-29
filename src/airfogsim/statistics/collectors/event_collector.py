"""
AirFogSim事件收集器

该模块提供了用于收集仿真事件数据的收集器。
"""

import time
import json
import csv
import os
from datetime import datetime
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class EventCollector:
    """
    事件收集器

    负责收集仿真过程中的各种事件数据。
    """

    def __init__(self, env):
        """
        初始化事件收集器

        Args:
            env: 仿真环境
        """
        self.env = env
        self.events = []  # 存储事件数据，格式：[{timestamp, source_id, event_name, ...}, ...]
        self.start_time = time.time()

        # 订阅事件
        self._subscribe_events()

    def _subscribe_events(self):
        """订阅事件"""
        # 订阅所有事件
        self.env.event_registry.subscribe(
            '*',
            '*',
            f'stats_event_collector_{self.start_time}',
            self._on_event
        )

    def _on_event(self, event_data):
        """
        处理事件

        Args:
            event_data: 事件数据
        """
        # 打印事件数据以进行调试
        # logger.info(f"\n时间 {self.env.now}: 收到事件: {event_data}")

        # 记录事件
        try:
            # 构造事件记录
            event_record = {
                'timestamp': self.env.now,
                'real_time': time.time() - self.start_time,
            }

            # 处理不同类型的事件数据
            if isinstance(event_data, dict):
                # 尝试不同的字段名称
                source_id = None
                event_name = None

                # 尝试不同的源ID字段
                if 'source_id' in event_data:
                    source_id = event_data['source_id']
                elif 'source' in event_data:
                    source_id = event_data['source']
                elif 'agent_id' in event_data:
                    source_id = event_data['agent_id']
                elif 'workflow_id' in event_data:
                    source_id = event_data['workflow_id']

                # 尝试不同的事件名称字段
                if 'event_name' in event_data:
                    event_name = event_data['event_name']
                elif 'event' in event_data:
                    event_name = event_data['event']
                elif 'type' in event_data:
                    event_name = event_data['type']

                # 如果没有找到源ID或事件名称，使用默认值
                if not source_id:
                    source_id = 'unknown_source'
                if not event_name:
                    event_name = 'unknown_event'

                # 添加到事件记录
                event_record['source_id'] = source_id
                event_record['event_name'] = event_name
                event_record['data'] = event_data

                # 添加到事件列表
                self.events.append(event_record)
                # logger.info(f"\n时间 {self.env.now}: 记录事件: {source_id}/{event_name}")
            else:
                # 如果事件数据不是字典，使用默认值
                event_record['source_id'] = 'unknown_source'
                event_record['event_name'] = 'unknown_event'
                event_record['data'] = str(event_data)

                # 添加到事件列表
                self.events.append(event_record)
        except Exception as e:
            logger.error(f"\n时间 {self.env.now}: 处理事件时出错: {str(e)}")

    def export_data(self, output_dir):
        """
        导出数据到文件

        Args:
            output_dir: 输出目录

        Returns:
            Dict: 导出的文件路径
        """
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 导出事件数据
        events_file = os.path.join(output_dir, "events.json")
        with open(events_file, "w") as f:
            json.dump(self.events, f, indent=2)

        # 创建 CSV 格式的数据，方便分析
        self._export_csv_data(output_dir)

        return {
            "events_file": events_file
        }

    def _export_csv_data(self, output_dir):
        """
        导出 CSV 格式的数据

        Args:
            output_dir: 输出目录
        """
        # 导出事件数据
        events_file = os.path.join(output_dir, "events.csv")
        with open(events_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "source_id", "event_name"])

            for event in self.events:
                writer.writerow([
                    event["timestamp"],
                    event["source_id"],
                    event["event_name"]
                ])
