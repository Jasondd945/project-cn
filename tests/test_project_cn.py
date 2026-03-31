import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
os.environ.setdefault("PYTHONUTF8", "1")


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import job_runner  # noqa: E402
import job_state  # noqa: E402
import headless_runner  # noqa: E402
import planning  # noqa: E402
import verify_outputs  # noqa: E402
from classification import classify_file  # noqa: E402


SUBPROCESS_ENV = {
    **os.environ,
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUTF8": "1",
}


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
            self.assertEqual(result["summary"]["llm_batch_count"], 1)
            self.assertFalse(result["summary"]["requires_confirmation"])

    def test_assess_project_assigns_stable_file_ids_and_batch_indexes_for_large_llm_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()

            for index in range(600):
                (src_root / f"doc-{index:03d}.md").write_text("# Demo\n", encoding="utf-8")

            result = planning.assess_project(src_root)
            items = result["items"]

            self.assertEqual(items[0]["file_id"], "F000001")
            self.assertEqual(items[-1]["file_id"], "F000600")
            self.assertEqual(items[0]["batch_index"], 1)
            self.assertEqual(items[19]["batch_index"], 1)
            self.assertEqual(items[20]["batch_index"], 2)
            self.assertEqual(items[-1]["batch_index"], 30)
            self.assertEqual(result["summary"]["llm_batch_count"], 30)

    def test_assess_project_assigns_priority_tiers_and_orders_core_files_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs" / "plans").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)
            (src_root / "agents").mkdir(parents=True)
            (src_root / "tests").mkdir(parents=True)

            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
            (src_root / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (src_root / "agents" / "openai.yaml").write_text("model: test\n", encoding="utf-8")
            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            (src_root / "docs" / "plans" / "migration-plan.md").write_text("# Plan\n", encoding="utf-8")
            (src_root / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

            result = planning.assess_project(src_root)
            items = {item["rel_path"]: item for item in result["items"]}
            tier_summary = result["summary"]["priority_tiers"]
            ordered_paths = [item["rel_path"] for item in result["items"]]
            project_profile = result["summary"]["project_profile"]

            self.assertEqual(items["README.md"]["priority_tier"], 1)
            self.assertEqual(items["src/app.py"]["priority_tier"], 1)
            self.assertEqual(items["package.json"]["priority_tier"], 1)
            self.assertEqual(items["agents/openai.yaml"]["priority_tier"], 1)
            self.assertEqual(items["docs/guide.md"]["priority_tier"], 2)
            self.assertEqual(items["docs/plans/migration-plan.md"]["priority_tier"], 3)
            self.assertEqual(items["tests/test_app.py"]["priority_tier"], 3)

            self.assertEqual(tier_summary["tier_1"]["document_files"], 1)
            self.assertEqual(tier_summary["tier_1"]["code_files"], 1)
            self.assertEqual(tier_summary["tier_1"]["other_files"], 2)
            self.assertEqual(tier_summary["tier_2"]["document_files"], 1)
            self.assertEqual(tier_summary["tier_3"]["document_files"], 1)
            self.assertEqual(tier_summary["tier_3"]["code_files"], 1)
            self.assertEqual(project_profile["primary_type"], "node-web-application")
            self.assertIn("agents", project_profile["contextual_tier_1_dirs"])
            self.assertIn("agents", project_profile["fixed_tier_1_dirs"])
            self.assertIn("package.json", project_profile["dynamic_tier_1_root_files"])
            self.assertIn("项目画像判定为", project_profile["user_summary"])
            self.assertIn("可以直接处理全部档位", project_profile["recommended_first_action"])

            self.assertLess(ordered_paths.index("README.md"), ordered_paths.index("docs/guide.md"))
            self.assertLess(ordered_paths.index("agents/openai.yaml"), ordered_paths.index("docs/guide.md"))
            self.assertLess(ordered_paths.index("docs/guide.md"), ordered_paths.index("tests/test_app.py"))

    def test_assess_project_uses_project_profile_to_promote_skill_runtime_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo-skill"
            (src_root / "agents").mkdir(parents=True)
            (src_root / "commands").mkdir(parents=True)
            (src_root / "hooks").mkdir(parents=True)
            (src_root / "docs").mkdir(parents=True)

            (src_root / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
            (src_root / "agents" / "openai.yaml").write_text("model: test\n", encoding="utf-8")
            (src_root / "commands" / "help.md").write_text("# Help\n", encoding="utf-8")
            (src_root / "hooks" / "before.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")

            result = planning.assess_project(src_root)
            items = {item["rel_path"]: item for item in result["items"]}
            project_profile = result["summary"]["project_profile"]

            self.assertEqual(project_profile["primary_type"], "agent-skill")
            self.assertIn("commands", project_profile["contextual_tier_1_dirs"])
            self.assertIn("hooks", project_profile["contextual_tier_1_dirs"])
            self.assertIn("commands", project_profile["dynamic_tier_1_dirs"])
            self.assertIn("hooks", project_profile["dynamic_tier_1_dirs"])
            self.assertIn("skill.md", project_profile["contextual_tier_1_root_files"])
            self.assertIn("skill.md", project_profile["dynamic_tier_1_root_files"])
            self.assertEqual(items["SKILL.md"]["priority_tier"], 1)
            self.assertEqual(items["commands/help.md"]["priority_tier"], 1)
            self.assertEqual(items["hooks/before.sh"]["priority_tier"], 1)
            self.assertEqual(items["docs/guide.md"]["priority_tier"], 2)
            self.assertIn("项目画像", items["commands/help.md"]["priority_reason"])
            self.assertIn("commands/help.md", project_profile["user_summary"])

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
            self.assertEqual(items["docs/README"]["file_id"], "F000001")
            self.assertEqual(items["src/app.ts"]["file_id"], "F000002")
            self.assertEqual(items["docs/README"]["batch_index"], 1)
            self.assertEqual(items["src/app.ts"]["batch_index"], 1)
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

    def test_assess_project_builds_preflight_summary_for_wrapper_and_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "wrapper"
            nested_root = src_root / "inner-project"
            hidden_dir = nested_root / ".claude-plugin"
            hidden_dir.mkdir(parents=True)
            (nested_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (hidden_dir / "manifest.json").write_text('{"name":"demo"}\n', encoding="utf-8")

            result = planning.assess_project(src_root)
            preflight = result["summary"]["preflight_summary"]

            self.assertEqual(preflight["source_root_signature"]["src_root"], str(src_root.resolve()))
            self.assertEqual(preflight["source_root_signature"]["top_level_dirs"], ["inner-project"])
            self.assertEqual(preflight["source_root_signature"]["top_level_files"], [])
            self.assertEqual(preflight["candidate_output_root"], str(src_root.parent / "wrapper-CN"))
            self.assertTrue(preflight["single_child_wrapper_detected"])
            self.assertGreaterEqual(preflight["hidden_dir_count"], 1)
            self.assertIn("inner-project/.claude-plugin", preflight["hidden_dir_examples"])
            self.assertTrue(preflight["requires_user_confirmation"])
            self.assertEqual(preflight["confirmation_reason"], "single-child-wrapper-detected")

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

    def test_large_noise_heavy_project_recommends_priority_tier_decision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "tests").mkdir(parents=True)
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            for index in range(45):
                (src_root / "tests" / f"test_{index:02d}.py").write_text(
                    "def test_ok():\n    assert True\n",
                    encoding="utf-8",
                )

            result = planning.assess_project(src_root)
            summary = result["summary"]

            self.assertTrue(summary["priority_tier_decision_recommended"])
            self.assertEqual(summary["priority_tier_recommended_scope"], "tier_1_and_2")
            self.assertIn("priority-tier-review-recommended", summary["risk_flags"])
            self.assertTrue(summary["requires_confirmation"])

    def test_start_job_defaults_large_projects_to_tier_1_scope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)
            (src_root / "tests").mkdir(parents=True)

            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            for index in range(45):
                (src_root / "tests" / f"test_{index:02d}.py").write_text(
                    "def test_ok():\n    assert True\n",
                    encoding="utf-8",
                )

            job = job_runner.start_job(src_root)
            progress = job_state.load_json(Path(job["job_dir"]) / job_state.PROGRESS_FILE)
            batch_paths = {item["rel_path"] for item in job["next_batch"]["items"]}

            self.assertEqual(progress["selected_priority_scope"], job_state.SCOPE_TIER_1_ONLY)
            self.assertTrue(progress["scope_decision_recommended"])
            self.assertEqual(progress["next_locked_tier"], 2)
            self.assertFalse(progress["awaiting_scope_decision"])
            self.assertEqual(batch_paths, {"README.md", "src/app.py"})

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
                encoding="utf-8",
                errors="replace",
                env=SUBPROCESS_ENV,
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
                encoding="utf-8",
                errors="replace",
                env=SUBPROCESS_ENV,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(output_file.exists())
            self.assertIn("source root", completed.stderr)

    def test_start_job_creates_manifest_progress_and_lock_under_cn_output_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)
            (src_root / "docs" / "guide.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            output_dir = Path(job["dst_root"]) / job_state.OUTPUT_DIR_NAME

            self.assertEqual(Path(job["job_dir"]), output_dir)
            self.assertTrue((output_dir / job_state.MANIFEST_FILE).exists())
            self.assertTrue((output_dir / job_state.PROGRESS_FILE).exists())
            self.assertTrue((output_dir / job_state.ORIGINALS_LOCK_FILE).exists())
            self.assertTrue((output_dir / job_state.JOB_INFO_FILE).exists())
            self.assertEqual(job["next_batch"]["batch_index"], 1)
            self.assertEqual(len(job["next_batch"]["items"]), 2)
            self.assertIn("preflight_summary", job)
            self.assertEqual(job["preflight_summary"]["candidate_output_root"], str(Path(tmpdir) / "demo-CN"))
            self.assertFalse(job["preflight_summary"]["requires_user_confirmation"])
            self.assertIn("项目画像判定为", job["project_profile_summary"])
            self.assertIn("首轮优先关注", job["user_message"])
            self.assertIn("当前下一步", job["internal_reason"])
            self.assertEqual(job["operator_advice"], job["user_message"])

    def test_start_exposes_preflight_summary_for_risky_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "wrapper"
            nested_root = src_root / "inner-project"
            nested_root.mkdir(parents=True)
            (nested_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            preflight = job["preflight_summary"]

            self.assertTrue(preflight["requires_user_confirmation"])
            self.assertEqual(preflight["confirmation_reason"], "single-child-wrapper-detected")
            self.assertEqual(preflight["source_root_signature"]["top_level_dirs"], ["inner-project"])

    def test_status_reads_progress_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "docs" / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            status = job_runner.get_job_status(job["dst_root"])

            self.assertEqual(status["summary"]["in_progress_llm_files"], 1)
            self.assertEqual(status["current_batch"]["batch_index"], 1)
            self.assertEqual(status["refresh_checkpoint_count"], 1)
            self.assertIn("preflight_summary", status)
            self.assertEqual(status["preflight_summary"]["candidate_output_root"], str(Path(tmpdir) / "demo-CN"))
            self.assertIn("context_usage_hint", status["summary"])
            self.assertFalse(status["summary"]["context_usage_hint"]["allow_completed_file_full_content_in_context"])
            self.assertEqual(
                status["summary"]["context_usage_hint"]["completed_file_context_policy"],
                "metadata-only-unless-explicit-reopen",
            )
            self.assertIn("项目画像判定为", status["project_profile_summary"])
            self.assertIn("先完成当前这一批文件", status["user_message"])
            self.assertIn("先完成这一批文件", status["internal_reason"])

    def test_status_reports_scope_gate_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)
            (src_root / "tests").mkdir(parents=True)

            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            for index in range(45):
                (src_root / "tests" / f"test_{index:02d}.py").write_text(
                    "def test_ok():\n    assert True\n",
                    encoding="utf-8",
                )

            job = job_runner.start_job(src_root)
            status = job_runner.get_job_status(job["dst_root"])

            self.assertEqual(status["selected_priority_scope"], job_state.SCOPE_TIER_1_ONLY)
            self.assertEqual(status["next_locked_tier"], 2)
            self.assertFalse(status["awaiting_scope_decision"])
            self.assertEqual(status["next_action"], "finish_current_batch")
            self.assertEqual(status["next_action_reason_code"], "current_batch_still_in_progress")
            self.assertIn("当前批次仍在处理中", status["next_action_reason"])
            self.assertIn("decision_evidence", status)
            self.assertIn("tier_2", status["remaining_priority_tiers"])
            self.assertIn("先完成当前这一批文件", status["user_message"])

    def test_mark_updates_progress_after_each_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "docs" / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            Path(batch_item["cn_file"]).write_text("# 演示\n", encoding="utf-8")

            result = job_runner.mark_job_file(job["dst_root"], batch_item["file_id"], status="completed")
            progress = job_state.load_json(Path(job["job_dir"]) / job_state.PROGRESS_FILE)
            item = next(item for item in progress["items"] if item["file_id"] == batch_item["file_id"])

            self.assertEqual(result["updated_item"]["status"], "completed")
            self.assertEqual(item["status"], "completed")
            self.assertIsNotNone(item["completed_at"])

    def test_mark_rejects_completed_status_when_cn_file_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            result = job_runner.mark_job_file(job["dst_root"], batch_item["file_id"], status="completed")

            self.assertEqual(result["updated_item"]["status"], "failed")
            self.assertIn("cn_file", result["updated_item"]["last_error"])
            self.assertFalse(result["summary"]["batch_verification_passed"])
            self.assertEqual(result["summary"]["next_action"], "fix_current_batch_outputs")

    def test_heartbeat_updates_worker_activity_for_in_progress_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            result = job_runner.heartbeat_job_files(
                job["dst_root"],
                worker_id="worker-1",
                file_ids=[batch_item["file_id"]],
                note="first-pass",
            )
            progress = job_state.load_json(Path(job["job_dir"]) / job_state.PROGRESS_FILE)
            item = next(item for item in progress["items"] if item["file_id"] == batch_item["file_id"])
            worker = progress["worker_activity"]["worker-1"]

            self.assertEqual(item["assigned_worker_id"], "worker-1")
            self.assertIsNotNone(item["claimed_at"])
            self.assertIsNotNone(item["last_heartbeat_at"])
            self.assertEqual(item["heartbeat_count"], 1)
            self.assertEqual(item["watchdog_state"], "healthy")
            self.assertEqual(worker["active_file_ids"], [batch_item["file_id"]])
            self.assertEqual(result["watchdog"]["status"], "healthy")
            self.assertFalse(result["summary"]["watchdog_intervention_required"])

    def test_watchdog_detects_silent_items_without_first_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            progress_path = Path(job["job_dir"]) / job_state.PROGRESS_FILE
            progress = job_state.load_json(progress_path)
            stale_at = (datetime.now(timezone.utc) - timedelta(seconds=600)).replace(microsecond=0).isoformat()
            progress["active_batch"]["started_at"] = stale_at
            target = next(item for item in progress["items"] if item["file_id"] == batch_item["file_id"])
            target["started_at"] = stale_at
            job_state.atomic_write_json(progress_path, progress)

            result = job_runner.watchdog_job(job["dst_root"])

            self.assertTrue(result["watchdog"]["intervention_required"])
            self.assertEqual(result["watchdog"]["status"], "intervention_required")
            self.assertEqual(result["watchdog"]["stale_items"][0]["reason_code"], "no_heartbeat_since_batch_dispatch")
            self.assertEqual(result["watchdog"]["recommended_actions"][0]["action"], "reassign_unclaimed_files")
            self.assertEqual(result["summary"]["recommended_watchdog_action_count"], 1)
            self.assertEqual(result["summary"]["next_action"], "investigate_stuck_subagents")
            self.assertIn("卡住", result["user_message"])
            self.assertIn("建议动作清单", result["user_message"])

    def test_resume_returns_stale_active_batch_when_heartbeat_times_out(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            job_runner.heartbeat_job_files(job["dst_root"], worker_id="worker-2", file_ids=[batch_item["file_id"]])

            progress_path = Path(job["job_dir"]) / job_state.PROGRESS_FILE
            progress = job_state.load_json(progress_path)
            timed_out_at = (datetime.now(timezone.utc) - timedelta(seconds=900)).replace(microsecond=0).isoformat()
            target = next(item for item in progress["items"] if item["file_id"] == batch_item["file_id"])
            target["last_heartbeat_at"] = timed_out_at
            progress["worker_activity"]["worker-2"]["last_heartbeat_at"] = timed_out_at
            job_state.atomic_write_json(progress_path, progress)

            resumed = job_runner.resume_job(job["dst_root"])

            self.assertEqual(resumed["next_batch"]["status"], "stale_active_batch")
            self.assertEqual(resumed["batch_selection_reason_code"], "active_batch_requires_watchdog_intervention")
            self.assertTrue(resumed["decision_evidence"]["watchdog_intervention_required"])
            self.assertEqual(resumed["watchdog"]["recommended_actions"][0]["action"], "check_or_replace_worker")
            self.assertEqual(resumed["decision_evidence"]["recommended_watchdog_action_count"], 1)
            self.assertEqual(resumed["summary"]["next_action"], "investigate_stuck_subagents")
            self.assertIn("卡住", resumed["user_message"])

    def test_resume_blocks_when_current_batch_fails_disk_verification(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            job_runner.mark_job_file(job["dst_root"], batch_item["file_id"], status="completed")

            resumed = job_runner.resume_job(job["dst_root"])

            self.assertEqual(resumed["next_batch"]["status"], "blocked_by_batch_verification")
            self.assertFalse(resumed["summary"]["batch_verification_passed"])
            self.assertEqual(resumed["summary"]["next_action"], "fix_current_batch_outputs")
            self.assertIn("缺失输出", resumed["user_message"])

    def test_resume_reuses_existing_in_progress_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            for index in range(25):
                (src_root / f"doc-{index:02d}.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            resumed = job_runner.resume_job(job["dst_root"])

            self.assertTrue(resumed["next_batch"]["reused_in_progress_batch"])
            self.assertEqual(resumed["next_batch"]["batch_index"], 1)
            self.assertEqual(len(resumed["next_batch"]["items"]), 20)

    def test_headless_runner_stops_on_stale_active_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            job_runner.heartbeat_job_files(job["dst_root"], worker_id="worker-3", file_ids=[batch_item["file_id"]])

            progress_path = Path(job["job_dir"]) / job_state.PROGRESS_FILE
            progress = job_state.load_json(progress_path)
            timed_out_at = (datetime.now(timezone.utc) - timedelta(seconds=900)).replace(microsecond=0).isoformat()
            target = next(item for item in progress["items"] if item["file_id"] == batch_item["file_id"])
            target["last_heartbeat_at"] = timed_out_at
            progress["worker_activity"]["worker-3"]["last_heartbeat_at"] = timed_out_at
            job_state.atomic_write_json(progress_path, progress)

            payload = headless_runner.run_job(job["dst_root"])

            self.assertEqual(payload["stop_reason"], "stale_active_batch")
            self.assertEqual(payload["summary"]["next_action"], "investigate_stuck_subagents")
            self.assertEqual(payload["watchdog"]["status"], "intervention_required")
            self.assertEqual(payload["watchdog"]["recommended_actions"][0]["action"], "check_or_replace_worker")

    def test_resume_returns_batch_selection_reason(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            for index in range(25):
                (src_root / f"doc-{index:02d}.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            resumed = job_runner.resume_job(job["dst_root"])

            self.assertIn("batch_selection_reason", resumed)
            self.assertEqual(resumed["batch_selection_reason_code"], "reuse_current_in_progress_batch")
            self.assertIn("批次 1", resumed["batch_selection_reason"])
            self.assertIn("decision_evidence", resumed)
            self.assertEqual(
                resumed["decision_evidence"]["batch_selection_reason_code"],
                "reuse_current_in_progress_batch",
            )

    def test_resume_advances_to_next_batch_after_current_batch_is_completed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            for index in range(25):
                (src_root / f"doc-{index:02d}.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            for item in job["next_batch"]["items"]:
                Path(item["cn_file"]).write_text("generated\n", encoding="utf-8")
                job_runner.mark_job_file(job["dst_root"], item["file_id"], status="completed")

            resumed = job_runner.resume_job(job["dst_root"])

            self.assertFalse(resumed["next_batch"]["reused_in_progress_batch"])
            self.assertEqual(resumed["next_batch"]["batch_index"], 2)
            self.assertEqual(len(resumed["next_batch"]["items"]), 5)
            self.assertEqual(resumed["summary"]["refresh_checkpoint_count"], 2)

    def test_resume_sets_context_usage_hint_after_completed_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            for index in range(25):
                (src_root / f"doc-{index:02d}.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            for item in job["next_batch"]["items"]:
                Path(item["cn_file"]).write_text("generated\n", encoding="utf-8")
                job_runner.mark_job_file(job["dst_root"], item["file_id"], status="completed")

            resumed = job_runner.resume_job(job["dst_root"])
            hint = resumed["summary"]["context_usage_hint"]

            self.assertTrue(hint["should_start_fresh_context"])
            self.assertEqual(hint["reason"], "completed-files-threshold-reached")
            self.assertIn("translate-progress.json", hint["state_files_to_read"])
            self.assertIn("translate-manifest.json", hint["state_files_to_read"])
            self.assertIn("completed_cn_file_full_content", hint["disallowed_context_sources"])

    def test_resume_returns_complete_when_no_pending_files_remain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            Path(batch_item["cn_file"]).write_text("generated\n", encoding="utf-8")
            job_runner.mark_job_file(job["dst_root"], batch_item["file_id"], status="completed")

            resumed = job_runner.resume_job(job["dst_root"])

            self.assertEqual(resumed["next_batch"]["status"], "complete")
            self.assertEqual(resumed["summary"]["pending_llm_files"], 0)

    def test_resume_waits_for_scope_decision_after_tier_1_finishes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)
            (src_root / "tests").mkdir(parents=True)

            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            for index in range(45):
                (src_root / "tests" / f"test_{index:02d}.py").write_text(
                    "def test_ok():\n    assert True\n",
                    encoding="utf-8",
                )

            job = job_runner.start_job(src_root)
            for item in job["next_batch"]["items"]:
                Path(item["cn_file"]).write_text("generated\n", encoding="utf-8")
                job_runner.mark_job_file(job["dst_root"], item["file_id"], status="completed")

            resumed = job_runner.resume_job(job["dst_root"])

            self.assertEqual(resumed["next_batch"]["status"], "awaiting_scope_decision")
            self.assertTrue(resumed["summary"]["awaiting_scope_decision"])
            self.assertEqual(resumed["summary"]["next_locked_tier"], 2)
            self.assertEqual(resumed["summary"]["next_action"], "ask_user_about_tier_2")

            status = job_runner.get_job_status(job["dst_root"])
            self.assertIn("是否继续进入 2 档", status["user_message"])
            self.assertIn("请先向用户确认是否进入 2 档", status["internal_reason"])

    def test_scope_decision_unlocks_tier_2_after_tier_1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)
            (src_root / "tests").mkdir(parents=True)

            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            for index in range(45):
                (src_root / "tests" / f"test_{index:02d}.py").write_text(
                    "def test_ok():\n    assert True\n",
                    encoding="utf-8",
                )

            job = job_runner.start_job(src_root)
            for item in job["next_batch"]["items"]:
                Path(item["cn_file"]).write_text("generated\n", encoding="utf-8")
                job_runner.mark_job_file(job["dst_root"], item["file_id"], status="completed")

            decision = job_runner.decide_job_scope(job["dst_root"], job_state.SCOPE_TIER_1_AND_2)
            resumed = job_runner.resume_job(job["dst_root"])

            self.assertEqual(decision["selected_priority_scope"], job_state.SCOPE_TIER_1_AND_2)
            self.assertFalse(decision["summary"]["awaiting_scope_decision"])
            self.assertIn("已放开 1+2 档", decision["user_message"])
            self.assertIn("scope 决策为 `tier_1_and_2`", decision["internal_reason"])
            self.assertEqual(decision["operator_advice"], decision["user_message"])
            self.assertEqual(resumed["next_batch"]["status"], "ready")
            self.assertEqual({item["rel_path"] for item in resumed["next_batch"]["items"]}, {"docs/guide.md"})
            self.assertEqual(resumed["summary"]["next_locked_tier"], 3)

    def test_scope_cannot_advance_with_unverified_completed_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)
            (src_root / "tests").mkdir(parents=True)

            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            for index in range(45):
                (src_root / "tests" / f"test_{index:02d}.py").write_text(
                    "def test_ok():\n    assert True\n",
                    encoding="utf-8",
                )

            job = job_runner.start_job(src_root)
            for item in job["next_batch"]["items"]:
                job_runner.mark_job_file(job["dst_root"], item["file_id"], status="completed")

            with self.assertRaisesRegex(ValueError, "batch verification"):
                job_runner.decide_job_scope(job["dst_root"], job_state.SCOPE_TIER_1_AND_2)

    def test_skip_tier_3_keeps_report_complete_without_missing_cn_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)
            (src_root / "tests").mkdir(parents=True)

            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            for index in range(45):
                (src_root / "tests" / f"test_{index:02d}.py").write_text(
                    "def test_ok():\n    assert True\n",
                    encoding="utf-8",
                )

            job = job_runner.start_job(src_root)
            for item in job["next_batch"]["items"]:
                Path(item["cn_file"]).write_text("generated\n", encoding="utf-8")
                job_runner.mark_job_file(job["dst_root"], item["file_id"], status="completed")

            job_runner.decide_job_scope(job["dst_root"], job_state.SCOPE_TIER_1_AND_2)
            resumed = job_runner.resume_job(job["dst_root"])
            for item in resumed["next_batch"]["items"]:
                Path(item["cn_file"]).write_text("generated\n", encoding="utf-8")
                job_runner.mark_job_file(job["dst_root"], item["file_id"], status="completed")

            decision = job_runner.decide_job_scope(job["dst_root"], job_state.SCOPE_DECISION_SKIP_TIER_3)
            report = job_runner.build_job_report(job["dst_root"])

            self.assertIn("跳过 3 档", decision["user_message"])
            self.assertIn("scope 决策为 `skip_tier_3`", decision["internal_reason"])
            self.assertEqual(report["status"], "complete_with_skipped_tiers")
            self.assertEqual(report["skipped_priority_tiers"], [3])
            self.assertEqual(report["missing_cn_files"], [])
            self.assertFalse(report["awaiting_scope_decision"])

    def test_report_flags_modified_source_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            Path(batch_item["cn_file"]).write_text("# 演示\n", encoding="utf-8")
            job_runner.mark_job_file(job["dst_root"], batch_item["file_id"], status="completed")

            (src_root / "guide.md").write_text("# Mutated\n", encoding="utf-8")
            report = job_runner.build_job_report(job["dst_root"])

            self.assertFalse(report["source_integrity_ok"])
            self.assertEqual(len(report["modified_source_files"]), 1)

    def test_report_flags_modified_original_copies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "guide.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            Path(batch_item["cn_file"]).write_text("# 演示\n", encoding="utf-8")
            job_runner.mark_job_file(job["dst_root"], batch_item["file_id"], status="completed")

            Path(batch_item["copied_file"]).write_text("# Mutated\n", encoding="utf-8")
            report = job_runner.build_job_report(job["dst_root"])

            self.assertFalse(report["copied_original_integrity_ok"])
            self.assertEqual(len(report["modified_original_copies"]), 1)

    def test_build_job_report_writes_reports_under_cn_output_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            for item in job["next_batch"]["items"]:
                Path(item["cn_file"]).write_text("generated\n", encoding="utf-8")
                job_runner.mark_job_file(job["dst_root"], item["file_id"], status="completed")

            report = job_runner.build_job_report(job["dst_root"])
            output_dir = Path(job["dst_root"]) / job_state.OUTPUT_DIR_NAME
            final_report_text = (output_dir / job_state.TEXT_REPORT_FILE).read_text(encoding="utf-8")

            self.assertEqual(report["status"], "ok")
            self.assertIn("preflight_summary", report)
            self.assertFalse(report["preflight_summary"]["requires_user_confirmation"])
            self.assertIn("查看结果", report["user_message"])
            self.assertIn("项目画像判定为", report["internal_reason"])
            self.assertTrue((output_dir / job_state.VERIFY_REPORT_FILE).exists())
            self.assertTrue((output_dir / job_state.TEXT_REPORT_FILE).exists())
            self.assertIn("=== 项目翻译结果报告 ===", final_report_text)
            self.assertIn("工作量摘要：", final_report_text)
            self.assertIn("项目画像：", final_report_text)
            self.assertIn("用户可读摘要：", final_report_text)
            self.assertIn("对用户提示：", final_report_text)
            self.assertIn("内部判断：", final_report_text)
            self.assertIn("优先级分档：", final_report_text)
            self.assertIn("范围闸门：", final_report_text)
            self.assertIn("1 档 核心理解层", final_report_text)
            self.assertIn("进度摘要：", final_report_text)
            self.assertIn("原文件保护：", final_report_text)

    def test_build_job_report_flags_source_root_pollution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            job = job_runner.start_job(src_root)
            batch_item = job["next_batch"]["items"][0]
            Path(batch_item["cn_file"]).write_text("# 演示\n", encoding="utf-8")
            job_runner.mark_job_file(job["dst_root"], batch_item["file_id"], status="completed")

            (src_root / "scan_result.json").write_text('{"debug": true}\n', encoding="utf-8")
            report = job_runner.build_job_report(job["dst_root"])

            self.assertEqual(report["status"], "incomplete")
            self.assertTrue(report["source_root_pollution_detected"])
            self.assertEqual(len(report["source_root_pollution"]), 1)
            self.assertEqual(report["source_root_pollution"][0]["rel_path"], "scan_result.json")

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
                encoding="utf-8",
                errors="replace",
                env=SUBPROCESS_ENV,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(output_file.exists())
            self.assertIn("source root", completed.stderr)

    def test_headless_runner_honors_max_batches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            for index in range(25):
                (src_root / f"doc-{index:02d}.md").write_text("# Demo\n", encoding="utf-8")

            payload = headless_runner.run_job(src_root, max_batches=1)
            output_dir = Path(payload["job_dir"])

            self.assertEqual(payload["stop_reason"], "max_batches_reached")
            self.assertEqual(payload["processed_batches"], 1)
            self.assertEqual(payload["dispatched_batches"], [1])
            self.assertEqual(payload["next_batch"]["status"], "ready")
            self.assertTrue((output_dir / headless_runner.HEADLESS_RUN_SUMMARY_FILE).exists())

    def test_headless_runner_stops_at_scope_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            (src_root / "docs").mkdir(parents=True)
            (src_root / "src").mkdir(parents=True)
            (src_root / "tests").mkdir(parents=True)

            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (src_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            for index in range(45):
                (src_root / "tests" / f"test_{index:02d}.py").write_text(
                    "def test_ok():\n    assert True\n",
                    encoding="utf-8",
                )

            job = job_runner.start_job(src_root)
            for item in job["next_batch"]["items"]:
                Path(item["cn_file"]).write_text("generated\n", encoding="utf-8")
                job_runner.mark_job_file(job["dst_root"], item["file_id"], status="completed")

            payload = headless_runner.run_job(job["dst_root"], max_batches=3)

            self.assertEqual(payload["stop_reason"], "awaiting_scope_decision")
            self.assertEqual(payload["processed_batches"], 0)
            self.assertEqual(payload["summary"]["next_action"], "ask_user_about_tier_2")
            self.assertIn("2 档", payload["user_message"])

    def test_verify_outputs_reports_missing_cn_files_and_integrity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")

            manifest = planning.prepare_project_copy(src_root)
            lock = job_state.build_originals_lock(manifest)
            progress = job_state.build_progress(manifest, job_id="demo")
            manifest_path = Path(tmpdir) / "manifest.json"
            progress_path = Path(tmpdir) / "progress.json"
            lock_path = Path(tmpdir) / "lock.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
            lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")

            readme_cn = Path(manifest["dst_root"]) / "README-CN.md"
            readme_cn.write_text("# 演示\n", encoding="utf-8")

            verify_script = SCRIPTS_DIR / "verify_outputs.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(verify_script),
                    str(manifest_path),
                    "--progress",
                    str(progress_path),
                    "--lock",
                    str(lock_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=SUBPROCESS_ENV,
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            report = json.loads(completed.stdout)
            self.assertEqual(report["generated"]["document_cn_files"], 1)
            self.assertEqual(report["generated"]["code_cn_files"], 0)
            self.assertEqual(len(report["missing_cn_files"]), 1)
            self.assertTrue(report["source_integrity_ok"])

    def test_verify_outputs_detects_source_root_pollution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "demo"
            src_root.mkdir()
            (src_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (src_root / "translate-final-report.txt").write_text("debug\n", encoding="utf-8")

            manifest = planning.prepare_project_copy(src_root)
            report = verify_outputs.build_report(manifest)

            self.assertTrue(report["source_root_pollution_detected"])
            self.assertEqual(len(report["source_root_pollution"]), 1)
            self.assertEqual(report["source_root_pollution"][0]["rel_path"], "translate-final-report.txt")

    def test_skill_mentions_large_project_protocol(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        required_phrases = [
            "translate-progress.json",
            "translate-originals-lock.json",
            "preflight_summary",
            "source_root_signature",
            "requires_user_confirmation",
            "context_usage_hint",
            "heartbeat",
            "watchdog",
            "recommended_actions",
            "建议动作清单",
            "子代理心跳",
            "卡住",
            "completed_file_context_policy",
            "resume",
            "status",
            "mark",
            "scope",
            "1 档",
            "2 档",
            "3 档",
            "priority_tiers",
            "priority_tier_decision_recommended",
            "priority_tier_recommended_scope",
            "项目画像",
            "固定进入 1 档",
            "动态提升到 1 档",
            "`agents/` 目录",
            "`user_message`",
            "`internal_reason`",
            "`operator_advice`",
            "默认自动开始 `1 档`",
            "`1 档` 完成后必须暂停并问用户是否进入 `2 档`",
            "用户选择必须通过状态命令写入作业文件",
            "每 20 个文件强制刷新",
            "不得改 copied_file",
            "不得改源目录",
            "预检优先于批量执行",
            "不信任子代理自报完成",
            "禁止绕过自动化脚本",
            "禁止将调试/报告文件写到项目根目录",
            "超大任务按批次推进",
            "manifest + progress",
            "下一批文件只能从进度账本里取",
        ]

        for phrase in required_phrases:
            self.assertIn(phrase, skill_text)

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

    def test_python_sources_disable_bytecode_cache(self):
        targets = [
            SKILL_ROOT / "scripts" / "job_runner.py",
            SKILL_ROOT / "scripts" / "job_state.py",
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
