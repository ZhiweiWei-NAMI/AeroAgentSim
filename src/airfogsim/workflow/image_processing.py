"""
AirFogSim环境图像感知处理工作流模块

该模块定义了环境图像感知处理工作流及其元类，实现了无人机感知环境图像和处理数据的流程。
主要功能包括：
1. 图像感知任务管理
2. 数据处理任务管理
3. 状态机转换和事件触发
4. 动态任务生成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core import Workflow, WorkflowMeta
from airfogsim.core.enums import TriggerOperator, WorkflowStatus
import uuid
from typing import List, Tuple, Dict, Any, Optional

class ImageProcessingWorkflowMeta(WorkflowMeta):
    """环境图像感知处理工作流元类"""

    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)

        # 注册环境图像感知处理工作流专用的属性模板
        mcs.register_template(cls, 'sensing_locations', list, True,
                            lambda points: all(isinstance(p, (list, tuple)) and len(p) == 3 for p in points),
                            "感知位置列表，每个点为3D坐标 (x, y, z)")

        mcs.register_template(cls, 'image_resolution', str, False,
                            lambda res: isinstance(res, str),
                            "图像分辨率，如'1920x1080'")

        mcs.register_template(cls, 'image_format', str, False,
                            lambda fmt: isinstance(fmt, str),
                            "图像格式，如'jpeg'或'png'")

        return cls

class ImageProcessingWorkflow(Workflow, metaclass=ImageProcessingWorkflowMeta):
    """
    环境图像感知处理工作流，包含感知和计算两个阶段。
    """

    @classmethod
    def get_description(cls):
        """获取工作流类型的描述"""
        return "环境图像感知处理工作流 - 实现无人机感知环境图像和处理数据的流程"

    def __init__(self, env, name, owner, timeout=None,
                 event_names=[], initial_status='idle', callback=None, properties=None):
        # 感知位置列表
        self.sensing_locations = properties.get('sensing_locations', [])
        # 图像参数
        self.image_resolution = properties.get('image_resolution', '1920x1080')
        self.image_format = properties.get('image_format', 'jpeg')
        # 当前感知位置索引
        self.current_location_index = 0
        # 感知到的文件ID列表
        self.collected_file_ids = []
        # 处理后的文件ID列表
        self.processed_file_ids = []

        # 工作流事件
        event_names = [
            'sensing_started', 'sensing_completed',
            'processing_started', 'processing_completed',
            'workflow_completed'
        ]

        super().__init__(
            env=env,
            name=name,
            owner=owner,
            timeout=timeout,
            event_names=event_names,
            initial_status=initial_status,
            callback=callback,
            properties=properties or {}
        )

    def get_details(self):
        """获取工作流详细信息"""
        details = super().get_details()
        details.update({
            'sensing_locations': self.sensing_locations,
            'image_resolution': self.image_resolution,
            'image_format': self.image_format,
            'collected_file_ids': self.collected_file_ids,
            'processed_file_ids': self.processed_file_ids
        })
        return details

    def get_current_suggested_task(self):
        """
        获取当前状态下建议执行的任务

        根据当前状态机状态，动态生成任务信息。

        返回:
            Dict: 任务信息字典
            None: 如果没有找到匹配的任务
        """
        if not self.owner or self.status != WorkflowStatus.RUNNING:
            return None

        current_state = self.status_machine.state

        # 根据当前状态生成相应任务
        if current_state.startswith('sensing_at_location_'):
            # 感知任务
            try:
                location_index = int(current_state.split('_')[-1]) - 1
                if 0 <= location_index < len(self.sensing_locations):
                    location = self.sensing_locations[location_index]
                    return {
                        'component': 'Sensing',
                        'task_class': 'FileCollectTask',
                        'task_name': f'感知位置 {location_index + 1} 的环境图像',
                        'workflow_id': self.id,
                        'target_state':None,
                        'properties': {
                            'file_name': f'image_{self.owner.id}_{location_index + 1}',
                            'file_type': 'image',
                            'file_size': 5120,  # 假设5MB的图像文件
                            'content_type': 'image',
                            'location': location,
                            'sensing_difficulty': 1.0,
                            'resolution': self.image_resolution,
                            'format': self.image_format
                        }
                    }
            except (ValueError, IndexError):
                pass

        elif current_state.startswith('processing_data_at_location_'):
            # 处理任务
            try:
                location_index = int(current_state.split('_')[-1]) - 1
                if 0 <= location_index < len(self.sensing_locations) and location_index < len(self.collected_file_ids):
                    return {
                        'component': 'Computation',
                        'task_class': 'FileComputeTask',
                        'task_name': f'处理位置 {location_index + 1} 的感知数据',
                        'workflow_id': self.id,
                        'target_state':None,
                        'properties': {
                            'file_id': self.collected_file_ids[location_index],
                            'result_file_name': f'processed_image_{self.owner.id}_{location_index + 1}',
                            'result_file_type': 'processed_image'
                        }
                    }
            except (ValueError, IndexError):
                pass

        return None

    def _setup_transitions(self):
        """设置状态机转换规则"""
        # 工作流启动时，转换到第一个感知位置的状态
        self.status_machine.set_start_transition('sensing_at_location_1')

        # 定义感知任务完成的回调
        def on_sensing_completed(event_data):
            # 获取文件ID
            file_id = event_data.get('event_value', {}).get('object_id')
            if file_id:
                # 记录感知到的文件ID
                self.collected_file_ids.append(file_id)

                # 获取当前位置索引
                current_state = self.status_machine.state
                location_index = int(current_state.split('_')[-1]) - 1

                # 触发感知完成事件
                self.env.event_registry.trigger_event(
                    self.id, 'sensing_completed',
                    {
                        'location_index': location_index,
                        'location': self.sensing_locations[location_index],
                        'file_id': file_id,
                        'time': self.env.now
                    }
                )

        # 定义处理任务完成的回调
        def on_processing_completed(event_data):
            # 获取源文件ID和结果文件ID
            file_id = event_data.get('task_properties', {}).get('file_id')
            result_file_id = event_data.get('task_result', {}).get('result_file_id')

            if file_id and result_file_id:
                # 记录处理后的文件ID
                self.processed_file_ids.append(result_file_id)

                # 触发处理完成事件
                self.env.event_registry.trigger_event(
                    self.id, 'processing_completed',
                    {
                        'source_file_id': file_id,
                        'result_file_id': result_file_id,
                        'time': self.env.now
                    }
                )

                # 更新当前位置索引
                self.current_location_index += 1

                # 如果已完成所有位置的感知和处理，触发工作流完成事件
                if self.current_location_index >= len(self.sensing_locations):
                    self.env.event_registry.trigger_event(
                        self.id, 'workflow_completed',
                        {
                            'workflow_id': self.id,
                            'collected_file_ids': self.collected_file_ids,
                            'processed_file_ids': self.processed_file_ids,
                            'time': self.env.now
                        }
                    )

        # 为每个感知位置创建转换规则
        for i, location in enumerate(self.sensing_locations):
            current_state = f'sensing_at_location_{i+1}'

            # 感知完成后转换到处理状态 - 监听possessing_object_added事件
            self.status_machine.add_transition(
                current_state,
                f'processing_data_at_location_{i+1}',
                event_trigger={
                    'source_id': self.owner.id,
                    'event_name': 'possessing_object_added',
                    'operator': TriggerOperator.CUSTOM,
                    'target_value': lambda event_data: event_data.get('object_name', '').startswith('file_') and
                        self._validate_sensed_file(self.env.file_manager.get_file(event_data.get('object_id')))
                },
                callback=on_sensing_completed,
                description=f"当位置{i+1}的感知任务完成并添加图像文件时，转换到数据处理状态"
            )

        # 处理完成后，如果还有感知位置，转换到下一个感知位置；否则完成工作流
        for i in range(len(self.sensing_locations)):
            next_state = f'sensing_at_location_{i+2}' if i+1 < len(self.sensing_locations) else 'completed'

            self.status_machine.add_transition(
                f'processing_data_at_location_{i+1}',
                next_state,
                event_trigger={
                    'source_id': self.owner.id,
                    'event_name': 'possessing_object_added',
                    'operator': TriggerOperator.CUSTOM,
                    'target_value':lambda event_data: event_data.get('object_name', '').startswith('file_') and
                        self._validate_computed_file(self.env.file_manager.get_file(event_data.get('object_id')))
                },
                callback=on_processing_completed,
                description=f"当数据处理任务完成并添加处理后的图像文件且当前位置索引为{i}时，转换到{'下一个感知位置' if i+1 < len(self.sensing_locations) else '完成状态'}"
            )

        # 失败处理
        self.status_machine.add_transition(
            '*',
            'failed',
            event_trigger={
                'source_id': self.id,
                'event_name': 'status_changed',
                'value_key': 'new_status',
                'operator': TriggerOperator.EQUALS,
                'target_value': WorkflowStatus.FAILED
            }
        )


    def _validate_sensed_file(self, file_info):
        """
        验证感知文件是否符合要求

        Args:
            file_info: 文件信息字典
            loc: 位置索引，用于验证位置是否匹配

        Returns:
            bool: 文件是否符合要求
        """
        if not file_info:
            return False

        # 验证文件类型
        if file_info.get('type') != 'image':
            return False

        # 验证文件名格式
        expected_name_prefix = f'image_{self.owner.id}'
        if not file_info.get('name', '').startswith(expected_name_prefix):
            return False

        loc = file_info.get('name', '')[len(expected_name_prefix)+1:]
        if loc.isdigit():
            loc = int(loc) - 1
        else:
            return False

        # 检查感知位置是否匹配
        if 'sensing_location' in file_info:
            sensed_location = file_info.get('sensing_location')
            expected_location = self.sensing_locations[loc]

            # 检查位置是否接近（考虑浮点误差）
            if sensed_location and expected_location:
                distance_threshold = 1e-6
                for i in range(min(len(sensed_location), len(expected_location))):
                    if abs(sensed_location[i] - expected_location[i]) > distance_threshold:
                        return False

        return True

    def _validate_computed_file(self, file_info):
        """
        验证计算处理后的文件是否符合要求

        Args:
            file_info: 文件信息字典

        Returns:
            bool: 文件是否符合要求
        """
        if not file_info:
            return False

        # 验证文件类型
        if file_info.get('type') != 'processed_image':
            return False

        # 验证文件名格式
        expected_name_prefix = f'processed_image_{self.owner.id}'
        if not file_info.get('name', '').startswith(expected_name_prefix):
            return False

        # 验证是否有源文件ID
        if 'source_file_id' not in file_info:
            return False

        loc = file_info.get('name', '')[len(expected_name_prefix)+1:]
        if loc.isdigit():
            loc = int(loc) - 1
        else:
            return False

        # 如果指定了位置索引，验证源文件是否是在该位置感知的
        if loc is not None and self.collected_file_ids:
            # 检查源文件ID是否在已收集的文件列表中
            source_file_id = file_info.get('source_file_id')
            if source_file_id not in self.collected_file_ids:
                return False

            # 获取源文件信息
            source_file = self.env.file_manager.get_file(source_file_id)
            if not source_file:
                return False

            # 验证源文件是否是在指定位置感知的
            if self._validate_sensed_file(source_file):
                self.processed_file_ids.append(file_info.get('id'))
                return True
            return False

        return True


# 使用示例
def create_image_processing_workflow(env, agent, sensing_locations,
                                    image_resolution='1920x1080', image_format='jpeg'):
    """
    创建环境图像感知处理工作流

    Args:
        env: 仿真环境
        agent: 执行感知和处理的代理
        sensing_locations: 感知位置列表，每个位置为 (x, y, z) 坐标
        image_resolution: 图像分辨率，默认为'1920x1080'
        image_format: 图像格式，默认为'jpeg'

    Returns:
        Workflow: 创建的工作流对象
    """
    from airfogsim.core.trigger import TimeTrigger

    workflow = env.create_workflow(
        ImageProcessingWorkflow,
        name=f"环境图像感知处理_{agent.id}",
        owner=agent,
        properties={
            'sensing_locations': sensing_locations,
            'image_resolution': image_resolution,
            'image_format': image_format
        },
        start_trigger=TimeTrigger(env, interval=10),
        max_starts=1
    )

    return workflow