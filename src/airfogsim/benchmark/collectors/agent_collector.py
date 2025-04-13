"""
AirFogSim代理状态收集器

该模块提供了用于收集代理状态数据的收集器。
"""

import time
import json
import csv
import os


class AgentStateCollector:
    """
    代理状态收集器

    负责收集代理的状态数据，包括位置、电量等。
    """

    def __init__(self, env):
        """
        初始化代理状态收集器

        Args:
            env: 仿真环境
        """
        self.env = env
        self.agent_states = {}  # 存储代理状态数据，格式：{agent_id: [{timestamp, position, ...}, ...]}
        self.start_time = time.time()

        # 订阅事件
        self._subscribe_events()

    def _subscribe_events(self):
        """订阅事件"""

        # 订阅代理状态变化事件
        self.env.event_registry.subscribe(
            '*',
            'state_changed',
            'benchmark_agent_state_collector',
            self._on_agent_state_changed
        )

    def _on_agent_state_changed(self, event_data):
        """
        处理代理状态变化事件

        Args:
            event_data: 事件数据
        """
        source_id = event_data.get('agent_id')
        if not source_id or not source_id.startswith('agent_'):
            return

        # 获取代理
        agent = self.env.agents.get(source_id)
        if not agent:
            return

        # 获取状态变化信息
        key = event_data.get('key')
        old_value = event_data.get('old_value')
        new_value = event_data.get('new_value')

        # 初始化代理状态存储
        if source_id not in self.agent_states:
            self.agent_states[source_id] = []

        # 记录状态变化
        self.agent_states[source_id].append({
            'timestamp': self.env.now,
            'real_time': time.time() - self.start_time,
            'agent_id': source_id,
            'agent_type': agent.__class__.__name__,
            'state_key': key,
            'old_value': old_value,
            'new_value': new_value
        })

    def _collect_all_agent_states(self):
        """采集所有代理的当前状态"""
        for agent_id, agent in self.env.agents.items():
            # 初始化代理状态存储
            if agent_id not in self.agent_states:
                self.agent_states[agent_id] = []

            # 获取代理的全部状态
            state_data = {
                'timestamp': self.env.now,
                'real_time': time.time() - self.start_time,
                'agent_id': agent_id,
                'agent_type': agent.__class__.__name__,
                'full_state': True  # 标记为全部状态
            }

            # 添加代理的所有状态
            for key, value in agent.state.items():
                state_data[key] = value

            # 记录状态
            self.agent_states[agent_id].append(state_data)

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

        # 导出代理状态数据
        agent_states_file = os.path.join(output_dir, "agent_states.json")
        with open(agent_states_file, "w") as f:
            json.dump(self.agent_states, f, indent=2)

        # 创建 CSV 格式的数据，方便分析
        self._export_csv_data(output_dir)

        return {
            "agent_states_file": agent_states_file
        }

    def _export_csv_data(self, output_dir):
        """
        导出 CSV 格式的数据

        Args:
            output_dir: 输出目录
        """
        # 导出代理位置数据
        positions_file = os.path.join(output_dir, "agent_positions.csv")
        with open(positions_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "agent_id", "agent_type", "x", "y", "z"])

            for agent_id, states in self.agent_states.items():
                for state in states:
                    if state["state_key"] == "position" and state["new_value"] is not None:
                        position = state["new_value"]
                        if isinstance(position, list) and len(position) >= 3:
                            writer.writerow([
                                state["timestamp"],
                                agent_id,
                                state["agent_type"],
                                position[0],
                                position[1],
                                position[2]
                            ])

        # 导出代理电量数据
        battery_file = os.path.join(output_dir, "agent_battery.csv")
        with open(battery_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "agent_id", "agent_type", "battery_level"])

            for agent_id, states in self.agent_states.items():
                for state in states:
                    if state["state_key"] == "battery_level" and state["new_value"] is not None:
                        battery_level = state["new_value"]

                        writer.writerow([
                            state["timestamp"],
                            agent_id,
                            state["agent_type"],
                            battery_level
                        ])

        # 导出代理状态数据
        status_file = os.path.join(output_dir, "agent_status.csv")
        with open(status_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "agent_id", "agent_type", "status"])

            for agent_id, states in self.agent_states.items():
                for state in states:
                    if state["state_key"] == "status" and state["new_value"] is not None:
                        status = state["new_value"]

                        writer.writerow([
                            state["timestamp"],
                            agent_id,
                            state["agent_type"],
                            status
                        ])

        # 导出代理持有物品数据
        possessing_file = os.path.join(output_dir, "agent_possessing.csv")
        with open(possessing_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "agent_id", "agent_type", "possessing_object"])

            for agent_id, states in self.agent_states.items():
                for state in states:
                    if state["state_key"] == "possessing_object" and state["new_value"] is not None:
                        possessing_object = state["new_value"]

                        writer.writerow([
                            state["timestamp"],
                            agent_id,
                            state["agent_type"],
                            possessing_object
                        ])

        # 返回所有CSV文件路径
        return {
            "positions_file": positions_file,
            "battery_file": battery_file,
            "status_file": status_file,
            "possessing_file": possessing_file
        }
