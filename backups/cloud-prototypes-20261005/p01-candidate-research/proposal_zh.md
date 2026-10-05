# P01：Transformer + 图主干与 Jev 式概率接口建议

核查日期：2026-10-05。范围：架构研究；未下载权重、未运行训练、未使用收费 API。数据背景沿用任务上下文：1,640 个 TRAIN 窗口、820 个 VALID 窗口，当前 2 个谓词，计划 12 个家族、72 个可执行谓词、91 个 typed 字段；这些统计本次未重新核验。

## 结论

按最新收窄，主候选定为 **每实体局部 typed 子图编码器 → entity-time tokens → 完全冻结的 Qwen3.5-2B-Base → 并行 predicate-query 概率头**。第一阶段只训练局部图编码器、projector 和输出头；LoRA 留到有证据需要时再做第二阶段。图分支保留到输出端的直接连接，避免丢失数值边界。一次前向输出所有谓词的 11-bin 分布，不调用文本生成。4B-Base 是后续规模对照，不先上 9B/27B。

这不是已证明最优的架构。当前数据量很小且只有两个谓词，**原生 GraphGPS + 同一概率头**可能更稳、更快；必须与主候选同预算比较。大模型的选择是研究假设，不是性能结论。GRU 可留历史基线，不作为目标主干。

## 1. 已核实的外部事实

- Qwen 官方发布了 Qwen3.5-2B-Base、4B-Base。Base 卡片顶部明确标为预训练权重，面向 fine-tuning/研究；卡片下方通用 overview 仍写了 Pre-training & Post-training，应以顶部明确说明为准。2B 文本主干为 24 层、维度 2048；4B 为 32 层、维度 2560。[S1–S3]
- Qwen3.5 是 3:1 的 Gated DeltaNet / full attention 因果混合主干。它不是普通全注意力 Transformer，不能声称删除一个 causal mask 就得到双向 encoder。官方 Transformers 的 Qwen3_5TextModel 接受 inputs_embeds，返回 last_hidden_state，因此可接自定义概率头，不必生成任何 token。[S4–S5]
- GraphGPS 的核心配方是结构/位置编码、真实边局部消息传递、全局注意力。Graphormer 说明相对图结构偏置可放入 attention。两者是结构设计依据；Graphormer 已公开预训练主要来自分子数据，不能假设其权重适配本任务的状态与关系。[S6–S8]
- GraphAdapter 证明 GNN 与预训练 LLM 的结合值得研究，但原论文主要是 text-attributed graph 和节点文本训练，不能作为本任务数值轨迹校准有效的实验证据。[S9]
- 此处 Jev 是 TypeSafe 的 System One 模型，不是 JEPA。官方接口让多项 typed questions 对同一个 state 并行、独立求值；Choice/Score 返回分布，Noul 返回 yes 的概率。可借鉴这种接口；已查材料没有足够架构、训练细节或公开权重支持“复现 Jev”。[S10–S11]
- Jev 的 confidence 是由输出分布计算的集中度统计，Noul 没有另一个 confidence。类型合法、分布尖锐、概率校准是三个不同问题；不能把 softmax 或 schema 校验称为解决了校准。[S12]

## 2. 建议数据流

输入只包含预测时点可得的历史、地图/规则、以及由这些历史生成的 K 条预测状态轨迹。

