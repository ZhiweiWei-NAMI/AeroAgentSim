<!-- guide-language: en-US -->

# Traffic-accident experiment

This experiment combines road motion, UAV motion, weather, model-assisted decisions and a shared event-chain runtime. A stopped pair of vehicles creates an incident. A reporter chooses a bypass and reports it. The coordinator solicits bids and dispatches an eligible UAV to photograph the scene. The UAV's upload completes the task.

Run it after [installation](../../../docs/getting-started/install.md):

```bash
aeroagentsim demo traffic-accident
```

The command imports an editable project and opens the workbench. Configure it, Validate, Save version, Initialize, then press Start; initialization holds the world at simulated time zero. For a file-based run without the console, use `aeroagentsim demo traffic-accident --headless`.

## What is included

The committed [scenario](../../demos/traffic-accident/scenario.yaml) contains 63 road vehicles, eight UAVs and a nonspatial coordinator, together with task and incident entities. The [registry snapshot](../../demos/traffic-accident/registry.snapshot.json) and local [overlay](../../demos/traffic-accident/registry.overlay.yaml) describe their types, fields, events and relations. The AeroGraph repository is optional.

The default decision profile uses [authored scripted replies](../../demos/traffic-accident/fixtures/decisions.json). These are explicit experimental inputs, not historical or live model responses. The [lite city](../../demos/traffic-accident/inputs/lite-city/scene.json) uses OSM-derived footprints and roads with procedural building, car and UAV geometry; see its [attribution](../../demos/traffic-accident/inputs/lite-city/ATTRIBUTION.txt). No separately licensed mesh pack is required.

## Follow the simulated timeline

These times describe the shipped kinematic/scripted configuration. They are simulated seconds, not wall-clock durations; live decisions or changed physics can change them.

| Simulated time | Stage | What to inspect |
| --- | --- | --- |
| 0 s | Automatic routines begin | Road and UAV poses, active tasks and chain instances |
| 8 s | Accident activation | Incident state and its participant relations; actual road-owner stop commands |
| After 8 s | Detection, report and solicitation | Committed stopping/distance predicates, reporter route choice and agent records |
| About 15.07 s | Award committed | Bravo's current eligibility, reservation and task suspension; Alpha keeps its medical task |
| Before 49 s | Travel, hold and dwell | Physical command receipts and the three-second sampled stationary/position condition |
| About 49.07 s | Capture stored | The real Chromium-rendered PNG and its request association |
| About 49.27 s | Upload accepted | Separate acceptance event and completed capture chain |
| 90 s | Run horizon | Completed run status and journal available for replay |

Default road/air publication is 1 Hz. The shared message lag is 66,666,667 ns, so message times need not fall on whole seconds. On the current development machine, upload has been observed at roughly 27 seconds of wall time; hardware, rendering and inference affect that duration. Use the CLI output and journal for the times of your own experiment.

The decisions propose actions; runtime rules authorize them against current committed state. Alpha's task is noninterruptible. Bravo's patrol can be interrupted, but assignment still requires current eligibility and an available capture reservation. A proposed award cannot substitute for an owner receipt, and a stored photograph is distinct from an accepted upload.

## Who owns what

| Plugin / engine | Responsibility |
| --- | --- |
| `traffic_road_motion` / `road_motion` | Road vehicle pose, speed and road-command execution |
| `kinematic` / `air_motion` | UAV pose, velocity, motion execution and energy state |
| `environment` / `weather` | Weather forcing |
| `traffic_route_inputs` / `route_inputs` | Authored road and UAV route inputs |
| `traffic_assessment` / `traffic_assessment` | Computed feasibility and eligibility inputs |
| `behaviour` / `behaviour` | Incident/task state, relations, reservations and event-chain execution |
| `traffic_decisions` / `decisions` | Explicit scripted proposals; the live profile replaces decision production |
| `traffic_camera_capture` / `capture` | Camera rendering and stored capture events |
| `traffic_capture_bridge` / `capture_bridge` | Upload checks and acceptance |

