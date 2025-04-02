# core/resource.py

import uuid
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Generic, TypeVar, Callable, Tuple

class Resource:
    """基本资源类"""
    
    def __init__(self, resource_id: str, attributes: Dict = None):
        self.id = resource_id
        self.attributes = attributes or {}
        self.status = "available"  # available, allocated, maintenance
        self.current_allocations = set()  # 当前活跃分配ID集合


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
        self.resource_allocations: Dict[str, List[str]] = {}
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
        self.resource_allocations[resource.id] = []
        
        return True
        
    def unregister_resource(self, resource_id: str) -> bool:
        """从管理器移除资源"""
        # 检查资源是否存在
        if resource_id not in self.resources:
            return False
            
        # 检查资源是否有活跃分配
        active_allocations = [
            a_id for a_id in self.resource_allocations.get(resource_id, [])
            if self.allocations[a_id]['status'] == 'active'
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
        if hasattr(resource, 'status') and resource.status != 'available':
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
            'status': 'active'
        }
        
        self.allocations[allocation_id] = allocation_info
        
        # 更新索引
        self.resource_allocations[resource_id].append(allocation_id)
        self.user_allocations.setdefault(user_id, []).append(allocation_id)
        
        # 更新资源状态
        if hasattr(resource, 'status'):
            resource.status = 'allocated'
            
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
        if allocation['status'] != 'active':
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
                self.allocations[a_id]['status'] == 'active' 
                for a_id in self.resource_allocations[resource_id]
                if a_id != allocation_id
            )
            
            if not other_active:
                resource.status = 'available'
                
        # 从资源的分配记录中移除
        if hasattr(resource, 'current_allocations'):
            if allocation_id in resource.current_allocations:
                resource.current_allocations.remove(allocation_id)
                
        # 更新分配状态
        allocation['status'] = 'released'
        allocation['end_time'] = self._get_current_time()
        
        return True
    
    #----------------------------------------------------
    # 3. 资源查询
    #----------------------------------------------------
    
    def find_resources(self, requirements: Dict) -> List[R]:
        raise NotImplementedError("find_resources method must be implemented in subclass")

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