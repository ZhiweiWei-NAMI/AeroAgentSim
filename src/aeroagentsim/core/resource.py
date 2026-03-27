"""
AirFogSim资源(Resource)核心模块

该模块定义了仿真系统中资源的基础类和管理机制。资源是系统中被代理使用的
各种实体，如空域、频率、着陆点等。模块采用泛型设计，支持不同类型的资源
管理。主要内容包括：
1. Resource类：基本资源类，定义资源的通用属性和状态
2. ResourceManager类：资源管理器基类，负责资源的注册、分配和释放
3. 资源分配机制：包括分配查找、记录和释放

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import uuid
from .enums import ResourceStatus, AllocationStatus # 导入 AllocationStatus
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Generic, TypeVar, Callable, Tuple, Any

class Resource:
    """基本资源类"""
    
    def __init__(self, resource_id: str, attributes: Dict = None):
        self.id = resource_id
        self.attributes = attributes or {}
        self.status = ResourceStatus.AVAILABLE  # available, allocated, maintenance
        self.current_allocations = set()  # 当前活跃分配ID集合

    def get_attribute(self, key: str, default) -> Optional[Any]:
        """获取资源属性"""
        return self.attributes.get(key, default)

    def set_status(self, new_status: str):
        """
        直接设置资源的状态。
        警告：谨慎使用，这可能会绕过管理器的状态逻辑。
        主要用于外部事件（如天气）强制更新状态。
        """
        # TODO: Consider adding validation against ResourceStatus enum if available
        old_status = self.status
        if old_status != new_status:
            self.status = new_status
            # Optionally trigger a status change event here if needed globally
            # print(f"DEBUG Resource {self.id} status set to {new_status} (was {old_status})")
            return True
        return False

# 资源类型泛型
R = TypeVar('R')

class ResourceManager(Generic[R]):
    """
    资源管理器基类
    
    管理单一类型资源的注册、分配和释放
    
    泛型参数R表示管理的资源类型
    """
    
    def __init__(self, env=None):
        # 资源存储
        self.resources: Dict[str, R] = {}
        
        # 分配记录
        self.allocations: Dict[str, Dict] = {}
        self.resource_allocations: Dict[str, Dict[str]] = {}
        self.user_allocations: Dict[str, List[str]] = {}
        
        # 环境引用(用于时间等)
        self.env = env
    
    #----------------------------------------------------
    # 1. 资源生命周期管理
    #----------------------------------------------------
    
    def register_resource(self, resource: R) -> bool:
        """注册资源到管理器"""
        # 检查资源是否已存在
        if resource.id in self.resources:
            return False
            
        # 添加资源
        self.resources[resource.id] = resource
        self.resource_allocations[resource.id] = {}
        
        return True
        
    def unregister_resource(self, resource_id: str) -> bool:
        """从管理器移除资源"""
        # 检查资源是否存在
        if resource_id not in self.resources:
            return False
            
        # 检查资源是否有活跃分配
        active_allocations = [
            a_id for a_id in self.resource_allocations.get(resource_id, {})
            if self.allocations[a_id]['status'] == AllocationStatus.ACTIVE
        ]
        
        if active_allocations:
            return False  # 有活跃分配，不能移除
            
        # 移除资源
        del self.resources[resource_id]
        if resource_id in self.resource_allocations:
            del self.resource_allocations[resource_id]
            
        return True
    
    def update_resource(self, resource_id: str, attributes: Dict) -> bool:
        """更新资源属性"""
        if resource_id not in self.resources:
            return False
            
        # 更新资源属性
        resource = self.resources[resource_id]
        if hasattr(resource, 'attributes'):
            resource.attributes.update(attributes)
        else:
            # 直接设置属性
            for key, value in attributes.items():
                setattr(resource, key, value)
                
        return True
    
    #----------------------------------------------------
    # 2. 资源分配管理
    #----------------------------------------------------
    
    def allocate_resource(self, user_id: str, requirements: Dict) -> Tuple[Optional[str], Optional[str]]:
        """
        分配符合要求的资源
        
        Args:
            user_id: 用户ID
            requirements: 资源需求
            
        Returns:
            (allocation_id, resource_id) 或 (None, None)
        """
        # 查找符合要求的资源
        suitable_resources = self.find_resources(requirements)
        
        if not suitable_resources:
            return None, None
            
        # 选择第一个符合要求的资源
        resource = suitable_resources[0]
        resource_id = resource.id
        
        # 检查资源是否可以分配
        if hasattr(resource, 'status') and resource.status != ResourceStatus.AVAILABLE:
            return None, None
            
        # 创建分配
        allocation_id = f"alloc_{uuid.uuid4().hex[:8]}"
        
        # 记录分配信息
        allocation_info = {
            'id': allocation_id,
            'resource_id': resource_id,
            'user_id': user_id,
            'requirements': requirements,
            'start_time': self._get_current_time(),
            'status': AllocationStatus.ACTIVE
        }
        
        self.allocations[allocation_id] = allocation_info
        
        # 更新索引
        self.resource_allocations[resource_id].append(allocation_id)
        self.user_allocations.setdefault(user_id, []).append(allocation_id)
        
        # 更新资源状态
        if hasattr(resource, 'status'):
            resource.status = ResourceStatus.FULLY_ALLOCATED # 使用枚举
            
        # 记录分配到资源
        if hasattr(resource, 'current_allocations'):
            resource.current_allocations.add(allocation_id)
            
        return allocation_id, resource_id
    
    def release_allocation(self, allocation_id: str) -> bool:
        """释放资源分配"""
        # 检查分配是否存在
        if allocation_id not in self.allocations:
            return False
            
        allocation = self.allocations[allocation_id]
        
        # 检查分配是否已释放
        if allocation['status'] != AllocationStatus.ACTIVE:
            return False
            
        resource_id = allocation['resource_id']
        
        # 检查资源是否存在
        if resource_id not in self.resources:
            return False
            
        resource = self.resources[resource_id]
        
        # 更新资源状态
        if hasattr(resource, 'status'):
            # 检查是否还有其他活跃分配
            other_active = any(
                self.allocations[a_id]['status'] == AllocationStatus.ACTIVE
                for a_id in self.resource_allocations[resource_id]
                if a_id != allocation_id
            )
            
            if not other_active:
                resource.status = ResourceStatus.AVAILABLE
                
        # 从资源的分配记录中移除
        if hasattr(resource, 'current_allocations'):
            if allocation_id in resource.current_allocations:
                resource.current_allocations.remove(allocation_id)
                
        # 更新分配状态
        allocation['status'] = AllocationStatus.RELEASED
        allocation['end_time'] = self._get_current_time()
        
        return True
    
    #----------------------------------------------------
    # 3. 资源查询
    #----------------------------------------------------
    
    def find_resources(self, requirements: Dict) -> List[R]:
        pass

    def get_user_allocations(self, user_id: str) -> List[Dict]:
        """获取用户的所有分配"""
        allocation_ids = self.user_allocations.get(user_id, [])
        return [self.allocations[alloc_id] for alloc_id in allocation_ids if alloc_id in self.allocations]
    
    def get_resource_allocations(self, resource_id: str) -> List[Dict]:
        """获取资源的所有分配"""
        allocation_ids = self.resource_allocations.get(resource_id, [])
        return [self.allocations[alloc_id] for alloc_id in allocation_ids if alloc_id in self.allocations]
    
    def get_allocation(self, allocation_id: str) -> Optional[Dict]:
        """获取分配信息"""
        return self.allocations.get(allocation_id)
    
    #----------------------------------------------------
    # 辅助方法
    #----------------------------------------------------
    
    def _get_current_time(self) -> float:
        """获取当前时间"""
        return self.env.now