All engines share one runtime. Field bindings in the scenario assign one writer to each state field. A domain replacement changes the binding/configuration and starts a new run; it cannot write alongside the existing owner.

## Change the experiment

In Configure, change one factor at a time, validate, save a version and initialize a new run, then press Start. For file-based experiments, copy the complete scenario directory to keep relative snapshot, behaviour and input references together.

- **Accident timing:** the [behaviour package](../../demos/traffic-accident/behaviours.yaml) declares the accident timer and the named `accident` injection point. Change the timer for a scheduled experiment, or use [external ingress](../../../docs/guides/external-events.md) for operator-controlled timing.
- **Motion resolution:** change both motion engines' `step_ns` deliberately. Check sampled dwell conditions and message lag when changing publication cadence.
- **Weather:** configure the environment engine and preserve its field ownership. Follow [Weather](../../../docs/guides/weather.md).
- **Task policy:** edit eligibility predicates, relation selectors and guards in the package. Keep the Alpha/Bravo distinction explicit if it is a research factor.
- **Decision model:** follow [Deploy an agent](../../../docs/guides/agents.md) and use `--profile live-llm` with configured model and credentials. Live inference currently blocks simulated time while producing a decision; replay uses recorded decisions without calling the provider.
- **Native physics:** the demo's SUMO/PX4 profiles are configuration contracts, not ready-to-run substitutions. Start from the working [SUMO](../../../docs/guides/sumo.md) or [PX4/Gazebo](../../../docs/guides/px4-gazebo.md) adapter examples and supply the declared mappings and services.

## Inspect and compare

Select Bravo in the 3D scene, object list or graph to follow its task relations and receipts in the inspector; open the capture artifact after storage. Graph and 3D views use the same entity identity and selected journal cut. Nonspatial tasks and coordinators remain in the graph even when they have no 3D representation.

Use the top bar to pause simulation, resume or stop; use Records for replay and comparison. An input wait is visible as `waiting_for_input`; advance the declared stream rather than interpreting the wait as completion. Playback controls only move the viewing timeline.

Replay a completed run with `aeroagentsim replay <run-directory>`. Keep configuration changes in separate runs and compare their committed outcomes. Default lean provenance retains replay and semantic events; select `--provenance full` for deeper engine diagnostics. See [Runs and replay](../../../docs/concepts/runs.md) and [Visualize and inspect](../../../docs/guides/visualization.md).

<!-- guide-language: zh-CN -->

# 交通事故响应实验

本实验把道路运动、无人机运动、天气、辅助决策与共享事件链运行时接在一起。两辆停止车辆形成事故，报告者选择绕行并报告；协调者征集竞价，派遣符合条件的无人机拍摄现场，上传被接受后完成任务。

## 创建与运行

在工作台打开本指南，创建项目、配置、校验、保存版本并初始化。初始化后保持静止，按“启动”才开始仿真。也可在安装后运行：

```bash
aeroagentsim demo traffic-accident
```

无控制台的文件运行可用 `aeroagentsim demo traffic-accident --headless`。

## 场景包含什么

仓库场景包含 63 辆道路车辆、八架无人机、一个没有空间位置的协调者，以及任务与事故实体。注册表快照和本地 overlay 声明它们的类型、字段、事件和关系；AeroGraph 仓库是可选项。

默认决策使用明确编写的脚本回复，它们是实验输入，并非历史或实时模型响应。lite-city 使用来自 OSM 的建筑轮廓和道路，加上程序生成的建筑、车辆与无人机几何，不需要额外授权的网格资源包。

## 仿真时间线

下列时间来自当前运动学与脚本配置，单位为仿真秒，不是墙钟秒；实时决策和不同物理配置可能改变它们。

