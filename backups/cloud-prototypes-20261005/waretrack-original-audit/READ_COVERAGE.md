# 阅读覆盖与验证清单

固定提交：f2bd7329e95629bb09e761929c7ddbf3abfa6183。当前 main 在2026-10-05 12:36 UTC核验相同。

## 全仓库枚举

完整递归树共14个跟踪文件；truncated=false。以下列出全部文件，未省略目录深处文件。

|文件|Git blob SHA|大小（字节）|阅读状态|
|---|---|---:|---|
|.gitignore|779765b71ce0001bb73e18ffbdc499299cfa7232|39|全部4行通读|
|.nojekyll|e69de29bb2d1d6434b8b29ae775ad8c2e48c5391|0|空文件核验|
|README.md|09f638f1d2ab89e8bf3fd573356bd98caafe0d29|6866|全部76行通读|
|WareTrack_game_rules.xlsx|4f4fd263d55abfbd0c0864f897701155287e790f|27134|10表全部使用单元格、公式、缓存与备注读取|
|docs/media/city.jpg|b86bc76f914b2a559941d667d8e3a0c67f151718|196660|仅核对完整树/README引用；非代码媒体，未看像素|
|docs/media/inspector.jpg|e96720c38a84c22ee7bd38d0af6815a6727e729e|181757|仅核对完整树/README引用；非代码媒体，未看像素|
|docs/media/order-requests.jpg|5fda9a7f5103019ca17452737ee97f9cdc64ac9f|182515|仅核对完整树/README引用；非代码媒体，未看像素|
|docs/media/preview.gif|e544c0e300e750e77db414859673588112f10338|1527951|仅核对完整树/README引用；非代码媒体，未看像素|
|docs/media/shift.jpg|430627688ffade0e5268316d5fe832dc1f3ede1f|182310|仅核对完整树/README引用；非代码媒体，未看像素|
|docs/media/site-select.jpg|b594c719b2d24e2dd821edd78a04f0f262f74c00|135187|仅核对完整树/README引用；非代码媒体，未看像素|
|docs/media/town-planner.jpg|bd872be41f7f6891068bd58793642be299308799|171796|仅核对完整树/README引用；非代码媒体，未看像素|
|docs/superpowers/plans/2026-10-04-construction-system.md|5b1227466d40c6f04463dae97b93459b19e8e07f|28309|全部567行通读|
|docs/superpowers/specs/2026-10-04-construction-system-design.md|2bd158198e0c38326e8257345c01b410793937e6|5804|全部128行通读|
|index.html|fc49a4102d35883f2aa904a47e2abaf6c30e40c1|319001|全部4022行连续通读|

## index.html逐行覆盖

以下是实际顺序全文读取区间；每个读取均未截断，不以关键词命中代替全文。四组并集为1–4022，缺口0行。

- 1–180、181–360、361–535、536–715
- 716–1000、1001–1289、1290–1500、1501–1765、1766–2030
- 2031–2200、2201–2370、2371–2540、2541–2710、2711–2880、2881–2999
- 3000–3139、3140–3274、3275–3412、3413–3556、3557–3697、3698–3817、3818–3943、3944–4022

各模块另有跨区间调用链核对。源码唯一可执行应用文件是index.html；README、施工计划/设计包含说明与代码示例，也已全文阅读，未将历史计划当作实现事实。

## 工作簿覆盖

|工作表|使用区域|非空单元格|公式|
|---|---|---:|---:|
|Shift calculator|A1:C25|45|8|
|Warehouses|A1:P12|100|5|
|Scoring|A1:C15|38|0|
|Incidents|A1:G12|53|1|
|Actions|A1:D8|22|0|
|Upgrades|A1:F9|27|4|
|World and traffic|A1:C20|53|2|
|Deliveries|A1:C13|32|4|
|Delivery upgrades|A1:G13|50|8|
|Challenge and rewards|A1:C17|44|0|

共479个序列化单元格，其中464非空、15格式空白、32公式、1备注。没有仅抽样表头/部分行，也没有将缓存结果当作当前应用运行证明。

## 验证边界

- 7份应用/文档/配置/工作簿原始字节的Git blob SHA均一致
- 内联JavaScript已进行node --check：通过
- 未启动/操作游戏；未做浏览器视觉、性能或交互回归
- 6张JPG与1份GIF仅枚举分类，不属于应用代码，本轮未读取像素
- 无应用改动、BENCH资产替换、部署或外部写入
- 交付包只含审查报告与覆盖清单，不包含第三方完整源码、工作簿原件或图像资产
