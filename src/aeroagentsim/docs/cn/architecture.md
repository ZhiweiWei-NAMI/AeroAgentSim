## AeroAgentSim 系统架构

### 1. 简介

AeroAgentSim 将离散事件仿真后端与开发者工作台结合在一起，用于工作流配置、校验、运行控制以及运行时检查。

当前架构的核心是基于 SimPy 的 `aeroagentsim` 仿真内核。工作台在这个内核之上提供目录提取、配置快照、关系图检查、运行诊断、轨迹回放和日志分析能力。

### 2. 架构分层

* **仿真核心层：** environment、agent、component、task、workflow、trigger 和各类 manager
* **Registry 层：** `registry/aeroagentsim/` 下的文件主源定义，以及轻量 DB 索引和运行引用
* **目录与配置层：** 内置/自定义目录提取、兼容性查询、配置快照、校验和关系图生成
* **运行时产物层：** `runtime/aeroagentsim/` 下的配置快照与按 `run_id` 划分的运行目录
* **Workbench API 层：** 面向 catalog、config、run 的 REST 接口和状态推送 WebSocket
* **可视化层：** 实时 2D marker、轨迹 polyline 和 workflow-agent-state 关系图

### 3. 工作台交互模型

工作台由以下页面组成：

* `Overview`
* `Class Catalog`
* `Workflow Studio`
* `Run Console`
* `Trajectories & Logs`

`Workflow Studio` 通过表格和表单完成持久化配置编辑。关系图是交互式检查画布，支持自动布局、画布内滚轮或触控板缩放、空白背景拖拽平移，以及节点拖拽微调布局。

### 4. 运行时持久化模型

* 配置快照保存在 `runtime/aeroagentsim/configs/`
* 每次运行生成独立 `run_id`
* 运行产物保存在 `runtime/aeroagentsim/runs/<run_id>/`
* 日志、工作流状态差异、轨迹、空间快照和指标分别持久化
* SQLite 用于活动运行索引和轻量缓存

### 5. 控制与推送

* REST 负责配置保存、校验、关系图生成和运行控制
* REST 同时负责 registry 的增删改查和定义级校验
* WebSocket 推送 `sim_status`、`workflow_state_diff`、`spatial_snapshot`、`log_event`
* 状态消息携带 `run_id`，便于前端区分活动运行与历史运行

运行启动前会经过 preflight。`POST /api/configs/{config_id}/preflight` 进入启动链路，只有 preflight 出现阻塞性错误时，`POST /api/runs` 才会停止启动。

像默认 `create_airspace` 或 `create_frequency` 注入被跳过这类兼容性发现，会继续以 warning 形式展示。

### 6. Registry 与代理编译

自定义 `agent`、`task`、`workflow` 定义采用声明式文件模型。后端从 `registry/aeroagentsim/` 读取定义文件、完成校验，并编译成代理或适配对象，再复用现有运行时原语执行。

### 7. 核心仿真模型

后端执行模型建立在以下基础抽象之上：

* `Environment`
* `Agent`
* `Component`
* `Task`
* `Workflow`
* `Trigger`
* 各类资源和服务管理器

这些原语仍然是执行逻辑的真实来源。AeroAgentSim 工作台在它们之上增加了目录提取、配置适配、运行观测和前端检查能力。
