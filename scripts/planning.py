from __future__ import annotations

import math
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

from classification import build_cn_filename, classify_file
from job_state import DEFAULT_BATCH_SIZE, LLM_CATEGORIES, atomic_write_json
from text_metrics import collect_text_metrics, estimate_input_tokens


CHUNK_CHAR_TARGET = 8000
SINGLE_FILE_RISK_CHARS = 120000
TOTAL_LLM_FILES_RISK = 200
TOTAL_ROUNDS_RISK = 120
TOTAL_INPUT_TOKENS_RISK = 400000
TOTAL_FILES_TIER_DIALOG_RISK = 150
TIER_3_FILES_RISK = 40
TIER_3_LLM_FILES_RISK = 20

PRIORITY_TIER_LABELS = {
    1: "核心理解层",
    2: "重要扩展层",
    3: "外围噪声层",
}

CATEGORY_SORT_ORDER = {
    "document": 0,
    "code": 1,
    "other": 2,
}

ROOT_CORE_DOC_NAMES = {
    "readme",
    "changelog",
    "contributing",
    "license",
    "install",
    "installation",
    "quickstart",
    "quick-start",
    "getting-started",
    "overview",
}

CORE_DOC_HINTS = {
    "readme",
    "index",
    "overview",
    "getting-started",
    "quickstart",
    "quick-start",
    "architecture",
    "api",
}

CORE_CODE_DIRS = {
    "src",
    "app",
    "api",
    "server",
    "client",
    "frontend",
    "backend",
    "core",
    "lib",
    "cmd",
}

CORE_CODE_STEMS = {
    "main",
    "app",
    "server",
    "client",
    "index",
    "api",
    "router",
    "routes",
    "entry",
}

IMPORTANT_DOC_DIRS = {
    "docs",
    "doc",
    "guide",
    "guides",
    "manual",
    "manuals",
    "reference",
    "references",
    "wiki",
}

IMPORTANT_CODE_DIRS = {
    "scripts",
    "script",
    "tools",
    "tool",
    "bin",
    "cli",
    "internal",
    "pkg",
    "modules",
    "components",
    "services",
    "controllers",
    "handlers",
}

LOW_PRIORITY_DIRS = {
    "test",
    "tests",
    "__tests__",
    "spec",
    "specs",
    "fixtures",
    "fixture",
    "mocks",
    "mock",
    "example",
    "examples",
    "demo",
    "demos",
    "sample",
    "samples",
    "bench",
    "benches",
    "benchmark",
    "benchmarks",
    "coverage",
    "history",
    "archive",
    "archives",
    "legacy",
    "deprecated",
    "plans",
    "plan",
    "draft",
    "drafts",
    "tmp",
    "temp",
}

LOW_PRIORITY_DOC_HINTS = {
    "plan",
    "plans",
    "roadmap",
    "todo",
    "backlog",
    "notes",
    "meeting",
    "draft",
    "archive",
    "history",
    "retro",
    "retrospective",
}

ROOT_CORE_OTHER_FILENAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "poetry.lock",
    "pipfile",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "gemfile",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env.example",
}

DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "coverage",
    ".coverage",
    ".next",
    ".nuxt",
    "out",
    "target",
}


