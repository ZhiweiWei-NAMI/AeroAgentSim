# AirFogSim Helper 模块

Helper 模块提供了一系列工具函数和脚本，用于辅助开发和调试 AirFogSim 系统。

## 主要功能

### 类检查工具

类检查工具用于检查系统中已实现的各种类及其关键信息，帮助开发者了解系统中已有的类，避免重复创建。

#### 使用方法

1. 在代码中使用：

```python
from airfogsim.helper import check_all_classes, check_agent_classes, find_compatible_agents

# 创建环境
env = Environment()

# 检查所有类
check_all_classes(env)

# 检查代理类
check_agent_classes(env)

# 查找支持特定状态的代理类
find_compatible_agents(env, ['position', 'battery_level'])
```

2. 作为命令行工具使用：

```bash
# 显示所有类
python -m airfogsim.helper.class_finder --all

# 显示代理类
python -m airfogsim.helper.class_finder --agent

# 显示组件类
python -m airfogsim.helper.class_finder --component

# 显示任务类
python -m airfogsim.helper.class_finder --task

# 显示工作流类
python -m airfogsim.helper.class_finder --workflow

# 查找支持特定状态的代理类
python -m airfogsim.helper.class_finder --find-agent position,battery_level

# 查找产生特定指标的组件类
python -m airfogsim.helper.class_finder --find-component speed,processing_power

# 查找产生特定状态的任务类
python -m airfogsim.helper.class_finder --find-task position,direction
```

## 开发指南

在开发新的代理、组件、任务或工作流类之前，建议先使用类检查工具查看系统中已有的类，避免重复创建。

### 开发新类的步骤

1. 使用类检查工具查看系统中已有的类：

```bash
python -m airfogsim.helper.class_finder --all
```

2. 如果需要特定功能的类，可以使用查找功能：

```bash
# 例如，查找支持位置和电池电量状态的代理类
python -m airfogsim.helper.class_finder --find-agent position,battery_level
```

3. 如果找到了符合需求的类，可以直接使用；如果没有找到，再考虑创建新类。

4. 创建新类时，确保遵循以下规范：
   - 代理类：定义 `PRODUCED_STATES` 属性
   - 组件类：定义 `PRODUCED_METRICS` 和 `MONITORED_STATES` 属性
   - 任务类：定义 `NECESSARY_METRICS` 和 `PRODUCED_STATES` 属性
   - 工作流类：定义属性模板和创建函数

5. 创建新类后，使用类检查工具验证类是否正确注册：

```bash
python -m airfogsim.helper.class_finder --all
```
