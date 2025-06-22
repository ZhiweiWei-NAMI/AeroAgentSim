"""
AirFogSim文件管理器模块

该模块定义了文件(File)管理器，负责文件的创建、注册、跟踪和查询。
主要功能包括：
1. 文件创建和注册
2. 文件位置和状态跟踪
3. 文件查询接口
4. 与空间管理器集成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from typing import Dict, List, Optional, Tuple, Any
import uuid
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

class FileManager:
    """
    文件管理器

    负责管理系统中的文件(File)，包括创建、注册、跟踪和查询文件。
    """

    def __init__(self, env):
        """
        初始化文件管理器

        Args:
            env: 仿真环境
        """
        self.env = env
        self.files = {}  # 存储所有文件，格式：file_id -> file_info
        self.file_owners = {}  # 存储文件的所有者，格式：file_id -> agent_id
        self.file_status = {}  # 存储文件状态，格式：file_id -> status
        self.file_records = {} # 存储文件记录，格式：file_id -> record

        # 文件状态枚举
        self.STATUS_CREATED = 'created'  # 已创建
        self.STATUS_TRANSFERRING = 'transferring'  # 正在传输
        self.STATUS_TRANSFERRED = 'transferred'  # 已传输
        self.STATUS_COMPUTING = 'computing'  # 正在计算
        self.STATUS_COMPUTED = 'computed'  # 已计算

        # 确保事件存在
        self.env.event_registry.get_event(self.id, 'file_created')
        self.env.event_registry.get_event(self.id, 'file_transfer_started')
        self.env.event_registry.get_event(self.id, 'file_transferred')
        self.env.event_registry.get_event(self.id, 'file_computing_started')
        self.env.event_registry.get_event(self.id, 'file_computed')
        self.env.event_registry.get_event(self.id, 'file_location_changed')

        # 如果环境有空间管理器，设置引用
        self.airspace_manager = getattr(env, 'airspace_manager', None)

        # 通过event_registry监听所有代理的possessing_object事件
        self.env.event_registry.subscribe(
            "*",  # 使用通配符监听所有代理
            "possessing_object_added",
            f"{self.id}_object_added_monitor",
            self._on_possessing_object_added
        )

        self.env.event_registry.subscribe(
            "*",  # 使用通配符监听所有代理
            "possessing_object_removed",
            f"{self.id}_object_removed_monitor",
            self._on_possessing_object_removed
        )

    @property
    def id(self):
        """获取管理器ID"""
        return 'file_manager'

    def create_file(self, owner_agent_id, file_name, file_size, file_type, content=None, properties: Dict[str, Any] = None) -> str:
        """
        创建新文件

        Args:
            owner_agent_id: 文件所有者的代理ID
            file_name: 文件名称
            file_size: 文件大小（KB）
            file_type: 文件类型
            content: 文件内容（可以是任何形式的数据）
            properties: 文件附加属性

        Returns:
            str: 文件ID
        """
        properties = properties or {}

        # 生成唯一ID
        file_id = properties.get('id') or f"file_{uuid.uuid4().hex[:8]}"

        # 确保ID唯一
        if file_id in self.files:
            raise ValueError(f"文件ID {file_id} 已存在")

        # 创建文件信息
        file_info = {
            'id': file_id,
            'name': file_name,
            'size': file_size,
            'type': file_type,
            'content': content,
            'create_time': self.env.now,
            'owner_agent_id': owner_agent_id,
            **properties
        }

        # 存储文件信息
        self.files[file_id] = file_info
        self.file_owners[file_id] = owner_agent_id
        self.file_status[file_id] = self.STATUS_CREATED

        # 触发文件创建事件
        self.env.event_registry.trigger_event(
            self.id, 'file_created',
            {
                'file_id': file_id,
                'owner_agent_id': owner_agent_id,
                'properties': file_info,
                'time': self.env.now
            }
        )
        logger.info(f"时间：{self.env.now}: 文件 {file_id} ({file_name}) 已创建")
        return file_id

    def register_file(self, file_id: str, properties: Dict[str, Any]) -> bool:
        """
        注册已有文件

        Args:
            file_id: 文件ID
            properties: 文件属性

        Returns:
            bool: 是否成功注册
        """
        if file_id in self.files:
            logger.warning(f"警告: 文件 {file_id} 已存在，将更新属性")
            self.files[file_id].update(properties)
        else:
            self.files[file_id] = {
                'id': file_id,
                'create_time': self.env.now,
                **properties
            }
            self.file_status[file_id] = self.STATUS_CREATED
            if 'owner_agent_id' in properties:
                self.file_owners[file_id] = properties['owner_agent_id']

        return True

    def mark_file_transfer_started(self, file_id: str, source_agent_id: str, target_agent_id: str) -> bool:
        """
        标记文件开始传输

        Args:
            file_id: 文件ID
            source_agent_id: 源代理ID
            target_agent_id: 目标代理ID

        Returns:
            bool: 是否成功标记
        """
        if file_id not in self.files:
            logger.warning(f"警告: 文件 {file_id} 不存在，无法标记为开始传输")
            return False

        if self.file_status.get(file_id) not in [self.STATUS_CREATED, self.STATUS_COMPUTED, self.STATUS_TRANSFERRED]:
            logger.warning(f"警告: 文件 {file_id} 当前状态为 {self.file_status.get(file_id)}，不能标记为开始传输")
            return False

        # 更新状态
        self.file_status[file_id] = self.STATUS_TRANSFERRING

        # 更新文件信息
        self.files[file_id]['transfer_start_time'] = self.env.now
        self.files[file_id]['source_agent_id'] = source_agent_id
        self.files[file_id]['target_agent_id'] = target_agent_id

        # 触发文件开始传输事件
        self.env.event_registry.trigger_event(
            self.id, 'file_transfer_started',
            {
                'file_id': file_id,
                'source_agent_id': source_agent_id,
                'target_agent_id': target_agent_id,
                'time': self.env.now
            }
        )

        logger.info(f"时间 {self.env.now}: 文件 {file_id} 开始从代理 {source_agent_id} 传输到代理 {target_agent_id}")
        return True

    def mark_file_transferred(self, file_id: str, target_agent_id: str, location: Optional[Tuple[float, float, float]] = None) -> bool:
        """
        标记文件已传输完成

        Args:
            file_id: 文件ID
            target_agent_id: 目标代理ID
            location: 传输位置 (x, y, z)，可选

        Returns:
            bool: 是否成功标记
        """
        if file_id not in self.files:
            logger.warning(f"警告: 文件 {file_id} 不存在，无法标记为已传输")
            return False

        if self.file_status.get(file_id) != self.STATUS_TRANSFERRING:
            logger.warning(f"警告: 文件 {file_id} 当前状态为 {self.file_status.get(file_id)}，不能标记为已传输")
            return False

        # 更新状态和所有者
        self.file_status[file_id] = self.STATUS_TRANSFERRED
        self.file_owners[file_id] = target_agent_id

        # 更新文件信息
        self.files[file_id]['transfer_end_time'] = self.env.now
        if location:
            self.files[file_id]['transfer_location'] = location

        # 触发文件传输完成事件
        self.env.event_registry.trigger_event(
            self.id, 'file_transferred',
            {
                'file_id': file_id,
                'target_agent_id': target_agent_id,
                'location': location,
                'time': self.env.now
            }
        )

        logger.info(f"时间 {self.env.now}: 文件 {file_id} 已传输到代理 {target_agent_id}")
        return True

    def mark_file_computing_started(self, file_id: str, agent_id: str) -> bool:
        """
        标记文件开始计算

        Args:
            file_id: 文件ID
            agent_id: 执行计算的代理ID

        Returns:
            bool: 是否成功标记
        """
        if file_id not in self.files:
            logger.warning(f"警告: 文件 {file_id} 不存在，无法标记为开始计算")
            return False

        if self.file_status.get(file_id) not in [self.STATUS_CREATED, self.STATUS_TRANSFERRED]:
            logger.warning(f"警告: 文件 {file_id} 当前状态为 {self.file_status.get(file_id)}，不能标记为开始计算")
            return False

        # 更新状态
        self.file_status[file_id] = self.STATUS_COMPUTING

        # 更新文件信息
        self.files[file_id]['computing_start_time'] = self.env.now
        self.files[file_id]['computing_agent_id'] = agent_id

        # 触发文件开始计算事件
        self.env.event_registry.trigger_event(
            self.id, 'file_computing_started',
            {
                'file_id': file_id,
                'agent_id': agent_id,
                'time': self.env.now
            }
        )

        logger.info(f"时间 {self.env.now}: 文件 {file_id} 开始在代理 {agent_id} 上计算")
        return True

    def mark_file_computed(self, file_id: str, result_file_id: str) -> bool:
        """
        标记文件已计算完成

        Args:
            file_id: 源文件ID
            result_file_id: 计算结果文件ID

        Returns:
            bool: 是否成功标记
        """
        if file_id not in self.files:
            logger.warning(f"警告: 文件 {file_id} 不存在，无法标记为已计算")
            return False

        if self.file_status.get(file_id) != self.STATUS_COMPUTING:
            logger.warning(f"警告: 文件 {file_id} 当前状态为 {self.file_status.get(file_id)}，不能标记为已计算")
            return False

        # 更新状态
        self.file_status[file_id] = self.STATUS_COMPUTED

        # 更新文件信息
        self.files[file_id]['computing_end_time'] = self.env.now
        self.files[file_id]['result_file_id'] = result_file_id

        # 触发文件计算完成事件
        self.env.event_registry.trigger_event(
            self.id, 'file_computed',
            {
                'file_id': file_id,
                'result_file_id': result_file_id,
                'time': self.env.now
            }
        )

        logger.info(f"时间 {self.env.now}: 文件 {file_id} 已计算完成，生成结果文件 {result_file_id}")
        return True

    def get_file(self, file_id: str) -> Optional[Dict]:
        """
        获取文件信息

        Args:
            file_id: 文件ID

        Returns:
            Dict: 文件信息，如果不存在则返回None
        """
        return self.files.get(file_id)

    def get_file_location(self, file_id: str) -> Optional[Tuple[float, float, float]]:
        """
        获取文件位置

        Args:
            file_id: 文件ID

        Returns:
            Tuple[float, float, float]: 文件位置，如果不存在则返回None
        """
        # 获取当前的所有者，然后查询其位置
        owner_id = self.file_owners.get(file_id)
        if owner_id and self.airspace_manager:
            return self.airspace_manager.get_agent_position(owner_id)
        return None

    def get_file_status(self, file_id: str) -> Optional[str]:
        """
        获取文件状态

        Args:
            file_id: 文件ID

        Returns:
            str: 文件状态，如果不存在则返回None
        """
        return self.file_status.get(file_id)

    def get_file_owner(self, file_id: str) -> Optional[str]:
        """
        获取文件所有者

        Args:
            file_id: 文件ID

        Returns:
            str: 所有者ID，如果不存在则返回None
        """
        return self.file_owners.get(file_id)

    def get_all_files(self) -> Dict[str, Dict]:
        """
        获取所有文件

        Returns:
            Dict[str, Dict]: 文件ID到文件信息的映射
        """
        return self.files.copy()

    def get_files_by_status(self, status: str) -> Dict[str, Dict]:
        """
        获取指定状态的文件

        Args:
            status: 文件状态

        Returns:
            Dict[str, Dict]: 文件ID到文件信息的映射
        """
        result = {}
        for file_id, file_status in self.file_status.items():
            if file_status == status:
                result[file_id] = self.files[file_id]
        return result

    def get_files_by_owner(self, agent_id: str) -> Dict[str, Dict]:
        """
        获取指定代理拥有的文件

        Args:
            agent_id: 代理ID

        Returns:
            Dict[str, Dict]: 文件ID到文件信息的映射
        """
        result = {}
        for file_id, owner_id in self.file_owners.items():
            if owner_id == agent_id:
                result[file_id] = self.files[file_id]
        return result

    def get_files_by_type(self, file_type: str) -> Dict[str, Dict]:
        """
        获取指定类型的文件

        Args:
            file_type: 文件类型

        Returns:
            Dict[str, Dict]: 文件ID到文件信息的映射
        """
        result = {}
        for file_id, file_info in self.files.items():
            if file_info.get('type') == file_type:
                result[file_id] = file_info
        return result

    def _on_possessing_object_added(self, event_data):
        """
        处理代理添加possessing_object事件

        Args_id、object_name和object_id
        """
        agent_id = event_data.get('agent_id')
        object_name = event_data.get('object_name')
        object_id = event_data.get('object_id')

        # 只关注file类型的对象
        if object_name.startswith('file_') and object_id:
            # 如果file已在系统中注册
            if object_id in self.files:
                # 更新文件所有者
                self.file_owners[object_id] = agent_id

                # 触发文件位置变更事件
                self.env.event_registry.trigger_event(
                    self.id, 'file_location_changed',
                    {
                        'file_id': object_id,
                        'agent_id': agent_id,
                        'time': self.env.now
                    }
                )

    def _on_possessing_object_removed(self, event_data):
        """
        处理代理移除possessing_object事件

        含agent_id、object_name和object_id
        """
        agent_id = event_data.get('agent_id')
        object_name = event_data.get('object_name')
        object_id = event_data.get('object_id')
        target_agent_id = event_data.get('target_agent_id')

        # 只关注file类型的对象
        if object_name.startswith('file_') and object_id:
            if object_id in self.files and target_agent_id:
                # 如果是传输状态，可能需要更新为已传输
                if self.file_status.get(object_id) == self.STATUS_TRANSFERRING:
                    position = None
                    if self.airspace_manager:
                        position = self.airspace_manager.get_agent_position(agent_id)
                    self.mark_file_transferred(object_id, target_agent_id, position)