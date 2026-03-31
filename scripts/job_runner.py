#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

import job_state
import planning
import verify_outputs


def start_job(
    src_root: str | Path,
    dst_root: str | Path | None = None,
    exclude_dirs: list[str] | None = None,
    replace_existing: bool = True,
    batch_size: int = job_state.DEFAULT_BATCH_SIZE,
) -> dict:
    manifest = planning.prepare_project_copy(
        src_root,
        dst_root=dst_root,
        replace_existing=replace_existing,
        exclude_dirs=exclude_dirs,
        batch_size=batch_size,
    )

    job_id = _new_job_id()
    job_dir = job_state.ensure_output_dir(manifest["dst_root"])
    _remove_stale_internal_outputs(job_dir)

    manifest_path = job_dir / job_state.MANIFEST_FILE
    job_state.atomic_write_json(manifest_path, manifest)

    originals_lock = job_state.build_originals_lock(manifest)
    originals_lock_path = job_dir / job_state.ORIGINALS_LOCK_FILE
    job_state.atomic_write_json(originals_lock_path, originals_lock)

    progress = job_state.build_progress(
        manifest,
        job_id=job_id,
        batch_size=batch_size,
        refresh_every_batches=job_state.DEFAULT_REFRESH_EVERY_BATCHES,
        refresh_every_files=batch_size,
    )
    next_batch = job_state.checkout_next_batch(progress)
    progress_path = job_dir / job_state.PROGRESS_FILE
    job_state.atomic_write_json(progress_path, progress)
    guidance = _build_start_guidance(manifest["summary"], progress["summary"])

    job_info = {
        "job_id": job_id,
        "job_dir": str(job_dir),
        "manifest_path": str(manifest_path),
        "progress_path": str(progress_path),
        "originals_lock_path": str(originals_lock_path),
        "src_root": manifest["src_root"],
        "dst_root": manifest["dst_root"],
        "summary": manifest["summary"],
        "preflight_summary": manifest["summary"].get("preflight_summary", {}),
        "progress_summary": progress["summary"],
        "context_usage_hint": progress["summary"].get("context_usage_hint", {}),
        "project_profile": manifest["summary"].get("project_profile", {}),
        "project_profile_summary": manifest["summary"].get("project_profile", {}).get("user_summary"),
        "user_message": guidance["user_message"],
        "internal_reason": guidance["internal_reason"],
        "operator_advice": guidance["user_message"],
        "batch_size": batch_size,
        "selected_priority_scope": progress["summary"].get("selected_priority_scope"),
        "created_at": _utc_timestamp(),
    }
    job_state.atomic_write_json(job_dir / job_state.JOB_INFO_FILE, job_info)

    return {
        **job_info,
        "next_batch": next_batch,
    }


def resume_job(job_ref: str | Path, retry_failed: bool = False) -> dict:
    job_dir = job_state.resolve_job_dir(job_ref)
    progress = job_state.load_json(job_dir / job_state.PROGRESS_FILE)
    batch = job_state.checkout_next_batch(progress, retry_failed=retry_failed)
    job_state.atomic_write_json(job_dir / job_state.PROGRESS_FILE, progress)
    job_info = job_state.load_json_if_exists(job_dir / job_state.JOB_INFO_FILE)
    manifest = job_state.load_json(job_dir / job_state.MANIFEST_FILE)
    guidance = _build_runtime_guidance(manifest.get("summary", {}), progress["summary"])
    decision_evidence = job_state.build_decision_evidence(progress, batch)

    return {
        "job_id": job_info.get("job_id", job_dir.name),
        "job_dir": str(job_dir),
        "progress_path": str(job_dir / job_state.PROGRESS_FILE),
        "summary": progress["summary"],
        "context_usage_hint": progress["summary"].get("context_usage_hint", {}),
        "watchdog": progress["summary"].get("watchdog", {}),
        "batch_selection_reason_code": decision_evidence.get("batch_selection_reason_code"),
        "batch_selection_reason": decision_evidence.get("batch_selection_reason"),
        "decision_evidence": decision_evidence,
        "user_message": guidance["user_message"],
        "internal_reason": guidance["internal_reason"],
        "operator_advice": guidance["user_message"],
        "next_batch": batch,
    }


def decide_job_scope(job_ref: str | Path, decision: str) -> dict:
    job_dir = job_state.resolve_job_dir(job_ref)
    progress = job_state.load_json(job_dir / job_state.PROGRESS_FILE)
    manifest = job_state.load_json(job_dir / job_state.MANIFEST_FILE)
    result = job_state.set_scope_decision(progress, decision)
    job_state.atomic_write_json(job_dir / job_state.PROGRESS_FILE, progress)
    job_info = job_state.load_json_if_exists(job_dir / job_state.JOB_INFO_FILE)
    guidance = _build_scope_guidance(manifest.get("summary", {}), progress["summary"], decision)

    return {
        "job_id": job_info.get("job_id", job_dir.name),
        "job_dir": str(job_dir),
        "progress_path": str(job_dir / job_state.PROGRESS_FILE),
        "user_message": guidance["user_message"],
        "internal_reason": guidance["internal_reason"],
        "operator_advice": guidance["user_message"],
        **result,
    }