1. **每实体局部子图。** 将一个实体关联的状态、属性、必要邻居/关系组织成 typed 子图。连续量按字段/单位标准化，角度采用周期表示，类别用 embedding；区分 observed、predicted、unknown、not-applicable，并保留有效性 mask。未知值不得当 0 或 False。91 个字段按实体及语义组组织，不转成大段小数文本。
2. **局部图编码器。** 从 GraphGPS 的“结构编码 + 真实边消息传递 + attention”配方取适合局部子图的部分。起点为 2–4 层、宽度 128–256；对每实体、每时刻的局部图输出一个 token，然后投影到 Qwen 的 2048 维。第一版 S=1 个 token/实体/时刻；S=2–4 个属性组 slots 只作后续信息瓶颈消融，不先把模型做复杂。
3. **关系不能在池化时消失。** 跨实体边必须经 relation-aware message passing 写进局部表示，或保留带源/目标绑定的边界关系 tokens。可组合使用；不能只有实体属性 token，再假设 Qwen 自动知道邻接关系。规则 AST 的实体绑定、边类型和时间编码必须与 token 索引一致。attention 发生在 tokens 之间；实体 token 内部细节由图编码器保留。
4. **entity-time 序列。** 以时间优先、实体采用一致的可审计顺序排列；加入实体角色、相对时间、状态来源和 missingness 信息。K 条预测轨迹不串到同一序列：有效 batch 为 B×K。若历史含 H 个点（含 t0）、未来10点、每实体时刻 S 个 slots，则每条序列 L≈L_schema + N×(H+10)×S + R_boundary + L_summary。实际需按每场景实体数与边界边数统计，而非提前声称 token 很少。
5. **冻结 Qwen。** Qwen3.5-2B-Base 文本主干不训练，不使用 vision tower、LM vocab head 或 generate；保留因果混合结构，训练时 use_cache=False。输入走 inputs_embeds。局部图 token 已携带局部属性/关系，Qwen 提供跨实体/跨时间上下文变换。注意它不是双向全图 attention：后 token 可读前 token，前 token 不能读后 token；输出头必须能读取整个序列，可另加一个通用末尾 summary token，不能把所有实体 hidden state 都说成已融合全场景。只有实验证明后才能称冻结语言主干学会了本领域交互。
6. **显式绑定的独立查询头。** 在 Qwen 外部放一到两层共享 cross-attention。每个 predicate query 由 AST、家族、参数、绑定实体及对应 entity-time 索引生成，独立读取完整 Qwen hidden states；可把依赖子图做 attention bias，并结合图特征 skip path 与规则 margin trace。query 之间不做 self-attention；query 不进入共享 Qwen 上下文。因此增加/调换其他问题不应改变当前问题输出，也不必每个谓词重跑主干。
7. **输出。** 每个 query 读出11个 logits，整理成 [B,P,K,11]；在11维各自 softmax，再对K等权平均得到 [B,P,11]。若预测器提供带权样本，按其合法权重求和。绝不先平均 logits。K 条轨迹各自编码，不做跨轨迹 attention；K1 与 K8 来自同一预测分布，不能一边用均值轨迹、一边用随机样本。

例如 N=20、H=5、S=1，仅 entity-time 就有300 tokens/轨迹，K8是8份300-token序列；若 N=100、H=10，已经有2,000 tokens/轨迹，另加规则语义和边界关系。P=72 只扩展共享 head 的查询数量，不把 Qwen 前向复制72遍。第一版先做场景真实 N/H/边数分位数统计，再定 padding、bucket 和 token budget。
“一次前向”指 batch 内一次非自回归计算图，P 个问题并行。K=8 仍有接近 8 份轨迹相关计算，不能承诺 K 增大没有成本。可共享历史编码，但跨样本 Qwen/DeltaNet cache 复用必须单独验证状态、mask 和梯度语义，不能先假设可行。

## 3. 规则应放在哪里

**执行语义由 AST/程序保留，模型学习 forecast 到真实结果的残差与不确定性。**

若完整、无噪声的状态样本已经给出，AST 运行即可确定该样本的谓词真值与首次翻转时刻。此时让网络重新猜规则属于近似执行，不天然优于程序。故必须有 Rule-MC：每条预测轨迹精确执行 AST，得到 first-flip 的 one-hot，再对 K 求均值。

神经头有意义的部分是：预测器误差、未观测字段、模型不确定性、有限 K，以及新规则/新场景的共享校准。输入可包含对**预测状态**计算的真假/unknown/margin 轨迹，不能包含真实未来的规则结果。完整样本规则 one-hot 可作为可审计辅助分支；学习残差时不得把任意 logit 修正包装成规则正确性保证。

如任务标签确为 first flip，定义 tau 为第一个与已知 t0 谓词值不同的未来点。第 11 类仅表示在完整的 10 点窗口内没有观察到翻转；它不区分“以后翻转”和“永不翻转”。起点未知、期间关键字段缺失、窗口不足，是 mask/censoring，不是第 11 类。若产品其实要 first-true/onset，必须另立标签，不能混用。

## 4. 损失与验证

