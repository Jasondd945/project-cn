# project-cn

> 将项目复制为中文版的 Claude Code 技能

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

## 概述 📖

**project-cn** 是一个 Claude Code 技能，用于将项目复制为中文版（A → A-CN），保留原文件不覆盖，并为文档生成 `-CN` 翻译副本、为代码生成 `-CN` 中文注释增强副本。

**核心特性**：
- ✅ 原始项目绝不修改
- ✅ 完整保留所有原始文件
- ✅ 智能文件分类（文档/代码/其他）
- ✅ 支持中断恢复
- ✅ 支持多子智能体并行
- ✅ 零外部依赖

## 快速开始 🚀

### 前置要求

- Python 3.8+
- Claude Code

### 基本使用

```bash
# 启动翻译作业
python "<skill_dir>/scripts/job_runner.py" start "<项目根目录>"

# 查看作业状态
python "<skill_dir>/scripts/job_runner.py" status "<目标目录>"

# 继续中断的作业
python "<skill_dir>/scripts/job_runner.py" resume "<目标目录>"

# 生成最终报告
python "<skill_dir>/scripts/job_runner.py" report "<目标目录>"
```

### 示例

```bash
# 将当前项目翻译为中文版
python ".claude/skills/project-cn/scripts/job_runner.py" start "C:\work\my-project"

# 排除特定目录
python ".claude/skills/project-cn/scripts/job_runner.py" start "C:\work\my-project" --exclude-dir tests --exclude-dir examples
```

## 特性列表 ✨

### 文件分类

| 类型 | 扩展名 | 处理方式 |
|------|--------|----------|
| **A 类（文档）** | `.md`, `.txt`, `.rst`, `.adoc`, README, LICENSE 等 | 翻译为 `-CN` 副本 |
| **B 类（代码）** | `.py`, `.js`, `.ts`, `.java`, `.go`, `.rs`, `.c`, `.cpp` 等 | 添加中文注释 |
| **C 类（其他）** | 配置、资源、二进制文件等 | 仅复制 |

### 核心功能

- **完整评估**：处理前先分析项目规模、文件数量、预计耗时
- **批次处理**：按批次处理文件，默认每批 20 个
- **进度追踪**：实时记录处理状态（pending/in_progress/completed/failed/skipped）
- **中断恢复**：任务中断后可从断点继续
- **并行支持**：支持多子智能体并行处理大项目
- **结果校验**：生成详细报告，验证文件完整性

### 安全机制

- **三重保护**：输出路径检查 + 原文件哈希校验 + 最终完整性验证
- **原子操作**：写入失败不会损坏原文件
- **详细日志**：记录每个文件的处理结果

## 技术栈 🛠️

- **语言**：Python 3.8+
- **依赖**：零外部依赖（仅使用 Python 标准库）
- **平台**：Windows, macOS, Linux
- **测试框架**：unittest（35+ 测试用例）

## 项目结构 📁

```
project-cn/
├── SKILL.md                    # 主技能文档（519行）
├── README.md                   # 本文件
├── .gitignore                  # Git 忽略规则
├── agents/
│   └── openai.yaml            # Agent 配置
├── references/
│   ├── document-rules.md      # 文档翻译硬约束
│   └── code-rules.md          # 代码注释硬约束
├── scripts/                    # 核心功能模块
│   ├── job_runner.py          # 主入口（CLI工具）
│   ├── job_state.py           # 状态管理
│   ├── planning.py            # 规划逻辑
│   ├── prepare_job.py         # 低层调试入口
│   ├── classification.py      # 文件分类
│   ├── text_metrics.py        # 文本度量
│   └── verify_outputs.py      # 结果验证
└── tests/
    └── test_project_cn.py     # 测试套件（35+测试用例）
```

## 测试 🧪

```bash
# 运行所有测试
python -m unittest tests.test_project_cn

# 运行特定测试
python -m unittest tests.test_project_cn.ProjectCnTests.test_classify_document_code_and_other_files

# 查看测试覆盖率（需要安装 coverage）
pip install coverage
coverage run -m unittest tests.test_project_cn
coverage report
```

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

## 作者 👤

Claude (Anthropic)

---

**类比**：project-cn 就像一个"智能复印机+翻译官"组合——它不仅完整复印你的项目，还能把说明书翻译成中文，给代码加上中文注释贴纸，而且原件一个字都不改！📠🌏