def get_job_status(job_ref: str | Path) -> dict:
    job_dir = job_state.resolve_job_dir(job_ref)
    job_info = job_state.load_json_if_exists(job_dir / job_state.JOB_INFO_FILE)
    progress = job_state.load_json(job_dir / job_state.PROGRESS_FILE)
    manifest = job_state.load_json(job_dir / job_state.MANIFEST_FILE)
    if progress.get("active_batch"):
        job_state.run_watchdog_check(progress, source="status")
        job_state.atomic_write_json(job_dir / job_state.PROGRESS_FILE, progress)

    active_batch = progress.get("active_batch")
    if active_batch:
        current_batch = {
            "batch_index": active_batch.get("batch_index"),
            "checkpoint_id": active_batch.get("checkpoint_id"),
            "file_ids": active_batch.get("file_ids", []),
        }
    else:
        current_batch = None
    guidance = _build_runtime_guidance(manifest.get("summary", {}), progress["summary"])
    decision_evidence = job_state.build_decision_evidence(progress)

    return {
        "job_id": job_info.get("job_id", job_dir.name),
        "job_dir": str(job_dir),
        "src_root": manifest["src_root"],
        "dst_root": manifest["dst_root"],
        "summary": progress["summary"],
        "preflight_summary": manifest.get("summary", {}).get("preflight_summary", {}),
        "context_usage_hint": progress["summary"].get("context_usage_hint", {}),
        "watchdog": progress["summary"].get("watchdog", {}),
        "project_profile": manifest.get("summary", {}).get("project_profile", {}),
        "project_profile_summary": manifest.get("summary", {}).get("project_profile", {}).get("user_summary"),
        "user_message": guidance["user_message"],
        "internal_reason": guidance["internal_reason"],
        "operator_advice": guidance["user_message"],
        "next_action_reason_code": decision_evidence.get("next_action_reason_code"),
        "next_action_reason": decision_evidence.get("next_action_reason"),
        "decision_evidence": decision_evidence,
        "current_batch": current_batch,
        "next_pending_batch_index": progress["summary"].get("next_pending_batch_index"),
        "refresh_checkpoint_count": progress["summary"].get("refresh_checkpoint_count", 0),
        "selected_priority_scope": progress["summary"].get("selected_priority_scope"),
        "next_locked_tier": progress["summary"].get("next_locked_tier"),
        "awaiting_scope_decision": progress["summary"].get("awaiting_scope_decision", False),
        "next_action": progress["summary"].get("next_action"),
        "remaining_priority_tiers": progress["summary"].get("remaining_priority_tiers", {}),
    }


def mark_job_file(
    job_ref: str | Path,
    file_id: str,
    status: str,
    error: str | None = None,
) -> dict:
    job_dir = job_state.resolve_job_dir(job_ref)
    progress = job_state.load_json(job_dir / job_state.PROGRESS_FILE)
    updated_item = job_state.update_item_status(progress, file_id=file_id, status=status, error=error)
    job_state.atomic_write_json(job_dir / job_state.PROGRESS_FILE, progress)

    return {
        "job_dir": str(job_dir),
        "updated_item": updated_item,
        "summary": progress["summary"],
    }


def heartbeat_job_files(
    job_ref: str | Path,
    worker_id: str,
    file_ids: list[str],
    note: str | None = None,
) -> dict:
    job_dir = job_state.resolve_job_dir(job_ref)
    progress = job_state.load_json(job_dir / job_state.PROGRESS_FILE)
    heartbeat = job_state.heartbeat_items(progress, file_ids=file_ids, worker_id=worker_id, note=note)
    job_state.atomic_write_json(job_dir / job_state.PROGRESS_FILE, progress)
    return {
        "job_dir": str(job_dir),
        "worker_id": worker_id,
        "file_ids": heartbeat["file_ids"],
        "checked_at": heartbeat["checked_at"],
        "summary": progress["summary"],
        "watchdog": progress["summary"].get("watchdog", {}),
    }


def watchdog_job(job_ref: str | Path) -> dict:
    job_dir = job_state.resolve_job_dir(job_ref)
    job_info = job_state.load_json_if_exists(job_dir / job_state.JOB_INFO_FILE)
    progress = job_state.load_json(job_dir / job_state.PROGRESS_FILE)
    manifest = job_state.load_json(job_dir / job_state.MANIFEST_FILE)
    watchdog = job_state.run_watchdog_check(progress, source="manual")
    job_state.atomic_write_json(job_dir / job_state.PROGRESS_FILE, progress)
    guidance = _build_runtime_guidance(manifest.get("summary", {}), progress["summary"])
    decision_evidence = job_state.build_decision_evidence(progress)
    return {
        "job_id": job_info.get("job_id", job_dir.name),
        "job_dir": str(job_dir),
        "summary": progress["summary"],
        "watchdog": watchdog,
        "context_usage_hint": progress["summary"].get("context_usage_hint", {}),
        "user_message": guidance["user_message"],
        "internal_reason": guidance["internal_reason"],
        "operator_advice": guidance["user_message"],
        "decision_evidence": decision_evidence,
    }