对平均分布 p_bar 定义 F_j=sum(p_bar[1:j])，j=1…10：

- RPS = mean_j (F_j - 1[tau<=j])²
- event probability = 1 - p_bar[11]
- 主损失 = RPS + lambda × BCE(event probability, 1[tau<=10])

lambda 从小范围验证，而不是把 BCE 权重设为无限大牺牲时序。RPS 与无偏 BCE 的正权和可同时约束时间与窗口内发生概率；class weighting/focal loss 会改变概率目标，采用时需解释及重新校准。缺失/删失标签只对可识别的部分计分，不能硬塞完整 11 类监督。

如果 K 条是针对同一真实未来的预测样本，实际观测只给聚合分布一个标签；**不能把同一真值复制给每条样本，宣称增加了 K 倍独立监督**。可另做 AST 确定性一致性辅助任务，但它测的是规则执行。

1,640 个窗口也未必是 1,640 个独立场景；先按原始轨迹/场景/时间分组划分，避免重叠窗口泄漏。820 VALID 不宜同时承担反复调参、校准和最终报告：需保留独立 test，或用分组嵌套/交叉拟合。

最小比较矩阵：Rule-MC；原生 GraphGPS；局部图编码器 + 冻结 Qwen；在该冻结方案上加 Qwen 2B LoRA；最后才是 4B。公平固定预测器、划分、mask、K 和计算预算。报告 RPS、Brier/NLL、事件 PR-AUC、reliability/ECE、每家族表现、分组 bootstrap 置信区间、峰值显存及延迟。

必要消融：去掉图真实边；去掉 Qwen；语义名称替换成随机 ID；去掉 AST/margin；去掉残差校准。局部图节点置换、问题排列、追加无关问题、K1 作为 K8 子样本等应作不变量测试；Qwen 的实体序列排序不天然置换不变，需单独测敏感性，并用一致排序/排序增强降低影响，不能声称已由 graph encoder 自动解决。只有这组证据能说明收益来自图结构或预训练语义，而非参数量/泄漏。

当前 2 个谓词的结果不能推出 72 个谓词或 12 家族泛化。扩展需要真实字段覆盖、AST 可执行性、足够的可识别标签，以及按家族/参数留出的泛化测试。真实未来只作训练标签；把真实未来放入输入的实验必须独立标成 oracle 上界。

## 5. 选型取舍

| 路线 | 优点 | 本任务主要风险 | 定位 |
|---|---|---|---|
| 原生 typed GraphGPS | 图结构直接、数值路径短、参数少、双向图上下文自然 | 需任务内预训练/监督，语义迁移有限 | 最强必要对照；可能是实际部署赢家 |
| 状态/图全部序列化后 Qwen LoRA | 可快速复用语言主干 | 图偏置弱、数字分词、排列敏感、长上下文浪费 | 不建议作为主路线 |
| 每实体局部图 + 冻结 Qwen3.5-2B-Base + 独立头 | 符合最新方案；训练范围小；共享并行读出 | 图到语言隐空间失配、冻结因果主干未必适合数值交互、激活成本 | 第一阶段主候选 |
| 同上 + 小 LoRA | 允许主干适应 typed tokens | 额外过拟合及调参风险 | 第二阶段，由冻结方案表现触发 |
| GraphGPS + Qwen3-1.7B-Base | 纯文本、常规 attention，修改和部署路径较简单 | 仍是语言预训练，收益同样待证 | Qwen3.5 内核/适配不稳时的对照或替代 [S13] |
| JEPA/V-JEPA | 可借鉴自监督 latent prediction 作未来研究 | 已核实的 Jev 与之不同；视频权重也不直接适配 typed 状态图 | 本轮不混入主路线 |

LoRA 只减少可训练参数，并不会自动给予图结构能力。语言预训练可能帮助字段/规则描述、组合语义和少量迁移；精确数值边界、物理关系与校准仍须由表示、执行器和任务数据证明。

## 6. 显存与 LoRA 初步预算

RTX 3090 官方为每卡 24 GB；“3 张 3090”尚未在本次任务实际核验，也不能视为一块统一 72 GB 显存。[S14]

