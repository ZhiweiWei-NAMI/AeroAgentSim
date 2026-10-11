<!-- guide-language: en-US -->

# Parcel delivery

Eight drones deliver seeded orders from two vertiports to 25 customer rooftops.
Use the fleet size, order rate, allocator, path planner and wind knobs to compare
completed deliveries, latency and energy in a 300 s run. Orders arrive in bursts of four and are allocated every 10 s over all pending orders and free drones, so `nearest`, `auction` and `hungarian` give different results (see `docs/guides/algorithms.md`).

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).

New order bursts stop at 180 s, leaving two minutes for the backlog to clear.
Unreleased authored task slots are capacity, not created orders. Multi-bay
vertiports have four bays; parcel pickup does not use the passenger pad queue.
Battery, airspace and wind policy is editable in [the shared rules](../../../docs/guides/lowalt-rules.md).

At the default seed 42, the shipped `hold_and_go` resolver completes 18 of 20
created orders by 300 s, versus 13 with altitude layers at the same demand and
horizon. Two orders remain unfinished, not reported as failures. This is a worked
example, not a guarantee: the matched three-seed experiment found that eight
aircraft completed fewer orders than four under heavier shared-site congestion.
See [the measured comparison](../../../docs/guides/algorithms.md#comparing-allocators).

<!-- guide-language: zh-CN -->

# 城市包裹配送

八架无人机从两个起降站向 25 个客户屋顶配送按种子生成的订单。调节机队数量、订单速率、分配算法、航路规划和风速，比较 300 秒内的完成量、延迟和能耗。订单每批四个，每 10 秒对待分配订单与空闲无人机统一分配，因此 `nearest`、`auction` 和 `hungarian` 会产生不同结果。

## 运行与查看

创建项目、配置、校验、保存版本并初始化。初始化后没有运动，按“启动”开始仿真。使用“记录”对比不同配置的运行；底部播放控件只移动观看时间。

## 衡量指标

完成任务数量、平均任务延迟、能耗、天气等待与间隔冲突。右侧“衡量指标”列出模板实际记录的 KPI 字段；“可调整的控制项”列出当前模板声明的全部控制项。
## 黄浦城区布置

坐标来自仓库中的 lite-city 建筑轮廓、屋顶高度和道路，由 `tools/templates/place.py` 生成。屋顶起降点使用实际屋顶高度；站点间飞行先垂直起降，再在 105 米屋顶高度上限之上巡航。名称和业务元数据会随运行固定保存。参见[共享布置指南](../../../docs/examples/city-day.md#huangpu-placements-la3)。

新订单在 180 秒停止到达，留出两分钟清理积压。尚未释放的任务槽位是容量，不是已创建订单。起降站有四个泊位，包裹取货不使用乘客降落平台队列。电池、空域和风速策略可在[共享规则](../../../docs/guides/lowalt-rules.md)中编辑。

默认种子 42、当前 `hold_and_go` 配置下，300 秒内完成 20 个已创建订单中的 18 个；相同需求和时限下，高度分层完成 13 个。剩余两个订单是尚未完成，并未报告为失败。这只是一个已测案例：三个种子的配对实验中，共享站点拥堵更严重时，八架飞行器反而比四架完成得少。参见[算法对比](../../../docs/guides/algorithms.md#comparing-allocators)。
