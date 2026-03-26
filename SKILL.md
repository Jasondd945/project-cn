---
name: project-cn
description: 当用户要翻译项目为中文版、生成 -CN 副本、项目中文化时使用，保留原文件不覆盖，并为文档生成 -CN 翻译副本、为代码生成 -CN 中文注释增强副本时使用。
---

# project-cn

## 概述

把用户提供的项目根目录 `A` 复制为同级目录 `A-CN`，并满足下面三条：

- 原始 `A` 绝不修改。
- `A-CN` 中先完整保留所有复制过来的原始文件。
- 仅对文档文件和代码文件额外新增同目录 `-CN` 副本，其他文件只复制不增强。

默认目标目录策略：

- 如果 `A-CN` 已存在，默认先删除旧目录再重建。
- 只有用户明确要求保留旧结果时，才使用 `--keep-existing`。
- 目标目录名只能是“用户给出的目录名 + `-CN`”，禁止擅自改成 `-中文版`、`-中文`, `-zh` 或其他后缀。

默认排除目录策略：

- 评估和复制时默认跳过 `.git`、`vendor`、`node_modules`、`dist`、`build`、`.venv`、`__pycache__`、`coverage` 等明显不该进入翻译流程的目录。
- 如果用户还想额外排除某些目录，可以追加 `--exclude-dir <name>`。

Python 缓存约束：

- skill 自带的 Python 脚本和测试模块默认要禁用 `.pyc` 写盘，避免在 skill 目录生成 `__pycache__`。
- 交付前必须清理已存在的 `__pycache__` 目录。

## 资源导航

- `scripts/job_runner.py`
  结果导向的高层入口。默认把除翻译文件之外的内部产物统一放到 `A-CN/AAA-translate-output`。
- `scripts/prepare_job.py`
  低层调试入口。用于“整体工作量评估”以及“复制目录并生成清单”。
- `scripts/verify_outputs.py`
  用于在处理完成后检查 `-CN` 文件是否齐全。
- `references/document-rules.md`
  文档翻译硬约束。
- `references/code-rules.md`
  代码中文注释增强硬约束。

## 必须遵守的原则

- 先完整评估，再决定是否继续执行。
- 除非任务确实大到明显超出单次承载，否则默认继续整项目执行。
- 不能因为文件多、文本长、预计 token 高就擅自放弃、缩减范围或只做一部分。
- 必须把用户明确给出的路径当作项目根目录，绝不因为“根目录下面只有一个子目录”就自动下钻并把那个子目录当成真正根目录。
- 文档翻译和代码注释增强必须由当前模型直接完成，不能调用外部翻译 API、外部注释 API 或在线机器翻译服务。
- 文档处理必须忠实直译，不总结、不润色、不扩写。
- 代码处理只能翻译或新增中文注释，不能改代码逻辑。
- 默认只对外关注 `A-CN` 结果目录；其余内部产物必须统一进入 `A-CN/AAA-translate-output`，不要散落到项目根目录或系统临时目录。

## 输出目录规则

假设输入目录为：

```powershell
C:\work\A
```

则默认输出为：

```powershell
C:\work\A-CN
```

其中：

- 项目翻译结果文件直接写在 `A-CN` 的对应目录下。
- 除翻译结果之外的内部产物统一写到：

```powershell
C:\work\A-CN\AAA-translate-output
```

当前内部产物文件名固定为：

- `translate-job.json`
- `translate-manifest.json`
- `translate-verify-report.json`
- `translate-final-report.txt`

禁止行为：

- 不要把 `scan_result.json`、`manifest.json`、`report.json`、临时日志或任何额外 JSON 写回原项目目录 `A`。
- 即使使用低层调试入口 `prepare_job.py --output` 或 `job_runner.py report --output`，输出路径也不得落在源目录 `A` 之内。

## 文件分类

### A 类：文档文件

脚本会把下列文件归类为 `document`：

