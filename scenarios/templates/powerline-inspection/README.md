<!-- guide-language: en-US -->

# Power line inspection

A beginner template: one drone flies along an authored power-line corridor,
records survey observations at measured positions, and pauses its flight
when wind is stronger than its limit.

## What happens

- Inspection tasks are released at 0 s, 130 s and 220 s between `line-start`
  and `line-end`.
- The `survey` work route flies the drone through the configured waypoints of
  the line; each observation event carries a committed position.
- A wind gust is authored between 35 s and 75 s at 9 m/s, above the
  inspection wind limit of 6 m/s, so the drone holds and then continues.
- The airspace engine owns geofence zones; the survey follows the northern
  road corridor above the city roofs.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Number of aircraft (1–12) | 1 |
| `wind_speed` | Base wind speed in m/s (0–15) | 2.0 |

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`, and `inspection_observations`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/powerline-inspection/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # browser console for the same scenario
```

One shared kernel: task state, motion, weather, airspace and energy are
separate engines with one writer per field on the same clock.

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).

## Operator story

Choose **interactive** Run mode, Apply controls to draft, Validate, Save version, Initialize, then Start. Select line-end and inject report-defect for unused task line-survey-2 at that site. Watch the high-priority inspection release, dispatch and real waypoint observations in Why?. Events require a live run; receipt acceptance is followed by recorded domain effects.

<!-- guide-language: zh-CN -->

# 输电线路巡检

一架无人机沿配置的线路走廊飞行，在测得的位置记录巡检观察；风速超过自身上限时暂停飞行。

## 场景过程

- 巡检任务于 0、130 和 220 秒在 `line-start` 与 `line-end` 之间释放。
- `survey` 航路经过已配置的线路航点；每条观察事件都包含已提交的位置。
- 35–75 秒安排 9 米/秒阵风，超过 6 米/秒巡检风速上限；无人机等待后继续飞行。
- 空域引擎管理围栏区域，巡检沿北部道路走廊在城市屋顶上方进行。

## 控制项

| 控制项 | 含义 | 默认值 |
| --- | --- | --- |
| `fleet_size` | 飞行器数量（1–12） | 1 |
| `wind_speed` | 常态风速，米/秒（0–15） | 2.0 |

## 衡量指标

`tasks_completed`、`mean_task_latency_s`、`energy_used_kj`、`weather_holds`、`separation_conflicts` 和 `inspection_observations`。

## 运行与查看

创建项目、配置、校验、保存版本、初始化，再按“启动”。命令行入口：

```bash
aeroagentsim run scenarios/templates/powerline-inspection/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
```

任务、运动、天气、空域和能量引擎共享同一内核与时钟，每个字段只有一个写入者。
## 黄浦城区布置

坐标来自仓库中的 lite-city 建筑轮廓、屋顶高度和道路，由 `tools/templates/place.py` 生成。屋顶起降点使用实际屋顶高度；站点间飞行先垂直起降，再在 105 米屋顶高度上限之上巡航。名称和业务元数据会随运行固定保存。参见[共享布置指南](../../../docs/examples/city-day.md#huangpu-placements-la3)。

## 操作员故事

选择 **实时交互** 运行方式，应用到草稿、校验、保存版本、初始化，再明确启动。选择 line-end，注入 report-defect，指定尚未释放的 line-survey-2 和该站点。观察高优先级复查任务释放、派发与实际航点观测，并查看“为什么？”。 事件须在实时运行中提交；接收回执之后，以日志中的领域影响确认执行。
