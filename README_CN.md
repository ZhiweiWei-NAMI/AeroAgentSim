<a href="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9"><img src="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9/status.svg"></a>
[![DOI](https://zenodo.org/badge/735258267.svg)](https://doi.org/10.5281/zenodo.15779000)

# AeroAgentSim

<div align="center">
<img src="src/airfogsim/docs/img/logo.png" alt="AeroAgentSim Logo" width="300">
</div>

AeroAgentSim 是当前对外使用的产品名。本仓库的 Python 包名、导入路径和技术命名仍然保持为 `airfogsim`。

AeroAgentSim 基于现有 `airfogsim` 离散事件仿真核心，面向低空车载雾计算中的协同智能研究与开发。当前版本重点在开发者工作台、配置管理、工作流耦合关系可视化、运行控制和轻量 2D 可视化，不再提供 3D 页面。

[English Version](README.md)

## 项目概览

- 安装目标仍然是 `airfogsim`：`pip install airfogsim`
- 本地开发环境建议先执行 `conda activate airfogsim`
- 顶层导入已修复：`from airfogsim import Environment`
- 历史兼容导入仍可用：`from airfogsim import AirFogSimEnv`
- 前端已经重写为 2D 开发者工作台
- 3D 页面和 3D 前端依赖已移除
- 配置快照与运行产物已按 `runtime/aeroagentsim/` 分层存储
- 自定义 `agent` / `task` / `workflow` 定义采用 `registry/aeroagentsim/` 文件主源

## 开发者工作台

当前 AeroAgentSim 工作台包含五个页面：

1. `Overview`：当前配置版本、最近一次运行、校验状态
2. `Class Catalog`：agent/component/task/workflow 元数据和兼容关系
3. `Workflow Studio`：表格表单编辑 + workflow-agent-state 关系图
4. `Run Console`：启动、暂停、恢复、重置、关键路径、实时日志和实时 2D 地图
5. `Trajectories & Logs`：按 `run_id` 查看轨迹和日志

可视化策略固定为轻量 2D：

- 基于 Leaflet
- `simulation_plane` 模式使用 `CRS.Simple`
- `geo_osm` 模式使用真实地理坐标
- 实时展示 agent marker、状态颜色、当前 workflow / task 和近期日志
- 历史轨迹用 2D polyline 展示

关系图仍然不是拖拽式编辑器。配置修改通过表格和表单完成，图负责展示耦合关系、定位节点、突出关键路径、提供校验上下文，并支持缩放和平移查看。在图谱画布内滚轮缩放、拖动画布平移时，不会再联动外层工作台页面滚动。

![Workflow Studio 关系图](docs/images/workflow-studio-relation-graph.png)

当前工作台还支持：

- 全局 `zh-CN` / `en-US` 语言切换
- `Workflow Studio` 页内 `Validate`，用于校验当前草稿
- 集中的 `Review / Validate` 草稿一致性校验入口
- 内置定义与自定义定义合并后的统一目录视图
- 通过表格/表单扩展 workflow 相关定义，而不是上传 Python 脚本

## 自定义注册目录

自定义定义以文件为主源，固定存放在：

- `registry/aeroagentsim/agents/`
- `registry/aeroagentsim/tasks/`
- `registry/aeroagentsim/workflows/`

每个定义都带有统一元数据，例如：

- `id`
- `version`
- `display_name`
- `description`
- `schema_version`
- `source`
- `created_at`
- `updated_at`

当前 v1 采用声明式模型。用户可以在前端扩展 agent、task 和 workflow 定义，但不能直接上传任意 Python 插件代码；运行时仍通过现有 `airfogsim` 组件体系和 workflow 执行逻辑进行适配。

## 运行时模型

配置与运行时产物彻底分层：

- 配置快照是不可变的，存放在 `runtime/aeroagentsim/configs/`
- 每次运行生成独立 `run_id`
- 运行产物写入 `runtime/aeroagentsim/runs/<run_id>/`
- 常见子目录包括 `logs/`、`workflow_states/`、`trajectories/`、`spatial/`、`metrics/`
- SQLite 只保留活动运行缓存和轻量索引，不再作为历史日志主存储

控制与推送分离：

- REST 负责启动、暂停、恢复、重置、保存配置、校验配置和生成关系图
- WebSocket 只负责推送 `sim_status`、`workflow_state_diff`、`spatial_snapshot`、`log_event`

运行启动前还会执行 runtime preflight。像当前运行时未暴露 `create_airspace` / `create_frequency`、因此跳过默认资源注入这类兼容性问题，会继续以 warning 展示；只有 preflight `errors` 才会阻塞 `POST /api/runs`。

## 安装

```bash
conda activate airfogsim
pip install airfogsim
```

> **注意：** PyPI 发布版本可能滞后于源码。如果遇到
> `ImportError`（例如无法导入 `AirFogSimEnv`），请从源码安装：
> ```bash
> pip install git+https://github.com/ZhiweiWei-NAMI/AirFogSim.git
> ```
> 或者克隆仓库后以可编辑模式安装 — 参见 [INSTALL.md](INSTALL.md)。

完整安装步骤、源码安装和前端依赖请参阅 [INSTALL.md](INSTALL.md)。

### 安装后验证

```bash
python -c "import airfogsim; from airfogsim import Environment, AirFogSimEnv; print('ok')"
```

`AirFogSimEnv` 仍然保留为兼容别名，新代码建议优先使用 `Environment`。

## 快速示例

```python
from airfogsim import Environment
from airfogsim.agent import DroneAgent
from airfogsim.component import ChargingComponent, MoveToComponent
from airfogsim.workflow.inspection import create_inspection_workflow

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

当前界面是基于 `airfogsim` 后端的 AeroAgentSim 2D 工作台，不再提供 3D 地图页面。

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
- [系统架构](src/airfogsim/docs/cn/architecture.md)

## 引用

如果您在研究中使用本项目，请引用：

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