- `.txt`
- `.md`
- `.markdown`
- `.rst`
- `.adoc`
- `.text`
- `.mdx`
- 无扩展名但文件名属于 `README`、`CHANGELOG`、`CONTRIBUTING`、`LICENSE`、`NOTES`、`GUIDE`、`FAQ`、`MANUAL`

处理结果：

- 原文件保留。
- 新增同目录 `-CN` 文档副本。

### B 类：代码文件

脚本会把常见源码文件归类为 `code`，包括但不限于：

- `.py`
- `.js`
- `.ts`
- `.tsx`
- `.jsx`
- `.java`
- `.go`
- `.rs`
- `.c`
- `.cpp`
- `.h`
- `.hpp`
- `.cs`
- `.rb`
- `.php`
- `.swift`
- `.kt`
- `.scala`
- `.sh`
- `.bash`
- `.sql`
- `.html`
- `.css`
- `.scss`
- `.vue`
- `.svelte`
- `Dockerfile`
- `Makefile`
- `CMakeLists.txt`

处理结果：

- 原文件保留。
- 新增同目录 `-CN` 代码副本。

### C 类：其他文件

配置、资源、Office、PDF、音视频、数据库、压缩包、模型文件、二进制文件等归类为 `other`。

处理结果：

- 只复制，不生成 `-CN` 副本。

## 标准流程

### 1. 接收项目根目录

拿到用户给出的项目根目录绝对路径，例如：

```powershell
C:\work\A
```

这里的 `A` 就是唯一合法根目录。

- 如果 `A` 下面只有一个子目录，也仍然把 `A` 当根目录处理。
- 只有用户明确说“请处理 A\child”时，才允许把 `child` 当根目录。

### 2. 先做整体评估（禁止跳过！）

**强制要求：必须运行脚本，禁止手动处理**

```powershell
python "<skill_dir>\scripts\job_runner.py" start "<src_root>"
```

**警告：**
- 禁止绕过脚本直接手动扫描文件
- 禁止用自己的判断代替脚本的分类结果
- 如果脚本不存在，立即停止并告知用户，不要手动替代
- 脚本会自动生成 manifest 清单，必须按清单处理，不得遗漏

这个命令会：

- 先完成整体评估。
- 默认删除旧的 `A-CN` 后重建复制目录。
- 把内部产物写入 `A-CN\AAA-translate-output`。
- 返回 `job_id`、`job_dir`、`dst_root` 和 `summary`。
- **生成 `translate-manifest.json` 清单，列出所有文件及分类（A类文档、B类代码、C类其他）**

必须先查看返回结果里的 `summary`，重点读取：

- `total_files`
- `document_files`
- `code_files`
- `other_files`
- `llm_files`
- `estimated_text_chars`
- `estimated_rounds`
- `estimated_input_tokens`
- `estimated_tokens_low`
- `estimated_tokens_high`
- `estimated_minutes_low`
- `estimated_minutes_high`
- `risk_flags`
- `requires_confirmation`
- `excluded_dirs`

### 3. 判断是否需要先提醒用户

仅当 `requires_confirmation = true` 时，才先告诉用户：

- 为什么这是超大任务。
- 预计总耗时范围。
- 预计 token 范围。
- 风险点是什么。
- 推荐继续整项目执行，还是让用户明确批准分批策略。

如果 `requires_confirmation = false`，直接继续，不要额外拖延。

### 4. 启动作业并复制目录

默认命令：

```powershell
python "<skill_dir>\scripts\job_runner.py" start "<src_root>"
```

如果还要额外排除目录：

```powershell
python "<skill_dir>\scripts\job_runner.py" start "<src_root>" --exclude-dir fixtures --exclude-dir examples
```

如果用户明确要求保留旧目录，再使用：

```powershell
python "<skill_dir>\scripts\job_runner.py" start "<src_root>" --keep-existing
```

此命令会完成：

- 计算同级目标目录 `A-CN`
- 删除旧 `A-CN`（默认）
- 完整复制全部目录结构和原始文件
- 在 `A-CN/AAA-translate-output` 保存内部清单和作业信息

