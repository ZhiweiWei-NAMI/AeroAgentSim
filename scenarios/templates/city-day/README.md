<!-- guide-language: en-US -->

# City day

A busy but beginner-friendly template: several low-altitude businesses plus
the public traffic-accident chain share one simulated district for 300 s.
A companion walkthrough lives at [docs/examples/city-day.md](../../../docs/examples/city-day.md).

## What happens

- Parcels, inspections, passenger taxi flights and urgent medical deliveries
  run concurrently in the same airspace, from the same vertiports.
- The traffic-accident chain runs in the same world: a road incident forms,
  a reporter chooses a bypass, a responder UAV is dispatched, captures the
  scene and uploads it. Responders keep their own sole motion owners
  (`air_motion`, `road_motion`); the low-altitude airspace engine only
  watches their published positions and moves the low-altitude fleet around
  them.
- Authored timeline: accident activates at 8 s, a wind gust 65–105 s,
  the operator closes `east-port` at 120 s, and an urgent medical order is
  injected at 160 s. A dynamic no-fly zone around the accident activates on detection and expires at
  110 s (responders are exempted). When the responder actually arrives is a
  measured outcome of your run — read it from the journal, not this page.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Parcel drones (1–12), plus three specialist aircraft | 3 |
| `wind_speed` | Wind front speed in m/s (0–15) | 9.0 |
| `allocator` | Task allocation: `nearest`, `auction`, `hungarian`, `llm` | nearest |

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`, `geofence_violations`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/city-day/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # existing browser console
```

Field ownership follows the shared kernel contract:

| Fields / actions | Sole owner |
| --- | --- |
| `lowalt.position`, `lowalt.velocity` | `motion` |
| Traffic UAV pose, velocity and energy | `air_motion` |
| Road position, velocity and occupancy | `road_motion` |
| Lowalt task state, payload, site configuration/open state; static zone geometry | `tasks` |
| Zone IDs, incident zone geometry/activation, membership, conflict distances and measured wind | `airspace` |
| Lowalt SOC, charging, consumed energy, distance and available motion energy | `energy` |
| Shared `lowalt.wind` | `environment` |
| Traffic incident/task/bid/report state, operator event admission, `lowalt.weather_held`, `lowalt.returning_to_charge`, `lowalt.wind_limit_mps` | `behaviour` |
| Traffic route inputs | `route_inputs` |
| Bid feasibility measurements | `traffic_assessment` |
| Real PNG artifacts and capture metadata | `capture` |
| Capture/incident relation and receipt forwarding | `capture_bridge` |
| Scripted decision proposals | `decisions` (no field writes) |

Offline defaults schedule the two operator decisions. For live SDK control, use an operator stream starting at watermark 0 and advance progress after admitting the next command; see the fabric test and the city-day guide. The traffic camera uses the existing headless Chromium/Playwright renderer.

The city camera reuses the traffic capture owner and binds storage to the actual RunSession directory. It honors an explicit Chromium override, discovers an installed Playwright headless shell, or uses Playwright's standard browser installation. No image fixture substitutes for a failed render.

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).

The green field is an explicitly authored demonstration polygon beyond the
northern dense core, not source OSM cropland. Spraying rows are 5 m above its
ground plane; the standalone swap pad is at the field edge.

The four operational rules are editable predicates and event chains; see
[the rules guide](../../../docs/guides/lowalt-rules.md).

## Operator story

Choose **interactive** Run mode to replace the built-in scheduled operator demonstration with live input. Select east-port, open Operate and inject close-vertiport. Why? follows the recorded rerouting through dispatch and completion. Scheduled mode retains the original demonstration.

<!-- guide-language: zh-CN -->

# 城市的一天

多个低空业务与交通事故事件链在同一个城区共享空域，运行 300 仿真秒。另有[完整场景说明](../../../docs/examples/city-day.md)。

## 场景过程

- 包裹、巡检、载客出租飞行和紧急医疗配送在相同空域、相同起降站同时进行。
- 交通事故也在同一个世界发生：道路事件形成后，报告者选择绕行，响应无人机被派出、拍摄现场并上传。响应者保留自己的唯一运动写入者 `air_motion` 和 `road_motion`；低空空域引擎只观察其已发布位置，并调整低空机队避让。
- 编写的时间线为：8 秒激活事故，65–105 秒阵风，120 秒关闭 `east-port`，160 秒注入紧急医疗订单。检测到事故后激活动态禁飞区，110 秒失效，响应者获得豁免。响应者实际何时到达属于运行结果，必须从日志读取。

## 控制项

| 控制项 | 含义 | 默认值 |
| --- | --- | --- |
| `fleet_size` | 包裹无人机数量（1–12），另加三架专业飞行器 | 3 |
| `wind_speed` | 风锋速度，米/秒（0–15） | 9.0 |
| `allocator` | 分配算法：`nearest`、`auction`、`hungarian`、`llm` | nearest |

## 衡量指标

`tasks_completed`、`mean_task_latency_s`、`energy_used_kj`、`weather_holds`、`separation_conflicts`、`geofence_violations`。

## 运行与查看

创建项目、配置、校验、保存版本、初始化，再按“启动”。命令行入口：

```bash
aeroagentsim run scenarios/templates/city-day/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
```

## 字段与动作的唯一写入者

| 字段或动作 | 唯一写入者 |
| --- | --- |
| `lowalt.position`、`lowalt.velocity` | `motion` |
| 交通无人机位姿、速度和能量 | `air_motion` |
| 道路位置、速度和占用 | `road_motion` |
| 低空任务状态、载荷、站点配置与开放状态、静态区域几何 | `tasks` |
| 区域 ID、事故区域几何与激活、区域归属、冲突距离、实测风 | `airspace` |
| 低空电量、充电、能耗、距离和可用运动能量 | `energy` |
| 共享 `lowalt.wind` | `environment` |
| 交通事故、任务、竞价、报告状态，操作员事件准入，天气等待、返航充电和风速上限 | `behaviour` |
| 交通航路输入 | `route_inputs` |
| 竞价可行性测量 | `traffic_assessment` |
| 真实 PNG 文件与拍摄元数据 | `capture` |
| 拍摄与事故关联、回执转发 | `capture_bridge` |
| 脚本决策提案，不写状态字段 | `decisions` |

离线默认配置安排两次操作员决策。实时 SDK 控制使用从水位线零开始的操作员流，并在准入下一条命令后推进进度。交通相机使用现有的无头 Chromium/Playwright 渲染器，相机存储绑定到实际 RunSession 目录；渲染失败不会被图片样例替代。
## 黄浦城区布置

坐标来自仓库中的 lite-city 建筑轮廓、屋顶高度和道路，由 `tools/templates/place.py` 生成。屋顶起降点使用实际屋顶高度；站点间飞行先垂直起降，再在 105 米屋顶高度上限之上巡航。名称和业务元数据会随运行固定保存。参见[共享布置指南](../../../docs/examples/city-day.md#huangpu-placements-la3)。

绿色地块是在密集城区以北明确编写的演示多边形，并非 OSM 农田。喷洒行位于地面上方 5 米，独立换电平台位于田边。四个运行规则是可编辑的谓词与事件链，参见[规则指南](../../../docs/guides/lowalt-rules.md)。

## 操作员故事

选择 **实时交互** 运行方式，以实时输入替代模板自带的定时操作演示。选择east-port，打开“操作”并注入close-vertiport。“为什么？”会沿真实记录追踪改航、派发和完成。自动运行仍保留原有演示。
