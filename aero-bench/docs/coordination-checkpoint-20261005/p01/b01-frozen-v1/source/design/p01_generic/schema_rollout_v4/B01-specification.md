[B01完整规范开始]
### S01.01 定义规范记录schema

把静态schema、实体/Observer身份、时刻状态/关系/事件和观测索引定义成可验证规范记录。
责任作者：schema_author。前置：S00.03。需求映射：R04, R05, R06。

精确输入：
- 原始字段/关系定义
- manifests/locations.json

本步唯一可写范围：
- src/p01v4/contracts/schema.py
- contracts/schema-v1.json
- tests/unit/test_schema.py
- tests/fixtures/schema_cases.json

必须通过的正例：
- bool/enum/real/bounded/circular/vector/optional/ragged支持；单位与frame存在；missing/inapplicable/absent/zero区分

必须能识别的反例：
- 重复ID、未知字段族、非法单位、NaN/Inf、混用世界与相机坐标被定位到字段路径

本微任务必核问题（在所属交付包选择有价值的问题交互，其余由明确证据覆盖）：
1. 规范记录覆盖了用户哪些字段/关系，逐项映射在哪里？
2. missing、实体不存在和零在序列化中如何区分？
3. 任意新字段是自动支持还是需要注册新family，边界如何被测试？

第二轮重点：独立构造或复核上述反例，结合该步生产源码与原始日志，输出独立结论。第三轮重点：只重验修正影响范围，再检查本步带来的正确性、效果或计算价值，决定保留、简化或继续修正。

证据目录：reports/B01。复用所属交付包的现有日志、实际内容对照、问答与独立审查；包含实际GLM路由/模型/effort、文件owner、命令和资源用量。

首轮预算：{"wall_minutes": 90, "gpu_training_minutes": 0}。
停止门槛：真实字段漏映射不准用UNKNOWN占位后宣称完成；若确需信息则列具体字段和最小问题。

实现后核验入口：${python_executable} -m pytest tests/unit/test_schema.py -q --junitxml reports/S01.01/junit.xml。

### S01.02 实现只读episode导入

从原始数据逐时刻导入全部状态、关系和观测路径，保留来源索引。
责任作者：data_author。前置：S01.01。需求映射：R01, R04, R05, R07。

精确输入：
- 原始一个完整episode
- contracts/schema-v1.json

本步唯一可写范围：
- src/p01v4/data/import_episode.py
- tests/unit/test_import_episode.py
- manifests/episode-pilot.json

必须通过的正例：
- 所有原始时点与边有coverage计数；首末与中间事件均可追溯；原数据保持只读并核路径/记录数

必须能识别的反例：
- 缺模态/缺时间、截断历史、边丢失、跨episode ID误合并被检测

本微任务必核问题（在所属交付包选择有价值的问题交互，其余由明确证据覆盖）：
1. 为何选择这个episode，模态覆盖和时间范围是什么？
2. 原始记录数与规范记录数逐类是否一致？
3. 对照一条中间时刻关系，能从规范记录回到原始证据吗？

第二轮重点：独立构造或复核上述反例，结合该步生产源码与原始日志，输出独立结论。第三轮重点：只重验修正影响范围，再检查本步带来的正确性、效果或计算价值，决定保留、简化或继续修正。

证据目录：reports/B01。复用所属交付包的现有日志、实际内容对照、问答与独立审查；包含实际GLM路由/模型/effort、文件owner、命令和资源用量。

首轮预算：{"wall_minutes": 60, "gpu_training_minutes": 0}。
停止门槛：仅允许读取原始数据并写新版本派生物；缺失模态使对应覆盖项未通过但不阻断其它独立导入。

实现后核验入口：${python_executable} -m pytest tests/unit/test_import_episode.py -q --junitxml reports/S01.02/junit.xml。

### S01.03 实现无损结构文本

实现JSONL与紧凑表可逆编码，并用同一规范记录输出graph/typed输入所需字典。
责任作者：schema_author。前置：S01.02。需求映射：R04, R05, R16。

精确输入：
- 规范episode
- contracts/schema-v1.json

本步唯一可写范围：
- src/p01v4/data/serialization.py
- tests/unit/test_roundtrip.py
- configs/serialization.json

必须通过的正例：
- decode(encode(x))逐字段等价；精度/enum/单位/边/缺失标记保持；顺序重排后语义等价

必须能识别的反例：
- 舍入、漏边、schema漂移、列表顺序语义被破坏时失败

本微任务必核问题（在所属交付包选择有价值的问题交互，其余由明确证据覆盖）：
1. 无损的比较容差来自原始数据精度还是任意设定？
2. 压缩减少了多少实际token，是否丢任何信息？
3. 跨实体/字段顺序变化后的回填ID是否稳定？

第二轮重点：独立构造或复核上述反例，结合该步生产源码与原始日志，输出独立结论。第三轮重点：只重验修正影响范围，再检查本步带来的正确性、效果或计算价值，决定保留、简化或继续修正。

证据目录：reports/B01。复用所属交付包的现有日志、实际内容对照、问答与独立审查；包含实际GLM路由/模型/effort、文件owner、命令和资源用量。

首轮预算：{"wall_minutes": 60, "gpu_training_minutes": 0}。
停止门槛：不能用摘要替代完整规范历史；tokenizer未加载时只报字节数不冒充token数。

实现后核验入口：${python_executable} -m pytest tests/unit/test_roundtrip.py -q --junitxml reports/S01.03/junit.xml。

### S01.04 锁定目标与因果切分

生成明确cutoff与target_times的监督样本，fit归一化仅用允许历史/训练集。
责任作者：data_author。前置：S01.03。需求映射：R04, R07, R12, R17。

精确输入：
- 规范episode
- 字段定义

本步唯一可写范围：
- src/p01v4/data/windows.py
- src/p01v4/data/normalization.py
- configs/pilot-windows.json
- tests/unit/test_no_future_leak.py

必须通过的正例：
- 每项input_time&lt;=cutoff；future RGB/depth/seg只存在target支路；scaler有fit范围版本定位

必须能识别的反例：
- futurecaption、futurepose、未来visibility决定input集合、test-fit normalization均触发失败

本微任务必核问题（在所属交付包选择有价值的问题交互，其余由明确证据覆盖）：
1. 目标时刻和输入截止相差多少物理时间？
2. 哪些未来信息只在label/target encoder，如何防进入context？
3. 同一episode窗口用于工程测试时，报告如何避免称跨episode泛化？

第二轮重点：独立构造或复核上述反例，结合该步生产源码与原始日志，输出独立结论。第三轮重点：只重验修正影响范围，再检查本步带来的正确性、效果或计算价值，决定保留、简化或继续修正。

证据目录：reports/B01。复用所属交付包的现有日志、实际内容对照、问答与独立审查；包含实际GLM路由/模型/effort、文件owner、命令和资源用量。

首轮预算：{"wall_minutes": 60, "gpu_training_minutes": 0}。
停止门槛：未来泄漏检查失败不得开始学习实验。

实现后核验入口：${python_executable} -m pytest tests/unit/test_no_future_leak.py -q --junitxml reports/S01.04/junit.xml。

[B01完整规范结束：S01.01-S01.04，共4步；请确认全部收到]

