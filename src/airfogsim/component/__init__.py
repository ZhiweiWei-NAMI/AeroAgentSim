"""
AirFogSim组件(Component)模块初始化文件

该模块负责动态导入和注册所有组件类，使它们可以在仿真系统中使用。
主要功能包括：
1. 自动发现和导入所有Component子类
2. 将组件类添加到全局命名空间
3. 提供注册函数，向ComponentManager注册所有组件类
4. 提供获取所有组件类和描述的函数

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import importlib
import pkgutil
import inspect
from airfogsim.core.component import Component

# 存储所有导出的组件类
__all__ = []
_COMPONENT_CLASSES = []

# 动态导入所有组件类
for loader, module_name, is_pkg in pkgutil.walk_packages(__path__):
    module = importlib.import_module(f"{__name__}.{module_name}")

    # 查找模块中的所有Component子类
    for name, obj in inspect.getmembers(module):
        if (inspect.isclass(obj) and
            issubclass(obj, Component) and
            obj.__module__ == module.__name__ and
            obj is not Component):  # 排除基类

            # 添加到全局命名空间
            globals()[name] = obj
            __all__.append(name)
            _COMPONENT_CLASSES.append(obj)

# 提供注册函数
def register_all_components(component_manager):
    """向指定的ComponentManager注册所有组件类"""
    for component_class in _COMPONENT_CLASSES:
        component_manager.register_component_class(component_class)
    return len(_COMPONENT_CLASSES)

# 提供获取所有组件类的函数
def get_all_component_classes():
    """获取所有组件类"""
    return _COMPONENT_CLASSES

# 提供获取组件类描述的函数
def get_component_descriptions():
    """获取所有组件类的描述信息"""
    descriptions = []
    for component_class in _COMPONENT_CLASSES:
        descriptions.append({
            'id': component_class.__name__,
            'name': component_class.__name__,
            'description': component_class.__doc__ or f"{component_class.__name__} 组件",
            'produced_metrics': getattr(component_class, 'PRODUCED_METRICS', []),
            'monitored_states': getattr(component_class, 'MONITORED_STATES', [])
        })
    return descriptions

# 导出常用组件类
from .mobility import MoveToComponent
from .computation import CPUComponent
from .charging import ChargingComponent
from .img_sensor import ImageSensingComponent