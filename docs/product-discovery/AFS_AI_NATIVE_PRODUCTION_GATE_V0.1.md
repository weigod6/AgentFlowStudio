# AFS AI 原生生产体验 Gate v0.1

状态：`research / prototype / structure / runtime-simulation evidence`

面向对象：AFS 控制线程与后续正式实现任务。本文不是产品发布说明，不改变 PR #155 的既有领域语义，也不代表人类验收、真实 Provider/媒体 QA、商业成熟或 production readiness。

## 1. Fresh 基线与边界

- 2026-07-15 fresh 复核：primary 与 `origin/master` 均为 `d0efbb451bd2172a9b1d565d605bfd8c1b38cb5b`，primary clean。
- Draft PR #155：`codex/episode-loop-program-20260715`，head `2249f0bfab171ee6199410a0ca6348f58a0adb31`，仍未合并、未部署；本 Gate 基于该 head 的隔离分支工作。
- `/opt` 仍为 `d0efbb45`。8790 local/auth/ready，但 root-owned systemd drop-ins 打开了 llm/image/video/vision gates；本任务没有调用 Provider、没有写服务器、没有部署，也没有触碰 `/home`、`/test` 或 8791。
- 本 turn 能力为 `approval_policy=never`、`sandbox=danger-full-access`；没有请求 `require_escalated`。
- 写入仅限 `experiments/product-discovery/ai-native-production-gate/`、本目录文档与对应测试；没有修改 `apps/studio/`、PR #155 frozen domain contract 或共享 common。

## 2. 冻结 Goal Contract

### 2.1 目标用户与首要任务

目标用户是 1–8 人的小型漫剧团队与个人创作者。首要任务不是“操作模型”或“编辑 Shot ID”，而是：给出故事/剧本、参考与约束，得到可编辑的生产计划；一次批准后让智能制片中枢并行执行；在同一事实面上理解进度、成本、阻断和产物；只修需要修的部分并恢复到可审核、可交付状态。

### 2.2 默认入口与三层 IA

1. **Mission / Agent Control**：目标、剧本、参考、约束、计划、预算、Provider gate 与批准。
2. **Production Cockpit**：Agent、任务、并行分支、进度、成本、等待、阻断、暂停、重试与人工介入。
3. **Artifact Workspace**：Storyboard 是规范化单集产物与审核面；素材库承载可复用资产；可选 visual board 只服务非线性探索、分支与素材组织；contextual inspector 做精确控制。

默认入口是带项目语境的 Mission，不是全局悬浮聊天框。现有 Project/Episode shell + Storyboard + inspector 保持 `review_and_recovery_workspace_candidate` 定位，只是第三层的一部分。

### 2.3 冻结 scenario

`雨灯 · 第一集`：输入 2,020 个中文字符、4 条参考约束，计划产生 3 条有边界的并行任务，写回 15 个镜头。场景包含一个待人工决定的角色设定阻断；Shot7 只修灯扣、疤痕、雨线与表情，Shot8 的对象身份与版本必须保持不变；随后进入连续性检查、Review 与 Delivery 事实。

### 2.4 非目标

- 不在本 Gate 内调用真实 LLM、image、video、vision、ASR 或外部下载。
- 不把 visual board、无限画布或聊天框当作完整生产系统。
- 不连接 production Studio，不部署，不 merge PR #155，不新建 PR。
- 不修改 PR #155 的 aggregate、continuity、typed command、review 或 delivery 语义。
- 不声称真实媒体质量、用户接受度、商业价值、法律结论或生产就绪。

## 3. 外部产品研究

证据标签：`O` = 本轮可直接观察的官方界面/公开仓库；`V` = vendor claim；`T` = third-party field evidence；`I` = 基于已列证据的推断。官方动态站点、登录后界面或 JavaScript-only 页面无法直接验证时，不把搜索摘要或推测升级为 observation。

