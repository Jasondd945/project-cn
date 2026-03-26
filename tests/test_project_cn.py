import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import job_runner  # noqa: E402
import planning  # noqa: E402
from classification import classify_file  # noqa: E402


class ProjectCnTests(unittest.TestCase):
    def test_classify_document_code_and_other_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            readme = root / "README"
            code = root / "app.py"
            config = root / "settings.json"

            readme.write_text("Project docs", encoding="utf-8")
            code.write_text("print('hi')\n", encoding="utf-8")
            config.write_text('{"debug": true}\n', encoding="utf-8")

            self.assertEqual(classify_file(readme), "document")
            self.assertEqual(classify_file(code), "code")
            self.assertEqual(classify_file(config), "other")

    def test_assess_project_builds_cn_destination_and_counts_work(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)
            (src_root / "assets").mkdir(parents=True)

            (src_root / "docs" / "guide.md").write_text("# Guide\n\nHello\n", encoding="utf-8")
            (src_root / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (src_root / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

            result = planning.assess_project(src_root)

            self.assertEqual(result["dst_root"], str(src_root.parent / "demo-CN"))
            self.assertEqual(result["summary"]["total_files"], 3)
            self.assertEqual(result["summary"]["document_files"], 1)
            self.assertEqual(result["summary"]["code_files"], 1)
            self.assertEqual(result["summary"]["other_files"], 1)
            self.assertEqual(result["summary"]["llm_files"], 2)
            self.assertFalse(result["summary"]["requires_confirmation"])

    def test_prepare_project_copy_replaces_existing_destination_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            dst_root = src_root.parent / "demo-CN"
            dst_root.mkdir()
            stale_file = dst_root / "stale.txt"
            stale_file.write_text("stale", encoding="utf-8")

            manifest = planning.prepare_project_copy(src_root)

            copied_original = dst_root / "README.md"
            cn_copy = dst_root / "README-CN.md"

            self.assertFalse(stale_file.exists())
            self.assertTrue(copied_original.exists())
            self.assertFalse(cn_copy.exists())
            self.assertEqual(manifest["summary"]["document_files"], 1)
            self.assertEqual(manifest["summary"]["planned_cn_document_files"], 1)

    def test_prepare_project_copy_generates_cn_targets_for_documents_and_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)

            (src_root / "docs" / "README").write_text("hello", encoding="utf-8")
            (src_root / "src" / "app.ts").write_text("export const value = 1;\n", encoding="utf-8")

            manifest = planning.prepare_project_copy(src_root)
            items = {item["rel_path"]: item for item in manifest["items"]}

            self.assertEqual(items["docs/README"]["cn_rel_path"], "docs/README-CN")
            self.assertEqual(items["src/app.ts"]["cn_rel_path"], "src/app-CN.ts")
            self.assertIsNone(items["docs/README"]["copy_failure"])
            self.assertIsNone(items["src/app.ts"]["copy_failure"])

    def test_prepare_project_copy_preserves_exact_user_root_when_only_child_directory_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "wrapper"
            nested_root = src_root / "inner-project"
            nested_root.mkdir(parents=True)
            (nested_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            manifest = planning.prepare_project_copy(src_root)
            dst_root = Path(manifest["dst_root"])

            self.assertEqual(dst_root, src_root.parent / "wrapper-CN")
            self.assertTrue((dst_root / "inner-project" / "README.md").exists())
            self.assertFalse((src_root.parent / "inner-project-CN").exists())

    def test_build_destination_root_always_uses_cn_suffix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()

            dst_root = planning.build_destination_root(src_root)

            self.assertEqual(dst_root.name, "demo-CN")
            self.assertNotIn("中文版", dst_root.name)

    def test_extreme_project_is_flagged_for_confirmation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "large-demo"
            src_root.mkdir()

            huge_text = ("line " * 2000 + "\n") * 80
            for index in range(10):
                (src_root / f"doc-{index}.md").write_text(huge_text, encoding="utf-8")

            result = planning.assess_project(src_root)

            self.assertTrue(result["summary"]["requires_confirmation"])
            self.assertTrue(result["summary"]["risk_flags"])

    def test_assess_project_excludes_default_noise_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / ".git" / "hooks").mkdir(parents=True)
            (src_root / "vendor" / "package").mkdir(parents=True)
            (src_root / "node_modules" / "lib").mkdir(parents=True)

            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            (src_root / ".git" / "hooks" / "pre-commit.sample").write_text("#!/bin/sh\n", encoding="utf-8")
            (src_root / "vendor" / "package" / "README.md").write_text("Vendor docs\n", encoding="utf-8")
            (src_root / "node_modules" / "lib" / "index.js").write_text("export {};\n", encoding="utf-8")

            result = planning.assess_project(src_root)

            self.assertEqual(result["summary"]["total_files"], 1)
            self.assertEqual(result["summary"]["document_files"], 1)
            self.assertEqual(result["summary"]["code_files"], 0)
            self.assertEqual(result["summary"]["other_files"], 0)
            self.assertEqual(sorted(result["summary"]["excluded_dirs"]), [".git", "node_modules", "vendor"])

    def test_prepare_cli_supports_extra_exclude_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "fixtures").mkdir(parents=True)

            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            (src_root / "fixtures" / "sample.md").write_text("# Fixture\n", encoding="utf-8")

            output_file = Path(tmpdir) / "manifest.json"
            prepare_script = SCRIPTS_DIR / "prepare_job.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(prepare_script),
                    "assess",
                    str(src_root),
                    "--exclude-dir",
                    "fixtures",
                    "--output",
                    str(output_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(output_file.read_text(encoding="utf-8"))
            rel_paths = {item["rel_path"] for item in payload["items"]}

            self.assertEqual(rel_paths, {"docs/guide.md"})
            self.assertIn("fixtures", payload["summary"]["excluded_dirs"])

    def test_prepare_cli_rejects_output_file_inside_source_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            output_file = src_root / "scan_result.json"
            prepare_script = SCRIPTS_DIR / "prepare_job.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(prepare_script),
                    "assess",
                    str(src_root),
                    "--output",
                    str(output_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(output_file.exists())
            self.assertIn("source root", completed.stderr)

    def test_skill_mentions_parallel_multi_agent_strategy(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        required_phrases = [
            "多子智能体",
            "并行",
            "文件归属",
            "冲突",
            "主 agent",
            "汇总",
            "AAA-translate-output",
        ]

        for phrase in required_phrases:
            self.assertIn(phrase, skill_text)

    def test_skill_forbids_manual_scanning_and_requires_manifest_driven_processing(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        required_phrases = [
            "translate-manifest.json",
            "隐藏目录",
            "点开头",
            "无扩展名文档",
            "不要依赖手工扫描",
            "不要手动判断",
        ]

        for phrase in required_phrases:
            self.assertIn(phrase, skill_text)

    def test_start_job_stores_internal_outputs_under_cn_output_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            output_dir = Path(job["dst_root"]) / "AAA-translate-output"

            self.assertEqual(Path(job["job_dir"]), output_dir)
            self.assertTrue(output_dir.exists())
            self.assertTrue(Path(job["manifest_path"]).exists())
            self.assertEqual(Path(job["manifest_path"]).parent, output_dir)
            self.assertTrue((output_dir / "translate-job.json").exists())
            self.assertTrue((Path(job["dst_root"]) / "README.md").exists())

    def test_build_job_report_writes_reports_under_cn_output_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            dst_root = Path(job["dst_root"])
            output_dir = dst_root / "AAA-translate-output"
            (dst_root / "README-CN.md").write_text("# 演示\n", encoding="utf-8")

            report = job_runner.build_job_report(job["dst_root"])

            self.assertEqual(report["generated"]["document_cn_files"], 1)
            self.assertEqual(report["generated"]["code_cn_files"], 0)
            self.assertEqual(len(report["missing_cn_files"]), 1)
            self.assertTrue((output_dir / "translate-verify-report.json").exists())
            self.assertTrue((output_dir / "translate-final-report.txt").exists())
            final_report_text = (output_dir / "translate-final-report.txt").read_text(encoding="utf-8")
            self.assertIn("=== 项目翻译结果报告 ===", final_report_text)
            self.assertIn("工作量摘要：", final_report_text)
            self.assertIn("产出结果：", final_report_text)

    def test_job_runner_report_rejects_output_file_inside_source_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            output_file = src_root / "scan_result.json"
            job_runner_script = SCRIPTS_DIR / "job_runner.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(job_runner_script),
                    "report",
                    job["dst_root"],
                    "--output",
                    str(output_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(output_file.exists())
            self.assertIn("source root", completed.stderr)

    def test_verify_outputs_reports_missing_cn_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")

            manifest = planning.prepare_project_copy(src_root)
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

            readme_cn = Path(manifest["dst_root"]) / "README-CN.md"
            readme_cn.write_text("# 演示\n", encoding="utf-8")

            verify_script = SCRIPTS_DIR / "verify_outputs.py"
            completed = subprocess.run(
                [sys.executable, str(verify_script), str(manifest_path)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            report = json.loads(completed.stdout)
            self.assertEqual(report["generated"]["document_cn_files"], 1)
            self.assertEqual(report["generated"]["code_cn_files"], 0)
            self.assertEqual(len(report["missing_cn_files"]), 1)

    def test_python_sources_disable_bytecode_cache(self):
        targets = [
            SKILL_ROOT / "scripts" / "job_runner.py",
            SKILL_ROOT / "scripts" / "prepare_job.py",
            SKILL_ROOT / "scripts" / "planning.py",
            SKILL_ROOT / "scripts" / "verify_outputs.py",
            SKILL_ROOT / "scripts" / "classification.py",
            SKILL_ROOT / "scripts" / "text_metrics.py",
            SKILL_ROOT / "tests" / "test_project_cn.py",
        ]

        for target in targets:
            text = target.read_text(encoding="utf-8")
            self.assertIn("sys.dont_write_bytecode = True", text)


if __name__ == "__main__":
    unittest.main()