以下是**名义参数量的算术下界，不是实测**：冻结 BF16 文本主干仅权重，2B/4B/9B 约 4/8/18 GB（十进制）；4-bit 裸权重约 1/2/4.5 GB，尚未计量化元数据、未量化层、激活和工作区。2B 的 embedding 约 5.09 亿参数，保留/裁去必须按真实输入设计，不能随意丢掉后又声称复用了文字语义。

总训练显存约 2×冻结参数数 + 每可训练参数约12–18 bytes + 激活 + 临时缓冲；不同 optimizer/dtype 有差别。全参数 mixed-precision AdamW 的常见记账为约18 bytes/参数加激活，因此不建议当前上全参 2B/4B。[S15]

以下只是第二阶段可选预算，不属于第一阶段训练范围。按当前官方 2B 配置和实现，若只给最后8层的 full-attention q/k/v/o 与 DeltaNet in_proj_qkv、in_proj_z、out_proj 加 rank8 LoRA，约103万 LoRA 参数；rank16 约206万。这是明确模块集合的推算，未含 graph/projector/head 或 FFN LoRA；实施时应枚举模块、校验计数并固定版本。[S3,S5]

第一阶段先测完全冻结的 BF16 2B、真实 entity-time token budget、microbatch 1–2、activation checkpointing；如确需第二阶段，再测试 LoRA/QLoRA。冻结 Qwen 后若仍训练输入 projector，梯度仍需穿过 Qwen，不能整体 no_grad。QLoRA 是可选内存优化，不能预先保证混合 DeltaNet 的全部内核路径兼容或更快。[S4,S16]

DDP 在各卡复制主干；多卡主要提高吞吐，不能直接解决单卡模型放不下。K8 可按 microbatch 分段计算并保持同一个概率平均目标；不得把分段均值的非线性损失简单求平均冒充完整 mixture loss。先实际核验 GPU、驱动、PyTorch、Transformers、PEFT、causal_conv1d/fla 及 peak-memory，再承诺训练配置。

## 来源（仅官方模型卡、作者论文/代码、官方技术文档）

- S1 Qwen3.5-2B-Base：https://huggingface.co/Qwen/Qwen3.5-2B-Base
- S2 Qwen3.5-4B-Base：https://huggingface.co/Qwen/Qwen3.5-4B-Base
- S3 2B Base config：https://huggingface.co/Qwen/Qwen3.5-2B-Base/blob/main/config.json
- S4 Transformers Qwen3.5 文档：https://huggingface.co/docs/transformers/model_doc/qwen3_5
- S5 Transformers 官方实现：https://github.com/huggingface/transformers/blob/main/src/transformers/models/qwen3_5/modeling_qwen3_5.py
- S6 GraphGPS 作者论文：https://arxiv.org/abs/2205.12454；作者代码：https://github.com/rampasek/GraphGPS
- S7 Graphormer 原论文：https://proceedings.neurips.cc/paper/2021/file/f1c1592588411002af340cbaedd6fc33-Paper.pdf
- S8 Microsoft Graphormer：https://github.com/microsoft/Graphormer
- S9 GraphAdapter 作者论文：https://arxiv.org/abs/2402.12984
- S10 TypeSafe Jev 发布说明：https://typesafe.ai/blog/introducing-system-one-models-and-jev
- S11 TypeSafe 接口简介：https://docs.typesafe.ai/introduction；Noul：https://docs.typesafe.ai/primitives/noul
- S12 TypeSafe confidence 定义：https://docs.typesafe.ai/confidence
- S13 Qwen3-1.7B-Base：https://huggingface.co/Qwen/Qwen3-1.7B-Base
- S14 NVIDIA RTX3090 规格：https://www.nvidia.com/en-eu/geforce/graphics-cards/30-series/rtx-3090/
- S15 Hugging Face 训练内存记账：https://huggingface.co/docs/transformers/v4.47.1/model_memory_anatomy
- S16 QLoRA 原论文：https://arxiv.org/abs/2305.14314；PEFT 量化指南：https://huggingface.co/docs/peft/main/en/developer_guides/quantization

来源局限：QwenLM/Qwen3.5 仓库链接本次已重定向为更新系列，因此型号事实优先依据固定型号 HF 卡片和配置；实施必须固定模型 revision 与库版本。以上设计是本任务的架构建议，不是这些论文已经验证过的 P01 系统。
