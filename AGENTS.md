# GitHub 发版

- 升级版本时，同步更新 `agent-progress/plugin.json`、`README.md` 的正式版本链接与相关功能、安装和升级说明。
- 为对应标签发布或更新 GitHub Release 页，说明本版的用户可见变化、旧版升级方式和适用的固定升级入口。正式版应在 GitHub 标记为 Latest。
- 发版前运行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`。发布后核对插件清单、README、Git 标签和最新正式 Release 一致，并验证 Release 页的安装命令能取到该版本。
