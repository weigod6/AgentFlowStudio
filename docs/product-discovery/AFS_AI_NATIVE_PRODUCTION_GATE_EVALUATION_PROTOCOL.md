# AFS AI 原生生产体验独立评价协议

评价对象：`experiments/product-discovery/ai-native-production-gate/`

评价证据等级限定为 research / prototype / structure / runtime-simulation。评价者不得调用 Provider、写服务器、修改 PR #155、修改生产 Studio 或把模拟媒体质量当成真实 QA。

## 必须复核的同任务路径

1. 从 2k–5k 字故事与参考约束开始，不要求用户理解 Shot ID 或模型名。
2. 生成计划，修改一个 plan task，批准一次。
3. 观察至少 3 条有边界的并行任务；辨认 Agent、进度、成本、阻断、暂停、重试和人工决定。
4. 处理 waiting-human 决策；确认只解除对应任务。
5. 在 Storyboard 查看相同对象身份的 artifact。
6. 只修 Shot7，确认 Shot7 产生 successor、Shot8 exact version 不变，并看到影响证据。
7. reload，确认 plan、run、artifact、active artifact 与 pending decision 恢复。
8. 在桌面与约 390×844 移动端检查任务理解、可操作性、中文自然度、横向裁切与 console error。

## 独立评价问题

- 与当前 review/recovery console 相比，是否显著减少从故事输入到产物审核所需的操作和上下文切换？
- 是否体现 Agent 主动拆解、并行生产、报告进度与请求决定，而不是按钮堆？
- 是否避免把全局聊天框、无限画布或单一 Storyboard 当作完整答案？
- Mission、PlanTask、Run、Artifact、Shot、Decision 是否能映射到真实 AFS 领域对象与 exact refs？
- 状态、成本、Provider gate 和 simulation 标识是否足以避免误导？
- 哪些问题会阻止正式实现，哪些只是下一轮 polish？

## 判定格式

评价者返回：

1. `PASS / CONDITIONAL PASS / FAIL`
2. 八项验收硬门逐项结果与证据
3. 四个独立评价问题的判断
4. P0/P1/P2 问题（必须是机制或可复现 UI 问题）
5. evidence boundary / non-claims 审核
6. 是否建议进入正式 Contract/Runtime/Product lanes
