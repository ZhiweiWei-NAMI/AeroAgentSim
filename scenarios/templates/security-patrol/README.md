<!-- guide-language: en-US -->

# Security patrol

A beginner template: a drone patrols a fixed loop of waypoints while an
operator can report an incident, which creates a higher-priority
investigation task.

## What happens

- Patrol tasks at 0 s, 100 s and 200 s drive the drone around the
  configured patrol circuit (`lowalt.patrol.visited` at each point).
- At 55 s the operator ingress injects an incident report; the task engine
  creates an `incident_response` task with priority 100 toward
  `incident-site`.
- The drone breaks off the patrol (priority preemption), investigates, then
  returns to the loop.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Number of aircraft (1–12) | 1 |
| `wind_speed` | Base wind speed in m/s (0–15) | 2.0 |

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`, and `patrol_visits`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/security-patrol/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # browser console for the same scenario
```

Operator events enter through a declared ingress stream onto the same
shared runtime; behaviour owns task state, motion owns pose, one writer
per field.

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).

## Operator story

Choose **interactive** Run mode to replace the built-in scheduled operator demonstration with live input. Select investigation-1, open Operate and inject investigate with origin home and destination incident-site. Why? follows the recorded release through dispatch and completion. Scheduled mode retains the original demonstration.

<!-- guide-language: zh-CN -->

# 安防巡逻

无人机沿固定航点环路巡逻。操作员可报告事件，触发优先级更高的调查任务。

## 场景过程

- 0、100 和 200 秒的巡逻任务沿配置环路飞行，每个航点记录 `lowalt.patrol.visited`。
- 55 秒由操作员输入流注入事件报告，任务引擎生成优先级为 100 的 `incident_response` 任务，目标为 `incident-site`。
- 无人机暂停巡逻、先调查事件，再返回巡逻环路。

## 控制项

| 控制项 | 含义 | 默认值 |
| --- | --- | --- |
| `fleet_size` | 飞行器数量（1–12） | 1 |
| `wind_speed` | 常态风速，米/秒（0–15） | 2.0 |

## 衡量指标

`tasks_completed`、`mean_task_latency_s`、`energy_used_kj`、`weather_holds`、`separation_conflicts` 和 `patrol_visits`。

## 运行与查看

创建项目、配置、校验、保存版本、初始化，再按“启动”。命令行入口：

```bash
aeroagentsim run scenarios/templates/security-patrol/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
```

操作员事件从声明的输入流进入共享运行时；行为引擎写入任务状态，运动引擎写入位姿，每个字段只有一个写入者。
## 黄浦城区布置

坐标来自仓库中的 lite-city 建筑轮廓、屋顶高度和道路，由 `tools/templates/place.py` 生成。屋顶起降点使用实际屋顶高度；站点间飞行先垂直起降，再在 105 米屋顶高度上限之上巡航。名称和业务元数据会随运行固定保存。参见[共享布置指南](../../../docs/examples/city-day.md#huangpu-placements-la3)。

## 操作员故事

选择 **实时交互** 运行方式，以实时输入替代模板自带的定时操作演示。选择investigation-1，打开“操作”并注入investigate，设置起点 home、终点 incident-site。“为什么？”会沿真实记录追踪任务释放、派发和完成。自动运行仍保留原有演示。