如果确实需要显式导出清单，才退回低层入口：

```powershell
python "<skill_dir>\scripts\prepare_job.py" prepare "<src_root>" --output "<manifest.json>"
```

### 5. 逐个处理文档文件

先读 `references/document-rules.md`。

对 `translate-manifest.json` 中 `category=document` 的每个条目：

- 读取 `copied_file`
- 按规则生成完整中文内容
- 写入 `cn_file`

必须保证：

- 翻译完整
- 保留 Markdown、表格、列表、frontmatter、代码块结构
- 只翻译自然语言，不翻译代码、路径、URL、键名和标识符
- 无扩展名但属于自然语言文档范畴的文件，必须按文档规则处理，不要因为“看起来像声明文件、许可文件、说明文件”就主观跳过

### 6. 逐个处理代码文件

先读 `references/code-rules.md`。

对 `translate-manifest.json` 中 `category=code` 的每个条目：

- 读取 `copied_file`
- 生成中文注释增强版代码
- 写入 `cn_file`

必须保证：

- 不改逻辑
- 不改命名
- 翻译已有英文注释
- 为关键模块、类、函数、关键流程补充中文说明

### 7. 遇到失败时的处理

- 单个文件失败不能让整个任务中断。
- 继续处理后续文件。
- 记录失败文件和原因，放入最终报告。
- 对未知编码或无法安全处理的文本，不要写坏 `-CN` 文件，直接记为失败。

### 7.1 禁止用手工扫描替代清单流

- 不要依赖手工扫描方式代替标准流程，尤其不要依赖会漏掉隐藏目录的递归扫描实现。
- 对以点开头的隐藏目录、隐藏文件，以及其他不容易在普通视图里出现的路径，也必须纳入扫描和分类范围。
- 不要手动判断“这个文件看起来像不用翻译”，然后跳过它。分类必须以脚本评估结果和 `translate-manifest.json` 为准。
- 开始逐文件处理前，必须先读取 `A-CN/AAA-translate-output/translate-manifest.json`，再按其中的 `category`、`cn_file` 和 `llm_action` 执行。
- 如果清单里列出了隐藏目录中的文档、无扩展名文档、法律文本、说明文本或其他容易被主观误判的文档，就必须处理，不能因为路径隐藏或文件类型看起来特殊就自行跳过。

### 8. 多子智能体并行作业

默认情况下，可以由当前主 agent 顺序完成全部文件处理；但当项目里的文档和代码文件较多、目录天然可切分、且并行不会造成上下文冲突时，最好启用多子智能体并行作业。

推荐启用条件：

- `llm_files` 明显较多，单 agent 顺序处理会拖慢交付。
- 项目可以按目录、按文件类型或按批次稳定切分。
- 各批次之间不存在共享写入同一目标文件的需求。

并行作业规则：

- 主 agent 先完成整体评估、目录复制和清单生成。
- 主 agent 按批次切分任务，再把不同批次分配给多个子智能体。
- 每个子智能体只处理自己负责的 `cn_file`，不回写原文件，也不改其他批次的 `-CN` 文件。
- 每个子智能体都必须明确自己的文件归属，不能抢写、重写或覆盖别人的输出。
- 如果同时存在文档和代码任务，优先拆成“文档批次”和“代码批次”，再在各自内部继续分片。

推荐分片策略：

- 按目录分片，例如 `docs/`、`src/`、`tests/` 各自独立。
- 按文件类型分片，文档交给文档子智能体，代码交给代码子智能体。
- 按清单批次分片，把 `items` 按固定数量切成批次，例如每批 10 到 30 个文件。

冲突规避规则：

- 任何时候只允许一个子智能体写某个 `cn_file`。
- 不要让两个子智能体同时处理同一个目录下的同名派生目标。
- 若批次切分不够清晰，主 agent 先重划分，再发起并行，不要带着冲突风险硬跑。
- 子智能体只新增 `-CN` 副本，绝不覆盖复制后的原始文件。

