# Antigravity 执行进度

在 Antigravity 桌面版的原聊天中查看 Agent 的阶段汇报、任务进度、工具调用和耗时。安装包还提供一个独立的「Antigravity 中文」入口，用同一份官方 App 启动中文界面。

[最新版本与发布说明](https://github.com/hou3301-byte/antigravity-agent-progress/releases/latest)

## 功能

| 功能 | 使用效果 |
| --- | --- |
| 边做边汇报 | Agent 在多阶段任务开始前发一条进度消息，在有实质发现、切换阶段或遇到阻塞时继续汇报。简单问答直接回答。 |
| 聊天内的任务卡 | 用同一张卡显示待办、进行中、完成和阻塞。三阶段及以上的任务或明确要求显示进度卡时启用。 |
| 工具调用与计时 | 进度卡显示最近的工具调用、运行中的计时，以及返回、失败或中断状态。异步命令显示该次工具调用的耗时。 |
| 中文界面和中文规则 | 「Antigravity 中文」入口用本地 CDP 翻译界面控件。简体中文规则插件可以独立开关。 |

进度功能使用 Antigravity 的原生规则、hooks 和内联组件，官方图标启动的会话也能使用。CDP 监视器只由中文入口启动，随该次 App 会话结束。原生 `Worked for` 分组遵循客户端自己的折叠行为。

## 安装

需要 macOS、已安装的 [Antigravity App](https://antigravity.google/download) 和可用的 `/usr/bin/python3`。进度卡已在 Antigravity App 2.33.0 上验证。运行以下命令为当前用户的所有项目安装进度插件、中文规则和中文入口。

```bash
curl -fsSL https://raw.githubusercontent.com/hou3301-byte/antigravity-agent-progress/v0.2.1/install.sh | bash
```

可以在上面命令的 `bash` 后追加以下选项。

| 用法 | 命令后缀 |
| --- | --- |
| 只装进度插件与中文规则 | `-s -- --plugins-only` |
| 只装进度插件 | `-s -- --progress-only` |
| 只为指定项目装插件 | `-s -- --workspace "/完整项目路径"` |

全局插件安装到 `~/.gemini/config/plugins/`，项目插件安装到项目内的 `.agents/plugins/`。已有同名中文入口会复用。独立安装的上游中文规则会保留当前版本和启用状态。

## 使用

1. 想看中文界面时，在 Finder 的个人「应用程序」目录打开「Antigravity 中文」。若机器已装上游启动器，可继续使用已有图标。切换启动入口前先保存工作并按 **⌘Q** 完全退出。
2. 在 Antigravity 中新建对话，发送「演示执行进度卡」。演示会查看当前目录、运行一条等待命令并核对结果。也可以在输入框运行 `/progress-card` 技能。
3. 日常使用时，可以直接让 Agent 处理多阶段任务，或在任务里加一句「执行过程中展示进度卡」。需要停用某个插件时，在「自定义 → 已安装」找到它并选择禁用。

## 一键卸载与回退

```bash
curl -fsSL https://raw.githubusercontent.com/hou3301-byte/antigravity-agent-progress/v0.2.1/uninstall.sh | bash
```

离线时可以运行安装器留在本机的卸载脚本。

```bash
bash ~/.gemini/config/antigravity-agent-progress-install/uninstall.sh
```

- 想预览卸载范围，在线命令以 `bash -s -- --dry-run` 结尾。本机脚本末尾可直接加 `--dry-run`。
- 按项目安装时，在线卸载使用 `bash -s -- --workspace "/完整项目路径"`，与安装时指定同一个项目。
- 卸载会停止本包启动的监视器，移除本包管理的启动入口和插件。修改过的插件目录会移动到备份位置，终端会显示路径。原版 App、聊天历史和预先安装的上游组件会保留。
- 卸载完成后，按 **⌘Q** 退出当前会话，再点击官方 Antigravity 图标，即可恢复原版界面。

运行新版安装命令即可升级由 `v0.2.0` 安装器登记且未在本机修改的插件。如果直接从 `v0.1.0` 升级，先运行 `v0.2.0` 的安装命令登记旧插件，再安装最新版。安装器会提示处理未登记或有本地修改的进度插件。
