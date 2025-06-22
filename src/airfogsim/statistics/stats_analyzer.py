#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AirFogSim统计数据分析器

该模块提供了统计数据分析功能，用于分析仿真数据并生成统计报告。
"""

import os
import json
import csv
import math
from collections import defaultdict
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class StatsAnalyzer:
    """
    统计数据分析器

    负责分析仿真数据并生成统计报告，包括工作流完成率、电池消耗率、任务执行时间等。
    """

    def __init__(self, stats_dir):
        """
        初始化统计数据分析器

        Args:
            stats_dir: 统计数据目录
        """
        self.stats_dir = stats_dir

        # 加载统计数据
        self.metadata = self._load_json_file("metadata.json")
        self.agent_states = self._load_json_file("agent_states.json")
        self.workflow_states = self._load_json_file("workflow_states.json")
        self.events = self._load_json_file("events.json")

        # 加载CSV数据
        self.agent_positions = self._load_csv_file("agent_positions.csv")
        self.agent_battery = self._load_csv_file("agent_battery.csv")
        self.agent_status = self._load_csv_file("agent_status.csv")
        self.workflow_states_csv = self._load_csv_file("workflow_states.csv")
        self.weather = self._load_csv_file("weather.csv")

        # 如果工作流状态数据为空，尝试从基准测试数据中加载
        if not self.workflow_states:
            # 查找最新的基准测试数据目录
            benchmark_dirs = [d for d in os.listdir(os.path.dirname(self.stats_dir))
                             if d.startswith("benchmark_") and os.path.isdir(os.path.join(os.path.dirname(self.stats_dir), d))]
            if benchmark_dirs:
                # 按时间戳排序
                benchmark_dirs.sort(reverse=True)
                latest_benchmark_dir = os.path.join(os.path.dirname(self.stats_dir), benchmark_dirs[0])
                # 加载基准测试数据中的工作流状态数据
                benchmark_workflow_states = self._load_json_file_from_dir(latest_benchmark_dir, "workflow_states.json")
                if benchmark_workflow_states:
                    self.workflow_states = benchmark_workflow_states
                    logger.info(f"从基准测试数据中加载工作流状态数据: {latest_benchmark_dir}")

    def _load_json_file(self, filename):
        """
        加载JSON文件

        Args:
            filename: 文件名

        Returns:
            加载的JSON数据
        """
        return self._load_json_file_from_dir(self.stats_dir, filename)

    def _load_json_file_from_dir(self, directory, filename):
        """
        从指定目录加载JSON文件

        Args:
            directory: 目录路径
            filename: 文件名

        Returns:
            加载的JSON数据
        """
        filepath = os.path.join(directory, filename)
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                return json.load(f)
        return {}

    def _load_csv_file(self, filename):
        """
        加载CSV文件

        Args:
            filename: 文件名

        Returns:
            加载的CSV数据
        """
        filepath = os.path.join(self.stats_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                reader = csv.DictReader(f)
                return list(reader)
        return []

    def analyze_workflow_completion(self):
        """
        分析工作流完成率

        Returns:
            工作流完成率统计
        """
        # 统计工作流状态
        workflow_status = defaultdict(int)
        workflow_types = defaultdict(lambda: defaultdict(int))

        # 获取每个工作流的最终状态
        final_states = {}
        for workflow_id, states in self.workflow_states.items():
            if states:
                # 按时间戳排序
                sorted_states = sorted(states, key=lambda x: x.get('timestamp', 0))
                # 获取最后一个状态
                final_state = sorted_states[-1]
                status = final_state.get('new_status') or final_state.get('status')
                workflow_type = final_state.get('workflow_type', 'unknown')

                # 记录最终状态
                final_states[workflow_id] = {
                    'status': status,
                    'type': workflow_type
                }

                # 统计状态
                workflow_status[status] += 1
                workflow_types[workflow_type][status] += 1

        # 计算完成率
        total_workflows = len(final_states)
        completed_workflows = workflow_status.get('COMPLETED', 0)
        completion_rate = (completed_workflows / total_workflows * 100) if total_workflows > 0 else 0

        # 计算各类型工作流的完成率
        type_completion_rates = {}
        for workflow_type, status_counts in workflow_types.items():
            type_total = sum(status_counts.values())
            type_completed = status_counts.get('COMPLETED', 0)
            type_completion_rate = (type_completed / type_total * 100) if type_total > 0 else 0
            type_completion_rates[workflow_type] = {
                'total': type_total,
                'completed': type_completed,
                'completion_rate': type_completion_rate
            }

        return {
            'total_workflows': total_workflows,
            'completed_workflows': completed_workflows,
            'completion_rate': completion_rate,
            'status_distribution': dict(workflow_status),
            'type_completion_rates': type_completion_rates
        }

    def analyze_battery_consumption(self):
        """
        分析电池消耗率

        Returns:
            电池消耗率统计
        """
        # 按代理ID分组
        agent_battery_data = defaultdict(list)
        for row in self.agent_battery:
            agent_id = row.get('agent_id')
            timestamp = float(row.get('timestamp', 0))
            battery_level = float(row.get('battery_level', 0))

            agent_battery_data[agent_id].append({
                'timestamp': timestamp,
                'battery_level': battery_level
            })

        # 计算每个代理的电池消耗率
        battery_consumption_rates = {}
        for agent_id, data in agent_battery_data.items():
            # 按时间戳排序
            sorted_data = sorted(data, key=lambda x: x['timestamp'])

            # 如果数据点少于2个，无法计算消耗率
            if len(sorted_data) < 2:
                continue

            # 计算消耗率（百分比/小时）
            first_point = sorted_data[0]
            last_point = sorted_data[-1]

            time_diff = last_point['timestamp'] - first_point['timestamp']
            battery_diff = 0
            # 不算充电
            for i in range(1, len(sorted_data)):
                battery_diff += max(sorted_data[i]['battery_level'] - sorted_data[i-1]['battery_level'], 0)

            # 避免除以零
            if time_diff <= 0:
                continue

            # 计算每小时消耗率
            hourly_rate = (battery_diff / time_diff) * 3600

            battery_consumption_rates[agent_id] = {
                'initial_level': first_point['battery_level'],
                'final_level': last_point['battery_level'],
                'time_period': time_diff,
                'hourly_consumption_rate': hourly_rate
            }

        # 计算平均消耗率
        hourly_rates = [data['hourly_consumption_rate'] for data in battery_consumption_rates.values()]
        avg_hourly_rate = sum(hourly_rates) / len(hourly_rates) if hourly_rates else 0

        return {
            'agent_consumption_rates': battery_consumption_rates,
            'average_hourly_rate': avg_hourly_rate
        }

    def analyze_weather_impact(self):
        """
        分析天气对电池消耗的影响

        Returns:
            天气影响统计
        """
        # 按时间段分组的天气数据
        weather_periods = []
        for row in self.weather:
            timestamp = float(row.get('timestamp', 0))
            wind_speed = float(row.get('wind_speed', 0))

            weather_periods.append({
                'start_time': timestamp,
                'wind_speed': wind_speed,
                'condition': row.get('condition', 'unknown'),
                'severity': row.get('severity', 'unknown')
            })

        # 按时间排序
        weather_periods = sorted(weather_periods, key=lambda x: x['start_time'])

        # 为每个时间段添加结束时间
        for i in range(len(weather_periods) - 1):
            weather_periods[i]['end_time'] = weather_periods[i + 1]['start_time']

        # 最后一个时间段的结束时间设为仿真结束时间
        if weather_periods:
            sim_time = float(self.metadata.get('simulation_time', 0))
            weather_periods[-1]['end_time'] = sim_time

        # 计算每个天气时间段内的平均电池消耗率
        weather_impact = []
        for period in weather_periods:
            start_time = period['start_time']
            end_time = period['end_time']
            wind_speed = period['wind_speed']

            # 获取该时间段内的电池数据
            period_battery_data = []
            for row in self.agent_battery:
                timestamp = float(row.get('timestamp', 0))
                if start_time <= timestamp < end_time:
                    period_battery_data.append({
                        'agent_id': row.get('agent_id'),
                        'timestamp': timestamp,
                        'battery_level': float(row.get('battery_level', 0))
                    })

            # 按代理ID分组
            agent_battery_changes = defaultdict(list)
            for data in period_battery_data:
                agent_battery_changes[data['agent_id']].append(data)

            # 计算每个代理在该时间段内的电池消耗率
            period_consumption_rates = []
            for agent_id, data in agent_battery_changes.items():
                # 按时间戳排序
                sorted_data = sorted(data, key=lambda x: x['timestamp'])

                # 如果数据点少于2个，无法计算消耗率
                if len(sorted_data) < 2:
                    continue

                # 计算消耗率（百分比/小时）
                first_point = sorted_data[0]
                last_point = sorted_data[-1]

                time_diff = last_point['timestamp'] - first_point['timestamp']
                battery_diff = first_point['battery_level'] - last_point['battery_level']

                # 避免除以零
                if time_diff <= 0:
                    continue

                # 计算每小时消耗率
                hourly_rate = (battery_diff / time_diff) * 3600
                period_consumption_rates.append(hourly_rate)

            # 计算该时间段内的平均消耗率
            avg_rate = sum(period_consumption_rates) / len(period_consumption_rates) if period_consumption_rates else 0

            weather_impact.append({
                'start_time': start_time,
                'end_time': end_time,
                'duration': end_time - start_time,
                'wind_speed': wind_speed,
                'condition': period['condition'],
                'severity': period['severity'],
                'avg_battery_consumption_rate': avg_rate,
                'num_data_points': len(period_consumption_rates)
            })

        return {
            'weather_periods': weather_impact,
            'correlation': self._calculate_correlation(
                [p['wind_speed'] for p in weather_impact],
                [p['avg_battery_consumption_rate'] for p in weather_impact]
            )
        }

    def _calculate_correlation(self, x, y):
        """
        计算相关系数

        Args:
            x: x值列表
            y: y值列表

        Returns:
            相关系数
        """
        # 如果数据点少于2个，无法计算相关系数
        if len(x) < 2 or len(y) < 2:
            return 0

        # 计算均值
        mean_x = sum(x) / len(x)
        mean_y = sum(y) / len(y)

        # 计算协方差和标准差
        numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        denominator = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) * sum((yi - mean_y) ** 2 for yi in y))

        # 避免除以零
        if denominator == 0:
            return 0

        return numerator / denominator

    def analyze_task_execution_time(self):
        """
        分析任务执行时间

        Returns:
            任务执行时间统计
        """
        # 按工作流类型分组的任务执行时间
        workflow_execution_times = defaultdict(list)

        # 计算每个工作流的执行时间
        for workflow_id, states in self.workflow_states.items():
            if not states:
                continue

            # 按时间戳排序
            sorted_states = sorted(states, key=lambda x: x.get('timestamp', 0))

            # 获取工作流类型
            workflow_type = sorted_states[0].get('workflow_type', 'unknown')

            # 查找开始和结束时间
            start_time = None
            end_time = None

            for state in sorted_states:
                status = state.get('new_status') or state.get('status')

                if status == 'RUNNING' and start_time is None:
                    start_time = state.get('timestamp', 0)

                if status == 'COMPLETED':
                    end_time = state.get('timestamp', 0)

            # 如果有开始和结束时间，计算执行时间
            if start_time is not None and end_time is not None:
                execution_time = end_time - start_time
                workflow_execution_times[workflow_type].append(execution_time)

        # 计算每种工作流类型的平均执行时间
        avg_execution_times = {}
        for workflow_type, times in workflow_execution_times.items():
            avg_time = sum(times) / len(times) if times else 0
            min_time = min(times) if times else 0
            max_time = max(times) if times else 0

            avg_execution_times[workflow_type] = {
                'avg_time': avg_time,
                'min_time': min_time,
                'max_time': max_time,
                'num_workflows': len(times)
            }

        return {
            'workflow_execution_times': dict(workflow_execution_times),
            'avg_execution_times': avg_execution_times
        }

    def generate_report(self):
        """
        生成统计报告

        Returns:
            统计报告
        """
        # 分析工作流完成率
        workflow_completion = self.analyze_workflow_completion()

        # 分析电池消耗率
        battery_consumption = self.analyze_battery_consumption()

        # 分析天气影响
        weather_impact = self.analyze_weather_impact()

        # 分析任务执行时间
        task_execution_time = self.analyze_task_execution_time()

        # 生成报告
        report = {
            'metadata': self.metadata,
            'workflow_completion': workflow_completion,
            'battery_consumption': battery_consumption,
            'weather_impact': weather_impact,
            'task_execution_time': task_execution_time
        }

        return report

    def save_report(self, output_file=None):
        """
        保存统计报告

        Args:
            output_file: 输出文件路径

        Returns:
            保存的文件路径
        """
        # 生成报告
        report = self.generate_report()

        # 如果没有指定输出文件，使用默认路径
        if output_file is None:
            output_file = os.path.join(self.stats_dir, "report.json")

        # 保存报告
        with open(output_file, "w") as f:
            json.dump(report, f, indent=2)

        logger.info(f"统计报告已保存到: {output_file}")

        return output_file