def build_job_report(job_ref: str | Path) -> dict:
    job_dir = job_state.resolve_job_dir(job_ref)
    manifest = job_state.load_json(job_dir / job_state.MANIFEST_FILE)
    progress = job_state.load_json(job_dir / job_state.PROGRESS_FILE)
    originals_lock = job_state.load_json(job_dir / job_state.ORIGINALS_LOCK_FILE)
    job_info = job_state.load_json_if_exists(job_dir / job_state.JOB_INFO_FILE)

    verify_report = verify_outputs.build_report(
        manifest,
        progress=progress,
        originals_lock=originals_lock,
    )
    progress_summary = verify_report["progress_summary"]
    unfinished_in_scope = (
        progress_summary.get("pending_llm_files_in_scope", 0) > 0
        or progress_summary.get("in_progress_llm_files_in_scope", 0) > 0
        or progress_summary.get("failed_llm_files_in_scope", 0) > 0
    )
    status = "ok"
    if (
        verify_report["missing_original_copies"]
        or verify_report["modified_source_files"]
        or verify_report["modified_original_copies"]
        or verify_report["missing_cn_files"]
        or verify_report["source_root_pollution"]
        or unfinished_in_scope
    ):
        status = "incomplete"
    elif progress_summary.get("awaiting_scope_decision", False):
        status = "awaiting_scope_decision"
    elif progress_summary.get("skipped_priority_tiers"):
        status = "complete_with_skipped_tiers"
    guidance = _build_runtime_guidance(
        manifest.get("summary", {}),
        progress_summary,
        report_mode=True,
        report_status=status,
    )

    final_report = {
        "job_id": job_info.get("job_id", job_dir.name),
        "job_dir": str(job_dir),
        "src_root": manifest["src_root"],
        "dst_root": manifest["dst_root"],
        "summary": manifest.get("summary", {}),
        "preflight_summary": manifest.get("summary", {}).get("preflight_summary", {}),
        "context_usage_hint": progress_summary.get("context_usage_hint", {}),
        "watchdog": progress_summary.get("watchdog", {}),
        "project_profile": manifest.get("summary", {}).get("project_profile", {}),
        "project_profile_summary": manifest.get("summary", {}).get("project_profile", {}).get("user_summary"),
        "user_message": guidance["user_message"],
        "internal_reason": guidance["internal_reason"],
        "operator_advice": guidance["user_message"],
        "progress_summary": progress_summary,
        "batch_verifications": verify_report.get("batch_verifications", []),
        "active_batch_verification": verify_report.get("active_batch_verification", {}),
        "generated": verify_report["generated"],
        "missing_original_copies": verify_report["missing_original_copies"],
        "missing_cn_files": verify_report["missing_cn_files"],
        "modified_source_files": verify_report["modified_source_files"],
        "modified_original_copies": verify_report["modified_original_copies"],
        "source_integrity_ok": verify_report["source_integrity_ok"],
        "copied_original_integrity_ok": verify_report["copied_original_integrity_ok"],
        "source_root_pollution": verify_report["source_root_pollution"],
        "source_root_pollution_detected": verify_report["source_root_pollution_detected"],
        "selected_priority_scope": progress_summary.get("selected_priority_scope"),
        "selected_priority_scope_label": progress_summary.get("selected_priority_scope_label"),
        "scope_finalized": progress_summary.get("scope_finalized", False),
        "skipped_priority_tiers": progress_summary.get("skipped_priority_tiers", []),
        "awaiting_scope_decision": progress_summary.get("awaiting_scope_decision", False),
        "next_locked_tier": progress_summary.get("next_locked_tier"),
        "next_action": progress_summary.get("next_action"),
        "remaining_priority_tiers": progress_summary.get("remaining_priority_tiers", {}),
        "status": status,
        "reported_at": _utc_timestamp(),
    }

    job_state.atomic_write_json(job_dir / job_state.VERIFY_REPORT_FILE, final_report)
    job_state.atomic_write_text(job_dir / job_state.TEXT_REPORT_FILE, _format_text_report(final_report))
    return final_report


