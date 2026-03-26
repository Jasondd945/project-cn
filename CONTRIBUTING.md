# Contributing

感谢你关注 `project-cn`。

## 提交前

- 先阅读 [SKILL.md](./SKILL.md)
- 保持修改范围尽量小，不做无关重构
- 不要提交 `*-CN/`、`AAA-translate-output/`、`translate-*.json`、`translate-*.txt`
- 不要提交本机绝对路径、账号信息、临时报告或缓存目录

## 开发约定

- Python 代码尽量保持标准库优先
- 不修改源目录保护、`A-CN` 输出规则和 `AAA-translate-output` 收口规则，除非有明确理由
- 大项目分档协议必须保持一致：先评估，再按 `1/2/3` 档推进
- 所有新行为都应补对应测试

## 本地验证

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONUTF8='1'; python -m unittest discover -s "tests" -p "test_*.py"
python "scripts/job_runner.py" --help
```

如果你在支持 skill 校验的环境里，也建议额外运行对应的技能校验工具。

无论使用什么环境，至少确保：

- 测试通过
- `SKILL.md`、`README.md`、脚本和测试彼此一致
- 没有新增敏感路径或运行产物

## Pull Request

PR 说明里至少写清楚：

- 改了什么
- 为什么要改
- 怎么验证
- 是否影响 `SKILL.md`、状态机、分档协议或输出结构
