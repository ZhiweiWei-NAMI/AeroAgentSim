"""
AirFogSim核心模块初始化文件

该文件导入并暴露了AirFogSim核心模块中的关键类和枚举，使它们可以直接从airfogsim.core包中访问。
这些类构成了仿真系统的基础架构，包括代理、工作流、任务和组件等核心概念。

作者: zhiwei wei 2311769@tongji.edu.cn
"""

from .agent import Agent, AgentMeta
from .workflow import Workflow, WorkflowMeta
from .task import Task
from .component import Component
from .environment import Environment
from .trigger import Trigger
from .enums import *