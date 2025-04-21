<a href="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9"><img src="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9/status.svg"></a>

# AirFogSim: 低空车载雾计算协同智能基准测试框架

<div align="center">
  <img src="src/airfogsim/docs/img/logo.png" alt="AirFogSim Logo" width="300">
</div>

AirFogSim是一个基于SimPy构建的离散事件仿真框架，旨在为无人机集成的雾计算环境中的协同智能提供基准测试。它提供了一个全面的平台，用于模拟异构空中和地面节点之间的复杂交互，重点关注现实的通信、计算、能源和移动性建模。

[English Version](README.md)

## 📋 项目概述

AirFogSim提供了一个全面的仿真环境，用于：

- 模拟无人机等自治代理在复杂环境中的行为
- 研究资源分配和任务卸载策略
- 评估低空车载雾计算中的协同智能
- 对不同工作流和协议进行基准测试
- 可视化仿真过程和结果分析

该框架采用模块化设计，支持高度自定义的仿真场景，并提供直观的可视化界面，便于研究人员和开发者进行实验和分析。

如果您在研究中使用了AirFogSim，请引用我们的论文：

```bibtex
@misc{wei2024airfogsimlightweightmodularsimulator,
      title={AirFogSim: A Light-Weight and Modular Simulator for UAV-Integrated Vehicular Fog Computing},
      author={Zhiwei Wei and Chenran Huang and Bing Li and Yiting Zhao and Xiang Cheng and Liuqing Yang and Rongqing Zhang},
      year={2024},
      eprint={2409.02518},
      archivePrefix={arXiv},
      primaryClass={cs.NI},
      url={https://arxiv.org/abs/2409.02518},
}
```

## ✨ 核心特性

- **高性能事件驱动仿真核心：** 优化的事件驱动仿真引擎，关键操作的计算复杂度低于O(n log n)，能够高效模拟大规模场景。

- **基于工作流的任务组合框架：** 灵活且模块化的工作流驱动任务模型，明确捕获任务依赖关系、资源约束和异构节点之间的协作交互。

- **符合标准的现实建模：** 基于既定标准的综合模型，包括符合3GPP标准的通信信道模型、经验验证的能耗模型和基于物理的移动模式。

- **以代理为中心的自治系统：** 代理（如无人机）作为主要行动者，拥有内部状态，能够基于其状态、分配的工作流和环境感知自主决策。

- **基于组件的能力：** 关注点明确分离，组件封装特定功能（移动性、计算、感知）并管理任务执行环境。

- **基于触发器的反应性：** 灵活的机制，用于对各种条件（事件、状态变化、时间）做出反应，驱动工作流状态机转换并启用自动响应。

- **资源管理：** 仿真资源（着陆点、CPU、空域、频谱）由专用管理器类管理，处理注册、分配、争用和动态属性变化。

- **实时可视化：** 集成的前端界面，支持实时监控和数据分析。

- **LLM集成：** 通过大型语言模型支持任务规划和决策。

## 🏗️ 系统架构

AirFogSim围绕事件驱动的基于代理的建模（ABM）架构构建，能够高效模拟异构代理之间的复杂交互。该平台扩展了SimPy离散事件仿真库，为无人机集成的雾计算场景提供专门的组件。

### 后端架构

AirFogSim的后端架构设计注重模块化、可扩展性和性能。它由几个关键组件组成：

#### 1. 仿真环境 (Environment)

仿真的中心枢纽，扩展SimPy的Environment用于离散事件调度：
- **事件注册中心（EventRegistry）：** 用于在所有仿真实体之间发布和订阅命名事件的中央总线，实现解耦通信。
- **空域管理器（AirspaceManager）：** 基于八叉树的空间管理，用于位置和碰撞信息。
- **着陆点管理器（LandingManager）：** 管理着陆点和充电站。
- **频谱管理器（FrequencyManager）：** 管理频谱资源，采用符合3GPP标准的信道模型。
- **合同管理器（ContractManager）：** 管理合同和交易。
- **工作流管理器（WorkflowManager）：** 管理工作流的生命周期。
- **任务管理器（TaskManager）：** 管理任务的创建和执行。
- **数据提供器（DataProvider）：** 提供实时数据和统计信息，包括天气和交通流。