| 产品 | 用户如何开始 | AI 拆解与执行 | 产物回写 / 人介入 | 画布 / Storyboard | 成本、失败、恢复 | Agent 外部接口 | 证据判断 |
|---|---|---|---|---|---|---|---|
| WorkRally | Project → Series → Shot；生成请求绑定稳定对象 ID | `count > 1` 可创建多个独立任务；公开状态含 queued/running/paused/success/failed/cancelled | 运行占位会更新为结果，并进入 media library | Canvas 是对象与产物的组织面，不等于任务控制面 | 有 pause/cancel 状态；未找到公开的批准前成本、幂等或失败恢复机制 | 官方 Skill/CLI/MCP 暴露 Project/Series/Shot 与生成命令 | `O/V`：公开 Skill 与仓库；`T`：腾讯云实测文章；缺失项不作正面断言 |
| LibTV | Agent-IM 接收用户请求；新 CLI 直接进入 workspace/project/node/group/script/storyboard | vendor claim 为后台 Agent 拆解模型、参数与步骤；第三方演示脚本到批量分镜/视频与并行节点 | 同一 session/projectUuid/canvas URL 轮询与下载；用户可在画布继续编辑 | 画布承担项目、节点与分镜组织 | 未找到公开 per-task cost、waiting-human、pause/resume、幂等证据 | 官方 Skill 与 CLI 形成外部 Agent 入口 | `O/V`：官方 Skill、CLI；`T`：品玩、新榜；登录后真实工作区本轮不可直接观察 |
| Runway Workflows | 从 workflow 或 published app 开始 | node、dependency 与 parallel groups；Active Runs 展示执行中实例 | node history 可恢复旧输出、设为 current/favorite，节点可 lock | Workflow canvas 是生成图；不是故事审核的唯一表面 | Active Runs 可 cancel；项目 credits 可见；节点历史支持恢复 | Workflow 可发布为 App 或 endpoint | `V/O`：官方 Help Center；应用内部运行结果未登录实测 |
| LTX Studio | 从 brief/script 进入 scene、shot、element 拆解 | vendor claim 为 preview breakdown 与自动 storyboard | Storyboard 非破坏编辑；Retake 只替换选中片段；Elements 保持身份 | Storyboard 是规范化叙事产物，Gen Space 更接近探索面 | 未找到独立 Production Cockpit、每任务成本或 waiting-human 公开证据 | LTX product 与 LTX Video API 是相邻但不同边界 | `V`：官方教程/支持/API；`T`：TechRadar 评测 |
| Firefly Boards | 从 moodboard、storyboard、vision board 开始 | 适合概念探索与素材生成，不展示任务编排 | 产物留在 board 供组合、比较和继续生成 | 明确是 visual exploration board | 未发现 Agent run、阻断或恢复 cockpit | Firefly API 提供媒体能力，不等于 Boards 控制面 | `V/O`：Adobe 官方说明与 API 文档 |

### 3.1 可核验来源

