# airfogsim/agent/__init__.py
import importlib
import pkgutil
import inspect
from airfogsim.core.agent import Agent

# 存储所有导出的代理类
__all__ = []
_AGENT_CLASSES = []

# 动态导入所有代理类
for loader, module_name, is_pkg in pkgutil.walk_packages(__path__):
    module = importlib.import_module(f"{__name__}.{module_name}")
    
    # 查找模块中的所有Agent子类
    for name, obj in inspect.getmembers(module):
        if (inspect.isclass(obj) and
            issubclass(obj, Agent) and
            obj.__module__ == module.__name__ and
            obj is not Agent):  # 排除基类
            
            # 添加到全局命名空间
            globals()[name] = obj
            __all__.append(name)
            _AGENT_CLASSES.append(obj)

# 提供获取所有代理类的函数
def get_all_agent_classes():
    """获取所有代理类"""
    return _AGENT_CLASSES

# 提供获取代理类描述的函数
def get_agent_descriptions():
    """获取所有代理类的描述信息"""
    descriptions = []
    for agent_class in _AGENT_CLASSES:
        descriptions.append({
            'id': agent_class.__name__,
            'name': agent_class.__name__,
            'description': agent_class.get_description(),
            'templates': agent_class.get_state_templates()
        })
    return descriptions

from .drone import DroneAgent