#### 2. 代理 (Agent)

自治的决策实体，如无人机和地面站：
- **状态管理：** 通过基于元类的状态模板维护内部状态，进行类型验证。
- **决策逻辑：** SimPy进程定义代理的行为循环，感知状态、工作流和事件，决定执行哪些任务。
- **组件所有权：** 拥有代表其能力（移动性、感知、计算）的组件。
- **任务发起：** 通过委托给适当的组件来发起任务。
- **事件处理：** 触发和订阅状态变化、任务生命周期和对象拥有的事件。

#### 3. 组件 (Component)

抽象特定能力（移动性、计算、充电）并提供任务执行环境：
- **任务执行：** 管理任务生命周期、资源获取、指标计算和清理。
- **资源交互：** 定义资源需求并从适当的管理器请求资源。
- **指标计算：** 基于资源属性和代理状态计算性能指标。
- **事件发射：** 为任务状态和指标变化触发命名空间事件。

#### 4. 任务 (Task)

封装特定动作的逻辑，定义工作如何执行：
- **执行逻辑：** SimPy生成器，消耗组件提供的性能指标。
- **指标消耗：** 声明执行组件所需的必要指标。
- **状态产出：** 基于任务逻辑和进度更新代理状态。
- **生命周期管理：** 管理任务状态（PENDING、RUNNING、COMPLETED、FAILED、CANCELED）。

#### 5. 工作流 (Workflow) 和状态机 (WorkflowStatusMachine)

代表高级目标或过程，作为监视器和协调器：
- **状态机：** 包含管理内部状态和转换的WorkflowStatusMachine实例。
- **触发器驱动的转换：** 使用触发器基于代理状态、事件或时间定义规则。
- **上下文/指导：** 提供工作流上下文，并根据当前状态为代理建议下一个任务。

#### 6. 触发器 (Trigger)

监视特定仿真条件并在满足时执行回调：
- **条件监视：** 检查事件发生、代理状态变化或时间流逝。
- **激活/停用：** 可以激活进行监视，也可以停用。
- **类型：** EventTrigger、StateTrigger、TimeTrigger和CompositeTrigger满足不同的监视需求。

#### 7. 资源层 (Resource 和 ResourceManager)

模拟被利用或消耗的实体：
- **资源基类：** 定义常见属性，如id、attributes和status。
- **资源管理器基类：** 管理特定类型资源的通用基类。
- **特定管理器：** 实现资源特定的查找、分配、释放和争用建模逻辑。

### 可视化系统

AirFogSim集成了一个完整的可视化系统，包括：

- **仪表盘：** 显示仿真状态、代理信息和系统事件
- **无人机监控：** 实时追踪无人机位置、状态和轨迹
- **工作流配置：** 配置和监控工作流执行
- **数据分析：** 资源使用情况和性能指标分析

<div align="center">
  <img src="src/airfogsim/docs/img/状态监控.png" alt="状态监控界面" width="800">
  <p><em>状态监控界面 - 实时跟踪无人机位置和状态</em></p>
</div>

可视化系统采用客户端-服务器架构：
- **前端：** 基于React的Web应用
  <div align="center">
    <img src="src/airfogsim/docs/img/前端.png" alt="前端界面" width="600">
    <p><em>前端界面 - 用户交互与数据可视化</em></p>
  </div>
- **前端：** 基于SUMO的3D交通仿真可视化
  <div align="center">
    <img src="src/airfogsim/docs/img/前端2.png" alt="前端界面" width="600">
    <p><em>前端界面 - 3D交通仿真可视化</em></p>
  </div>
- **后端：** FastAPI服务，与仿真引擎集成
  <div align="center">
    <img src="src/airfogsim/docs/img/后端.png" alt="后端架构" width="600">
    <p><em>后端架构 - 数据处理与仿真引擎集成</em></p>
  </div>
