#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AirFogSim统计数据可视化器

该模块提供了统计数据可视化功能，用于生成图表和图形。
"""

import os
import json
import matplotlib.pyplot as plt
import numpy as np
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

# 设置中文字体
try:
    # 尝试设置中文字体
    plt.rcParams['font.family']=["Noto Sans CJK JP", "Droid Sans Fallback", "Ubuntu", "DejaVu Sans"]
    plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题
    logger.info("已设置中文字体支持")
except Exception as e:
    logger.error(f"设置中文字体时出错: {e}")

class StatsVisualizer:
    """
    统计数据可视化器

    负责生成统计数据的图表和图形，包括工作流完成率、电池消耗率、任务执行时间等。
    """

    def __init__(self, stats_dir, report_file=None):
        """
        初始化统计数据可视化器

        Args:
            stats_dir: 统计数据目录
            report_file: 报告文件路径
        """
        self.stats_dir = stats_dir

        # 如果没有指定报告文件，使用默认路径
        if report_file is None:
            report_file = os.path.join(stats_dir, "report.json")

        # 加载报告
        if os.path.exists(report_file):
            with open(report_file, "r") as f:
                self.report = json.load(f)
        else:
            self.report = {}

        # 创建输出目录
        self.output_dir = os.path.join(stats_dir, "visualizations")
        os.makedirs(self.output_dir, exist_ok=True)

    def visualize_workflow_completion(self):
        """
        可视化工作流完成率

        Returns:
            生成的图表文件路径
        """
        # 获取工作流完成率数据
        workflow_completion = self.report.get('workflow_completion', {})
        status_distribution = workflow_completion.get('status_distribution', {})
        type_completion_rates = workflow_completion.get('type_completion_rates', {})

        # 创建图表
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

        # 绘制状态分布饼图
        labels = list(status_distribution.keys())
        sizes = list(status_distribution.values())

        ax1.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
        ax1.axis('equal')  # Equal aspect ratio ensures that pie is drawn as a circle
        ax1.set_title('工作流状态分布')

        # 绘制各类型工作流完成率柱状图
        types = list(type_completion_rates.keys())
        completion_rates = [data.get('completion_rate', 0) for data in type_completion_rates.values()]

        ax2.bar(types, completion_rates)
        ax2.set_xlabel('工作流类型')
        ax2.set_ylabel('完成率 (%)')
        ax2.set_title('各类型工作流完成率')
        ax2.set_ylim(0, 100)

        # 添加数值标签
        for i, v in enumerate(completion_rates):
            ax2.text(i, v + 2, f"{v:.1f}%", ha='center')

        # 调整布局
        plt.tight_layout()

        # 保存图表
        output_file = os.path.join(self.output_dir, "workflow_completion.png")
        plt.savefig(output_file, dpi=300)
        plt.close()

        return output_file

    def visualize_battery_consumption(self):
        """
        可视化电池消耗率

        Returns:
            生成的图表文件路径
        """
        # 获取电池消耗率数据
        battery_consumption = self.report.get('battery_consumption', {})
        agent_consumption_rates = battery_consumption.get('agent_consumption_rates', {})

        # 创建图表
        fig, ax = plt.subplots(figsize=(12, 6))

        # 绘制每个代理的电池消耗率柱状图
        agent_ids = list(agent_consumption_rates.keys())
        hourly_rates = [data.get('hourly_consumption_rate', 0) for data in agent_consumption_rates.values()]

        # 按消耗率排序
        sorted_indices = np.argsort(hourly_rates)
        sorted_agent_ids = [agent_ids[i] for i in sorted_indices]
        sorted_hourly_rates = [hourly_rates[i] for i in sorted_indices]

        # 使用简化的代理ID（只保留最后6位）
        simplified_agent_ids = [agent_id[-6:] for agent_id in sorted_agent_ids]

        ax.bar(simplified_agent_ids, sorted_hourly_rates)
        ax.set_xlabel('代理ID')
        ax.set_ylabel('每小时电池消耗率 (%/h)')
        ax.set_title('各代理电池消耗率')

        # 旋转x轴标签
        plt.xticks(rotation=90)

        # 添加平均消耗率水平线
        avg_hourly_rate = battery_consumption.get('average_hourly_rate', 0)
        ax.axhline(y=avg_hourly_rate, color='r', linestyle='--', label=f'平均消耗率: {avg_hourly_rate:.2f} %/h')
        ax.legend()

        # 调整布局
        plt.tight_layout()

        # 保存图表
        output_file = os.path.join(self.output_dir, "battery_consumption.png")
        plt.savefig(output_file, dpi=300)
        plt.close()

        return output_file

    def visualize_weather_impact(self):
        """
        可视化天气影响

        Returns:
            生成的图表文件路径
        """
        # 获取天气影响数据
        weather_impact = self.report.get('weather_impact', {})
        weather_periods = weather_impact.get('weather_periods', [])
        correlation = weather_impact.get('correlation', 0)

        # 创建图表
        fig, ax = plt.subplots(figsize=(12, 6))

        # 提取数据
        start_times = [period.get('start_time', 0) for period in weather_periods]
        wind_speeds = [period.get('wind_speed', 0) for period in weather_periods]
        consumption_rates = [period.get('avg_battery_consumption_rate', 0) for period in weather_periods]

        # 创建双y轴
        ax1 = ax
        ax2 = ax1.twinx()

        # 绘制风速折线图
        line1, = ax1.plot(start_times, wind_speeds, 'b-', marker='o', label='风速')
        ax1.set_xlabel('仿真时间 (s)')
        ax1.set_ylabel('风速 (m/s)', color='b')
        ax1.tick_params(axis='y', labelcolor='b')

        # 绘制电池消耗率折线图
        line2, = ax2.plot(start_times, consumption_rates, 'r-', marker='x', label='电池消耗率')
        ax2.set_ylabel('电池消耗率 (%/h)', color='r')
        ax2.tick_params(axis='y', labelcolor='r')

        # 添加相关系数
        plt.title(f'风速与电池消耗率关系 (相关系数: {correlation:.2f})')

        # 添加图例
        lines = [line1, line2]
        labels = [line.get_label() for line in lines]
        ax1.legend(lines, labels, loc='upper left')

        # 调整布局
        plt.tight_layout()

        # 保存图表
        output_file = os.path.join(self.output_dir, "weather_impact.png")
        plt.savefig(output_file, dpi=300)
        plt.close()

        return output_file

    def visualize_task_execution_time(self):
        """
        可视化任务执行时间

        Returns:
            生成的图表文件路径
        """
        # 获取任务执行时间数据
        task_execution_time = self.report.get('task_execution_time', {})
        avg_execution_times = task_execution_time.get('avg_execution_times', {})

        # 创建图表
        fig, ax = plt.subplots(figsize=(12, 6))

        # 提取数据
        workflow_types = list(avg_execution_times.keys())
        avg_times = [data.get('avg_time', 0) for data in avg_execution_times.values()]
        min_times = [data.get('min_time', 0) for data in avg_execution_times.values()]
        max_times = [data.get('max_time', 0) for data in avg_execution_times.values()]

        # 计算误差范围
        yerr_min = [avg - min for avg, min in zip(avg_times, min_times)]
        yerr_max = [max - avg for avg, max in zip(avg_times, max_times)]
        yerr = [yerr_min, yerr_max]

        # 绘制柱状图
        ax.bar(workflow_types, avg_times, yerr=yerr, capsize=5)
        ax.set_xlabel('工作流类型')
        ax.set_ylabel('执行时间 (s)')
        ax.set_title('各类型工作流平均执行时间')

        # 添加数值标签
        for i, v in enumerate(avg_times):
            ax.text(i, v + 2, f"{v:.1f}s", ha='center')

        # 调整布局
        plt.tight_layout()

        # 保存图表
        output_file = os.path.join(self.output_dir, "task_execution_time.png")
        plt.savefig(output_file, dpi=300)
        plt.close()

        return output_file

    def visualize_all(self):
        """
        生成所有可视化图表

        Returns:
            生成的图表文件路径列表
        """
        output_files = []

        # 可视化工作流完成率
        output_files.append(self.visualize_workflow_completion())

        # 可视化电池消耗率
        output_files.append(self.visualize_battery_consumption())

        # 可视化天气影响
        output_files.append(self.visualize_weather_impact())

        # 可视化任务执行时间
        output_files.append(self.visualize_task_execution_time())

        return output_files