def _format_text_report(report: dict) -> str:
    summary = report["summary"]
    progress_summary = report["progress_summary"]
    generated = report["generated"]
    priority_tiers = summary.get("priority_tiers", {})
    project_profile = summary.get("project_profile", {})
    root_mode = (
        "严格使用用户给定路径"
        if summary.get("root_interpretation", "exact-user-path") == "exact-user-path"
        else summary.get("root_interpretation")
    )
    wrapper_detected = "是" if summary.get("single_child_wrapper_detected", False) else "否"
    status_text = {
        "ok": "完成",
        "incomplete": "未完成",
        "awaiting_scope_decision": "等待用户决定下一档",
        "complete_with_skipped_tiers": "已完成（含用户跳过档位）",
    }.get(report["status"], report["status"])
    source_integrity = "通过" if report["source_integrity_ok"] else "失败"
    copied_integrity = "通过" if report["copied_original_integrity_ok"] else "失败"
    tier_decision = "是" if summary.get("priority_tier_decision_recommended") else "否"
    recommended_scope = {
        "tier_1_only": "仅 1 档",
        "tier_1_and_2": "1 档 + 2 档",
        "all_tiers": "全部 1 + 2 + 3 档",
    }.get(summary.get("priority_tier_recommended_scope"), summary.get("priority_tier_recommended_scope"))
    selected_scope = report.get("selected_priority_scope_label") or progress_summary.get("selected_priority_scope_label")
    skipped_tiers = report.get("skipped_priority_tiers", [])
    skipped_tiers_text = "无" if not skipped_tiers else "、".join(f"{tier} 档" for tier in skipped_tiers)
    awaiting_text = "是" if report.get("awaiting_scope_decision", False) else "否"
    scope_finalized_text = "是" if report.get("scope_finalized", False) else "否"
    next_locked_tier_text = (
        f"{report['next_locked_tier']} 档" if report.get("next_locked_tier") is not None else "无"
    )
    next_action_text = _format_next_action(report.get("next_action"))
    remaining_priority_tiers = report.get("remaining_priority_tiers", {})
    project_signals = "、".join(project_profile.get("signals", [])) or "无"
    project_notes = "；".join(project_profile.get("analysis_notes", [])) or "无"
    fixed_dirs = "、".join(project_profile.get("fixed_tier_1_dirs", [])) or "无"
    dynamic_dirs = "、".join(project_profile.get("dynamic_tier_1_dirs", [])) or "无"
    dynamic_root_files = "、".join(project_profile.get("dynamic_tier_1_root_files", [])) or "无"
    profile_summary_text = report.get("project_profile_summary") or project_profile.get("user_summary", "无")
    user_message = report.get("user_message", "无")
    internal_reason = report.get("internal_reason", "无")

    lines = [
        "=== 项目翻译结果报告 ===",
        f"任务 ID：{report['job_id']}",
        f"源目录：{report['src_root']}",
        f"目标目录：{report['dst_root']}",
        f"状态：{status_text}",
        "",
        "根目录处理：",
        f"- 根目录解释：{root_mode}",
        f"- 顶层目录数：{summary.get('top_level_dirs', 0)}",
        f"- 顶层文件数：{summary.get('top_level_files', 0)}",
        f"- 是否检测到单子目录包装壳：{wrapper_detected}",
        "",
        "项目画像：",
        f"- 主类型：{project_profile.get('label', '通用项目')}",
        f"- 识别信号：{project_signals}",
        f"- 固定进入 1 档的核心目录：{fixed_dirs}",
        f"- 动态提升到 1 档的目录：{dynamic_dirs}",
        f"- 动态提升到 1 档的根文件：{dynamic_root_files}",
        f"- 画像判断：{project_notes}",
        f"- 用户可读摘要：{profile_summary_text}",
        f"- 对用户提示：{user_message}",
        f"- 内部判断：{internal_reason}",
        "",
        "工作量摘要：",
        f"- 文件总数：{summary.get('total_files', 0)}",
        f"- 文档文件数：{summary.get('document_files', 0)}",
        f"- 代码文件数：{summary.get('code_files', 0)}",
        f"- 其他文件数：{summary.get('other_files', 0)}",
        f"- LLM 文件数：{summary.get('llm_files', 0)}",
        f"- LLM 批次数：{summary.get('llm_batch_count', 0)}",
        f"- 预计输入 token：{summary.get('estimated_input_tokens', 0)}",
        f"- 预计总 token 范围：{summary.get('estimated_tokens_low', 0)}-{summary.get('estimated_tokens_high', 0)}",
        "",
        "优先级分档：",
        _format_tier_line("1 档 核心理解层", priority_tiers.get("tier_1")),
        _format_tier_line("2 档 重要扩展层", priority_tiers.get("tier_2")),
        _format_tier_line("3 档 外围噪声层", priority_tiers.get("tier_3")),
        f"- 是否建议先按档位向用户确认：{tier_decision}",
        f"- 推荐处理范围：{recommended_scope}",
        "",
        "范围闸门：",
        f"- 当前选择范围：{selected_scope}",
        f"- 是否已最终定档：{scope_finalized_text}",
        f"- 是否等待用户决定下一档：{awaiting_text}",
        f"- 已跳过档位：{skipped_tiers_text}",
        f"- 下一待解锁档位：{next_locked_tier_text}",
        f"- 下一建议动作：{next_action_text}",
        _format_remaining_tier_line("1 档剩余", remaining_priority_tiers.get("tier_1")),
        _format_remaining_tier_line("2 档剩余", remaining_priority_tiers.get("tier_2")),
        _format_remaining_tier_line("3 档剩余", remaining_priority_tiers.get("tier_3")),
        "",
        "进度摘要：",
        f"- 批次大小：{progress_summary.get('batch_size', 0)}",
        f"- 待处理 LLM 文件：{progress_summary.get('pending_llm_files', 0)}",
        f"- 进行中 LLM 文件：{progress_summary.get('in_progress_llm_files', 0)}",
        f"- 已完成 LLM 文件：{progress_summary.get('completed_llm_files', 0)}",
        f"- 失败 LLM 文件：{progress_summary.get('failed_llm_files', 0)}",
        f"- 当前范围内待处理 LLM 文件：{progress_summary.get('pending_llm_files_in_scope', 0)}",
        f"- 当前范围内失败 LLM 文件：{progress_summary.get('failed_llm_files_in_scope', 0)}",
        f"- 刷新检查点数：{progress_summary.get('refresh_checkpoint_count', 0)}",
        f"- 子代理巡检状态：{progress_summary.get('watchdog_status', 'unknown')}",
        f"- 需要介入的卡住文件数：{progress_summary.get('stale_in_progress_file_count', 0)}",
        f"- 需要介入的子代理数：{progress_summary.get('stale_worker_count', 0)}",
        f"- watchdog 建议动作数：{progress_summary.get('recommended_watchdog_action_count', 0)}",
        "",
        "产出结果：",
        f"- 已确认存在的原始复制文件：{generated.get('original_copied_files_present', 0)}",
        f"- 文档 -CN 文件数：{generated.get('document_cn_files', 0)}",
        f"- 代码 -CN 文件数：{generated.get('code_cn_files', 0)}",
        f"- 缺失的原始复制文件数：{len(report.get('missing_original_copies', []))}",
        f"- 缺失的 -CN 文件数：{len(report.get('missing_cn_files', []))}",
        "",
        "原文件保护：",
        f"- 源目录完整性：{source_integrity}",
        f"- 复制后原始文件完整性：{copied_integrity}",
        f"- 被修改的源文件数：{len(report.get('modified_source_files', []))}",
        f"- 被修改的原始复制文件数：{len(report.get('modified_original_copies', []))}",
        f"- 源目录污染项数：{len(report.get('source_root_pollution', []))}",
    ]

    recommended_actions = progress_summary.get("recommended_watchdog_actions", [])
    if recommended_actions:
        lines.extend(["", "watchdog 建议动作："])
        for action in recommended_actions:
            worker_text = action.get("worker_id") or "未绑定子代理"
            file_ids = "、".join(action.get("file_ids", [])) or "无"
            reason_summary = action.get("reason_summary", "无")
            instruction = action.get("instruction", "无")
            lines.append(
                f"- [{action.get('action', 'unknown')}] worker={worker_text} files={file_ids}；异常：{reason_summary}；建议：{instruction}"
            )

    if report.get("missing_cn_files"):
        lines.extend(["", "缺失的 -CN 文件："])
        for item in report["missing_cn_files"]:
            lines.append(f"- {item['rel_path']} -> {item['expected_cn_file']}")

    if report.get("missing_original_copies"):
        lines.extend(["", "缺失的原始复制文件："])
        for item in report["missing_original_copies"]:
            lines.append(f"- {item['rel_path']} -> {item['expected_original_file']}")

    if report.get("modified_source_files"):
        lines.extend(["", "被修改的源文件："])
        for item in report["modified_source_files"]:
            lines.append(f"- {item['rel_path']} ({item['reason']})")

    if report.get("modified_original_copies"):
        lines.extend(["", "被修改的原始复制文件："])
        for item in report["modified_original_copies"]:
            lines.append(f"- {item['rel_path']} ({item['reason']})")

    if report.get("source_root_pollution"):
        lines.extend(["", "源目录污染："])
        for item in report["source_root_pollution"]:
            lines.append(f"- {item['rel_path']} ({item['reason']})")

    return "\n".join(lines) + "\n"


