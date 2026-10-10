# WareTrack 完整源码审查

审查日期：2026-10-05。仓库：[siddik-web/waretrack](https://github.com/siddik-web/waretrack)。当前 main 已核验仍为 [`f2bd7329e95629bb09e761929c7ddbf3abfa6183`](https://github.com/siddik-web/waretrack/commit/f2bd7329e95629bb09e761929c7ddbf3abfa6183)，与此前报告版本相同。

## 直接回答

**有订单，而且订单与仓库作业、车辆配送连在一起。** 它同时有订单请求卡、订单队列和逐阶段追踪，不只是把订单数字摆在画面上。

订单请求显示客户、SKU、一托盘数量、库存和倒计时，可接受、加急或拒绝；接受后进入队列，叉车取货、搬到暂存区，再转入配送车任务，最终送到店铺或住宅。队列显示订单号、品类、客户、截止时间、加急、迟到、缺货和Picking/Staged状态，也可提升优先级。追踪条按订单ID连接“下单→拣选→暂存→发货→配送中→送达”。[请求和操作：2031–2113](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L2031-L2113)、[订单面板：3514–3521](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L3514-L3521)、[追踪：3630–3639](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L3630-L3639)

## 完整功能版图

1. **库存和货物**：5类SKU、固定每托盘件数、货架位置、占用/预留、托盘ID、批次和接收时间标签、低库存提醒、补货/调拨入口。[2157–2242](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L2157-L2242)
2. **仓内作业**：自动选择入库/拣选任务，叉车行走、转向、升降、取放托盘，电池/回充、快充、临时工、故障和货架通道封锁。[2245–2394](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L2245-L2394)
3. **卡车与月台**：在途货单、ETA、门口排队、靠台倒车、卸货进度、离场、等待扣分和快速周转奖励。[2398–2460](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L2398-L2460)
4. **城市配送和跨仓**：配送任务队列、车队容量、单车1/2单、道路寻路、客户投递、返仓、承诺时限、评级和费用；五个仓库之间有调拨车，其他四仓持续运行轻量作业模型。[1778–1922](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L1778-L1922)、[2634–2779](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L2634-L2779)
5. **交互与导航**：点选卡车、叉车、月台、充电器、托盘、客户/其他站点；详情检查器、蓝色角框、浮动标签、路线和终点标记；平移、缩放、旋转、跟随、站点飞行、总览、搜索和通知。[3140–3453](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L3140-L3453)
6. **经营与异常决策**：480模拟分钟班次、目标/星级、分数/连击、XP、每日挑战、28项成就、引导教程；故障、洒漏、订单激增、迟到卡车和低库存决策。Hands-on增加手动靠台、释放订单、发车三个操作闸门。[2463–2603](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L2463-L2603)、[2814–2929](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L2814-L2929)
7. **建造和城市**：月台、货架、充电器、休息室和装饰升级；购地、道路、住宅、商店、办公楼、仓储、公园与树木；计时施工、道路连通收入、维护费、邻近加成和撤销。道路有信号灯、跟车与转弯规则。[887–959](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L887-L959)、[1290–1765](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L1290-L1765)、[2931–3135](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L2931-L3135)
8. **进度和呈现**：浏览器本地存档、升级/涂装/累计统计、三个滚动任务、离线收益公式、声音/音量、合成背景音乐、昼间光照、提示和彩纸反馈。[865–1115](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L865-L1115)、[1969–2025](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L1969-L2025)

## 页面布局

它是**固定满视口3D底图 + 覆盖式HUD**：`html/body`锁定100%宽高并禁止页面滚动，`#stage/#hud`固定铺满；顶部站点/工具条、左侧目标/动作/任务、对象检查器、底部业务面板和追踪条覆盖其上。列表在面板内部滚动。宽度≤760px时业务面板变成底部弹出层，打开时隐藏追踪条。[CSS：74–81](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L74-L81)、[移动布局：568–607](https://github.com/siddik-web/waretrack/blob/f2bd7329e95629bb09e761929c7ddbf3abfa6183/index.html#L568-L607)

## 实现边界

- 订单是随机生成的单SKU、整托盘模拟订单，没有通用多商品订单录入/拆单系统。批次与接收时间是标签，拣选不执行FIFO/FEFO。失败的“Backorder”只被记录/扣分，没有持续待补货实体。
- 订单ID能持续追踪；托盘3D对象到发货时删除，再转为配送任务，没有全程同一货物对象及保管交接证据链。跨仓调拨不预留/扣除源仓具体库存。
- 其他仓库用填充率、计数器和随机托盘模拟；部分地面托盘、邻近公司、加油站是布景，部分统计是合成初值。交通有防死锁穿透机制。它没有真实物流后端、工业协议或经验证的交通物理模型。
- 本地保存的是成长/建造进度，重新载入站点会重置现场订单、具体库存与车辆任务。离线收益是公式补发，非离线持续逐单运行。
- 文档不能完全当作实现：README写24项成就，实际28项；规则工作簿的准时率计算器分母用错，实际源码规则不同。

## 用于现有BENCH的参考方式

按用户明确的方向，**保留现有BENCH的GLB资产、地图、渲染器和相机体系**，借鉴以下交互组织：

1. 固定满屏地图，边缘放状态/操作/检查器，列表局部滚动。
2. 订单列表、货物、载具、目的地互相定位；选择同步详情、路线、终点与跟随。
3. 用订单/任务的可追踪阶段，把“谁在执行、货物在哪、下一步是什么、为什么等待”显示清楚。
4. 地图动画和业务面板使用BENCH同一权威帧/事件；保留用户现有ID、回执、来源和证据语义。WareTrack可作为交互参考，其随机业务状态、轻量库存和游戏分数不作为BENCH数据来源。

这次交付是源码审查与参考提炼，没有重写、复制进BENCH、部署或修改任何外部项目。

## 阅读与验证

- 完整递归树共14个跟踪文件，`truncated=false`。
- 全部应用源码仅在`index.html`：**4022行全部按连续区间读完**；全部文本/配置文件及10个工作表也完成读取，含464个非空单元格与32个公式。
- 7份源码/文档/工作簿本地副本均与Git blob哈希匹配；6张JPG和1份GIF是README配图，仅枚举与分类，未冒充已看图或运行视觉测试。
- 内联JS的`node --check`语法检查通过。未启动应用，未做浏览器交互/视觉回归，功能结论来自源码及调用链。
- 详细证据见`WAREHOUSE_ORDERS.md`、`UI_MAINLOOP.md`、`ENGINE_NETWORK.md`、`SUPPORTING_DOCUMENTS.md`；逐文件及连续行覆盖见`READ_COVERAGE.md`、`repository_manifest.json`。