| 仿真时间 | 阶段 | 查看什么 |
| --- | --- | --- |
| 0 秒 | 自动日常任务开始 | 车辆与无人机位姿、活动任务、事件链实例 |
| 8 秒 | 事故激活 | 事故状态、参与者关系、道路写入者实际停止命令 |
| 8 秒以后 | 检测、报告、征集竞价 | 已提交的停止与距离谓词、绕行选择、智能体记录 |
| 约 15.07 秒 | 中标提交 | Bravo 当前资格、资源预留与任务暂停；Alpha 保留医疗任务 |
| 49 秒以前 | 赶赴、等待、驻留 | 物理命令回执、三秒采样静止与位置条件 |
| 约 49.07 秒 | 照片存储 | 真实 Chromium 渲染的 PNG 与请求关联 |
| 约 49.27 秒 | 上传接受 | 独立接受事件与完成的拍摄事件链 |
| 90 秒 | 到达运行时限 | 已完成状态与可回放日志 |

道路和空中状态默认以 1 Hz 发布。共享消息延迟为 66,666,667 纳秒，事件时间不必落在整秒。开发机曾观察到上传约需 27 墙钟秒，硬件、渲染和推理会影响耗时；以本次运行的输出与日志为准。

决策只提出动作，运行规则依当前已提交状态授权。Alpha 的任务不可中断，Bravo 的巡逻可中断，但分配仍要求当前资格和可用的拍摄预留。中标提案不能替代写入者回执，照片存储也不同于上传被接受。

## 引擎分工

| 插件或引擎 | 职责 |
| --- | --- |
| `traffic_road_motion` / `road_motion` | 道路车辆位姿、速度和道路命令执行 |
| `kinematic` / `air_motion` | 无人机位姿、速度、运动执行和能量状态 |
| `environment` / `weather` | 天气输入 |
| `traffic_route_inputs` / `route_inputs` | 编写的车辆与无人机航路输入 |
| `traffic_assessment` | 计算可行性与资格 |
| `behaviour` | 事故与任务状态、关系、预留和事件链执行 |
| `traffic_decisions` / `decisions` | 明确的脚本提案；实时配置替换决策生产方式 |
| `traffic_camera_capture` / `capture` | 相机渲染与已存储拍摄事件 |
| `traffic_capture_bridge` / `capture_bridge` | 上传检查与接受 |

引擎共享同一运行时。场景字段绑定为每个状态字段指定唯一写入者。替换一个领域实现时，需要改变绑定或配置并开始新运行，不能与原写入者同时写同一字段。

## 改变实验

在配置面板中一次改变一个因素，校验、保存版本并初始化新运行。文件实验应复制完整场景目录，保持快照、行为和输入的相对引用。

- 事故时间：修改行为包中的定时器，或使用 `accident` 外部注入点控制时间。
- 运动分辨率：有意地修改两个运动引擎的 `step_ns`，同时检查采样驻留条件与消息延迟。
- 天气：修改环境引擎配置，保留字段写入归属。
- 任务策略：修改资格谓词、关系选择器和守卫。研究需要时保留 Alpha 与 Bravo 的明确区别。
- 决策模型：配置模型和凭据后使用 `--profile live-llm`。当前实时推理生成决策时会阻塞仿真时间，回放使用记录的决策，不调用模型提供商。
- 原生物理：SUMO/PX4 配置是接口契约，不是可直接运行的替代实现；需要适配器示例所声明的映射与服务。

## 检查与对比

选择 Bravo，查看任务关系与回执，照片存储后打开拍摄文件。图与 3D 使用同一实体身份和日志切片；没有空间位置的任务和协调者仍在图中。

顶部控制暂停、继续或停止仿真。`waiting_for_input` 表示正在等输入，应推进声明的输入流；底部播放控件只改变观看时间。使用“记录”回放、对比运行或“修改后重试”，为不同配置保留独立运行。

```bash
aeroagentsim replay <run-directory>
```

默认精简溯源保留回放与语义事件；需要更深入的引擎诊断时选择 `--provenance full`。