def _format_tier_line(label: str, tier_summary: dict | None) -> str:
    if not tier_summary:
        return f"- {label}：0 个文件"
    return (
        f"- {label}：{tier_summary.get('total_files', 0)} 个文件 "
        f"(文档 {tier_summary.get('document_files', 0)} / "
        f"代码 {tier_summary.get('code_files', 0)} / "
        f"其他 {tier_summary.get('other_files', 0)})"
    )


def _format_remaining_tier_line(label: str, tier_summary: dict | None) -> str:
    if not tier_summary:
        return f"- {label}：0"
    state = "已跳过" if tier_summary.get("skipped") else "已开放" if tier_summary.get("allowed") else "锁定"
    return (
        f"- {label}：{tier_summary.get('remaining_files', 0)} "
        f"(文档 {tier_summary.get('remaining_document_files', 0)} / "
        f"代码 {tier_summary.get('remaining_code_files', 0)} / "
        f"其他 {tier_summary.get('remaining_other_files', 0)})，状态：{state}"
    )


def _format_next_action(next_action: str | None) -> str:
    mapping = {
        "finish_current_batch": "先完成当前批次",
        "check_subagent_heartbeat": "先做一次子代理心跳巡检",
        "investigate_stuck_subagents": "先介入卡住的子代理和文件",
        "resume": "继续领取下一批",
        "report": "生成最终报告",
        "ask_user_about_tier_2": "向用户确认是否进入 2 档",
        "ask_user_about_tier_3": "向用户确认是否进入 3 档",
        "await_scope_decision": "等待用户做档位决定",
    }
    return mapping.get(next_action, next_action or "无")


