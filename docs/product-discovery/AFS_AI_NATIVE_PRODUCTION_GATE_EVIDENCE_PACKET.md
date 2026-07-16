# AFS AI 原生生产体验 Gate Evidence Packet

## Outcome

本任务形成了可供控制线程直接决策的外部研究、AFS 机制差距、v0.1 Goal Contract、单一事实源领域架构、可运行同任务原型、浏览器 QA 与独立评价入口。PR #155、production Studio、服务器与 Provider 均未被修改或调用。

## Deliverables

- 决策文档：`docs/product-discovery/AFS_AI_NATIVE_PRODUCTION_GATE_V0.1.md`
- 评价协议：`docs/product-discovery/AFS_AI_NATIVE_PRODUCTION_GATE_EVALUATION_PROTOCOL.md`
- 可运行原型：`experiments/product-discovery/ai-native-production-gate/`
- 冻结 scenario：`experiments/product-discovery/ai-native-production-gate/scenario.json`
- deterministic model tests：`experiments/product-discovery/ai-native-production-gate/tests/model.test.mjs`
- repository structure tests：`tests/test_ai_native_production_gate_prototype.py`

## Runtime-simulation Evidence

| Gate | 结果 | 证据 |
|---|---|---|
| 故事到可编辑计划 | PASS | 2020 中文字符 + 4 个约束；plan scope 可编辑并在批准后保存 |
| 一次批准启动 3 条任务 | PASS | 3 个 PlanTask 分别进入 completed / waiting-human / running |
| Cockpit 状态与控制 | PASS | Agent、进度、模拟成本、阻断、pause/resume/retry 与人工决定均可见 |
| 产物写回且身份一致 | PASS | 15 个 artifact 使用 stable entity/version refs；run provenance 进入 Storyboard proof |
| reload 恢复 | PASS | plan version、run states、artifacts、active artifact、pending decision 从 localStorage 恢复 |
| Shot7 selective revision | PASS | `shot-007-v1 -> shot-007-v2`；`shot-008-v1` 保持不变；impact proof 可见 |
| Provider 默认禁止 | PASS | 全部数据标记 simulated；`provider_dispatch_count=0` |
| evidence boundary | PASS | UI 与文档只声称 research/prototype/structure/runtime-simulation |

## Browser QA

- 桌面：1707×960；首屏 Mission 与计划可理解；批准后折叠为紧凑摘要，使 Cockpit/Artifact 在同页连续可达。
- 移动端：实际 391×844；使用 tasks / artifacts / decisions companion tabs；Artifact 只突出 Shot7/8 对照；无横向裁切。
- 交互：plan 修改/批准、pause/resume/retry、waiting-human 决策、Shot7 修订、reload 恢复均已 fresh 执行。
- console：原型流程未观察到 error log；页面无 error overlay。
- 当前 candidate fresh 对照：不存在 exact episode 时安全显示“找不到这个单集，或你已无权访问”，符合 review/recovery fail-closed 定位。

截图：

- `desktop-mission.png`
- `desktop-production.png`
- `desktop-recovered-artifact-v3.png`
- `mobile-tasks.png`
- `mobile-artifacts.png`
- `mobile-390x844-v3.png`
- `review-candidate-baseline.png`

这些截图位于本轮可视化目录，不作为 production media artifact。

## Fidelity Ledger

| 概念与实现差异 | 处理 | 判断 |
|---|---|---|
| desktop concept 将三层压缩在单屏 | 批准后 Mission 收为 118px，保留纵向滚动 | 接受：可读性优先，仍保留三层同页关系 |
| concept 使用带质感的分镜缩略图 | 实现使用明确标注 simulated 的线稿占位 | 接受：避免把生成概念图伪装成真实媒体输出 |
| concept 中计划极简 | 实现保留可编辑 scope、预算与批准语义 | 接受：Gate 需要证明计划可修改 |
| mobile concept 同时显示 runs 与 artifacts | 实现改为 companion tabs | 接受：391px 宽度下减少横向/纵向拥挤 |
| mobile concept 展示多个镜头 | 实现优先展示 Shot7/8 对照 | 接受：移动端聚焦待决事项和局部修订证据 |
| concept 有轻量蓝灰/铜色纸面 | tokens 与实现保持深雨蓝、暖铜、白纸面 | 保留 |

## Verification Commands

```powershell
node --test experiments/product-discovery/ai-native-production-gate/tests/model.test.mjs
D:\Projects\AgentFlowStudio\.venv\Scripts\python.exe -m pytest tests\test_ai_native_production_gate_prototype.py -q
git diff --check
```

复测结果：

- deterministic model：7 passed；覆盖故事长度、UI draft/批准时单次 domain revision、原子批准与 run-causal artifact writeback、人工决定、pause/resume/retry、Shot7/8 与 reload。
- repository prototype tests：3 passed。
- PR #155 聚焦领域回归：39 passed，1 个既有 StarletteDeprecationWarning。
- `git diff --check`：通过。
- maintenance audit：0 failed；3 passed、4 warning。warning 为维护审计的非阻断存量类别，另包含本隔离原型单文件 CSS 超过建议行数；该文件未接入生产 surface，正式 Product Lane 必须按组件拆分，不把此原型样式直接迁入 `apps/studio/`。

## Independent Evaluator

结论：`PASS`，限定为 research / prototype / structure / runtime-simulation evidence。

- 8 项硬门全部 PASS。
- evaluator 首轮发现两个 P1：计划输入绕过 command/event；初始 Storyboard 与 decision 预置导致 run→writeback 因果不成立。两项均已修正并增加 deterministic tests，复核后关闭。
- P0/P1：无。
- P2：移动 companion 可补充 per-task cost、阻断摘要以及持续可见的 private/no-training 状态；不阻止本 Gate，正式 Product Lane 需处理。
- evaluator 子任务没有获得独立 Browser 实例，因此其视觉判断基于最新源码、测试与主任务 fresh 截图；主任务已用 Browser 插件重新执行桌面、391×844、reload 与 selective revision。下一轮正式 Product Lane 应再次提供独立 Browser evaluator。
- 建议进入 Contract → Runtime → Product bounded lanes，并保持 additive implementation、Provider closed 与 PR #155 frozen semantics。

## Integration State

- Integration Queue：待 Owner 对 Production Control Contract、ledger/outbox、additive writeback、ProductionRun 分离、atomic plan approval 作决策。
- Runtime Surface Vector：primary/origin master、PR #155、production `/opt`、8790/8791、Provider gates 仅做 readback；没有 mutation。
- Improvement Queue：外部 Agent parity、成本 observable、移动端 companion 和 visual board 角色都保持 `limited_trial_evidence`，未上升为 active Company OS rule。

## Non-claims

无 human acceptance、真实 Provider/media QA、business validation、legal conclusion、public/SaaS readiness、production readiness 或 durable memory promotion 声称。