汇总规则：

- 每个子智能体返回自己完成的文件数、失败文件数、失败原因和未处理项。
- 主 agent 负责汇总所有子智能体结果，统一去重失败项，并生成最终报告。
- 主 agent 在全部子智能体完成后，统一运行 `verify_outputs.py` 做结果核验。

### 9. 最后做结果校验

处理结束后运行：

```powershell
python "<skill_dir>\scripts\job_runner.py" report "<A-CN>"
```

也可以直接传：

```powershell
python "<skill_dir>\scripts\job_runner.py" report "<A-CN>\AAA-translate-output"
```

这个命令会：

- 从 `A-CN/AAA-translate-output/translate-manifest.json` 读取清单。
- 检查 `A-CN` 中的原始复制文件和 `-CN` 文件是否齐全。
- 在 `A-CN/AAA-translate-output` 生成最终 JSON 和文本报告。

读取报告里的：

- `generated.document_cn_files`
- `generated.code_cn_files`
- `missing_original_copies`
- `missing_cn_files`

如果用户明确要求导出报告到指定路径，再追加 `--output "<report.json>"`。

## 最终报告必须包含

- 扫描文件总数
- 文档文件数
- 代码文件数
- 其他文件数
- 生成的文档 `-CN` 文件数
- 生成的代码 `-CN` 文件数
- 仅复制文件数
- 失败文件数
- 失败文件清单及原因
- 工作量评估摘要
- 预计耗时与实际耗时
- 预计 token 范围，以及能估算到的实际 token

## 命名规则

- `A -> A-CN`
- `B.md -> B-CN.md`
- `README -> README-CN`
- `app.ts -> app-CN.ts`
- `main.py -> main-CN.py`

## 常见错误

- 复制后直接覆盖原文件，而不是新增 `-CN` 副本。
- 擅自把用户提供根目录下面的唯一子目录当成真正项目根目录，结果生成了 `child-CN` 而不是 `A-CN`。
- 把目标目录后缀改成 `-中文版`、`-中文` 等非约定命名。
- 把 JSON、YAML、图片、PDF 也拿去做翻译或注释增强。
- 用会漏掉隐藏目录的手工扫描方式代替标准清单流，导致点开头目录或隐藏路径下的文件漏处理。
- 看到无扩展名文档、法律文本、声明文本、说明文本后主观判断“这个不用翻译”，而不是按 skill 的文档分类规则执行。
- 文档翻译时改坏 Markdown 结构。
- 代码增强时顺手改逻辑或改命名。
- 因为文件太多就默认缩减范围。
- 未经用户许可擅自改成分批模式。
- 把内部 JSON 或报告散落到项目根目录，而不是收口到 `A-CN/AAA-translate-output`。

## Skill 触发词自优化

当用户在对话中主动停掉当前流程，强制要求使用 `project-cn` skill 时，说明 frontmatter 的 `description` 触发词覆盖不够。

处理流程：

1. 先按用户要求正常完成项目翻译任务。
2. 任务完成后，分析用户原始请求中的关键表述，这就是缺失的触发词。
3. 把这些表述抽象成简洁、通用的词，追加到本文件 frontmatter 的 `description` 中。
4. 避免把一次性的项目名、路径名、具体目录名写进触发词。

示例抽象方式：

- “把项目复制成中文版” 可以沉淀为 “复制项目为中文版本”
- “生成 A-CN” 可以沉淀为 “生成 A-CN”
- “给代码做中文注释副本” 可以沉淀为 “代码中文注释增强版副本”

## 经验积累机制

当你经过 2 次及以上尝试才完成关键步骤时，必须把经验简要记录到本文末尾的“踩坑经验”区域。

记录标准：

- 只记录经过 2 次及以上尝试才成功的情况。
- 记录格式：`- 模块或命令 / 场景描述：经验要点`
- 内容只写“下次再遇到时该怎么做”，不要写长篇复盘。
- 重点记录容易复发的坑，例如输出目录收口、编码检测、Markdown 结构保护、代码注释插入位置、报告路径解析等。