def _build_start_guidance(summary: dict, progress_summary: dict) -> dict:
    profile = summary.get("project_profile", {})
    profile_summary = profile.get("user_summary", "已完成项目画像分析。")
    tier_1 = summary.get("priority_tiers", {}).get("tier_1", {})
    tier_1_count = tier_1.get("total_files", 0)
    tier_1_doc = tier_1.get("document_files", 0)
    tier_1_code = tier_1.get("code_files", 0)
    tier_1_other = tier_1.get("other_files", 0)
    focus_paths = "、".join(profile.get("first_pass_focus_paths", [])[:3]) or "无"
    recommended_scope = summary.get("priority_tier_recommended_scope")

    if recommended_scope == "tier_1_only":
        scope_advice = (
            f"建议先只处理 1 档，共 {tier_1_count} 个文件"
            f"（文档 {tier_1_doc} / 代码 {tier_1_code} / 其他 {tier_1_other}），"
            "处理完后再确认是否进入 2 档。"
        )
    elif recommended_scope == "tier_1_and_2":
        scope_advice = (
            f"建议先处理 1 档和 2 档，但当前先从 1 档开始建立项目理解；"
            f"1 档共有 {tier_1_count} 个文件（文档 {tier_1_doc} / 代码 {tier_1_code} / 其他 {tier_1_other}）。"
        )
    else:
        scope_advice = (
            f"当前项目可以直接处理全部档位，但仍建议先从 1 档入手；"
            f"1 档共有 {tier_1_count} 个文件（文档 {tier_1_doc} / 代码 {tier_1_code} / 其他 {tier_1_other}）。"
        )

    next_action = _format_next_action(progress_summary.get("next_action"))
    user_message = f"{scope_advice} 首轮优先关注：{focus_paths}。"
    internal_reason = f"{profile_summary} {scope_advice} 当前下一步：{next_action}。"
    return {
        "user_message": user_message,
        "internal_reason": internal_reason,
    }


