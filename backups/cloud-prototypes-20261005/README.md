# 云端成果恢复包 · 2026-10-05

目标分支：`backup/cloud-prototypes-20261005`。本分支用于保存项目成果，不代表合并、部署或功能验收。默认分支和现有 PR 不作修改。

## 内容和状态

- **P02 稳定快照**：`frontend/console-prototype/` 完整保留提交 `68b3d4a65799008e8d3ae0f175faf64d9ef60b44` 的 46 个文件。远端备份以新提交保存逐字节快照；`68b3d4a` 是来源标识，不声称原始 commit 对象已发布。基础是公共仓库已有的 `488face80242131638cd69a188678e100bad648e`。
- **P02 未完成 WIP**：`p02-wip/` 包含相对于上述稳定版的 patch、3 个新增文件及暂停状态。它们尚未应用到稳定版。原有有限 smoke 检查不等于完整回归或视觉验收。
- **术语和图契约**：`docs/integration/` 8 篇文档逐字节来自 `ed2dc9a11873971774b0b4dbdf097efefe2cb128`；该原提交的另外 2 个文档导航改动不在本次正文清单内。
- **WareTrack 原创审查**：`waretrack-original-audit/` 8 份成果，包括源码定位、阅读覆盖和第三方仓库文件元数据。这里没有第三方源码、图片、工作簿或其他原始资产，也不授予第三方内容再分发许可。报告中的第三方链接仅用于溯源。
- **P01 架构候选**：`p01-candidate-research/proposal_zh.md` 是研究建议，未实施、未训练、未证明性能最优；原文数据背景本次未重新验证。
- **应急图源码**：`docs/examples/emergency-delivery/` 包含生成器、模板和历史/当前测试；`docs/examples/emergency-delivery-graph.config.json` 是归档配置。大体积派生 HTML、fixture 和 coverage 不在本分支重复保存，可从这里的源码重建。P08 单独备份，不混入本分支。
- **清单**：`MANIFEST.json` 列出本次成果的路径、字节数、SHA-256、Git blob SHA；`SHA256SUMS` 可直接验证。清单不声称覆盖继承自公共仓库的全部历史文件。清单文件自身不列入清单，避免自引用。

恢复说明和验证记录优先于归档文件中“尚未发布”等历史状态描述。

## 新增交接文件（2026-10-05）

- `handoffs/integration/`：AeroBench 为运行与原城市场景权威的集成实施规格、服务器下一步、固定版本源码清单，以及 `city.w17.s03` 的精简源证据。源证据保留 15 个节点及所选节点之间的 16 条边、原始 ID/JSON pointer/hash；它不是完整图或运行结果，私有来源页面链接已省略。源代码本身使用清单中的固定公共链接读取，不重复发布检索缓存。
- `handoffs/p01-qwen/`：Qwen3.5-2B-Base 训练交接、初始示例配置、文档/算术核验记录。未下载权重，未实测服务器显存，未开始 Qwen 训练。
- `handoffs/p09-enhancement/REVIEW.md`：通信/计算到实际动作的最小闭环增强，以及一次采集前的 A/B/C 分类建议；未实施采集或仿真，最终采用范围与数量仍需服务器证据。

本次新增 8 个交接文件，并更新本文、总清单和校验和。现有稳定版、WIP、源码和审查成果保持原样。交接中的历史本地项目路径用于来源定位，不表示那些文件也包含在本次新增文件中。报告仅作研究/实施交接，不能当作已部署、已训练或已采集的证明。


## 获取和完整性检查

```bash
git clone --branch backup/cloud-prototypes-20261005 \
  https://github.com/ZhiweiWei-NAMI/AeroAgentSim.git AeroAgentSim-recovery
cd AeroAgentSim-recovery
sha256sum -c backups/cloud-prototypes-20261005/SHA256SUMS
```

## 启动稳定版

要求 Node.js >=24.15.0、npm；这里验证使用 Node.js 24.19.0、npm 11.9.0。首次安装需要访问 npm 注册表。

