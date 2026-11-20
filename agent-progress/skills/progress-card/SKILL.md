---
name: progress-card
description: 为三阶段及以上任务或明确要求演示执行进度卡的任务，在当前 Antigravity 对话显示 Todo、工具调用和耗时。
---

# 执行进度卡

每轮开始时选择一个由 3 到 20 个小写字母或数字组成的新 ID。用原生文件创建工具（通常为 `write_file` 或 `write_to_file`）在当前会话 artifact 目录新建 `agent-progress-<id>.html`。在工具支持的字段中设 `IsArtifact: true`、`ArtifactMetadata` 的 `UserFacing: true`、`RequestFeedback: false`。标题写「执行进度」。插件 hooks 将带有以下数据的 HTML 渲染为卡片，并补入工具名称、状态和实际观察到的调用耗时。

HTML 内容只需包含下列模板。`items` 中每项有 `label` 和 `state`，允许 `pending`、`running`、`done`、`blocked`。每轮保持相同的任务顺序和进度卡 ID。JSON 字符串内的 `<` 写成 `\u003c`。

```html
<!doctype html>
<meta charset="utf-8">
<script type="application/json" id="agent-progress-input">
{"items":[{"label":"查看当前目录","state":"running"},{"label":"执行验证","state":"pending"},{"label":"核对结果","state":"pending"}]}
</script>
<p>执行进度</p>
```

工具返回后，在下一条**可见进度消息**内用该文件的完整绝对路径嵌入卡片，例如 `<agent-embed src="file:///absolute/artifact/path/agent-progress-demo97.html"></agent-embed>`。路径使用真实值，有空格时编码为 `%20`。

在阶段切换时，使用同一个原生文件创建工具覆盖同一路径（`Overwrite: true`），重新提交附有最新 `items` 的模板。已嵌入的卡片会更新。阶段汇报用普通文字发送，最终回复再引用同一个卡片，方便用户在本轮折叠后查看结束状态。任务提前停止时保留未完成状态，遇到阻塞时使用 `blocked`。

当用户请求演示时，先发布起始卡片，再查看目录，发布下一阶段卡片，执行无副作用的 `sleep 8`，最后核对并发布收尾卡片。`run_command` 支持等待阈值时，用至少 10000 毫秒，让命令在同一次调用内返回。演示只操作本次进度卡所需的文件。