def _build_runtime_guidance(
    summary: dict,
    progress_summary: dict,
    report_mode: bool = False,
    report_status: str | None = None,
) -> dict:
    profile = summary.get("project_profile", {})
    profile_summary = profile.get("user_summary", "已完成项目画像分析。")
    next_action = progress_summary.get("next_action")
    pending_in_scope = progress_summary.get("pending_llm_files_in_scope", 0)
    failed_in_scope = progress_summary.get("failed_llm_files_in_scope", 0)
    next_locked_tier = progress_summary.get("next_locked_tier")

    if report_mode and report_status == "complete_with_skipped_tiers":
        skipped = progress_summary.get("skipped_priority_tiers", [])
        skipped_text = "、".join(f"{tier} 档" for tier in skipped) or "无"
        return {
            "user_message": f"当前范围已完成，已按你的决定跳过：{skipped_text}。",
            "internal_reason": f"{profile_summary} 已按用户决策完成当前范围，跳过档位：{skipped_text}。",
        }

    if report_mode and report_status == "ok":
        return {
            "user_message": "当前允许范围内的文件已经处理完成，可以直接查看结果或结束本轮任务。",
            "internal_reason": f"{profile_summary} 当前允许范围内的文件已经处理完成，可以直接查看结果或结束本轮任务。",
        }

    if next_action == "fix_current_batch_outputs":
        verification = progress_summary.get("active_batch_verification") or {}
        missing_cn = len(verification.get("missing_cn_files", []))
        missing_originals = len(verification.get("missing_original_copies", []))
        return {
            "user_message": f"当前批次还有缺失输出，先补齐这批再继续。缺失 CN 文件 {missing_cn} 个，缺失原始复制文件 {missing_originals} 个。",
            "internal_reason": (
                f"{profile_summary} current batch is blocked by batch verification; "
                f"missing_cn_files={missing_cn}, missing_original_copies={missing_originals}."
            ),
        }
    if next_action == "check_subagent_heartbeat":
        return {
            "user_message": "当前批次仍在执行，先做一次 watchdog 巡检，确认子代理心跳仍然正常。",
            "internal_reason": f"{profile_summary} 当前批次仍在进行中，已经达到巡检时间点，应先检查子代理心跳。",
        }
    if next_action == "investigate_stuck_subagents":
        stale_files = progress_summary.get("stale_in_progress_file_count", 0)
        stale_workers = progress_summary.get("stale_worker_count", 0)
        recommended_actions = progress_summary.get("recommended_watchdog_actions", [])
        action_hint = ""
        if recommended_actions:
            first_action = recommended_actions[0]
            worker_text = first_action.get("worker_id") or "未绑定子代理"
            action_hint = f" 先按 watchdog 建议动作清单介入，首条建议针对：{worker_text}。"
        return {
            "user_message": (
                f"当前批次疑似有子代理卡住，先介入处理。卡住文件 {stale_files} 个，"
                f"涉及子代理 {stale_workers} 个。{action_hint}".strip()
            ),
            "internal_reason": (
                f"{profile_summary} watchdog 检测到卡住的子代理或文件，必须先处理卡住项，"
                f"并按 recommended_actions 执行介入或重分配，再继续本批次。"
            ),
        }
    if next_action == "finish_current_batch":
        return {
            "user_message": "先完成当前这一批文件，完成后我再判断是否继续放开下一档。",
            "internal_reason": f"{profile_summary} 当前先完成这一批文件，完成后再根据状态决定是否继续放开下一档。",
        }
    if next_action == "resume":
        return {
            "user_message": f"当前范围内还有 {pending_in_scope} 个待处理文件，可以直接继续下一批。",
            "internal_reason": f"{profile_summary} 当前范围内还有 {pending_in_scope} 个待处理 LLM 文件，可以直接继续下一批。",
        }
    if next_action == "ask_user_about_tier_2":
        return {
            "user_message": "1 档已处理完，是否继续进入 2 档？",
            "internal_reason": f"{profile_summary} 1 档已处理完，请先向用户确认是否进入 2 档。",
        }
    if next_action == "ask_user_about_tier_3":
        return {
            "user_message": "1+2 档已处理完，是否继续进入 3 档？",
            "internal_reason": f"{profile_summary} 1+2 档已处理完，请先向用户确认是否进入 3 档。",
        }
    if next_action == "await_scope_decision":
        locked_text = f"{next_locked_tier} 档" if next_locked_tier is not None else "下一档"
        return {
            "user_message": f"当前正在等待你决定是否放开{locked_text}。",
            "internal_reason": f"{profile_summary} 当前正在等待用户决定是否放开{locked_text}。",
        }
    if next_action == "report":
        return {
            "user_message": "当前范围已处理完，可以生成最终报告。",
            "internal_reason": f"{profile_summary} 当前范围已处理完，可以生成最终报告。",
        }

    if failed_in_scope > 0:
        return {
            "user_message": f"当前范围内有 {failed_in_scope} 个失败文件，建议先处理失败项再继续。",
            "internal_reason": f"{profile_summary} 当前范围内有 {failed_in_scope} 个失败文件，建议先处理失败项再继续。",
        }

    return {
        "user_message": "当前可继续按状态机推进下一步。",
        "internal_reason": f"{profile_summary} 当前可继续按状态机推进下一步。",
    }


def _build_scope_guidance(summary: dict, progress_summary: dict, decision: str) -> dict:
    profile = summary.get("project_profile", {})
    profile_summary = profile.get("user_summary", "已完成项目画像分析。")
    next_action = progress_summary.get("next_action")
    next_action_text = _format_next_action(next_action)
    next_locked_tier = progress_summary.get("next_locked_tier")
    next_locked_tier_text = f"{next_locked_tier} 档" if next_locked_tier is not None else "无"

    if decision == job_state.SCOPE_TIER_1_ONLY:
        user_message = "已确认当前只处理 1 档。1 档处理完后，我会停下来再问你是否进入 2 档。"
    elif decision == job_state.SCOPE_TIER_1_AND_2:
        user_message = "已放开 1+2 档，可以继续处理下一批；2 档处理完后我会再问你是否进入 3 档。"
    elif decision == job_state.SCOPE_ALL_TIERS:
        user_message = "已放开全部档位，可以继续处理剩余文件。"
    elif decision == job_state.SCOPE_DECISION_SKIP_TIER_3:
        user_message = "已确认后续跳过 3 档，只处理 1+2 档。"
    else:
        user_message = "档位决策已更新。"

    internal_reason = (
        f"{profile_summary} 本次 scope 决策为 `{decision}`，"
        f"当前范围：{progress_summary.get('selected_priority_scope_label')}，"
        f"下一待解锁档位：{next_locked_tier_text}，"
        f"下一建议动作：{next_action_text}。"
    )
    return {
        "user_message": user_message,
        "internal_reason": internal_reason,
    }


def _remove_stale_internal_outputs(job_dir: Path) -> None:
    for filename in (
        job_state.JOB_INFO_FILE,
        job_state.MANIFEST_FILE,
        job_state.PROGRESS_FILE,
        job_state.ORIGINALS_LOCK_FILE,
        job_state.VERIFY_REPORT_FILE,
        job_state.TEXT_REPORT_FILE,
    ):
        path = job_dir / filename
        if path.exists():
            path.unlink()


