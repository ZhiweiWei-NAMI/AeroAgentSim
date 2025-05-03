"""
AirFogSim代理状态收集器

该模块提供了用于收集代理状态数据的收集器。
"""

import time
import json
import csv
import os
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class AgentStateCollector:
    """
    代理状态收集器

    负责收集代理的状态数据，包括位置、电量等。

    可以配置是否监听visual_update事件，如果开启，将在每次visual_update事件触发时
    收集所有代理的完整状态。否则，只收集状态变化事件触发的状态变化。
    """

    def __init__(self, env, config=None):
        """
        初始化代理状态收集器

        Args:
            env: 仿真环境
            config: 配置参数，可包含以下字段：
                - listen_visual_update: 是否监听visual_update事件，默认为False
                - debug: 是否输出调试信息，默认为False
        """
        self.env = env
        self.agent_states = {}  # 存储代理状态数据，格式：{agent_id: [{timestamp, position, ...}, ...]}
        self.start_time = time.time()

        # 默认配置
        self.default_config = {
            'listen_visual_update': False,  # 默认不监听visual_update事件
            'debug': False  # 是否输出调试信息
        }

        # 合并配置
        self.config = config or {}
        self.config = {**self.default_config, **self.config}

        logger.info(f"AgentStateCollector初始化，配置: {self.config}")

        # 订阅事件
        self._subscribe_events()

    def _subscribe_events(self):
        """订阅事件"""

        # 订阅代理状态变化事件
        self.env.event_registry.subscribe(
            '*',
            'state_changed',
            f'stats_agent_state_collector_{self.start_time}',
            self._on_agent_state_changed
        )

        # 订阅possessing_object_added和possessing_object_removed
        self.env.event_registry.subscribe(
            '*',
            'possessing_object_added',
            f'stats_agent_state_collector_{self.start_time}',
            lambda event_data:  self._on_agent_possessing_object_changed(event_data, event_type='added')
        )
        self.env.event_registry.subscribe(
            '*',
            'possessing_object_removed',
            f'stats_agent_state_collector_{self.start_time}',
            lambda event_data:  self._on_agent_possessing_object_changed(event_data, event_type='removed')
        )

        # 如果配置了监听visual_update事件，则订阅该事件
        if self.config['listen_visual_update']:
            logger.info(f"AgentStateCollector订阅visual_update事件")
            self.env.event_registry.subscribe(
                self.env.id,
                'visual_update',
                f'stats_agent_visual_update_{self.start_time}',
                self._on_visual_update
            )
    def _on_agent_possessing_object_changed(self, event_data, event_type):
        """
        处理代理状态变化事件

        Args:
            event_data: 事件数据
            event_type: 事件类型，added或removed
        """
        # {
        #     'object_name': object_name,
        #     'object_id': self._get_attribute(obj, 'id', None),
        #     'agent_id': self.id,
        #     'time': self.env.now
        # }
        source_id = event_data.get('agent_id')
        if not source_id or not source_id.startswith('agent_'):
            return

        # 获取代理
        agent = self.env.agents.get(source_id)
        if not agent:
            return

        # 获取状态变化信息
        key = 'possessing_object'
        current_value = agent.get_possessing_object_names()
        current_value.sort()
        if event_type == 'added':
            old_value = current_value.copy()
            old_value.remove(event_data.get('object_name'))
            new_value = current_value
        elif event_type == 'removed':
            old_value = current_value.copy() # Get the list *before* theoretical removal for the event
            old_value.append(event_data.get('object_name'))
            old_value.sort() # Sort for consistency if needed
            new_value = current_value # current_value is already sorted from earlier
        else:
            return

        # 初始化代理状态存储
        if source_id not in self.agent_states:
            self.agent_states[source_id] = []

        self.agent_states[source_id].append({
            'timestamp': self.env.now,
            'real_time': time.time() - self.start_time,
            'agent_id': source_id,
            'agent_type': agent.__class__.__name__,
            'state_key': key,
            'old_value': old_value,
            'new_value': new_value
        })


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
        if isinstance(old_value, set):
            old_value = sorted(list(old_value))
        if isinstance(new_value, set):
            new_value = sorted(list(new_value))

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

    def _on_visual_update(self, event_data):
        """
        处理visual_update事件

        Args:
            event_data: 事件数据，包含当前仿真时间
        """
        # 当收到visual_update事件时，采集所有代理的当前状态
        sim_time = event_data.get('time', self.env.now)
        if self.config.get('debug', False):
            logger.info(f"Visual update at time {sim_time}, collecting all agent states")
        self._collect_all_agent_states()

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

            # 添加代理的所有状态 (处理非JSON序列化类型)
            for key, value in agent.state.items():
                if isinstance(value, set):
                    state_data[key] = sorted(list(value))
                elif isinstance(value, (tuple, list)):
                    state_data[key] = list(value)
                elif isinstance(value, (str, int, float, bool)):
                    state_data[key] = value
                else:
                    # Attempt to convert others to string or handle specific complex types
                    try:
                        state_data[key] = str(value) # Fallback to string representation
                        logger.debug(f"Converted non-standard state '{key}' to string for agent {agent_id}")
                    except Exception:
                        state_data[key] = "Error: Non-serializable/stringifiable state"
                        logger.warning(f"State '{key}' for agent {agent_id} could not be serialized or converted to string.")

            objs = agent.get_possessing_object_names()
            objs.sort()
            # 记录possessing_object
            state_data['possessing_object'] = list(objs)

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
        # 辅助函数：收集数据并确保时间戳递增，相同时间戳的数据会覆盖之前的数据
        def collect_data_with_timestamp_check(states, data_key, process_func):
            """
            收集数据并确保时间戳递增，相同时间戳的数据会覆盖之前的数据

            Args:
                states: 代理状态列表
                data_key: 数据键名（如 'position', 'battery_level'）
                process_func: 处理数据的函数，接收状态和数据值，返回处理后的数据行

            Returns:
                按时间戳排序的数据行列表
            """
            # 使用字典存储每个时间戳的最新数据，键为 (timestamp, agent_id)
            data_by_timestamp = {}

            for state in states:
                timestamp = state["timestamp"]
                agent_id = state["agent_id"]
                key = (timestamp, agent_id)

                # 检查是否是完整状态记录
                full_state = state.get("full_state", False)
                if full_state:
                    value = state.get(data_key, None)
                    if value is not None:
                        data_row = process_func(state, value)
                        data_by_timestamp[key] = data_row
                    continue

                # 检查是否是特定状态变化记录
                if state["state_key"] == data_key and state["new_value"] is not None:
                    value = state["new_value"]
                    data_row = process_func(state, value)
                    data_by_timestamp[key] = data_row

            # 按时间戳和代理ID排序
            sorted_keys = sorted(data_by_timestamp.keys())
            return [data_by_timestamp[key] for key in sorted_keys]

        # 导出代理位置数据
        positions_file = os.path.join(output_dir, "agent_positions.csv")
        with open(positions_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "agent_id", "agent_type", "x", "y", "z"])

            for agent_id, states in self.agent_states.items():
                # 处理位置数据
                def process_position(state, position):
                    if (isinstance(position, list) or isinstance(position, tuple)) and len(position) >= 3:
                        return [
                            state["timestamp"],
                            agent_id,
                            state["agent_type"],
                            position[0],
                            position[1],
                            position[2]
                        ]
                    return None

                position_data = collect_data_with_timestamp_check(states, "position", process_position)
                for row in position_data:
                    if row:  # 确保数据有效
                        writer.writerow(row)

        # 导出代理电量数据
        battery_file = os.path.join(output_dir, "agent_battery.csv")
        with open(battery_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "agent_id", "agent_type", "battery_level"])

            for agent_id, states in self.agent_states.items():
                # 处理电量数据
                def process_battery(state, battery_level):
                    return [
                        state["timestamp"],
                        agent_id,
                        state["agent_type"],
                        battery_level
                    ]

                battery_data = collect_data_with_timestamp_check(states, "battery_level", process_battery)
                for row in battery_data:
                    writer.writerow(row)

        # 导出代理状态数据
        status_file = os.path.join(output_dir, "agent_status.csv")
        with open(status_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "agent_id", "agent_type", "status"])

            for agent_id, states in self.agent_states.items():
                # 处理状态数据
                def process_status(state, status):
                    return [
                        state["timestamp"],
                        agent_id,
                        state["agent_type"],
                        status
                    ]

                status_data = collect_data_with_timestamp_check(states, "status", process_status)
                for row in status_data:
                    writer.writerow(row)

        # 导出代理持有物品数据
        possessing_file = os.path.join(output_dir, "agent_possessing.csv")
        with open(possessing_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "agent_id", "agent_type", "possessing_object"])

            for agent_id, states in self.agent_states.items():
                # 处理持有物品数据
                def process_possessing(state, possessing_object):
                    return [
                        state["timestamp"],
                        agent_id,
                        state["agent_type"],
                        possessing_object
                    ]

                possessing_data = collect_data_with_timestamp_check(states, "possessing_object", process_possessing)
                for row in possessing_data:
                    writer.writerow(row)

        # 返回所有CSV文件路径
        return {
            "positions_file": positions_file,
            "battery_file": battery_file,
            "status_file": status_file,
            "possessing_file": possessing_file
        }
