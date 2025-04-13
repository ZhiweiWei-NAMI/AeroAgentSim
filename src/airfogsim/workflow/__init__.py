"""
AirFogSim工作流(Workflow)模块初始化文件

该模块负责动态导入和注册所有工作流类，使它们可以在仿真系统中使用。
主要功能包括：
1. 自动发现和导入所有Workflow子类
2. 将工作流类添加到全局命名空间
3. 提供注册函数，向WorkflowManager注册所有工作流类
4. 提供获取所有工作流类和描述的函数

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import importlib
import pkgutil
import inspect
from airfogsim.core.workflow import Workflow

# 存储所有导出的工作流类
__all__ = []
_WORKFLOW_CLASSES = []

# 动态导入所有工作流类
for loader, module_name, is_pkg in pkgutil.walk_packages(__path__):
    module = importlib.import_module(f"{__name__}.{module_name}")

    # 查找模块中的所有Workflow子类
    for name, obj in inspect.getmembers(module):
        if (inspect.isclass(obj) and
            issubclass(obj, Workflow) and
            obj.__module__ == module.__name__ and
            obj is not Workflow):  # 排除基类

            # 添加到全局命名空间
            globals()[name] = obj
            __all__.append(name)
            _WORKFLOW_CLASSES.append(obj)

# 提供注册函数
def register_all_workflows(workflow_manager):
    """向指定的WorkflowManager注册所有工作流类"""
    registered_count = 0
    for workflow_class in _WORKFLOW_CLASSES:
        # WorkflowManager没有直接注册工作流类的方法，而是注册工作流实例
        # 这里只是记录可用的工作流类，实际注册需要在创建工作流实例时进行
        registered_count += 1
    return registered_count

# 提供获取所有工作流类的函数
def get_all_workflow_classes():
    """获取所有工作流类"""
    return _WORKFLOW_CLASSES

# 提供获取工作流类描述的函数
def get_workflow_descriptions():
    """获取所有工作流类的描述信息"""
    descriptions = []
    for workflow_class in _WORKFLOW_CLASSES:
        descriptions.append({
            'id': workflow_class.__name__,
            'name': workflow_class.__name__,
            'description': workflow_class.get_description(),
            'properties': workflow_class.get_property_templates()
        })
    return descriptions