def assess_project(
    src_root: str | Path,
    dst_root: str | Path | None = None,
    exclude_dirs: list[str] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    src_path = Path(src_root).expanduser().resolve()
    if not src_path.is_dir():
        raise ValueError(f"source directory does not exist: {src_path}")

    dst_path = build_destination_root(src_path, dst_root)
    excluded_dir_names = _build_excluded_dir_names(exclude_dirs)
    skipped_dir_names: set[str] = set()
    raw_items = []
    summary = _empty_summary(batch_size)
    _set_root_summary(summary, src_path)

    for file_path in _iter_files(src_path, excluded_dir_names, skipped_dir_names):
        rel_path = file_path.relative_to(src_path).as_posix()
        category = classify_file(file_path)
        metrics = collect_text_metrics(file_path)
        cn_rel_path = _build_cn_rel_path(rel_path, category)
        estimated_rounds = _estimate_rounds(metrics["estimated_chars"], category)
        estimated_input_tokens = estimate_input_tokens(metrics["estimated_chars"])
        token_low, token_high = _estimate_total_token_range(category, estimated_input_tokens)
        priority_tier, priority_reason = _classify_priority_tier(rel_path, category)

        item = {
            "rel_path": rel_path,
            "src_file": str(file_path),
            "category": category,
            "copied_rel_path": rel_path,
            "cn_rel_path": cn_rel_path,
            "llm_action": _llm_action(category),
            "priority_tier": priority_tier,
            "priority_tier_label": PRIORITY_TIER_LABELS[priority_tier],
            "priority_reason": priority_reason,
            "size_bytes": metrics["size_bytes"],
            "estimated_chars": metrics["estimated_chars"],
            "estimated_input_tokens": estimated_input_tokens,
            "estimated_tokens_low": token_low,
            "estimated_tokens_high": token_high,
            "estimated_rounds": estimated_rounds,
            "encoding": metrics["encoding"],
            "read_error": metrics["read_error"],
            "sample_based": metrics["sample_based"],
        }
        raw_items.append(item)

    raw_items.sort(key=_item_sort_key)

    items = []
    sequence_index = 0
    llm_sequence = 0
    for item in raw_items:
        sequence_index += 1
        item["sequence_index"] = sequence_index
        item["file_id"] = f"F{sequence_index:06d}"
        item["batch_index"] = None
        if item["category"] in LLM_CATEGORIES:
            llm_sequence += 1
            item["batch_index"] = math.ceil(llm_sequence / batch_size)

        items.append(item)
        _update_summary(summary, item)

    summary["excluded_dirs"] = sorted(skipped_dir_names)
    _finalize_summary(summary)

    return {
        "src_root": str(src_path),
        "dst_root": str(dst_path),
        "generated_at": _utc_timestamp(),
        "strategy": "replace-existing-destination",
        "items": items,
        "summary": summary,
        "excluded_dir_rules": sorted(excluded_dir_names),
    }


def prepare_project_copy(
    src_root: str | Path,
    dst_root: str | Path | None = None,
    replace_existing: bool = True,
    exclude_dirs: list[str] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    manifest = assess_project(
        src_root,
        dst_root=dst_root,
        exclude_dirs=exclude_dirs,
        batch_size=batch_size,
    )
    src_path = Path(manifest["src_root"])
    dst_path = Path(manifest["dst_root"])
    excluded_dir_names = set(manifest.get("excluded_dir_rules", []))
    skipped_dir_names: set[str] = set(manifest["summary"].get("excluded_dirs", []))

    if dst_path.exists() and replace_existing:
        shutil.rmtree(dst_path)
    dst_path.mkdir(parents=True, exist_ok=True)

    created_directories = 0
    for directory in _iter_directories(src_path, excluded_dir_names, skipped_dir_names):
        rel_dir = directory.relative_to(src_path)
        target_dir = dst_path / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        created_directories += 1

    copied_files = 0
    copy_failures = []
    for item in manifest["items"]:
        src_file = Path(item["src_file"])
        copied_file = dst_path / item["copied_rel_path"]
        item["copied_file"] = str(copied_file)
        item["cn_file"] = str(dst_path / item["cn_rel_path"]) if item["cn_rel_path"] else None
        item["copy_failure"] = None

        try:
            copied_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, copied_file)
            copied_files += 1
        except OSError as exc:
            item["copy_failure"] = str(exc)
            copy_failures.append(
                {
                    "file_id": item["file_id"],
                    "rel_path": item["rel_path"],
                    "reason": str(exc),
                }
            )

    manifest["summary"]["created_directories"] = created_directories
    manifest["summary"]["copied_original_files"] = copied_files
    manifest["summary"]["copy_failures"] = len(copy_failures)
    manifest["summary"]["copy_failure_items"] = copy_failures
    manifest["summary"]["planned_cn_document_files"] = manifest["summary"]["document_files"]
    manifest["summary"]["planned_cn_code_files"] = manifest["summary"]["code_files"]
    manifest["summary"]["only_copy_files"] = manifest["summary"]["other_files"]
    manifest["summary"]["actual_prepare_completed_at"] = _utc_timestamp()
    return manifest


def build_destination_root(src_root: str | Path, dst_root: str | Path | None = None) -> Path:
    src_path = Path(src_root).expanduser().resolve()
    if dst_root is not None:
        return Path(dst_root).expanduser().resolve()
    return src_path.parent / f"{src_path.name}-CN"


def write_json(output_path: str | Path, payload: dict) -> None:
    atomic_write_json(output_path, payload)


def _build_cn_rel_path(rel_path: str, category: str) -> str | None:
    if category not in LLM_CATEGORIES:
        return None

    rel = Path(rel_path)
    return rel.with_name(build_cn_filename(rel.name)).as_posix()


def _llm_action(category: str) -> str | None:
    if category == "document":
        return "translate_document"
    if category == "code":
        return "annotate_code"
    return None


def _estimate_rounds(char_count: int, category: str) -> int:
    if category not in LLM_CATEGORIES:
        return 0
    return max(1, math.ceil(max(char_count, 1) / CHUNK_CHAR_TARGET))


def _estimate_total_token_range(category: str, input_tokens: int) -> tuple[int, int]:
    if category == "document":
        return math.ceil(input_tokens * 1.8), math.ceil(input_tokens * 3.2)
    if category == "code":
        return math.ceil(input_tokens * 1.6), math.ceil(input_tokens * 2.8)
    return 0, 0


def _empty_summary(batch_size: int) -> dict:
    return {
        "root_interpretation": "exact-user-path",
        "top_level_files": 0,
        "top_level_dirs": 0,
        "single_child_wrapper_detected": False,
        "total_files": 0,
        "document_files": 0,
        "code_files": 0,
        "other_files": 0,
        "llm_files": 0,
        "llm_batch_count": 0,
        "batch_size": batch_size,
        "estimated_text_chars": 0,
        "estimated_input_tokens": 0,
        "estimated_tokens_low": 0,
        "estimated_tokens_high": 0,
        "estimated_rounds": 0,
        "estimated_minutes_low": 0,
        "estimated_minutes_high": 0,
        "estimated_duration_minutes_low": 0,
        "estimated_duration_minutes_high": 0,
        "oversized_files": [],
        "undecodable_files": [],
        "risk_flags": [],
        "requires_confirmation": False,
        "largest_llm_file_chars": 0,
        "excluded_dirs": [],
        "priority_tiers": {
            "tier_1": _empty_tier_summary(1),
            "tier_2": _empty_tier_summary(2),
            "tier_3": _empty_tier_summary(3),
        },
        "priority_tier_decision_recommended": False,
        "priority_tier_recommended_scope": "all_tiers",
        "priority_tier_decision_options": [
            "tier_1_only",
            "tier_1_and_2",
            "all_tiers",
            "skip_tier_3",
        ],
    }


def _set_root_summary(summary: dict, src_path: Path) -> None:
    top_level_dirs = 0
    top_level_files = 0

    for child in src_path.iterdir():
        if child.is_dir():
            top_level_dirs += 1
        elif child.is_file():
            top_level_files += 1

    summary["top_level_dirs"] = top_level_dirs
    summary["top_level_files"] = top_level_files
    summary["single_child_wrapper_detected"] = top_level_dirs == 1 and top_level_files == 0


def _update_summary(summary: dict, item: dict) -> None:
    summary["total_files"] += 1
    summary[f"{item['category']}_files"] += 1

    tier_key = f"tier_{item['priority_tier']}"
    tier_summary = summary["priority_tiers"][tier_key]
    tier_summary["total_files"] += 1
    tier_summary[f"{item['category']}_files"] += 1
    if item["category"] in LLM_CATEGORIES:
        tier_summary["llm_files"] += 1
    if len(tier_summary["example_paths"]) < 5:
        tier_summary["example_paths"].append(item["rel_path"])

    if item["category"] in LLM_CATEGORIES:
        summary["llm_files"] += 1
        summary["llm_batch_count"] = max(summary["llm_batch_count"], item["batch_index"] or 0)
        summary["estimated_text_chars"] += item["estimated_chars"]
        summary["estimated_input_tokens"] += item["estimated_input_tokens"]
        summary["estimated_tokens_low"] += item["estimated_tokens_low"]
        summary["estimated_tokens_high"] += item["estimated_tokens_high"]
        summary["estimated_rounds"] += item["estimated_rounds"]
        summary["largest_llm_file_chars"] = max(
            summary["largest_llm_file_chars"],
            item["estimated_chars"],
        )

        if item["estimated_chars"] >= SINGLE_FILE_RISK_CHARS:
            summary["oversized_files"].append(
                {
                    "file_id": item["file_id"],
                    "rel_path": item["rel_path"],
                    "estimated_chars": item["estimated_chars"],
                }
            )

        if item["read_error"]:
            summary["undecodable_files"].append(
                {
                    "file_id": item["file_id"],
                    "rel_path": item["rel_path"],
                    "reason": item["read_error"],
                }
            )


def _finalize_summary(summary: dict) -> None:
    minutes_low = math.ceil(summary["estimated_rounds"] * 0.4)
    minutes_high = math.ceil(summary["estimated_rounds"] * 1.5)
    summary["estimated_minutes_low"] = minutes_low
    summary["estimated_minutes_high"] = minutes_high
    summary["estimated_duration_minutes_low"] = minutes_low
    summary["estimated_duration_minutes_high"] = minutes_high

    risk_flags = []
    if summary["llm_files"] > TOTAL_LLM_FILES_RISK:
        risk_flags.append("llm-file-count-high")
    if summary["estimated_rounds"] > TOTAL_ROUNDS_RISK:
        risk_flags.append("processing-rounds-high")
    if summary["estimated_input_tokens"] > TOTAL_INPUT_TOKENS_RISK:
        risk_flags.append("token-budget-high")
    if summary["oversized_files"]:
        risk_flags.append("oversized-text-files-detected")
    if summary["undecodable_files"]:
        risk_flags.append("undecodable-llm-files-detected")

    tier_decision_recommended = _should_recommend_tier_decision(summary)
    if tier_decision_recommended:
        risk_flags.append("priority-tier-review-recommended")

    summary["risk_flags"] = risk_flags
    summary["priority_tier_decision_recommended"] = tier_decision_recommended
    summary["priority_tier_recommended_scope"] = _recommended_tier_scope(summary)
    summary["requires_confirmation"] = bool(risk_flags)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _build_excluded_dir_names(exclude_dirs: list[str] | None) -> set[str]:
    merged = {name.lower() for name in DEFAULT_EXCLUDED_DIR_NAMES}
    if exclude_dirs:
        merged.update(name.lower() for name in exclude_dirs if name)
    return merged


def _empty_tier_summary(tier: int) -> dict:
    return {
        "tier": tier,
        "label": PRIORITY_TIER_LABELS[tier],
        "total_files": 0,
        "document_files": 0,
        "code_files": 0,
        "other_files": 0,
        "llm_files": 0,
        "example_paths": [],
    }


def _item_sort_key(item: dict) -> tuple[int, int, str]:
    return (
        item["priority_tier"],
        CATEGORY_SORT_ORDER.get(item["category"], 99),
        item["rel_path"].lower(),
    )


def _classify_priority_tier(rel_path: str, category: str) -> tuple[int, str]:
    pure_path = PurePosixPath(rel_path)
    parts = [part.lower() for part in pure_path.parts]
    filename = parts[-1]
    stem = pure_path.stem.lower()
    parent_dirs = parts[:-1]
    depth = len(parts) - 1

    matched_low_dir = next((part for part in parent_dirs if part in LOW_PRIORITY_DIRS), None)
    if matched_low_dir:
        return 3, f"位于低优先级目录 `{matched_low_dir}`"

    if _is_test_like_filename(filename, stem):
        return 3, "测试或规格文件"

    if category == "document":
        normalized_doc_name = _normalized_doc_name(filename)
        if depth == 0 and normalized_doc_name in ROOT_CORE_DOC_NAMES:
            return 1, "顶层核心项目文档"
        if parent_dirs[:1] and parent_dirs[0] in {"docs", "doc"} and stem in CORE_DOC_HINTS and depth <= 2:
            return 1, "核心项目说明文档"
        if stem in LOW_PRIORITY_DOC_HINTS:
            return 3, "历史计划或低优先级说明文档"
        if any(part in IMPORTANT_DOC_DIRS for part in parent_dirs) or depth <= 1:
            return 2, "重要说明文档"

    if category == "code":
        if depth == 0 and stem in CORE_CODE_STEMS:
            return 1, "顶层核心入口脚本"
        if any(part in {"api", "router", "routes"} for part in parent_dirs):
            return 1, "核心接口或路由代码"
        if parent_dirs[:1] and parent_dirs[0] in CORE_CODE_DIRS and (depth == 1 or stem in CORE_CODE_STEMS):
            return 1, "核心前后端入口代码"
        if any(part in IMPORTANT_CODE_DIRS for part in parent_dirs) or any(part in CORE_CODE_DIRS for part in parent_dirs):
            return 2, "重要支撑代码"

    if category == "other":
        if depth == 0 and filename in ROOT_CORE_OTHER_FILENAMES:
            return 1, "核心项目配置或依赖清单"
        if depth == 0:
            return 2, "顶层辅助文件"

    return 2, "默认归入重要扩展层"


def _normalized_doc_name(filename: str) -> str:
    pure_path = PurePosixPath(filename)
    stem = pure_path.stem.lower()
    if stem:
        return stem
    return filename.lower()


def _is_test_like_filename(filename: str, stem: str) -> bool:
    if stem.startswith("test_") or stem.endswith("_test"):
        return True
    if stem.startswith("spec_") or stem.endswith("_spec"):
        return True
    if ".test." in filename or ".spec." in filename:
        return True
    return False


def _should_recommend_tier_decision(summary: dict) -> bool:
    tier_3 = summary["priority_tiers"]["tier_3"]
    if summary["total_files"] >= TOTAL_FILES_TIER_DIALOG_RISK:
        return True
    if tier_3["total_files"] >= TIER_3_FILES_RISK:
        return True
    if tier_3["llm_files"] >= TIER_3_LLM_FILES_RISK:
        return True
    return False


def _recommended_tier_scope(summary: dict) -> str:
    tier_3 = summary["priority_tiers"]["tier_3"]
    if summary["total_files"] >= 800 or summary["llm_files"] >= 500:
        return "tier_1_only"
    if (
        tier_3["total_files"] >= TIER_3_FILES_RISK
        or tier_3["llm_files"] >= TIER_3_LLM_FILES_RISK
        or summary["total_files"] >= 300
        or summary["llm_files"] >= 200
    ):
        return "tier_1_and_2"
    return "all_tiers"


def _iter_files(src_root: Path, excluded_dir_names: set[str], skipped_dir_names: set[str]):
    for dirpath, dirnames, filenames in os.walk(src_root):
        _filter_dirnames(dirnames, excluded_dir_names, skipped_dir_names)
        current_dir = Path(dirpath)
        for filename in sorted(filenames):
            file_path = current_dir / filename
            if file_path.is_file():
                yield file_path


def _iter_directories(src_root: Path, excluded_dir_names: set[str], skipped_dir_names: set[str]):
    yield src_root
    for dirpath, dirnames, _filenames in os.walk(src_root):
        _filter_dirnames(dirnames, excluded_dir_names, skipped_dir_names)
        current_dir = Path(dirpath)
        for dirname in sorted(dirnames):
            yield current_dir / dirname


def _filter_dirnames(dirnames: list[str], excluded_dir_names: set[str], skipped_dir_names: set[str]) -> None:
    kept = []
    for dirname in dirnames:
        if dirname.lower() in excluded_dir_names:
            skipped_dir_names.add(dirname)
        else:
            kept.append(dirname)
    dirnames[:] = kept
    dirnames.sort()
