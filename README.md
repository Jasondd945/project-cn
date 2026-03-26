# project-cn

> 将项目复制为同级 `A-CN` 中文镜像的通用 skill

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

## 简介

`project-cn` 用于把一个项目目录复制为同级中文镜像目录：

- 原目录 `A` 不改
- 新目录为 `A-CN`
- 原始复制文件保留
- 文档生成同目录 `-CN` 翻译副本
- 代码生成同目录 `-CN` 中文注释增强副本
- 额外产物统一进入 `A-CN/AAA-translate-output`

它是一个通用 skill，不绑定单一 agent，实现重点是：

- 先完整评估，再执行
- 大项目按 `1/2/3` 档分层推进
- 支持 `status / resume / scope / mark / report`
- 支持中断续跑和结果校验
- 不依赖外部翻译或注释 API

**类比**：project-cn 就像一个"智能复印机+翻译官"组合——它不仅完整复印你的项目，还能把说明书翻译成中文，给代码加上中文注释贴纸，而且原件一个字都不改！📠

## 快速使用

```bash
# 创建作业并复制目录
python "<skill_dir>/scripts/job_runner.py" start "<src_root>"

# 查看当前状态
python "<skill_dir>/scripts/job_runner.py" status "<A-CN>"

# 继续下一批
python "<skill_dir>/scripts/job_runner.py" resume "<A-CN>"

# 写入档位决策
python "<skill_dir>/scripts/job_runner.py" scope "<A-CN>" --decision tier_1_and_2

# 单文件处理完成后回写状态
python "<skill_dir>/scripts/job_runner.py" mark "<A-CN>" "<file_id>" --status completed

# 生成最终报告
python "<skill_dir>/scripts/job_runner.py" report "<A-CN>"
```

## 分档策略

超大型项目会先按优先级分成三档：

- `1 档`：核心理解层，例如 `README`、`CHANGELOG`、核心 API、前后端入口脚本
- `2 档`：重要扩展层，例如重要 `docs`、工具脚本、支撑代码
- `3 档`：外围噪声层，例如 `tests`、`fixtures`、历史 plan、archive、draft

默认规则：

- 小项目直接处理全部档位
- 大项目默认先跑 `1 档`
- `1 档` 完成后，再决定是否放开 `2 档`
- `2 档` 完成后，再决定是否放开 `3 档`

## 输出结构

```text
A-CN/
├─ 原始复制文件
├─ 文档 -CN 副本
├─ 代码 -CN 副本
└─ AAA-translate-output/
   ├─ translate-job.json
   ├─ translate-manifest.json
   ├─ translate-progress.json
   ├─ translate-originals-lock.json
   ├─ translate-verify-report.json
   └─ translate-final-report.txt
```

## 主要命令

- `start`：评估项目、复制目录、初始化作业
- `status`：查看范围、批次、锁定档位和下一步建议
- `resume`：领取当前允许范围内的下一批文件
- `scope`：写入用户对 `1/2/3` 档的决定
- `mark`：逐文件回写完成或失败状态
- `report`：生成最终 JSON 和文本报告

## 约束

- 不修改源目录
- 不覆盖复制后的原始文件
- 文档必须忠实直译
- 代码只做中文注释增强，不改逻辑
- 所有状态推进都以 `translate-manifest.json` 和 `translate-progress.json` 为准

## 目录

```text
project-cn/
├─ SKILL.md
├─ README.md
├─ agents/
├─ references/
├─ scripts/
└─ tests/
```

详细规则以 [SKILL.md](C:/Users/11738/Desktop/test/skills/project-cn/SKILL.md) 为准。


## 工作流程 🔄

```
用户请求
    ↓
【评估】分析项目规模、文件分类、预计耗时
    ↓
【准备】复制目录结构、生成清单、创建状态文件
    ↓
【处理】按批次处理文档和代码文件
    ↓
【校验】生成最终报告、验证完整性
```

## 常见问题 ❓

### Q: 会修改原项目吗？

**A**: 绝对不会。project-cn 会在同级目录创建 `A-CN`，原项目完全不变。

### Q: 支持哪些文件类型？

**A**:
- 文档：`.md`, `.txt`, `.rst`, `.adoc`, README, LICENSE, CHANGELOG 等
- 代码：`.py`, `.js`, `.ts`, `.java`, `.go`, `.rs`, `.c`, `.cpp`, `.sh`, `.sql` 等
- 其他：配置文件、图片、PDF 等只复制不处理

### Q: 如何中断和恢复？

**A**:
- 中断：直接 Ctrl+C 停止当前处理
- 恢复：使用 `resume` 命令从断点继续

### Q: 支持并行处理吗？

**A**: 支持。对于大项目（文档和代码文件 >200），可以使用多子智能体并行处理，显著提升速度。

## 文档 📚

- **[SKILL.md](./SKILL.md)** - 完整技能文档（519行）
- **[references/document-rules.md](./references/document-rules.md)** - 文档翻译规则
- **[references/code-rules.md](./references/code-rules.md)** - 代码注释规则

## 贡献 🤝

欢迎提交 Issue 和 Pull Request！

## 许可证 📄

MIT License


🌏
