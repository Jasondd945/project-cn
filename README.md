# project-cn

> 通用项目中文镜像 skill — 一键将英文项目生成中文副本，原文零改动

适用于 Claude Code、Cursor、Gemini CLI 等支持 SKILL.md 规范的 AI 编程助手。

## 使用场景

| 场景 | 说明 |
|------|------|
| 开源项目中文化 | 将英文 README、文档、注释翻译成中文，方便中文开发者阅读和贡献 |
| 团队文档本地化 | 公司内部工具的英文文档批量生成中文版本，降低新人上手门槛 |
| 技术文档双语对照 | 保留原始英文文件的同时生成 `-CN` 副本，方便中英文对照阅读 |
| 代码注释中文增强 | 为代码文件生成带中文注释的副本，帮助中文团队理解代码逻辑 |
| 大型项目分批处理 | 通过分档策略（1/2/3 档）控制翻译优先级，先翻译核心文档，再逐步扩展 |
| 断点续翻 | 大项目翻译中断后可通过 `resume` 从上次停止的地方继续，不浪费已完成的工作 |
| 多子代理协作 | 主 agent 分配文件给多个子代理并行翻译，通过 `heartbeat` 和 `watchdog` 监控进度 |

## 安装

### Skills CLI（推荐）

| 用途 | 命令 |
|------|------|
| 项目级安装 | `npx skills add Jasondd945/project-cn` |
| 全局安装 | `npx skills add Jasondd945/project-cn -g` |
| 安装指定 skill | `npx skills add Jasondd945/project-cn --skill project-cn` |
| 验证安装 | `npx skills list` |
| 卸载 | `npx skills remove project-cn` |

### 手动安装

将以下目录复制到 `.claude/skills/project-cn/` 下：

```
SKILL.md
scripts/
references/
```

## 核心能力

- 先完整评估项目，再执行复制和中文副本生成
- 支持文档、代码、其他文件三类分类
- 大项目支持 `1/2/3` 档分层推进
- 使用 `manifest + progress + lock` 做可恢复状态管理
- 支持 `status / resume / scope / mark / report`
- 已完成文件默认只保留元数据，不再把全文反复带回上下文
- 提供有界 `headless_runner` 调度器，负责 `start / resume` 和停机条件，不负责替代模型翻译

## 许可证

[MIT](./LICENSE)