```bash
cd frontend/console-prototype
npm ci
npm test
npm run check
npm run build
npm start
```

打开 `http://127.0.0.1:4317`。服务仅绑定 loopback。配置和回放是合成 fixture；本地浏览器缓存不是后端数据库，不代表 BENCH/SUMO/ns-3/Atlas 已连接。

导出可重跑记录：

```bash
node scripts/emit-graph-inventory.mjs > graph-inventory.json
node scripts/emit-graph-fixtures.mjs > graph-fixtures.json
node scripts/emit-emergency-graph.mjs > emergency-canonical-projection.json
```

实际浏览器检查需在获授权的预览地址和已安装 Chromium 的环境另行运行：

```bash
P02_CONSOLE_URL=http://127.0.0.1:4317 \
  P02_CHROMIUM=/usr/bin/chromium \
  P02_EVIDENCE_DIR=/tmp/p02-browser-evidence npm run graph:browser:qa
```

本次没有执行上述实际浏览器命令；DOM 检查、几何断言和 build 不等于实际浏览器或响应式视觉通过。

## 从轻量源码恢复应急 HTML

完成上述 `npm ci` 后，在仓库根目录执行；输出目录必须尚不存在，原有文件不会被覆盖。

```bash
python3 backups/cloud-prototypes-20261005/rebuild_emergency.py \
  /tmp/aero-emergency-recovered
```

打开 `/tmp/aero-emergency-recovered/emergency-delivery-graph.html`。包装脚本先生成历史 fixture 基础，再投影稳定版 canonical graph，并运行当前 DOM 测试。归档源码保持原样；脚本仅在输出副本中替换 DOM 测试的两个机器特定路径。当前生成器中的 inventory 备用绝对路径不会用到，因为稳定 exporter 提供 inventory。

本次重建通过 4,244 条断言。配置和 coverage 与归档产物逐字节相同；fixture/HTML 中 `canonical_source_sha256` 随 exporter 的 `exported_config.exported_at` 新生成时间变化，因此二者不能直接按旧文件 SHA 判定失败。除此字段外，重建 fixture 与归档 fixture 深层比较完全相同。

`emergency-tests-historical.json` 是归档历史测试结果；`VERIFICATION.json` 是此次恢复检查结果。没有实际浏览器、真实物理执行或原生谓词真值验收。

## 可选：在新分支恢复 WIP

这不是继续开发或验收承诺。先保存已有本地改动，再创建新分支，不把 patch 应用到主线：

```bash
git switch -c recovery/p02-wip backup/cloud-prototypes-20261005
B=backups/cloud-prototypes-20261005/p02-wip
F=frontend/console-prototype
git apply --check "$B/unverified-working-tree.patch"
git apply "$B/unverified-working-tree.patch"
mkdir -p "$F/examples" "$F/verification" "$F/tests"
cp "$B/examples/charging-contention.config.json" "$F/examples/"
cp "$B/verification/completeness-ledger.json" "$F/verification/"
cp "$B/tests/completeness-ledger.test.js" "$F/tests/"
```

此后需独立完成回归、build 和真实浏览器检查。资源准入交互、事务编辑 UI、source/task/lease 详情及重新生成 inventory 仍有未完成内容，详见 `p02-wip/PAUSED_STATUS.json`。本次仅确认 patch 能干净应用，未将 WIP 标为稳定。

## 发布范围与不足

没有备份账号状态、凭据、私有会话、依赖目录、缓存、原始第三方资产、服务器运行目录、P08 大产物。文件名和明显凭据模式已扫描，所有新增内容限制为上述成果。继承自公共基础分支的历史不是新上传内容。原始 Git bundle 和 commit 历史包不包含在本次上传范围，原始本地文件保留不动。

所有模拟对象及应急情景均为独立编写的虚构示例；观察不是事实真值，场景策略不是法律，fixture 通过不是安全许可。WareTrack 报告和 P01 提案保存的是核查当时的结论，本次未重新审查第三方仓库或外部模型文档。