- **通信：** 通过WebSocket实现实时数据传输

## 🚀 安装指南

### 前提条件

- Python 3.8+
- Node.js 14+ (仅可视化系统需要)
- npm 6+ (仅可视化系统需要)

### 安装选项

#### 选项1：从PyPI安装（推荐）

最简单的安装方式是直接从PyPI安装：

```bash
pip install airfogsim
```

这将安装核心仿真框架。如果您想使用可视化系统，需要按照选项2中的说明克隆仓库。

#### 选项2：从源代码安装

1. 克隆仓库

```bash
git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
cd AirFogSim
```

2. 安装Python依赖

```bash
python -m venv airfogsim_venv
source airfogsim_venv/bin/activate  # Windows上: airfogsim_venv\Scripts\activate
pip install -r requirements.txt
pip install -e .  # 以开发模式安装
```

3. 安装前端依赖

```bash
cd frontend
npm install
cd ..
```

4. 启动可视化系统

```bash
python main_for_visualization.py
```

这将启动后端API服务和前端开发服务器，并自动在浏览器中打开可视化界面。


## 📝 使用示例

### 基本仿真示例

```python
from airfogsim.core.environment import Environment
from airfogsim.agent import DroneAgent
from airfogsim.component import MoveToComponent, ChargingComponent
from airfogsim.workflow.inspection import create_inspection_workflow
from airfogsim.helper import check_all_classes, find_compatible_components

# 创建环境
env = Environment()

# 检查系统中的类
check_all_classes(env)

# 创建无人机代理
drone = env.create_agent(
    DroneAgent,
    "drone1",
    initial_position=(10, 10, 0),
    initial_battery=100
)

# 查找合适的组件
find_compatible_components(env, drone, ['speed'])

# 添加组件
move_component = MoveToComponent(env, drone)
charging_component = ChargingComponent(env, drone)
drone.add_component(move_component)
drone.add_component(charging_component)

# 创建巡检工作流
waypoints = [
    (10, 10, 100),    # 起飞
    (400, 400, 150),  # 中间点
    (800, 800, 150),  # 目的地
    (800, 800, 0),    # 降落
    (800, 800, 100),  # 起飞返回
    (10, 10, 0)       # 返回起点
]
workflow = create_inspection_workflow(env, drone, waypoints)

# 启动工作流
workflow.start()

# 运行仿真
env.run(until=1000)
```

### 使用类检查工具

```bash
# 显示所有类
python -m airfogsim.helper.class_finder --all

# 查找支持特定状态的代理类
python -m airfogsim.helper.class_finder --find-agent position,battery_level

# 查找产生特定指标的组件类
python -m airfogsim.helper.class_finder --find-component speed,processing_power
```

### 启动可视化界面

```bash
python main_for_visualization.py --backend-port 8002 --frontend-port 3000
```

## 🧪 示例程序与自动测试

AirFogSim提供了丰富的示例程序，展示框架的各种功能和使用场景。这些示例位于`src/airfogsim/examples`目录下：

### 主要示例程序

- **基础触发器系统**: `example_trigger_basic.py` - 展示如何使用不同类型的触发器创建和管理工作流
- **工作流图表生成**: `example_workflow_diagram.py` - 演示如何将工作流状态机转换为可视化图表
- **图像处理工作流**: `example_workflow_image_processing.py` - 展示环境图像感知处理的完整工作流
- **多任务合约示例**: `example_workflow_contract.py` - 演示合约工作流如何管理多个任务
- **无人机巡检示例**: `example_workflow_inspection.py` - 展示无人机巡检路径规划和自动充电功能
- **天气数据集成**: `example_weather_provider.py` - 演示如何集成实时天气数据到仿真中
- **多工作流基准测试**: `example_benchmark_multi_workflow.py` - JOSS论文基准测试示例，包含巡检、物流和充电工作流

### 一键测试示例

我们提供了一个自动测试脚本，可以轻松运行和验证所有示例：

