import uuid
from airfogsim.core.enums import TriggerOperator, TriggerType
from typing import Dict, Any, Optional, Callable, List, Union, Tuple
from airfogsim.core.trigger import Trigger, TimeTrigger, StateTrigger, EventTrigger, CompositeTrigger
class TriggerManager:
    """触发器管理器"""

    def __init__(self, env):
        self.env = env
        self.triggers: Dict[str, Trigger] = {}
        self.manager_id = f"trigger_manager_{uuid.uuid4().hex[:8]}"
        self._register_manager_events()

    def _register_manager_events(self):
        """注册管理器事件"""
        self.env.event_registry.get_event(self.manager_id, 'trigger_created')
        self.env.event_registry.get_event(self.manager_id, 'trigger_activated')
        self.env.event_registry.get_event(self.manager_id, 'trigger_deactivated')
        self.env.event_registry.get_event(self.manager_id, 'trigger_fired')

    def create_event_trigger(self, source_id: str, event_name: str,
                           value_key: Optional[str] = None,
                           operator: Optional[TriggerOperator] = None,
                           target_value: Any = None,
                           name: Optional[str] = None) -> EventTrigger:
        """创建基于事件的触发器"""
        trigger = EventTrigger(self.env, source_id, event_name, value_key, operator, target_value, name)
        self.triggers[trigger.id] = trigger
        self.env.event_registry.trigger_event(self.manager_id, 'trigger_created', {
            'trigger_id': trigger.id,
            'trigger_name': trigger.name,
            'trigger_type': trigger.type.value,
            'time': self.env.now
        })
        return trigger

    def create_state_trigger(self, agent_id: str, state_key: str,
                           operator: TriggerOperator = TriggerOperator.EQUALS,
                           target_value: Any = None,
                           name: Optional[str] = None) -> StateTrigger:
        """创建基于状态的触发器"""
        trigger = StateTrigger(self.env, agent_id, state_key, operator, target_value, name)
        self.triggers[trigger.id] = trigger
        self.env.event_registry.trigger_event(self.manager_id, 'trigger_created', {
            'trigger_id': trigger.id,
            'trigger_name': trigger.name,
            'trigger_type': trigger.type.value,
            'time': self.env.now
        })
        return trigger

    def create_time_trigger(self, trigger_time: Optional[float] = None,
                           interval: Optional[float] = None,
                           cron_expr: Optional[str] = None,
                           name: Optional[str] = None) -> TimeTrigger:
        """创建基于时间的触发器"""
        trigger = TimeTrigger(self.env, trigger_time, interval, cron_expr, name)
        self.triggers[trigger.id] = trigger
        self.env.event_registry.trigger_event(self.manager_id, 'trigger_created', {
            'trigger_id': trigger.id,
            'trigger_name': trigger.name,
            'trigger_type': trigger.type.value,
            'time': self.env.now
        })
        return trigger

    def create_composite_trigger(self, triggers: List[Trigger],
                                operator: TriggerOperator = TriggerOperator.AND,
                                name: Optional[str] = None) -> CompositeTrigger:
        """创建组合触发器"""
        trigger = CompositeTrigger(self.env, triggers, operator, name)
        self.triggers[trigger.id] = trigger
        self.env.event_registry.trigger_event(self.manager_id, 'trigger_created', {
            'trigger_id': trigger.id,
            'trigger_name': trigger.name,
            'trigger_type': trigger.type.value,
            'time': self.env.now
        })
        return trigger

    def get_trigger(self, trigger_id: str) -> Optional[Trigger]:
        """获取触发器"""
        return self.triggers.get(trigger_id)

    def activate_trigger(self, trigger_id: str) -> bool:
        """激活触发器"""
        trigger = self.get_trigger(trigger_id)
        if trigger:
            trigger.activate()
            self.env.event_registry.trigger_event(self.manager_id, 'trigger_activated', {
                'trigger_id': trigger.id,
                'trigger_name': trigger.name,
                'time': self.env.now
            })
            return True
        return False

    def deactivate_trigger(self, trigger_id: str) -> bool:
        """停用触发器"""
        trigger = self.get_trigger(trigger_id)
        if trigger:
            trigger.deactivate()
            self.env.event_registry.trigger_event(self.manager_id, 'trigger_deactivated', {
                'trigger_id': trigger.id,
                'trigger_name': trigger.name,
                'time': self.env.now
            })
            return True
        return False

    def remove_trigger(self, trigger_id: str) -> bool:
        """移除触发器"""
        trigger = self.get_trigger(trigger_id)
        if trigger:
            trigger.deactivate()
            del self.triggers[trigger_id]
            return True
        return False
