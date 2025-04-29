"""
AirFogSim触发器(Trigger)核心模块

该模块实现了仿真系统中的触发器机制，用于在特定条件满足时执行相应的操作。
触发器是工作流和自动化任务的核心组件，支持多种触发方式和组合条件。主要内容包括：
1. Trigger基类：定义触发器的通用属性和行为
2. EventTrigger：基于事件的触发器，监听特定事件并在满足条件时触发
3. StateTrigger：基于代理状态的触发器，监听状态变化并在满足条件时触发
4. TimeTrigger：基于时间的触发器，在特定时间点或间隔触发
5. CompositeTrigger：组合触发器，将多个触发器组合成复杂的触发条件

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""
import uuid
from airfogsim.core.enums import TriggerOperator, TriggerType
from typing import Dict, Any, Optional, Callable, List, Union, Tuple
import simpy
import time as py_time
from datetime import datetime, timedelta
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)


def get_nested_value(data: Dict, key_path: str, default=None):
    """
    从嵌套字典中获取值，支持点分隔的路径

    Args:
        data: 字典数据
        key_path: 点分隔的键路径，如 'data.position'
        default: 如果路径不存在，返回的默认值

    Returns:
        找到的值或默认值
    """
    if not isinstance(data, dict) or not key_path:
        return default

    parts = key_path.split('.')
    current = data

    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]

    return current


class Trigger:
    """触发器基类"""

    def __init__(self, env, trigger_type: TriggerType, name: Optional[str] = None):
        self.env = env
        self.type = trigger_type
        self.id = f"trigger_{uuid.uuid4().hex[:8]}"
        self.name = name or self.id
        self.callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self.is_active = False
        self.trigger_process = None
        self.last_triggered_time = None
        self.trigger_count = 0
        self.max_triggers = None
        self.not_to_deactive = False

    def add_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """添加触发时的回调函数"""
        if callable(callback):
            self.callbacks.append(callback)
        return self

    def set_max_triggers(self, max_count: int):
        """设置最大触发次数"""
        self.max_triggers = max_count
        return self

    def activate(self):
        """激活触发器"""
        if not self.is_active:
            self.is_active = True
            self.trigger_process = self.env.process(self._monitor())
        return self

    def deactivate(self):
        """停用触发器"""
        if self.is_active and self.trigger_process and self.trigger_process.is_alive:
            self.trigger_process.interrupt()
            self.is_active = False
        return self

    def has_reached_max(self):
        """检查是否达到最大触发次数"""
        return self.max_triggers is not None and self.trigger_count >= self.max_triggers

    def _schedule_deactivation(self):
        """安排触发器停用，避免进程自我中断"""
        self.env.process(self._delayed_deactivate())

    def _delayed_deactivate(self):
        """延迟停用触发器"""
        yield self.env.timeout(0)  # 在下一个时间步停用
        self.deactivate()

    def _trigger(self, context: Dict[str, Any] = None):
        """触发回调函数"""
        if not self.is_active:
            return False

        context = context or {}
        context.update({
            'trigger_id': self.id,
            'trigger_name': self.name,
            'trigger_type': self.type.value,
            'time': self.env.now
        })

        self.last_triggered_time = self.env.now
        self.trigger_count += 1


        for callback in self.callbacks:
            try:
                callback(context)
            except Exception as e:
                logger.error(f"时间 {self.env.now}: 触发器 {self.name} 回调执行错误: {e}")

        # 停用，直到workflow重新激活
        if not self.not_to_deactive:
            self._schedule_deactivation()
        return True

    def _monitor(self):
        """监控条件并在满足时触发回调（由子类实现）"""
        raise NotImplementedError("子类必须实现_monitor方法")


class EventTrigger(Trigger):
    """基于事件及其状态的触发器，如数据传输成功后传输的数据量判断是否成功"""

    def __init__(self, env, source_id: str, event_name: str,
                 value_key: Optional[str] = None,
                 operator: Optional[TriggerOperator] = None,
                 target_value: Any = None,
                 name: Optional[str] = None):
        super().__init__(env, TriggerType.EVENT, name)
        self.source_id = source_id
        self.event_name = event_name
        self.value_key = value_key
        self.operator = operator
        self.target_value = target_value
        self.subscription_id = None

    def _monitor(self):
        """监控事件并在满足条件时触发回调"""
        try:
            # 订阅事件
            self.subscription_id = f"{self.id}_sub"
            self.env.event_registry.subscribe(
                self.source_id,
                self.event_name,
                self.subscription_id,
                lambda ev: self._handle_event(ev)
            )

            # 等待直到被中断
            while True:
                yield self.env.timeout(float('inf'))

        except simpy.Interrupt:
            # 触发器被停用
            pass
        finally:
            # 清理订阅
            if self.subscription_id:
                try:
                    self.env.event_registry.unsubscribe(
                        self.source_id,
                        self.event_name,
                        self.subscription_id
                    )
                except:
                    pass

    def _check_condition(self, event_value):
        """使用运算符检查条件"""
        # 如果没有指定运算符，则总是满足条件
        if not self.operator:
            return True

        # 如果指定了value_key，则从事件值中提取
        # 使用get_nested_value支持嵌套路径
        value = get_nested_value(event_value, self.value_key) if self.value_key else event_value

        if self.operator == TriggerOperator.EQUALS:
            return value == self.target_value
        elif self.operator == TriggerOperator.NOT_EQUALS:
            return value != self.target_value
        elif self.operator == TriggerOperator.GREATER_THAN:
            return value > self.target_value
        elif self.operator == TriggerOperator.LESS_THAN:
            return value < self.target_value
        elif self.operator == TriggerOperator.GREATER_EQUAL:
            return value >= self.target_value
        elif self.operator == TriggerOperator.LESS_EQUAL:
            return value <= self.target_value
        elif self.operator == TriggerOperator.CONTAINS:
            return self.target_value in value if hasattr(value, '__contains__') else False
        elif self.operator == TriggerOperator.NOT_CONTAINS:
            return self.target_value not in value if hasattr(value, '__contains__') else True
        elif self.operator == TriggerOperator.CUSTOM:
            # 使用自定义函数进行条件判断
            if callable(self.target_value):
                try:
                    return self.target_value(value)
                except Exception as e:
                    logger.error(f"时间 {self.env.now}: 触发器 {self.name} 自定义条件函数执行错误: {e}")
                    return False
            else:
                logger.error(f"时间 {self.env.now}: 触发器 {self.name} 使用CUSTOM运算符但target_value不是可调用对象")
                return False
        else:
            raise ValueError(f"不支持的运算符: {self.operator}")

    def _handle_event(self, event_value):
        """处理接收到的事件"""
        # 检查条件
        if not self._check_condition(event_value):
            return

        # 触发回调
        context = {
            'source_id': self.source_id,
            'event_name': self.event_name,
            'event_value': event_value,
            'value_key': self.value_key,
            'operator': self.operator.value if self.operator else None,
            'target_value': str(self.target_value) if self.operator == TriggerOperator.CUSTOM else self.target_value
        }
        self._trigger(context)


class StateTrigger(Trigger):
    """基于agent单一状态的触发器，对于多种状态联合监听，可以直接使用composite trigger"""

    def __init__(self, env, agent_id: str, state_key: str,
                 operator: TriggerOperator = TriggerOperator.EQUALS,
                 target_value: Any = None,
                 name: Optional[str] = None):
        super().__init__(env, TriggerType.STATE, name)
        self.agent_id = agent_id
        self.state_key = state_key
        self.operator = operator
        self.target_value = target_value
        self.last_value = None
        self.subscription_id = None

    def _monitor(self):
        """监听agent的state_changed事件"""
        try:
            # 获取代理
            agent = None
            for a in self.env.agents.values():
                if a.id == self.agent_id:
                    agent = a
                    break

            if not agent:
                logger.error(f"时间 {self.env.now}: 触发器 {self.name} 找不到代理 {self.agent_id}")
                return

            # 初始化上次值
            self.last_value = agent.get_state(self.state_key)

            # 订阅agent的state_changed事件
            self.subscription_id = f"{self.id}_sub"
            self.env.event_registry.subscribe(
                self.agent_id,
                'state_changed',
                self.subscription_id,
                lambda event: self._handle_state_change(event)
            )

            # 等待直到被中断
            while True:
                yield self.env.timeout(float('inf'))

        except simpy.Interrupt:
            # 触发器被停用
            pass
        finally:
            # 清理订阅
            if self.subscription_id:
                try:
                    self.env.event_registry.unsubscribe(
                        self.agent_id,
                        'state_changed',
                        self.subscription_id
                    )
                except:
                    pass

    def _check_condition(self, value):
        """使用运算符检查条件"""
        if self.operator == TriggerOperator.EQUALS:
            return value == self.target_value
        elif self.operator == TriggerOperator.NOT_EQUALS:
            return value != self.target_value
        elif self.operator == TriggerOperator.GREATER_THAN:
            return value > self.target_value
        elif self.operator == TriggerOperator.LESS_THAN:
            return value < self.target_value
        elif self.operator == TriggerOperator.GREATER_EQUAL:
            return value >= self.target_value
        elif self.operator == TriggerOperator.LESS_EQUAL:
            return value <= self.target_value
        elif self.operator == TriggerOperator.CONTAINS:
            return self.target_value in value if hasattr(value, '__contains__') else False
        elif self.operator == TriggerOperator.NOT_CONTAINS:
            return self.target_value not in value if hasattr(value, '__contains__') else True
        elif self.operator == TriggerOperator.CUSTOM:
            # 使用自定义函数进行条件判断
            if callable(self.target_value):
                try:
                    return self.target_value(value)
                except Exception as e:
                    logger.error(f"时间 {self.env.now}: 触发器 {self.name} 自定义条件函数执行错误: {e}")
                    return False
            else:
                logger.error(f"时间 {self.env.now}: 触发器 {self.name} 使用CUSTOM运算符但target_value不是可调用对象")
                return False
        else:
            raise ValueError(f"不支持的运算符: {self.operator}")

    def _handle_state_change(self, event):
        """处理state_changed事件"""
        # 检查是否是我们关注的状态键
        if event.get('key') != self.state_key:
            return

        # 获取当前值
        current_value = event.get('new_value')
        old_value = event.get('old_value', self.last_value)

        # 检查条件
        if current_value != old_value and self._check_condition(current_value):
            # 触发回调
            context = {
                'agent_id': self.agent_id,
                'state_key': self.state_key,
                'old_value': old_value,
                'new_value': current_value,
                'operator': self.operator.value,
                'target_value': str(self.target_value) if self.operator == TriggerOperator.CUSTOM else self.target_value
            }
            self._trigger(context)

        # 更新上次值
        self.last_value = current_value


class TimeTrigger(Trigger):
    """基于时间的触发器"""

    def __init__(self, env, trigger_time: Optional[float] = None,
                 interval: Optional[float] = None,
                 cron_expr: Optional[str] = None,
                 name: Optional[str] = None):
        super().__init__(env, TriggerType.TIME, name)
        self.trigger_time = trigger_time
        self.interval = interval
        self.cron_expr = cron_expr
        self.not_to_deactive = self.interval or self.cron_expr

        if not any([trigger_time is not None, interval is not None, cron_expr is not None]):
            raise ValueError("必须指定触发时间、间隔或cron表达式")

        if sum([trigger_time is not None, interval is not None, cron_expr is not None]) > 1:
            raise ValueError("只能指定触发时间、间隔或cron表达式中的一个")

    def _monitor(self):
        """监控时间并在满足条件时触发回调"""
        try:
            if self.trigger_time is not None:
                # 单次触发
                if self.trigger_time > self.env.now:
                    yield self.env.timeout(self.trigger_time - self.env.now)
                    self._trigger({'trigger_mode': 'one_time'})

            elif self.interval is not None:
                # 定期触发
                while True:
                    yield self.env.timeout(self.interval)
                    self._trigger({'trigger_mode': 'interval'})

            elif self.cron_expr is not None:
                # Cron表达式触发
                # 注意：这里简化了cron实现，实际应用中可能需要更复杂的解析
                while True:
                    next_time = self._calculate_next_cron_time()
                    if next_time > self.env.now:
                        yield self.env.timeout(next_time - self.env.now)
                        self._trigger({'trigger_mode': 'cron'})
                    else:
                        # 避免无限循环
                        yield self.env.timeout(1)

        except simpy.Interrupt:
            # 触发器被停用
            pass

    def _calculate_next_cron_time(self):
        """计算下一个cron触发时间（简化实现）"""
        # 这里应该有一个完整的cron表达式解析器
        # 简化起见，我们假设cron_expr是一个简单的间隔
        try:
            interval = float(self.cron_expr)
            return self.env.now + interval
        except:
            # 默认每小时触发一次
            return self.env.now + 60


class CompositeTrigger(Trigger):
    """组合触发器"""

    def __init__(self, env, triggers: List[Trigger],
                 operator: TriggerOperator = TriggerOperator.AND,
                 name: Optional[str] = None):
        super().__init__(env, TriggerType.COMPOSITE, name)
        self.triggers = triggers
        self.operator = operator
        self.trigger_states = {t.id: False for t in triggers}

    def activate(self):
        """激活所有子触发器"""
        super().activate()
        for trigger in self.triggers:
            trigger.add_callback(lambda ctx, t_id=trigger.id: self._handle_subtrigger(t_id, ctx))
            trigger.activate()
        return self

    def deactivate(self):
        """停用所有子触发器"""
        super().deactivate()
        for trigger in self.triggers:
            trigger.deactivate()
        return self

    def _monitor(self):
        """监控子触发器状态"""
        try:
            # 这个进程主要是为了保持触发器活动状态
            # 实际的触发逻辑在_handle_subtrigger中
            while True:
                yield self.env.timeout(float('inf'))

        except simpy.Interrupt:
            # 触发器被停用
            pass

    def _handle_subtrigger(self, trigger_id, context):
        """处理子触发器的触发"""
        # 更新触发状态
        self.trigger_states[trigger_id] = True

        # 检查组合条件
        if self.operator == TriggerOperator.AND:
            # 所有子触发器都必须触发
            if all(self.trigger_states.values()):
                self._trigger({'subtriggers': self.trigger_states.copy()})
                # 重置状态
                self.trigger_states = {t_id: False for t_id in self.trigger_states}

        elif self.operator == TriggerOperator.OR:
            # 任一子触发器触发即可
            self._trigger({'subtrigger_id': trigger_id, 'subtrigger_context': context})
            # 重置状态
            self.trigger_states = {t_id: False for t_id in self.trigger_states}
