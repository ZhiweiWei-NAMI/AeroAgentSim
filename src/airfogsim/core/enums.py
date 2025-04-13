"""
AirFogSim枚举(Enums)核心模块

该模块定义了仿真系统中使用的各种枚举类型，包括任务状态、工作流状态、
代理状态、资源状态等。模块还提供了可JSON序列化的枚举基类和辅助工具，
使枚举值可以方便地在系统内部传递和持久化。主要内容包括：
1. JSONSerializableEnum：可序列化的枚举基类
2. 各种状态枚举：TaskStatus, WorkflowStatus, AgentStatus等
3. 资源相关枚举：ResourceType, AllocationStatus等
4. 触发器相关枚举：TriggerType, TriggerOperator等

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import json
from enum import Enum, auto
from json import JSONEncoder

def get_enum_name_by_value(enum_class, value):
    enum_dict = {enum_constant.value: enum_constant.name for enum_constant in enum_class}
    return enum_dict.get(value)



class ContractStatus(Enum):
    """合约状态枚举"""
    PENDING = 'con_pending'    # 合约已创建但尚未被接受
    ACTIVE = 'con_active'     # 合约已被接受，正在执行
    COMPLETED = 'con_completed'  # 合约已成功完成
    FAILED = 'con_failed'     # 合约执行失败
    CANCELED = 'con_canceled'   # 合约被取消

class EnumJSONEncoder(JSONEncoder):
    """用于序列化枚举类型的JSON编码器"""
    def default(self, obj):
        if isinstance(obj, JSONSerializableEnum):
            return obj.to_json()
        return super().default(obj)

class JSONSerializableEnum(Enum):
    """
    可JSON序列化的枚举基类
    提供了to_json和from_json方法用于序列化和反序列化
    """
    def __str__(self):
        return self.name

    def to_json(self):
        """返回可JSON序列化的表示"""
        return {
            "name": self.name,
            "value": self.value
        }

    def __json__(self):
        """支持json.dumps直接序列化"""
        return self.to_json()

    @classmethod
    def from_json(cls, data):
        if isinstance(data, dict):
            # 如果是字典格式，尝试通过名称或值恢复
            if "name" in data:
                for member in cls:
                    if member.value == data["value"]:
                        return member
        elif isinstance(data, str):
            # 如果是字符串，尝试作为名称恢复
            return cls[data]
        elif isinstance(data, int):
            # 如果是整数，尝试作为值恢复
            return cls(data)

        # 如果无法恢复，抛出异常
        raise ValueError(f"无法从{data}恢复{cls.__name__}枚举值")

class TaskStatus(JSONSerializableEnum):
    """任务状态枚举"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    SUSPENDED = "SUSPENDED"

class WorkflowStatus(JSONSerializableEnum):
    PENDING = "PENDING"    # 工作流已创建但未开始执行
    RUNNING = "RUNNING"    # 工作流正在执行中
    COMPLETED = "COMPLETED"   # 工作流已成功完成
    FAILED = "FAILED"     # 工作流QoS未满足而失败
    TIMEOUT = "TIMEOUT"    # 工作流执行超时
    CANCELED = "CANCELED"   # 工作流被取消
    SUSPENDED = "SUSPENDED"       # 工作流被暂停/中止

class AgentStatus(JSONSerializableEnum):
    """Agent状态枚举"""
    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    MAINTENANCE = "MAINTENANCE"
    OFFLINE = "OFFLINE"
    UNAVAILABLE = "UNAVAILABLE"

class ResourceStatus(JSONSerializableEnum):
    """资源状态枚举"""
    AVAILABLE = "AVAILABLE"
    PARTIALLY_ALLOCATED = "PARTIALLY_ALLOCATED"
    FULLY_ALLOCATED = "FULLY_ALLOCATED"
    MAINTENANCE = "MAINTENANCE"
    UNAVAILABLE = "UNAVAILABLE"
    UNAVAILABLE_WEATHER = "UNAVAILABLE_WEATHER" # Added for weather impact

class UnitStatus(JSONSerializableEnum):
    AVAILABLE = "AVAILABLE"
    ALLOCATED = "ALLOCATED"
    MAINTENANCE = "MAINTENANCE"
    FAULTY = "FAULTY"

class ResourceType(JSONSerializableEnum):
    CONTINUOUS = "CONTINUOUS"
    DISCRETE = "DISCRETE"
    BANDWIDTH = "BANDWIDTH"

class EventStatus(JSONSerializableEnum):
    """事件状态枚举"""
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    PENDING = "PENDING"

class TriggerType(JSONSerializableEnum):
    """触发器类型枚举"""
    EVENT = "event"           # 基于事件的触发
    STATE = "state"           # 基于状态的触发
    TIME = "time"             # 基于时间的触发
    COMPOSITE = "composite"   # 组合触发器

class TriggerOperator(JSONSerializableEnum):
    """组合触发器的操作符"""
    AND = "and"  # 所有子触发器都满足
    OR = "or"    # 任一子触发器满足
    EQUALS = "equals"  # 触发器的结果相等
    NOT_EQUALS = "not_equals"  # 触发器的结果不相等
    GREATER_THAN = "greater_than"  # 触发器的结果大于
    LESS_THAN = "less_than"  # 触发器的结果小于
    GREATER_EQUAL = "greater_equal"  # 触发器的结果大于等于
    LESS_EQUAL = "less_equal"  # 触发器的结果小于等于
    CONTAINS = "contains"  # 触发器的结果包含
    NOT_CONTAINS = "not_contains"  # 触发器的结果不包含
    CUSTOM = "custom"  # 自定义函数

class TaskPriority(JSONSerializableEnum):
    """任务优先级枚举

    定义了任务的优先级级别，从低到高依次为：LOW、NORMAL、HIGH、CRITICAL。
    用于在资源竞争时决定任务的执行顺序和抢占策略。
    """
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3

    @classmethod
    def from_string(cls, priority_str):
        """从字符串转换为优先级枚举"""
        priority_map = {
            'low': cls.LOW,
            'normal': cls.NORMAL,
            'high': cls.HIGH,
            'critical': cls.CRITICAL
        }
        return priority_map.get(priority_str.lower(), cls.NORMAL)

    def __lt__(self, other):
        if self.__class__ is other.__class__:
            return self.value < other.value
        return NotImplemented

    def __gt__(self, other):
        if self.__class__ is other.__class__:
            return self.value > other.value
        return NotImplemented

    def __le__(self, other):
        if self.__class__ is other.__class__:
            return self.value <= other.value
        return NotImplemented

    def __ge__(self, other):
        if self.__class__ is other.__class__:
            return self.value >= other.value
        return NotImplemented

class AllocationStatus(JSONSerializableEnum):
    """资源分配状态枚举，典型的预留工作流程是：
        1. 客户端请求预留资源（调用reserve方法）
        2. 系统检查未来时间窗口的可用性
        3. 如果可用，创建一个PENDING状态的分配；如果不可用，创建一个REJECTED状态的分配
        4. 客户端在需要时确认使用（可能通过一个confirm_reservation方法，将状态从PENDING改为ACTIVE）
        5. 如果客户端不再需要，可以取消预留 PENDING -> CANCELLED
        6. 如果时间窗口过期，取消预留 PENDING -> EXPIRED
        7. 完成使用后，释放资源 ACTIVE -> RELEASED
    """
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    PENDING = "PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

# 示例代码：打印枚举成员及其值
# print(list(ResourceStatus))
# print(list(UnitStatus))
# print(list(AllocationStatus))
