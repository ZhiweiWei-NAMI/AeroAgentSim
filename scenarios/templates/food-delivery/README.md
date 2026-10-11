<!-- guide-language: en-US -->

# Restaurant meal delivery

Two modeled restaurant kitchens prepare seeded meal orders. Kitchen capacity is
the number of meals that can be prepared concurrently. Waiting orders enter
`preparing` when a slot becomes free, and enter the allocation queue only after
the configured preparation time elapses. FoodOrder is a distinct subtype of Task;
Restaurant is a distinct subtype of Site.

The existing allocation, kinematic motion, energy and airspace engines then move
the courier to the restaurant, load the meal on a successful arrival receipt,
and deliver it on the destination receipt. Every order's promised deadline is
creation time plus its authored delivery window. `on_time_rate` uses committed
food delivery events and deadlines. This models preparation and delivery timing;
it does not estimate food temperature or claim a food-safety outcome.

Edit restaurant position, preparation seconds, kitchen capacity and opening state
in Configure → Objects. Edit meal destinations, release rate,
payload and delivery window in the task generator; edit aircraft capacity,
battery and fleet count in its fleet generator. Template knobs expose the common
parameters directly. The six authored order slots bound this starter scenario;
additional orders require additional FoodOrder entities in the generator pool.

The `restaurant_delay` injection point accepts:

```json
{"restaurant": "restaurant-east", "delay_s": 60.0}
```

Choose `run_mode=interactive` when launching from the template to accept live
events. This mode begins with operator progress at zero and waits for the console
to advance simulation time.
Interactive waiting has no time limit: leaving the run view or disconnecting
does not terminate the run, and Stop remains available in the console.
The default `scheduled` mode closes operator input
through the run horizon, so unattended CLI runs finish without input waits;
scheduled typed commands can still be authored before launching.

In interactive mode, advance to 10 simulated seconds, then inject the event during
preparation. It extends the
remaining preparation of every active order at that kitchen by 60 seconds.
Waiting orders consequently start later when the occupied slot is freed. The
`lowalt.food.preparation_delayed` event records the actual affected order IDs;
an empty list means that no order was preparing there at the injection time.
Prepared meals and deliveries already underway are unaffected.

```bash
aeroagentsim run scenarios/templates/food-delivery/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
```

This is the same independent AeroKernel runtime and generic console as the other
templates. Replay consumes the committed journal without model calls. Site
coordinates come from the existing public Huangpu template placements; restaurant
names describe modeled facilities rather than identified real businesses.

With seed 42 and the default two couriers, all six orders arrive by 321 s of the
360 s run; five meet the 240 s delivery promise. Mean order latency is 169.83 s.
Injecting a 60 s delay at the east kitchen at 10 s moves the first meal's ready
time from 38 s to 98 s and its delivery from 89 s to 149 s. All orders still
finish by 327 s, but only three meet their deadlines and mean latency rises to
187.50 s. These are outcomes from the same committed state and event stream used
by the console and replay.

<!-- guide-language: zh-CN -->

# 餐食外卖配送

两个建模餐厅厨房准备按种子生成的餐食订单。厨房容量表示可同时准备的餐食数量；有空位时，等待订单进入 `preparing`，只有配置的准备时间结束后才进入分配队列。`FoodOrder` 是 `Task` 的独立子类型，`Restaurant` 是 `Site` 的独立子类型。

分配、运动、能量和空域引擎先把配送无人机派往餐厅，收到成功到达回执后装载餐食，再在目的地到达回执后完成配送。订单承诺时限为创建时间加配置的配送窗口。`on_time_rate` 使用已提交的配送事件和时限计算。本场景只建模准备与配送时间，不估算食物温度，也不声称得出食品安全结果。

## 配置与衡量

在“对象”的普通字段中编辑餐厅位置、准备秒数、厨房容量和营业状态。在任务生成器中编辑目的地、释放速率、载荷和配送窗口；在机队生成器中编辑飞行器容量、电池与机队数量。常用参数也直接显示在场景控制项中。这个入门场景只有六个已编写的订单槽位，增加订单需要在生成器池中添加 `FoodOrder` 实体。

右侧列出实际控制项和 KPI，包括完成订单、延迟和准时率。创建项目后，依次校验、保存版本、初始化并按“启动”。

## 餐厅延迟事件

`restaurant_delay` 注入点接受：

```json
{"restaurant": "restaurant-east", "delay_s": 60.0}
```

选择 `run_mode=interactive` 可接收实时事件。此模式从操作员进度零开始，等待控制台推进仿真时间。输入等待没有时限；离开运行视图或断开连接不会终止运行，仍可用“停止”结束。

默认 `scheduled` 模式把操作员输入关闭至运行时限，无人值守的命令行运行因此可以结束；启动前仍可编写计划中的带类型命令。

交互模式下，先推进到 10 仿真秒，在备餐期间注入事件。它将该厨房每个正在准备的订单的剩余准备时间延长 60 秒；占用槽位更晚释放，排队订单也更晚开始。`lowalt.food.preparation_delayed` 记录实际受影响的订单 ID，空列表表示注入时该厨房没有正在准备的订单。已备好或已开始配送的餐食不受影响。

```bash
aeroagentsim run scenarios/templates/food-delivery/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
```

本模板使用与其他模板相同的独立 AeroKernel 运行时和工作台。回放读取已提交日志，不调用模型。站点坐标来自黄浦模板的公开布置，餐厅名称描述建模设施，不代表已识别的真实商户。

## 已测案例

种子 42、默认两架配送无人机时，360 秒运行中的六个订单在 321 秒前全部到达，其中五个满足 240 秒配送承诺，平均订单延迟 169.83 秒。10 秒时给东侧厨房注入 60 秒延迟，第一餐的备好时间由 38 秒变为 98 秒，送达时间由 89 秒变为 149 秒。所有订单仍在 327 秒前完成，但只有三个准时，平均延迟升至 187.50 秒。这些结果来自工作台和回放共同使用的已提交状态与事件流。