def _reject_output_inside_source_root(output_path: Path, src_root: str | Path) -> None:
    src_path = Path(src_root).expanduser().resolve()
    try:
        output_path.relative_to(src_path)
    except ValueError:
        return
    raise ValueError(
        f"output path must not be inside source root: {output_path}. "
        "Write extra artifacts to the sibling A-CN/AAA-translate-output directory instead."
    )


def _new_job_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="以批次状态驱动项目翻译工作流。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser(
        "start",
        help="初始化作业、复制目录，并生成 manifest、progress、lock 三类内部文件。",
    )
    start_parser.add_argument("src_root", help="原项目根目录")
    start_parser.add_argument("--dst-root", help="可选目标目录，默认使用 A-CN")
    start_parser.add_argument("--exclude-dir", action="append", default=None, help="额外排除目录名，可重复传入")
    start_parser.add_argument("--keep-existing", action="store_true", help="保留已有目标目录，不先删除旧的 A-CN")

    status_parser = subparsers.add_parser(
        "status",
        help="读取 translate-progress.json，输出当前进度摘要。",
    )
    status_parser.add_argument("job_ref", help="A-CN 目录、AAA-translate-output 目录，或其内部文件路径")

    resume_parser = subparsers.add_parser(
        "resume",
        help="从 translate-progress.json 中选出下一批待处理文件。",
    )
    resume_parser.add_argument("job_ref", help="A-CN 目录、AAA-translate-output 目录，或其内部文件路径")
    resume_parser.add_argument("--retry-failed", action="store_true", help="没有 pending 文件时，允许重试 failed 文件")

    scope_parser = subparsers.add_parser(
        "scope",
        help="把用户选定的档位范围写入 translate-progress.json。",
    )
    scope_parser.add_argument("job_ref", help="A-CN 目录、AAA-translate-output 目录，或其内部文件路径")
    scope_parser.add_argument(
        "--decision",
        required=True,
        choices=job_state.SCOPE_DECISION_OPTIONS,
        help="档位范围决策：tier_1_only / tier_1_and_2 / all_tiers / skip_tier_3",
    )

    mark_parser = subparsers.add_parser(
        "mark",
        help="处理完单个文件后，更新 translate-progress.json 中的状态。",
    )
    mark_parser.add_argument("job_ref", help="A-CN 目录、AAA-translate-output 目录，或其内部文件路径")
    mark_parser.add_argument("file_id", help="translate-manifest.json 中的稳定 file_id")
    mark_parser.add_argument("--status", required=True, choices=["pending", "completed", "failed", "skipped"])
    mark_parser.add_argument("--error", help="失败原因，仅 status=failed 时使用")

    heartbeat_parser = subparsers.add_parser(
        "heartbeat",
        help="子代理周期性回写心跳，声明自己仍在正常执行。",
    )
    heartbeat_parser.add_argument("job_ref", help="A-CN 目录、AAA-translate-output 目录，或其内部文件路径")
    heartbeat_parser.add_argument("worker_id", help="子代理或 worker 的稳定标识")
    heartbeat_parser.add_argument("file_ids", nargs="+", help="当前由该子代理负责的 file_id，可一次传多个")
    heartbeat_parser.add_argument("--note", help="可选备注，例如当前处理阶段")

    watchdog_parser = subparsers.add_parser(
        "watchdog",
        help="对当前活跃批次执行一次心跳巡检，识别卡住的子代理和文件。",
    )
    watchdog_parser.add_argument("job_ref", help="A-CN 目录、AAA-translate-output 目录，或其内部文件路径")

    report_parser = subparsers.add_parser(
        "report",
        help="基于 manifest、progress、lock 生成最终校验报告。",
    )
    report_parser.add_argument("job_ref", help="A-CN 目录、AAA-translate-output 目录，或其内部文件路径")
    report_parser.add_argument("--output", help="额外导出 JSON 报告路径")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "start":
            payload = start_job(
                args.src_root,
                dst_root=args.dst_root,
                exclude_dirs=args.exclude_dir,
                replace_existing=not args.keep_existing,
            )
        elif args.command == "status":
            payload = get_job_status(args.job_ref)
        elif args.command == "resume":
            payload = resume_job(args.job_ref, retry_failed=args.retry_failed)
        elif args.command == "scope":
            payload = decide_job_scope(args.job_ref, decision=args.decision)
        elif args.command == "mark":
            payload = mark_job_file(args.job_ref, args.file_id, status=args.status, error=args.error)
        elif args.command == "heartbeat":
            payload = heartbeat_job_files(args.job_ref, args.worker_id, args.file_ids, note=args.note)
        elif args.command == "watchdog":
            payload = watchdog_job(args.job_ref)
        else:
            payload = build_job_report(args.job_ref)
            if args.output:
                output_path = Path(args.output).expanduser().resolve()
                _reject_output_inside_source_root(output_path, payload["src_root"])
                job_state.atomic_write_json(output_path, payload)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    raise SystemExit(main())
