# AFS Studio Canvas

`apps/studio` 是 AgentFlow Studio 当前用户侧前端。

画布借鉴成熟节点式创作工具的交互经验，但产品模型是 AFS 自己的生产流程：

```text
创作意图
  -> 剧本 / 分镜
  -> 角色与场景参考
  -> 导演台调度
  -> 关键帧与片段提示词
  -> 本地预览或显式授权后的 provider 任务
```

唯一用户可感知的提示词记忆能力，是每个 prompt 输入位旁边的小“优化”动作。专业规则、项目上下文、角色/场景摘要、用户偏好、trace 和 provider 状态都留在 Runtime Service 边界之后。

启动：

```powershell
.\.venv\Scripts\python.exe -m apps.cli.main runtime-service --host 127.0.0.1 --port 8790
```

打开：

```text
http://127.0.0.1:8790/studio/
```

单集生产工作区是同一静态树下的 review/recovery candidate 子路径，不替换现有画布入口，也不代表完整 AI 原生生产控制面：

```text
http://127.0.0.1:8790/studio/episode-workspace/?project=<project_id>&episode=<episode_id>&version=<episode_version_id>
```

该子路径只读取 authenticated creator-safe workspace projection，事实写入只走
typed episode commands；UI 恢复状态以 `episode_workspace` namespace 合并进现有
Studio state，不作为制作事实或完成度来源。

## 边界

- 不重新引入旧 Workbench 或 memory-workbench 页面。
- 不暴露 provider secret、本地绝对路径、signed URL、provider 原始响应、trace 内部字段或媒体字节。
- 提示词优化必须保持为节点输入位上的轻浮层，不做成工程页面。
- 文件保持单一职责，超过维护阈值时继续拆分。
