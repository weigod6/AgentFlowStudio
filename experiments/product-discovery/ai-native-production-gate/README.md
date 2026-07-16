# AFS AI 原生生产体验 Gate 原型

这是一个隔离的产品探索界面，用同一单集场景演示模拟生产闭环。它不挂载到
`apps/studio/`，也不会调用任何 Provider。

```text
Mission -> editable Plan -> one approval -> three bounded parallel Runs
  -> Artifact writeback -> Shot 7 selective revision
  -> continuity impact -> Review / Delivery truth
```

Run it from the repository root:

```powershell
python -m http.server 4173 --directory experiments/product-discovery/ai-native-production-gate
```

然后打开 `http://127.0.0.1:4173/`。

原型会把项目状态保存在浏览器 localStorage 中；使用“重置演示”可清除状态。
所有时序、成本、进度、Agent 和产物都明确标注为模拟；
`provider_dispatch_count` 固定为零。

验证命令：

```powershell
node --test experiments/product-discovery/ai-native-production-gate/tests/model.test.mjs
python -m pytest tests/test_ai_native_production_gate_prototype.py -q
```

这里只形成研究、结构与 runtime-simulation 证据，不代表 human acceptance、
Provider/media QA、business validation 或 production readiness。
