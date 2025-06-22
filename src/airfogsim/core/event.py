"""
AirFogSim事件(Event)核心模块

该模块实现了仿真系统的事件处理机制，基于发布-订阅模式设计，
允许系统中的不同组件（代理、任务、资源等）之间进行松耦合通信。
主要内容包括：
1. EventSubscription：事件订阅类，包含订阅信息和过滤功能
2. EventRegistry：事件注册表，管理所有事件和订阅关系
3. 支持特定事件和通配符订阅
4. 异步事件通知和回调执行

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from collections import defaultdict
from typing import Dict, List, Tuple
import warnings
import simpy
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

class EventSubscription:
    def __init__(self, source_id, event_name, listener_id, callback=None):
        self.source_id = source_id
        self.event_name = event_name
        self.listener_id = listener_id
        self.callback = callback
        self.source_filters = []
        self.listener_filters = []

    def add_source_filter(self, filter_func):
        self.source_filters.append(filter_func)
        return self

    def add_listener_filter(self, filter_func):
        self.listener_filters.append(filter_func)
        return self

    def match_filters(self, event_value):
        try:
            for filter_func in self.source_filters:
                if not filter_func(event_value): return False
            for filter_func in self.listener_filters:
                if not filter_func(event_value): return False
        except Exception as e:
             logger.error(f"Error applying filter for {self.listener_id} on event {self.source_id}/{self.event_name}: {e}")
             return False # Fail safe on filter error
        return True

class EventRegistry:
    def __init__(self, env, logger=None):
        self.env = env
        self.logger = logger  # 添加logger引用
        # source_id -> event_name -> SimPy Event
        self.events: Dict[str, Dict[str, simpy.Event]] = defaultdict(dict)
        # source_id -> event_name -> listener_id -> EventSubscription
        self.subscriptions: Dict[Tuple[str, str], Dict[str, EventSubscription]] = defaultdict(dict)
        # Listener lookup: listener_id -> list of (source_id, event_name)
        self._listener_map: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        # 存储通配符订阅
        self.wildcard_subscriptions: Dict[Tuple[str, str], Dict[str, EventSubscription]] = defaultdict(dict)

    def get_registered_events(self, source_id):
        return list(self.events.get(source_id, {}).keys())

    def get_all_agent_sources(self):
        return [sid for sid in self.events if sid.startswith('agent_')]

    def has_event(self, source_id, event_name):
        return source_id in self.events and event_name in self.events[source_id]

    def get_event(self, source_id, event_name):
        # 如果事件不存在，创建一个新事件
        if event_name not in self.events[source_id]:
            # print(f"DEBUG Registry: Auto-registering {source_id}/{event_name} on get")
            self.events[source_id][event_name] = self.env.event()
            return self.events[source_id][event_name]

        # 检查事件是否已被触发，如果是则创建新事件
        event = self.events[source_id][event_name]
        if event.triggered:
            # print(f"DEBUG Registry: Event {source_id}/{event_name} already triggered, creating new one")
            self.events[source_id][event_name] = self.env.event()
            return self.events[source_id][event_name]

        return event

    def subscribe(self, source_id, event_name, listener_id, callback=None):
        if not callable(callback):
            raise ValueError("Callback must be callable")

        # 处理通配符订阅
        is_wildcard = source_id == "*" or event_name == "*"

        subscription_key = (source_id, event_name)
        # print(f"DEBUG Registry: Subscribing '{listener_id}' to {source_id}/{event_name}")

        # Check for duplicate subscription by the same listener
        subscription_dict = self.wildcard_subscriptions if is_wildcard else self.subscriptions
        if listener_id in subscription_dict[subscription_key]:
            warnings.warn(f"Listener '{listener_id}' already subscribed to '{source_id}/{event_name}'. Overwriting callback/filters.")

        subscription = EventSubscription(source_id, event_name, listener_id, callback)
        subscription_dict[subscription_key][listener_id] = subscription
        self._listener_map[listener_id].append(subscription_key)

        # 对于非通配符订阅，确保事件存在
        if not is_wildcard:
            self.get_event(source_id, event_name)

        return subscription # Return the subscription object itself

    def unsubscribe(self, source_id, event_name, listener_id):
        subscription_key = (source_id, event_name)
        # print(f"DEBUG Registry: Unsubscribing '{listener_id}' from {source_id}/{event_name}")
        removed = False

        # 检查常规订阅
        if subscription_key in self.subscriptions:
            if listener_id in self.subscriptions[subscription_key]:
                del self.subscriptions[subscription_key][listener_id]
                # Clean up dict if empty
                if not self.subscriptions[subscription_key]:
                    del self.subscriptions[subscription_key]
                removed = True

        # 检查通配符订阅
        if (source_id == "*" or event_name == "*") and subscription_key in self.wildcard_subscriptions:
            if listener_id in self.wildcard_subscriptions[subscription_key]:
                del self.wildcard_subscriptions[subscription_key][listener_id]
                # Clean up dict if empty
                if not self.wildcard_subscriptions[subscription_key]:
                    del self.wildcard_subscriptions[subscription_key]
                removed = True

        # Clean up listener map
        if listener_id in self._listener_map:
            try:
                self._listener_map[listener_id].remove(subscription_key)
                if not self._listener_map[listener_id]:
                    del self._listener_map[listener_id]
            except ValueError:
                pass # Key already removed, ignore
        return removed

    def unsubscribe_all(self, listener_id):
        """Unsubscribe a listener from all events it's subscribed to."""
        # print(f"DEBUG Registry: Unsubscribing '{listener_id}' from all events.")
        count = 0
        if listener_id in self._listener_map:
            # Iterate over a copy because unsubscribe modifies the list
            subs_to_remove = list(self._listener_map[listener_id])
            for source_id, event_name in subs_to_remove:
                if self.unsubscribe(source_id, event_name, listener_id):
                    count += 1
        # print(f"DEBUG Registry: Removed {count} subscriptions for '{listener_id}'.")
        return count

    def _is_env(self, source_id):
        return source_id.startswith("env_")

    def trigger_event(self, source_id, event_name, event_value=None):
        # 获取当前事件
        current_event = self.get_event(source_id, event_name)

        # 通知所有匹配的订阅者
        notified_count = 0

        # 确保event_value是字典类型
        if event_value is None:
            event_value = {}
        elif not isinstance(event_value, dict):
            event_value = {"value": event_value}

        # 如果有logger，记录事件，但是不记录visual_update事件
        if self.logger and not (self._is_env(source_id) and event_name == "visual_update"):
            try:
                # 准备事件数据并传递给logger
                event_data = {
                    "type": "sim_event",
                    "time": self.env.now,
                    "source": source_id,
                    "event": event_name,
                    "value": event_value,
                    "level": "info"
                }
                self.logger(event_data)
            except Exception as e:
                logger.error(f"Error logging event {source_id}/{event_name}: {str(e)}")

        # 1. 通知特定源订阅者
        subscription_key = (source_id, event_name)
        if subscription_key in self.subscriptions:
            notified_count += self._notify_subscribers(self.subscriptions[subscription_key], event_value)

        # 2. 通知通配符订阅者
        # 先通知特定事件的通配符订阅者
        wildcard_key = ("*", event_name)
        if wildcard_key in self.wildcard_subscriptions:
            notified_count += self._notify_subscribers(self.wildcard_subscriptions[wildcard_key], event_value)

        # 通知特定源的所有事件的订阅者
        source_wildcard_key = (source_id, "*")
        if source_wildcard_key in self.wildcard_subscriptions:
            # 构造包含事件名称的事件值
            enhanced_event_value = event_value.copy()
            enhanced_event_value['event_name'] = event_name
            notified_count += self._notify_subscribers(self.wildcard_subscriptions[source_wildcard_key], enhanced_event_value)

        # 再通知全通配符订阅者
        all_wildcard_key = ("*", "*")
        if all_wildcard_key in self.wildcard_subscriptions:
            # 构造包含源和事件名称的事件值
            enhanced_event_value = event_value.copy()
            enhanced_event_value['source_id'] = source_id
            enhanced_event_value['event_name'] = event_name
            notified_count += self._notify_subscribers(self.wildcard_subscriptions[all_wildcard_key], enhanced_event_value)

        # 让当前事件成功完成
        if not current_event.triggered:
            current_event.succeed(event_value)

        # 在触发后立即创建一个新的事件替换旧事件
        self.events[source_id][event_name] = self.env.event()

        # print(f"DEBUG Registry: Notified {notified_count} listeners for {source_id}/{event_name}")
        return notified_count > 0

    def _notify_subscribers(self, subscribers_dict, event_value):
        """通知给定的订阅者集合"""
        notified_count = 0
        # 使用列表复制以防回调修改字典
        listeners = list(subscribers_dict.keys())
        for listener_id in listeners:
            # 检查订阅是否仍然存在
            if listener_id in subscribers_dict:
                subscription = subscribers_dict[listener_id]
                if subscription.match_filters(event_value):
                    if subscription.callback:
                        # 检查是否有注入的处理函数
                        handler_function = event_value.get('handler_function')

                        # 调度回调执行
                        self._run_callback(subscription.callback, event_value, listener_id, handler_function)
                        notified_count += 1
        return notified_count

    def _run_callback(self, callback, value, listener_id, handler_function=None):
        """
        Helper process to run subscriber callback asynchronously.

        Args:
            callback: The registered callback function
            value: The event value/data
            listener_id: ID of the listener
            handler_function: Optional handler function name to use instead of the callback
        """
        try:
            # 如果有处理函数名称，尝试从回调对象中获取该函数
            if handler_function and hasattr(callback.__self__, handler_function):
                # 获取处理函数
                handler = getattr(callback.__self__, handler_function)
                # 调用处理函数，传递订阅者和事件数据
                handler(callback.__self__, value)
            else:
                # 使用标准回调
                callback(value) # Direct call might be okay if callbacks are fast/non-blocking
        except Exception as e:
            logger.error(f"时间 {self.env.now}: Error in subscriber callback for '{listener_id}': {e}")
            import traceback
            logger.error(traceback.format_exc())
