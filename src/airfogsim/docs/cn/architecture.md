## AeroAgentSim / airfogsim 系统架构

### 1. 简介

`AeroAgentSim` 是当前对外使用的产品名，技术 Python 包名仍然保持为 `airfogsim`。

整体架构仍然建立在基于 SimPy 的 `airfogsim` 离散事件仿真核心之上。前端层已经重构为 2D 开发者工作台，重点面向配置编辑、工作流耦合关系检查、运行控制、轨迹和日志，而不是 3D 渲染页面。

### 2. 架构分层

* **仿真核心层：** 环境、agent、component、task、workflow、trigger 和各类 manager
* **注册层：** `registry/aeroagentsim/` 下的文件主源定义，以及轻量索引和运行引用
* **目录与配置层：** 内置与自定义合并后的目录提取、兼容性分析、配置快照、校验和关系图生成
* **运行时产物层：** `runtime/aeroagentsim/` 下的配置快照和按 `run_id` 划分的运行目录
* **工作台 API 层：** 面向 catalog/config/run 的 REST 接口和状态推送 WebSocket
* **2D 可视化层：** 基于 Leaflet 的 marker、轨迹 polyline 和 workflow-agent-state 关系图

### 3. 当前工作台重点

当前可视化明确不再提供 3D 页面，保留的视图为：

* `Overview`
* `Class Catalog`
* `Workflow Studio`
* `Run Console`
* `Trajectories & Logs`

工作流编辑以表格和表单为主，关系图只负责展示、定位、突出和辅助校验，不作为拖拽式主编辑器。当前图谱支持缩放和平移阅读，但编辑入口仍然只保留在表格和表单中。滚轮和触控板缩放只作用于图谱画布本身。

当前工作台还补充了：

* 全局 `zh-CN` / `en-US` 界面切换
* 面向草稿和关系图检查的集中 `Review / Validate` 流程
* `agent`、`task`、`workflow` 的内置与自定义统一视图

### 4. 运行时持久化模型

* 配置快照保存在 `runtime/aeroagentsim/configs/`
* 每次运行生成独立 `run_id`
* 运行产物保存在 `runtime/aeroagentsim/runs/<run_id>/`
* 日志、工作流状态、轨迹和空间快照分别落盘
* SQLite 仅保留活动运行索引和轻量缓存职责

### 5. 控制与推送

* REST 负责保存配置、校验配置、生成关系图和运行控制
* REST 同时负责 registry 的增删改查和定义级校验
* WebSocket 只推送 `sim_status`、`workflow_state_diff`、`spatial_snapshot`、`log_event`
* 状态消息附带 `run_id`，便于前端区分活动运行与历史运行

运行启动前会先经过 preflight。`POST /api/configs/{config_id}/preflight` 会进入启动链路，只有 preflight 出现 `errors` 时，`POST /api/runs` 才会被阻塞。

Preflight warning 会继续展示，但不阻塞启动。这包括当前运行时未暴露 `create_airspace` 或 `create_frequency`、因此跳过默认资源注入的兼容性场景。

### 6. 注册与代理编译

自定义 `agent`、`task`、`workflow` 定义采用声明式文件模型，而不是前端直接上传 Python 代码。后端会从 `registry/aeroagentsim/` 读取定义文件、完成校验，并将其编译为代理对象或适配器，再复用现有 `airfogsim` 原语执行。

这样可以让前端具备扩展能力，同时保持后端对可执行行为的边界控制。

### 7. 核心仿真模型

`airfogsim` 后端执行逻辑仍然建立在以下基础抽象之上：

* `Environment`
* `Agent`
* `Component`
* `Task`
* `Workflow`
* `Trigger`
* 各类资源和服务管理器

AeroAgentSim 工作台是在这些后端原语之上增加目录提取、配置适配、关系图构建和运行时观测能力，而不是重写核心仿真执行模型。
