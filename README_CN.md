<a href="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9"><img src="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9/status.svg" alt="JOSS 状态"></a>
[![DOI](https://zenodo.org/badge/735258267.svg)](https://doi.org/10.5281/zenodo.15779000)

# AeroAgentSim

<div align="center">
<img src="src/aeroagentsim/docs/img/logo.png" alt="AeroAgentSim Logo" width="300">
</div>

AeroAgentSim 是一个面向低空自主系统的离散事件仿真平台与开发者工作台，适用于工作流建模、算法验证、运行调试、轨迹回放和执行链路分析。

当前仓库、工作台与源码分发统一使用 `AeroAgentSim` 与 `aeroagentsim`。已有项目仍可继续导入 `airfogsim`，下文引用部分则对应以 `AirFogSim` 题目发表的 JOSS 论文。

[English Version](README.md)

## 项目概览

- 推荐开发安装方式：`pip install -e .[dev]`
- 推荐 Python 导入方式：`from aeroagentsim import Environment`
- 当前工作台页面：`Overview`、`Class Catalog`、`Workflow Studio`、`Run Console`、`Trajectories & Logs`
- 运行产物目录：`runtime/aeroagentsim/`
- 自定义 `agent` / `task` / `workflow` 文件主源目录：`registry/aeroagentsim/`
- 工作台内置工作流聚焦于可运行的 inspection、charging、logistics、image-processing 场景

## 开发者工作台

当前 AeroAgentSim 工作台面向配置、校验、执行与运行验证：

1. `Overview`：当前配置版本、最近一次运行、校验状态
2. `Class Catalog`：内置与自定义元数据以及兼容关系查询
3. `Workflow Studio`：表格/表单编辑与 workflow-agent-state 关系图
4. `Run Console`：启动、暂停、恢复、重置、实时日志、关键路径和实时 2D 地图
5. `Trajectories & Logs`：按 `run_id` 查看历史轨迹与结构化日志

可视化层保持轻量、明确、易验证：

- 基于 Leaflet 的 2D 空间视图
- `simulation_plane` 模式使用 `CRS.Simple`
- `geo_osm` 模式使用地理坐标
- 实时 marker 展示 workflow、task、状态和近期日志上下文
- 历史轨迹以 2D polyline 形式回放

`Workflow Studio` 以表格和表单作为持久化配置主源。关系图是交互式检查画布，支持自动布局、画布内滚轮或触控板缩放、空白背景拖拽平移，以及节点拖拽微调布局。

![Workflow Studio 关系图](docs/images/workflow-studio-relation-graph.png)

当前工作台还提供：

- 全局 `zh-CN` / `en-US` 界面切换
- `Workflow Studio` 页内 `Validate`
- 集中式 `Review / Validate` 草稿一致性与运行前校验
- 内置定义与自定义定义的统一浏览
- 基于 `run_id` 的结构化日志与轨迹检查

## Registry 与运行时

自定义定义采用文件主源：

- `registry/aeroagentsim/agents/`
- `registry/aeroagentsim/tasks/`
- `registry/aeroagentsim/workflows/`

配置快照与运行时产物分层存放：

- 配置快照：`runtime/aeroagentsim/configs/`
- 运行目录：`runtime/aeroagentsim/runs/<run_id>/`
- 常见子目录：`logs/`、`workflow_states/`、`trajectories/`、`spatial/`、`metrics/`

运行启动前会执行 runtime preflight。像默认 `create_airspace` 或 `create_frequency` 注入被跳过这样的兼容性发现，会保留为 `warning`；只有 preflight `errors` 才会阻塞 `POST /api/runs`。

## 开发配置

本地开发和自动化最常用的公开环境变量如下：

- 后端存储：
  - `AEROAGENTSIM_RUNTIME_DIR`
  - `AEROAGENTSIM_REGISTRY_DIR`
  - `AEROAGENTSIM_DB_PATH`
  - `AEROAGENTSIM_LOG_LEVEL`
- 后端兼容别名：
  - `AIRFOGSIM_RUNTIME_DIR`
  - `AIRFOGSIM_REGISTRY_DIR`
  - `AIRFOGSIM_DB_PATH`
  - `AIRFOGSIM_LOG_LEVEL`
- 前端地址：
  - `REACT_APP_API_BASE_URL`
  - `REACT_APP_WS_BASE_URL`
  - `REACT_APP_ENABLE_MOCK_FALLBACK`
- 示例依赖：
  - `OPENWEATHERMAP_API_KEY`：天气相关示例
  - `SUMO_HOME`：SUMO 交通工作流

## 安装

针对当前仓库和开发者工作台，推荐直接从源码安装：

```bash
python -m venv aeroagentsim_env
source aeroagentsim_env/bin/activate
pip install -e .[dev]
```

如果还需要文档构建能力：

```bash
pip install -e ".[dev,docs]"
```

### 安装后验证

```bash
python -c "import aeroagentsim, airfogsim; from aeroagentsim import Environment; from airfogsim import Environment as LegacyEnvironment; print(Environment.__name__, Environment is LegacyEnvironment)"
```

## 快速示例

```python
from aeroagentsim import Environment
from aeroagentsim.agent import DroneAgent
from aeroagentsim.component import ChargingComponent, MoveToComponent
from aeroagentsim.workflow.inspection import create_inspection_workflow

env = Environment()

drone = env.create_agent(
    DroneAgent,
    "drone1",
    properties={
        "position": [10, 10, 0],
        "battery_level": 100,
    },
)

drone.add_component(MoveToComponent(env, drone))
drone.add_component(ChargingComponent(env, drone))

workflow = create_inspection_workflow(
    env,
    drone,
    [
        (10, 10, 50),
        (100, 40, 80),
        (180, 120, 60),
        (10, 10, 0),
    ],
)

workflow.start()
env.run(until=600)
```

## 启动工作台

```bash
python main_for_visualization.py --backend-port 8002 --frontend-port 3000
```

## API 概览

当前工作台的主要接口分组如下：

- `GET /api/catalog/agents|components|tasks|workflows`
- `GET /api/catalog/compatibility`
- `GET/POST /api/registry/{kind}`
- `GET/PUT/DELETE /api/registry/{kind}/{definition_id}`
- `POST /api/registry/{kind}/{definition_id}/validate`
- `GET/PUT /api/configs/{config_id}`
- `GET/POST /api/configs/{config_id}/graph`
- `POST /api/configs/{config_id}/preflight`
- `POST /api/configs/{config_id}/validate`
- `GET /api/health`
- `POST /api/runtime/reset`
- `GET /api/runs`
- `POST /api/runs`
- `POST /api/runs/{run_id}/pause|resume|reset`
- `DELETE /api/runs/{run_id}`
- `GET /api/runs/{run_id}/status|logs|trajectories|spatial`

## 文档入口

- [安装指南](INSTALL.md)
- [文档导航](DOCUMENTATION_GUIDE.md)
- [文档中心](docs/README.md)
- [系统架构](src/aeroagentsim/docs/cn/architecture.md)

## 引用

如果您在研究中使用 AeroAgentSim，请引用 JOSS 论文：

- JOSS 页面：<https://joss.theoj.org/papers/10.21105/joss.08267>
- PDF：<https://www.theoj.org/joss-papers/joss.08267/10.21105.joss.08267.pdf>

```bibtex
@article{Wei2025,
  doi = {10.21105/joss.08267},
  url = {https://doi.org/10.21105/joss.08267},
  year = {2025},
  publisher = {The Open Journal},
  volume = {10},
  number = {111},
  pages = {8267},
  author = {Wei, Zhiwei and Li, Bing and Zhang, Rongqing},
  title = {AirFogSim: A Python Package for Benchmarking Collaborative Intelligence in Low-Altitude Vehicular Fog Computing},
  journal = {Journal of Open Source Software}
}
```