```bash
# 列出所有可用的示例
cd src/airfogsim/examples
python test_examples.py --list

# 运行特定示例
python test_examples.py --run example_workflow_diagram example_trigger_basic

# 运行所有示例
python test_examples.py
```

示例测试脚本会自动检查必要的依赖条件（如API密钥），并提供详细的测试结果报告。这使得新用户可以快速了解框架的功能，开发者也能方便地验证不同模块的正确性。

## 📁 项目结构

```
airfogsim-project/
├── .dockerignore             # Docker构建忽略文件（后端）
├── .env                      # 后端环境变量（本地，不提交到Git）
├── Dockerfile                # 后端Dockerfile
├── docker-compose.yml        # Docker Compose编排文件
├── frontend/                 # 前端可视化界面
│   ├── .dockerignore         # Docker构建忽略文件（前端）
│   ├── .env                  # 前端环境变量（本地，不提交到Git）
│   ├── Dockerfile            # 前端Dockerfile
│   ├── build/                # 前端构建产物（本地生成）
│   ├── node_modules/         # （本地，不提交到Git）
│   ├── package.json
│   ├── public/               # 静态资源
│   └── src/                  # 前端源代码
│       ├── pages/            # 页面组件
│       └── services/         # API服务
├── LICENSE                   # 项目许可证
├── main_for_visualization.py # 可视化系统启动脚本（本地开发用）
├── nginx.conf                # Nginx配置文件
├── pyproject.toml            # Python项目配置文件（含依赖）
├── README.md                 # 英文文档
├── README_CN.md              # 本文档
├── requirements.txt          # Python锁定依赖（由pip-compile生成）
├── src/                      # 后端源代码
│   └── airfogsim/            # 核心仿真框架
│       ├── agent/            # 代理实现
│       ├── component/        # 组件实现
│       ├── core/             # 核心类和接口
│       ├── docs/             # 文档
│       ├── event/            # 事件处理
│       ├── examples/         # 示例代码
│       ├── helper/           # 开发辅助工具
│       ├── manager/          # 各类管理器
│       ├── resource/         # 资源实现
│       ├── task/             # 任务实现
│       ├── visualization/    # 可视化相关（FastAPI应用）
│       └── workflow/         # 工作流实现
└── ...（其他配置文件、测试文件等）
```

## 📚 文档

详细的文档可在以下位置找到：

- [系统架构](src/airfogsim/docs/cn/architecture.md)
- [代理指南](src/airfogsim/docs/cn/agent_guide.md)
- [组件指南](src/airfogsim/docs/cn/component_guide.md)
- [任务指南](src/airfogsim/docs/cn/task_guide.md)
- [触发器指南](src/airfogsim/docs/cn/trigger_guide.md)
- [工作流指南](src/airfogsim/docs/cn/workflow_guide.md)
- [资源管理指南](src/airfogsim/docs/cn/resource_manager_guide.md)
- [数据提供者指南](src/airfogsim/docs/cn/dataprovider_guide.md)
- [开发辅助工具](src/airfogsim/helper/README.md)
- [测试示例](src/airfogsim/examples/README.md)

## 🤝 贡献指南

我们欢迎各种形式的贡献，包括但不限于：

- 报告问题和提出建议
- 提交代码改进和新功能
- 完善文档和示例
- 分享使用案例和应用场景

### 开发新类的最佳实践

在开发新的代理、组件、任务或工作流类之前，建议先使用helper模块的类检查工具查看系统中是否已有符合需求的类，避免重复创建。

```bash
# 检查系统中的所有类
python -m airfogsim.helper.class_finder --all

# 查找支持特定状态的代理类
python -m airfogsim.helper.class_finder --find-agent position,battery_level
```

请遵循以下步骤：

1. Fork仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 使用helper模块检查现有类
4. 提交更改 (`git commit -m 'Add some amazing feature'`)
5. 推送到分支 (`git push origin feature/amazing-feature`)
6. 创建Pull Request

## 📄 许可证

本项目采用MIT许可证 - 详情请参阅[LICENSE](LICENSE)文件。

---

**AirFogSim** - 为低空车载雾计算研究提供强大的仿真工具