## 踩坑经验

（以下由 AI 在实际调用中自动积累，请勿手动删除）

- `job_runner.py` / 收口额外产物：不要把 manifest 和报告放系统临时目录，统一落到 `A-CN/AAA-translate-output`，并让 `report` 直接接受 `A-CN` 或该子目录路径。
- `shell CN 副本生成` / Windows 中文路径：PowerShell 调内联 Python 时不要硬编码带中文的 `A-CN` 路径，优先用 glob 或从当前工作目录推导目标目录，避免路径被转成 `??` 导致读写失败。
- `shell CN 副本生成` / 中文注释写回：不要通过 PowerShell 内联脚本直接塞入中文注释文本再写 shell 文件，这会在非 UTF-8 控制台下把中文降成 `?`；优先用 `apply_patch` 直接写文件，或确保整条生成链路显式使用 UTF-8。
- `代码 CN 副本清洗` / 符号乱码判断：先区分 `?$`、`[]?`、`.*?` 这类语法元字符与真正的显示乱码；如果是原脚本里本该显示为符号或 emoji 的提示文本发生 mojibake，可以只修复显示字符串为可读符号或 ASCII，不要改条件、命令、变量、正则和控制流。
- `根目录判定` / 单子目录包装壳：即使源目录下面只有一个子目录，也必须坚持“精确使用用户给出的路径”这一规则，输出应是 `A-CN`，不能偷换成 `child-CN`。
- `额外产物落点` / 低层输出：`prepare_job.py --output` 和 `job_runner.py report --output` 都不能写回源目录；如果需要显式导出，只能写到 `A-CN/AAA-translate-output` 或源目录之外的其他位置。
- `Python 缓存` / skill 清洁度：入口脚本、模块文件和测试文件都要默认禁用 `.pyc` 写盘；交付前清理 skill 目录内的 `__pycache__`。
- `文件扫描` / 禁止手工脑补分类：不要用可能漏掉隐藏目录的手工扫描替代 `translate-manifest.json`；点开头路径、无扩展名文档、法律/声明/说明类文档都必须按清单处理，不能靠人工主观排除。
- `SKILL.md / Windows 控制台编码显示`：如果 `Get-Content` 读出的中文技能正文出现 mojibake，优先改用显式 `UTF-8` 的内联 Python 读取，不要在乱码文本上继续执行翻译规则。
- `translate-manifest.json / 字段名探测`：读取清单前先打印首个 `item` 的键集合，确认实际字段是 `rel_path`、`cn_rel_path` 还是其他命名，再批量消费，避免把固定假设写死成 `relative_path`。
- `项目扫描 / 隐藏目录遗漏`：使用 Glob 扫描项目时，`**/*` 模式默认会跳过以 `.` 开头的隐藏目录（如 `.claude-plugin`、`.git` 等）。必须显式扫描这些隐藏目录，或在复制阶段直接使用 `cp -r` 复制整个源目录（包括隐藏文件）。下次扫描前先检查是否有 `.claude-plugin`、`.github`、`.vscode` 等常见隐藏目录。
- `绕过脚本手动处理 / 致命错误`：**禁止绕过 `job_runner.py` 脚本直接手动处理**。手动处理会导致：1) 遗漏隐藏目录（如 `.claude-plugin`），2) 错误判断文件分类（如误以为 LICENSE 不需要翻译），3) 无法生成 manifest 清单导致无法验证完整性。如果脚本不存在，立即报错停止，不得手动替代。
- `文件分类错误 / LICENSE 遗漏`：LICENSE 明确属于 A 类文档文件（技能规范第 92 行明确列出），必须生成 `LICENSE-CN` 副本。不得因为"法律文件""标准模板"等主观判断而跳过。所有 A 类文件（README、CHANGELOG、CONTRIBUTING、LICENSE、NOTES、GUIDE、FAQ、MANUAL）都必须翻译。