- WorkRally：[官方 Skill](https://github.com/Tencent/workrally/blob/main/SKILL.md)、[AI generation guide](https://github.com/Tencent/workrally/blob/main/references/ai-generation-guide.md)、[repository](https://github.com/Tencent/workrally)、[腾讯云第三方实测](https://cloud.tencent.com/developer/article/2693070)
- LibTV：[官方 Agent Skill](https://github.com/libtv-labs/libtv-skills/blob/main/skills/libtv-skill/SKILL.md)、[CLI](https://www.liblib.tv/cli)、[品玩实测](https://www.pingwest.com/a/312278)、[新榜案例](https://www.newrank.cn/article/detail/34085)
- Runway：[Introduction to Workflows](https://help.runwayml.com/hc/en-us/articles/45763528999699-Introduction-to-Workflows)、[Utility Nodes](https://help.runwayml.com/hc/en-us/articles/47184761711379-Using-Utility-Nodes-in-Workflows)、[First Workflow](https://help.runwayml.com/hc/en-us/articles/45769159004691-Building-your-first-Workflows)、[Apps](https://help.runwayml.com/hc/en-us/articles/47865876793747-Publishing-Workflows-as-Apps)、[Endpoints](https://help.runwayml.com/hc/en-us/articles/50682960972947-Publishing-a-Workflow-as-an-Endpoint)
- LTX：[storyboard](https://ltx.io/blog/how-to-storyboard)、[storyboard update](https://ltx.io/blog/ltx-storyboard-generator-update)、[tutorial](https://ltx.io/blog/ltx-studio-tutorial)、[Retake](https://ltx.io/blog/retake-ai-directing-tool-ltx-studio)、[Elements](https://ltx.io/blog/getting-started-with-elements)、[product/API boundary](https://support.ltx.studio/hc/en-us/articles/32487503247122)、[API docs](https://docs.ltx.video/)、[third-party review](https://www.techradar.com/pro/software-services/ltx-studio-ai-video-production-review)
- Adobe：[Boards quick guide](https://helpx.adobe.com/uk/firefly/web/create-mood-boards/firefly-boards/use-quick-guides.html)、[Boards overview](https://www.adobe.com/products/firefly/features/boards.html)、[Firefly API](https://developer.adobe.com/firefly-services/docs/firefly-api/api/)

### 3.2 研究结论

可迁移的机制不是“再做一个画布”，而是：稳定领域对象、运行占位与写回、明确的 Active Runs、依赖与并行组、可恢复的节点/版本历史、局部 Retake、视觉探索与规范化 Storyboard 分工、UI 与外部 Agent 操作同一对象。没有公开证据表明任一参考产品同时完成了 AFS 本 Gate 的全部事实链，因此竞争产品只能提供机制线索，不能替代 AFS 自己的领域合同。

## 4. AFS 现状与机制差距

| 同一维度 | PR #155 已有机制强项 | 当前缺失层 | Gate 决策 |
|---|---|---|---|
| 开始 | Project/Episode/Version exact refs，私有、fail-closed | 没有 Mission、故事/参考 intake 与可编辑 Plan | 在 episode aggregate 之前增加 Production Control Contract |
| 计划 | typed commands、CAS、幂等 | 没有 PlanRevision、Task DAG、一次批准 | `plan.approve` 原子产生至少 3 个有边界任务 |
| 执行 | AssetCandidate job 轴与安全 projection | 没有 ProductionTask/Run/Attempt/AgentAssignment | ProductionRun 与 AssetCandidate.JobState 分离 |
| 人介入 | exact approval、selection、review、delivery facts | 没有 waiting-human、Blocker、HumanDecisionRequest | 决策是 first-class domain object，不是聊天消息 |
| 成本 / Gate | private/no-training、provider fail-closed | cost observability 当前 unavailable；无 BudgetEnvelope | 预算与 Provider gate 进入批准与 run admission |
| 产物回写 | stable IDs、immutable versions、exact refs | 缺少 Agent artifact writeback command/event | 增加 additive writeback commands，不修改既有 aggregate 语义 |
| 局部修订 | Shot7 exact successor、Shot8 preserved；continuity predicted/applied/undo | 缺少从 task/run 到 revision request 的因果链 | SelectiveRevisionRequest + ImpactAssessment 连接既有命令 |
| 恢复 | aggregate store restart、pending command idempotent replay | 没有 Plan/Run/Decision 的统一恢复投影 | append-only project event ledger + 可重建 projections |
| 外部 Agent | runtime API 有 typed commands | 没有 Mission/Plan/Run 的 Skill/CLI/MCP 等价面 | 所有入口复用同一 command/event gateway |
| 前端 | review/recovery workspace candidate 可精确审核与恢复 | 不是生产入口，也不构成 Agent Cockpit | 保持第三层定位，新增前两层而不是把它抬成权威前端 |

本轮 fresh 打开 `/studio/episode-workspace/`：页面标题为“AFS · 单集制作工作区”；当 exact project/episode/version 不存在时，安全显示“找不到这个单集，或你已无权访问”。这是正确的 fail-closed review/recovery 行为，同时也说明其入口依赖既有单集事实，不能承担 Mission → Plan 的默认入口。

## 5. v0.1 产品与领域架构

### 5.1 关键领域对象

- Mission / MissionRevision / ReferenceConstraint
- ProductionPlan / PlanRevision / PlanTask / PlanApprovalDecision
- AgentAssignment / ProductionRun / RunAttempt
- BudgetEnvelope / CostEstimate / CostEntry / ProviderGateDecision
- Blocker / HumanDecisionRequest / HumanDecision
- ArtifactWriteback / SelectiveRevisionRequest / ImpactAssessment
- 既有 Project / Episode / Scene / Shot / AssetCandidate / Continuity / Review / Delivery 对象保持不变

### 5.2 单一事实源

前端、Agent Skill/CLI/MCP 与后台 worker 只能调用同一个 domain command gateway，并订阅同一 project event ledger。命令最少携带：`org_id`、`project_id`、`actor`、`expected_version`、`idempotency_key`、`correlation_id`、`causation_id`、exact object refs、capability、provider/budget authorization。若 Production Control 暂时作为 sidecar 落地，必须使用 atomic outbox 与既有 aggregate 建立可恢复的一致性边界；禁止维护第二套 UI-only 或 Agent-only 流程事实。

### 5.3 事实链

```text
MissionRecorded
  -> PlanProposed -> PlanRevised -> PlanApproved
  -> TaskQueued x3+
  -> RunStarted / RunProgressed / RunWaitingHuman / RunRetried / RunCompleted
  -> ArtifactCandidateRegistered -> ArtifactWrittenBack
  -> SelectiveRevisionRequested -> ImpactAssessed
  -> Shot7 exact successor + Shot8 exact ref preserved
  -> ContinuityReviewRecorded -> ReviewDecisionRecorded -> DeliveryRecorded
```

### 5.4 最小 Run 状态模型

执行状态严格限定为：`queued / running / waiting-human / retrying / blocked / completed / cancelled`。控制状态另行表达：`active / pause-requested / paused / resume-requested / cancel-requested`。这样暂停不伪装成业务阻断，重试不覆盖失败 attempt，cancel 也不擦除历史。

关键语义：

- `pause`：停止领取下一安全单元，不撤销已经写回的已提交事实。
- `resume`：从可恢复 checkpoint 继续，同一 run identity，新 attempt 仅在需要时创建。
- `retry`：显式创建新 RunAttempt，复用 command idempotency，不能重复扣费或重复写回。
- `restart`：projection 可由 ledger 重建；未完成命令按 idempotency key 安全重放。
- `waiting-human`：必须绑定 exact HumanDecisionRequest、可选项、影响范围与截止条件。
- `blocked`：非用户选择即可解决的问题，必须有 blocker owner 与解除证据。
- `cancel`：保留已写回 artifact identity 与 provenance，不把取消等同于删除。

### 5.5 预算、成本与 Provider gate

- Plan 在批准前展示估算区间、计价单位、假设与最大预算。
- 每个 run 显示 `estimated / committed / actual`，模拟值必须带 `simulated=true`。
- Provider capability 默认 closed。只有能力级授权、预算 admission 与隐私策略同时通过，worker 才能产生 provider dispatch。
- 本原型 `provider_dispatch_count=0`；所有 Agent、进度、耗时、成本与 artifact 都显式标注 simulated。

### 5.6 人类决策点

1. 计划修改与一次批准；2. 超预算或 Provider gate；3. 影响故事/角色/场景连续性的选项；4. blocked run 的替代路径；5. selective revision 的影响范围；6. Review 与 Delivery。普通可恢复进度不要求用户逐步点按钮。

### 5.7 外部 Agent Skill / CLI / MCP 等价能力

```text
afs mission create|show|revise
afs plan show|patch|approve
afs task list|show
afs run list|show|pause|resume|retry|cancel
afs decision list|respond
afs artifact register|writeback
afs revision request --target shot-007 --protect shot-008
afs continuity inspect|apply|undo
afs review record
afs delivery record
```

这些命令不是另一个自动化后端；它们与前端使用相同 command schema、authorization、idempotency 与 event stream。

## 6. 可运行原型结论

原型位于 `experiments/product-discovery/ai-native-production-gate/`，没有接入 production Studio。它证明同一个 scenario 可在一个产品表面完成：

- 2020 字故事与参考约束 → 可编辑计划；
- 一次批准 → 3 条有边界的并行 run；
- 批准前 domain artifacts、decision 与 continuity 为空；批准原子产生 15 个带 `source_run_id` 的 simulated shots、1 个 HumanDecisionRequest、初始 writeback 与对应 events；
- Agent/负责人、进度、模拟成本、阻断、暂停/恢复/重试、人工决定；
- 15 个 storyboard artifact 保持稳定对象身份；
- Shot7 从 `shot-007-v1` 写回 `shot-007-v2`，Shot8 保持 `shot-008-v1`；
- continuity impact、Review/Delivery 状态与 writeback proof；
- localStorage reload 恢复计划、run、artifact、active artifact 与 pending decision；
- 桌面完整 cockpit 与移动端 tasks/artifacts/decisions companion；
- 无全局聊天框；没有把画布当成唯一答案；没有 Provider dispatch。

## 7. Integration Queue 与 decision_needed

正式实现前需要 Owner/控制线程确认：

1. **批准独立的 Production Control Contract v0.1**，冻结 PR #155 既有 aggregate/continuity/review/delivery 语义。
2. **选择 ledger 一致性边界**：推荐 append-only project event ledger + aggregate projections；若分库，必须批准 atomic outbox 策略。
3. **批准 additive artifact writeback commands**，将 PlanTask/Run provenance 写入既有 ArtifactCandidate/Shot successor，而不是 UI 直写。
4. **ProductionRun 与 AssetCandidate.JobState 分离**；后者继续表达单个 provider/queue job，不冒充跨任务 cockpit。
5. **`plan.approve` 原子创建 3+ PlanTask**，失败时不留下半批准计划。
6. **Provider 继续默认 closed**；下一轮仍先实现 deterministic simulation/harness，再单独申请 provider smoke。

建议后续 bounded lanes：

- Contract Lane：schema、commands/events、state machine、ledger/outbox 决策；禁止改 UI。
- Runtime Lane：deterministic scheduler、projection/restart/idempotency harness；禁止 Provider。
- Product Lane：Mission/Cockpit 与现有 Artifact Workspace 的 additive integration；禁止改 frozen aggregate semantics。
- External Interface Lane：Skill/CLI/MCP parity contract；只复用 gateway。
- Evaluator Lane：结构、runtime simulation、browser QA 与 evidence-boundary 审核。

## 8. Non-claims

本 Gate 只支持 `research / prototype / structure / runtime-simulation evidence`。它不支持 human acceptance、真实 Provider/media QA、business validation、legal readiness、SaaS/public release readiness、production readiness 或 durable Company OS promotion。
