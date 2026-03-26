from __future__ import annotations

import json
import math
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

from classification import build_cn_filename, classify_file
from text_metrics import collect_text_metrics, estimate_input_tokens


CHUNK_CHAR_TARGET = 8000
SINGLE_FILE_RISK_CHARS = 120000
TOTAL_LLM_FILES_RISK = 200
TOTAL_ROUNDS_RISK = 120
TOTAL_INPUT_TOKENS_RISK = 400000

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
) -> dict:
    src_path = Path(src_root).expanduser().resolve()
    if not src_path.is_dir():
        raise ValueError(f"source directory does not exist: {src_path}")

    dst_path = build_destination_root(src_path, dst_root)
    excluded_dir_names = _build_excluded_dir_names(exclude_dirs)
    skipped_dir_names: set[str] = set()
    items = []
    summary = _empty_summary()
    _set_root_summary(summary, src_path)

    for file_path in _iter_files(src_path, excluded_dir_names, skipped_dir_names):
        rel_path = file_path.relative_to(src_path).as_posix()
        category = classify_file(file_path)
        metrics = collect_text_metrics(file_path)
        cn_rel_path = _build_cn_rel_path(rel_path, category)
        estimated_rounds = _estimate_rounds(metrics["estimated_chars"], category)
        estimated_input_tokens = estimate_input_tokens(metrics["estimated_chars"])
        token_low, token_high = _estimate_total_token_range(category, estimated_input_tokens)

        item = {
            "rel_path": rel_path,
            "src_file": str(file_path),
            "category": category,
            "copied_rel_path": rel_path,
            "cn_rel_path": cn_rel_path,
            "llm_action": _llm_action(category),
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
) -> dict:
    manifest = assess_project(src_root, dst_root=dst_root, exclude_dirs=exclude_dirs)
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
            copy_failures.append({"rel_path": item["rel_path"], "reason": str(exc)})

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
    output_file = Path(output_path).expanduser().resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _iter_files(src_root: Path):
    for path in sorted(src_root.rglob("*")):
        if path.is_file():
            yield path


def _iter_directories(src_root: Path):
    yield src_root
    for path in sorted(src_root.rglob("*")):
        if path.is_dir():
            yield path


def _build_cn_rel_path(rel_path: str, category: str) -> str | None:
    if category not in {"document", "code"}:
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
    if category not in {"document", "code"}:
        return 0
    return max(1, math.ceil(max(char_count, 1) / CHUNK_CHAR_TARGET))


def _estimate_total_token_range(category: str, input_tokens: int) -> tuple[int, int]:
    if category == "document":
        return math.ceil(input_tokens * 1.8), math.ceil(input_tokens * 3.2)
    if category == "code":
        return math.ceil(input_tokens * 1.6), math.ceil(input_tokens * 2.8)
    return 0, 0


def _empty_summary() -> dict:
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
        "estimated_text_chars": 0,
        "estimated_input_tokens": 0,
        "estimated_tokens_low": 0,
        "estimated_tokens_high": 0,
        "estimated_rounds": 0,
        "estimated_minutes_low": 0,
        "estimated_minutes_high": 0,
        "oversized_files": [],
        "undecodable_files": [],
        "risk_flags": [],
        "requires_confirmation": False,
        "largest_llm_file_chars": 0,
        "excluded_dirs": [],
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

    if item["category"] in {"document", "code"}:
        summary["llm_files"] += 1
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
                    "rel_path": item["rel_path"],
                    "estimated_chars": item["estimated_chars"],
                }
            )

        if item["read_error"]:
            summary["undecodable_files"].append(
                {
                    "rel_path": item["rel_path"],
                    "reason": item["read_error"],
                }
            )


def _finalize_summary(summary: dict) -> None:
    summary["estimated_minutes_low"] = math.ceil(summary["estimated_rounds"] * 0.4)
    summary["estimated_minutes_high"] = math.ceil(summary["estimated_rounds"] * 1.5)

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

    summary["risk_flags"] = risk_flags
    summary["requires_confirmation"] = bool(risk_flags)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _build_excluded_dir_names(exclude_dirs: list[str] | None) -> set[str]:
    merged = {name.lower() for name in DEFAULT_EXCLUDED_DIR_NAMES}
    if exclude_dirs:
        merged.update(name.lower() for name in exclude_dirs if name)
    return merged


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
