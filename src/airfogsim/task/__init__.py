# airfogsim/task/__init__.py
import importlib
import pkgutil
import inspect
from airfogsim.core.task import Task

# 存储所有导出的任务类
__all__ = []
_TASK_CLASSES = []

# 动态导入所有任务类
for loader, module_name, is_pkg in pkgutil.walk_packages(__path__):
    module = importlib.import_module(f"{__name__}.{module_name}")
    
    # 查找模块中的所有Task子类
    for name, obj in inspect.getmembers(module):
        if (inspect.isclass(obj) and 
            issubclass(obj, Task) and 
            obj.__module__ == module.__name__ and
            obj is not Task):  # 排除基类
            
            # 添加到全局命名空间
            globals()[name] = obj
            __all__.append(name)
            _TASK_CLASSES.append(obj)

# 提供注册函数
def register_all_tasks(task_manager):
    """向指定的TaskManager注册所有任务类"""
    for task_class in _TASK_CLASSES:
        task_manager.register_task_class(task_class)
    return len(_TASK_CLASSES)