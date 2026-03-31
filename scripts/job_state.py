#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True


SKILL_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR_NAME = "AAA-translate-output"
JOB_INFO_FILE = "translate-job.json"
MANIFEST_FILE = "translate-manifest.json"
PROGRESS_FILE = "translate-progress.json"
ORIGINALS_LOCK_FILE = "translate-originals-lock.json"
VERIFY_REPORT_FILE = "translate-verify-report.json"
TEXT_REPORT_FILE = "translate-final-report.txt"

DEFAULT_BATCH_SIZE = 20
DEFAULT_REFRESH_EVERY_BATCHES = 1
DEFAULT_REFRESH_EVERY_FILES = 20
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 120
DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS = 600
DEFAULT_HEARTBEAT_CLAIM_GRACE_SECONDS = 240
DEFAULT_WATCHDOG_CHECK_INTERVAL_SECONDS = 180
COMPLETED_FILE_CONTEXT_POLICY = "metadata-only-unless-explicit-reopen"

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

FINAL_STATUSES = {STATUS_COMPLETED, STATUS_SKIPPED}
UNRESOLVED_STATUSES = {STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_FAILED}
LLM_CATEGORIES = {"document", "code"}

SCOPE_TIER_1_ONLY = "tier_1_only"
SCOPE_TIER_1_AND_2 = "tier_1_and_2"
SCOPE_ALL_TIERS = "all_tiers"
SCOPE_DECISION_SKIP_TIER_3 = "skip_tier_3"

PRIORITY_TIER_LABELS = {
    1: "核心理解层",
    2: "重要扩展层",
    3: "外围噪声层",
}

SCOPE_ALLOWED_TIERS = {
    SCOPE_TIER_1_ONLY: {1},
    SCOPE_TIER_1_AND_2: {1, 2},
    SCOPE_ALL_TIERS: {1, 2, 3},
}

SCOPE_DECISION_OPTIONS = [
    SCOPE_TIER_1_ONLY,
    SCOPE_TIER_1_AND_2,
    SCOPE_ALL_TIERS,
    SCOPE_DECISION_SKIP_TIER_3,
]

SCOPE_LABELS = {
    SCOPE_TIER_1_ONLY: "仅 1 档",
    SCOPE_TIER_1_AND_2: "1 档 + 2 档",
    SCOPE_ALL_TIERS: "全部 1 + 2 + 3 档",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_utc_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def atomic_write_json(path: str | Path, payload: dict) -> None:
    output_path = Path(path).expanduser().resolve()
    atomic_write_text(output_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def atomic_write_text(path: str | Path, content: str) -> None:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=output_path.parent,
        prefix=f"{output_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)

    os.replace(temp_path, output_path)


def load_json(path: str | Path) -> dict:
    json_path = Path(path).expanduser().resolve()
    return json.loads(json_path.read_text(encoding="utf-8"))


def load_json_if_exists(path: str | Path) -> dict:
    json_path = Path(path).expanduser().resolve()
    if not json_path.is_file():
        return {}
    return load_json(json_path)


def ensure_output_dir(dst_root: str | Path) -> Path:
    output_dir = Path(dst_root).expanduser().resolve() / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def resolve_job_dir(job_ref: str | Path) -> Path:
    candidate = Path(job_ref).expanduser()
    if not candidate.exists():
        raise FileNotFoundError(
            f"job output not found: {job_ref}. "
            f"Pass the A-CN directory or its {OUTPUT_DIR_NAME} child directory."
        )

    resolved = candidate.resolve()
    if resolved.is_file():
        resolved = resolved.parent

    if resolved.is_dir() and resolved.name == OUTPUT_DIR_NAME:
        return resolved

    output_dir = resolved / OUTPUT_DIR_NAME
    if output_dir.is_dir():
        return output_dir

    raise FileNotFoundError(
        f"{job_ref} is not an A-CN directory and does not contain {OUTPUT_DIR_NAME}."
    )


def build_progress(
    manifest: dict,
    job_id: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    refresh_every_batches: int = DEFAULT_REFRESH_EVERY_BATCHES,
    refresh_every_files: int = DEFAULT_REFRESH_EVERY_FILES,
) -> dict:
    created_at = utc_timestamp()
    items = []

    for manifest_item in manifest.get("items", []):
        status = _initial_status(manifest_item)
        last_error = manifest_item.get("copy_failure") if status == STATUS_FAILED else None
        completed_at = created_at if status in {STATUS_SKIPPED, STATUS_FAILED} else None

        items.append(
            {
                "file_id": manifest_item["file_id"],
                "rel_path": manifest_item["rel_path"],
                "category": manifest_item["category"],
                "llm_action": manifest_item.get("llm_action"),
                "copied_file": manifest_item.get("copied_file"),
                "cn_file": manifest_item.get("cn_file"),
                "batch_index": manifest_item.get("batch_index"),
                "priority_tier": manifest_item.get("priority_tier", 2),
                "priority_tier_label": manifest_item.get(
                    "priority_tier_label",
                    PRIORITY_TIER_LABELS.get(manifest_item.get("priority_tier", 2), "重要扩展层"),
                ),
                "status": status,
                "attempt_count": 0,
                "last_error": last_error,
                "started_at": None,
                "completed_at": completed_at,
                "processed_by_batch": None,
                "refresh_checkpoint_id": None,
                "assigned_worker_id": None,
                "claimed_at": None,
                "last_heartbeat_at": None,
                "heartbeat_count": 0,
                "watchdog_state": "pending",
                "stale_detected_at": None,
                "last_watchdog_reason": None,
            }
        )

    selected_priority_scope = _default_selected_scope(manifest)
    scope_decision_recommended = bool(manifest.get("summary", {}).get("priority_tier_decision_recommended"))

    progress = {
        "job_id": job_id,
        "src_root": manifest["src_root"],
        "dst_root": manifest["dst_root"],
        "batch_size": batch_size,
        "refresh_every_batches": refresh_every_batches,
        "refresh_every_files": refresh_every_files,
        "heartbeat_interval_seconds": DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        "heartbeat_stale_after_seconds": DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS,
        "heartbeat_claim_grace_seconds": DEFAULT_HEARTBEAT_CLAIM_GRACE_SECONDS,
        "watchdog_check_interval_seconds": DEFAULT_WATCHDOG_CHECK_INTERVAL_SECONDS,
        "selected_priority_scope": selected_priority_scope,
        "scope_decision_recommended": scope_decision_recommended,
        "scope_finalized": selected_priority_scope == SCOPE_ALL_TIERS,
        "skipped_priority_tiers": [],
        "next_locked_tier": None,
        "awaiting_scope_decision": False,
        "scope_history": [],
        "started_at": created_at,
        "updated_at": created_at,
        "summary": {},
        "active_batch": None,
        "batch_history": [],
        "batch_verifications": [],
        "refresh_checkpoints": [],
        "worker_activity": {},
        "watchdog_checks": [],
        "last_watchdog_check_at": None,
        "last_watchdog_snapshot": {},
        "items": items,
    }
    _refresh_scope_state(progress)
    progress["summary"] = summarize_progress(progress)
    return progress


def summarize_progress(progress: dict) -> dict:
    items = progress.get("items", [])
    llm_items = [item for item in items if item["category"] in LLM_CATEGORIES]
    in_scope_llm_items = [item for item in llm_items if is_item_in_selected_scope(progress, item)]
    all_batches = sorted({item["batch_index"] for item in llm_items if item.get("batch_index") is not None})
    pending_batches = sorted(
        {
            item["batch_index"]
            for item in in_scope_llm_items
            if item["status"] == STATUS_PENDING and item.get("batch_index") is not None
        }
    )
    active_batch = progress.get("active_batch")
    active_batch_verification = active_batch.get("verification") if active_batch else None
    watchdog = _build_watchdog_snapshot(progress)

    summary = {
        "total_items": len(items),
        "total_llm_files": len(llm_items),
        "total_batches": len(all_batches),
        "batch_size": progress.get("batch_size", DEFAULT_BATCH_SIZE),
        "pending_files": _count_by_status(items, STATUS_PENDING),
        "in_progress_files": _count_by_status(items, STATUS_IN_PROGRESS),
        "completed_files": _count_by_status(items, STATUS_COMPLETED),
        "failed_files": _count_by_status(items, STATUS_FAILED),
        "skipped_files": _count_by_status(items, STATUS_SKIPPED),
        "pending_llm_files": _count_by_status(llm_items, STATUS_PENDING),
        "in_progress_llm_files": _count_by_status(llm_items, STATUS_IN_PROGRESS),
        "completed_llm_files": _count_by_status(llm_items, STATUS_COMPLETED),
        "failed_llm_files": _count_by_status(llm_items, STATUS_FAILED),
        "pending_llm_files_in_scope": _count_by_status(in_scope_llm_items, STATUS_PENDING),
        "in_progress_llm_files_in_scope": _count_by_status(in_scope_llm_items, STATUS_IN_PROGRESS),
        "completed_llm_files_in_scope": _count_by_status(in_scope_llm_items, STATUS_COMPLETED),
        "failed_llm_files_in_scope": _count_by_status(in_scope_llm_items, STATUS_FAILED),
        "next_pending_batch_index": pending_batches[0] if pending_batches else None,
        "active_batch_index": active_batch.get("batch_index") if active_batch else None,
        "blocked_by_batch_verification": bool(active_batch and active_batch.get("blocked_by_verification")),
        "active_batch_verification": active_batch_verification,
        "batch_verification_passed": not bool(active_batch and active_batch.get("blocked_by_verification")),
        "batch_verification_count": len(progress.get("batch_verifications", [])),
        "last_batch_verification": (
            progress.get("batch_verifications", [])[-1] if progress.get("batch_verifications") else None
        ),
        "refresh_checkpoint_count": len(progress.get("refresh_checkpoints", [])),
        "selected_priority_scope": progress.get("selected_priority_scope"),
        "selected_priority_scope_label": SCOPE_LABELS.get(
            progress.get("selected_priority_scope"),
            progress.get("selected_priority_scope"),
        ),
        "scope_decision_recommended": progress.get("scope_decision_recommended", False),
        "scope_finalized": progress.get("scope_finalized", False),
        "skipped_priority_tiers": list(progress.get("skipped_priority_tiers", [])),
        "next_locked_tier": progress.get("next_locked_tier"),
        "awaiting_scope_decision": progress.get("awaiting_scope_decision", False),
        "locked_priority_tiers": _locked_priority_tiers(progress),
        "locked_llm_files": _count_locked_llm_files(progress),
        "remaining_priority_tiers": _build_remaining_priority_tiers(progress),
        "heartbeat_interval_seconds": progress.get(
            "heartbeat_interval_seconds",
            DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        ),
        "heartbeat_stale_after_seconds": progress.get(
            "heartbeat_stale_after_seconds",
            DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS,
        ),
        "heartbeat_claim_grace_seconds": progress.get(
            "heartbeat_claim_grace_seconds",
            DEFAULT_HEARTBEAT_CLAIM_GRACE_SECONDS,
        ),
        "watchdog_check_interval_seconds": progress.get(
            "watchdog_check_interval_seconds",
            DEFAULT_WATCHDOG_CHECK_INTERVAL_SECONDS,
        ),
        "last_watchdog_check_at": progress.get("last_watchdog_check_at"),
        "watchdog_status": watchdog.get("status"),
        "watchdog_check_due": watchdog.get("check_due", False),
        "watchdog_intervention_required": watchdog.get("intervention_required", False),
        "stale_in_progress_file_count": len(watchdog.get("stale_items", [])),
        "stale_in_progress_files": watchdog.get("stale_items", []),
        "stale_worker_count": len(watchdog.get("stale_workers", [])),
        "stale_workers": watchdog.get("stale_workers", []),
        "recommended_watchdog_action_count": len(watchdog.get("recommended_actions", [])),
        "recommended_watchdog_actions": watchdog.get("recommended_actions", []),
        "watchdog": watchdog,
    }
    summary["next_action"] = _next_action(progress, summary)
    summary["context_usage_hint"] = _build_context_usage_hint(progress, summary)
    return summary


def checkout_next_batch(progress: dict, retry_failed: bool = False) -> dict:
    _refresh_scope_state(progress)

    active_batch = progress.get("active_batch")
    if active_batch and active_batch.get("blocked_by_verification"):
        batch_index = active_batch.get("batch_index")
        checkpoint = _find_refresh_checkpoint(progress, active_batch.get("checkpoint_id"))
        batch_items = [
            item
            for item in progress["items"]
            if item.get("processed_by_batch") == batch_index and item["category"] in LLM_CATEGORIES
        ]
        progress["summary"] = summarize_progress(progress)
        progress["updated_at"] = utc_timestamp()
        return _build_blocked_batch_payload(progress, batch_index, batch_items, checkpoint, active_batch.get("verification"))

    active_items = _current_in_progress_items(progress)
    if active_items:
        batch_index = active_items[0]["batch_index"]
        checkpoint_id = active_items[0].get("refresh_checkpoint_id")
        checkpoint = _find_refresh_checkpoint(progress, checkpoint_id)
        watchdog = _build_watchdog_snapshot(progress)
        progress["summary"] = summarize_progress(progress)
        progress["updated_at"] = utc_timestamp()
        if watchdog.get("intervention_required"):
            return _build_stale_batch_payload(progress, batch_index, active_items, checkpoint, watchdog)
        return _build_batch_payload(progress, batch_index, active_items, checkpoint, reused=True)

    candidates = _scope_candidates(progress, STATUS_PENDING)
    if not candidates and retry_failed:
        candidates = _scope_candidates(progress, STATUS_FAILED)

    if not candidates:
        progress["active_batch"] = None
        _refresh_scope_state(progress)
        progress["summary"] = summarize_progress(progress)
        progress["updated_at"] = utc_timestamp()
        if progress.get("awaiting_scope_decision", False):
            return _build_scope_wait_payload(progress)
        return _build_complete_payload(progress)

    batch_index = min(item["batch_index"] for item in candidates if item.get("batch_index") is not None)
    batch_items = [item for item in candidates if item.get("batch_index") == batch_index]
    checkpoint = _append_refresh_checkpoint(progress, batch_index)
    started_at = utc_timestamp()

    for item in batch_items:
        item["status"] = STATUS_IN_PROGRESS
        item["attempt_count"] += 1
        item["last_error"] = None
        if item["started_at"] is None:
            item["started_at"] = started_at
        item["processed_by_batch"] = batch_index
        item["refresh_checkpoint_id"] = checkpoint["checkpoint_id"]

    progress["active_batch"] = {
        "batch_index": batch_index,
        "checkpoint_id": checkpoint["checkpoint_id"],
        "started_at": started_at,
        "priority_tier": batch_items[0].get("priority_tier"),
        "file_ids": [item["file_id"] for item in batch_items],
    }
    progress["updated_at"] = started_at
    _refresh_scope_state(progress)
    progress["summary"] = summarize_progress(progress)
    return _build_batch_payload(progress, batch_index, batch_items, checkpoint, reused=False)


def update_item_status(progress: dict, file_id: str, status: str, error: str | None = None) -> dict:
    item = next((candidate for candidate in progress["items"] if candidate["file_id"] == file_id), None)
    if item is None:
        raise ValueError(f"unknown file_id: {file_id}")

    if status not in {STATUS_PENDING, STATUS_COMPLETED, STATUS_FAILED, STATUS_SKIPPED}:
        raise ValueError(f"unsupported status: {status}")

    now = utc_timestamp()
    effective_status = status
    effective_error = error if status == STATUS_FAILED else None

    if status == STATUS_COMPLETED:
        verification = _verify_completed_output(item)
        if not verification["ok"]:
            effective_status = STATUS_FAILED
            effective_error = verification["reason"]

    item["status"] = effective_status
    item["last_error"] = effective_error if effective_status == STATUS_FAILED else None

    if effective_status == STATUS_PENDING:
        item["completed_at"] = None
        item["processed_by_batch"] = None
        item["refresh_checkpoint_id"] = None
        item["assigned_worker_id"] = None
        item["claimed_at"] = None
        item["last_heartbeat_at"] = None
        item["heartbeat_count"] = 0
        item["watchdog_state"] = "pending"
        item["stale_detected_at"] = None
        item["last_watchdog_reason"] = None
    else:
        item["completed_at"] = now
        item["watchdog_state"] = effective_status
        item["stale_detected_at"] = None
        item["last_watchdog_reason"] = None

    _refresh_worker_activity(progress)

    _finalize_active_batch(progress, now)
    progress["updated_at"] = now
    _refresh_scope_state(progress)
    progress["summary"] = summarize_progress(progress)
    return item


def heartbeat_items(progress: dict, file_ids: list[str], worker_id: str, note: str | None = None) -> dict:
    if not worker_id.strip():
        raise ValueError("worker_id must not be empty")
    if not file_ids:
        raise ValueError("file_ids must not be empty")

    now = utc_timestamp()
    touched = []
    seen = set()

    for file_id in file_ids:
        if file_id in seen:
            continue
        seen.add(file_id)
        item = next((candidate for candidate in progress["items"] if candidate["file_id"] == file_id), None)
        if item is None:
            raise ValueError(f"unknown file_id: {file_id}")
        if item.get("category") not in LLM_CATEGORIES:
            raise ValueError(f"heartbeat is only supported for LLM files: {file_id}")
        if item["status"] != STATUS_IN_PROGRESS:
            raise ValueError(f"heartbeat is only supported for in_progress items: {file_id}")

        item["assigned_worker_id"] = worker_id
        if item["claimed_at"] is None:
            item["claimed_at"] = now
        item["last_heartbeat_at"] = now
        item["heartbeat_count"] = item.get("heartbeat_count", 0) + 1
        item["watchdog_state"] = "healthy"
        item["stale_detected_at"] = None
        item["last_watchdog_reason"] = None
        touched.append(item)

    worker_activity = progress.setdefault("worker_activity", {})
    worker_entry = worker_activity.setdefault(
        worker_id,
        {
            "worker_id": worker_id,
            "heartbeat_count": 0,
            "last_heartbeat_at": None,
            "last_note": None,
            "active_file_ids": [],
        },
    )
    worker_entry["heartbeat_count"] = worker_entry.get("heartbeat_count", 0) + 1
    worker_entry["last_heartbeat_at"] = now
    worker_entry["last_note"] = note

    _refresh_worker_activity(progress)
    if progress.get("active_batch"):
        run_watchdog_check(progress, checked_at=now, source="heartbeat")
    else:
        progress["updated_at"] = now
        _refresh_scope_state(progress)
        progress["summary"] = summarize_progress(progress)

    return {
        "worker_id": worker_id,
        "file_ids": [item["file_id"] for item in touched],
        "checked_at": now,
        "summary": progress["summary"],
    }


def run_watchdog_check(
    progress: dict,
    checked_at: str | None = None,
    source: str = "manual",
) -> dict:
    now = checked_at or utc_timestamp()
    snapshot = _build_watchdog_snapshot(progress, checked_at=now)
    snapshot["source"] = source

    progress["last_watchdog_check_at"] = now
    progress["last_watchdog_snapshot"] = snapshot
    if progress.get("active_batch"):
        progress["active_batch"]["watchdog"] = snapshot
    _record_watchdog_check(progress, snapshot)
    progress["updated_at"] = now
    _refresh_scope_state(progress)
    progress["summary"] = summarize_progress(progress)
    return snapshot


def build_batch_selection_reason(progress: dict, batch_payload: dict) -> dict:
    summary = progress.get("summary") or summarize_progress(progress)
    status = batch_payload.get("status")
    batch_index = batch_payload.get("batch_index")
    scope_label = summary.get("selected_priority_scope_label") or summary.get("selected_priority_scope")

    if status == "blocked_by_batch_verification":
        verification = batch_payload.get("verification") or {}
        return {
            "code": "active_batch_blocked_by_disk_verification",
            "message": (
                f"当前批次 {batch_index} 已经完成状态回写，但磁盘验证未通过；"
                f"当前范围 {scope_label} 内必须先修复这批输出，再继续推进。"
            ),
        }

    if status == "stale_active_batch":
        watchdog = batch_payload.get("watchdog") or {}
        stale_files = len(watchdog.get("stale_items", []))
        stale_workers = len(watchdog.get("stale_workers", []))
        return {
            "code": "active_batch_requires_watchdog_intervention",
            "message": (
                f"当前批次 {batch_index} 仍在处理中，但巡检发现 {stale_files} 个卡住文件、"
                f"{stale_workers} 个需要介入的子代理；必须先处理卡住项，再继续推进。"
            ),
        }

    if batch_payload.get("reused_in_progress_batch"):
        return {
            "code": "reuse_current_in_progress_batch",
            "message": f"当前批次 {batch_index} 已在处理中，状态机要求继续复用这一批，而不是切到下一批。",
        }

    if status == "ready":
        first_item = batch_payload.get("items", [{}])[0] if batch_payload.get("items") else {}
        tier_label = first_item.get("priority_tier_label") or PRIORITY_TIER_LABELS.get(first_item.get("priority_tier"))
        return {
            "code": "select_lowest_pending_batch_in_scope",
            "message": f"当前范围 {scope_label} 内按批次顺序选择了最小待处理批次 {batch_index}，优先级为 {tier_label}。",
        }

    if status == "awaiting_scope_decision":
        next_locked_tier = summary.get("next_locked_tier")
        return {
            "code": "current_scope_exhausted_waiting_for_user_decision",
            "message": f"当前允许范围已经处理完，下一档 {next_locked_tier} 仍被锁定，必须先等待用户决定。",
        }

    if status == "complete":
        return {
            "code": "no_pending_batches_in_scope",
            "message": f"当前范围 {scope_label} 内已没有待处理批次，可以直接进入报告阶段。",
        }

    return {
        "code": "unknown_batch_selection_reason",
        "message": f"当前批次状态为 {status}，未命中更具体的批次选择解释。",
    }


def build_decision_evidence(progress: dict, batch_payload: dict | None = None) -> dict:
    summary = progress.get("summary") or summarize_progress(progress)
    reason = _build_next_action_reason(summary)
    evidence = {
        "selected_priority_scope": summary.get("selected_priority_scope"),
        "selected_priority_scope_label": summary.get("selected_priority_scope_label"),
        "active_batch_index": summary.get("active_batch_index"),
        "next_pending_batch_index": summary.get("next_pending_batch_index"),
        "next_locked_tier": summary.get("next_locked_tier"),
        "awaiting_scope_decision": summary.get("awaiting_scope_decision", False),
        "next_action": summary.get("next_action"),
        "next_action_reason_code": reason["code"],
        "next_action_reason": reason["message"],
        "context_refresh_required": summary.get("context_usage_hint", {}).get("should_start_fresh_context", False),
        "watchdog_status": summary.get("watchdog_status"),
        "watchdog_check_due": summary.get("watchdog_check_due", False),
        "watchdog_intervention_required": summary.get("watchdog_intervention_required", False),
        "stale_in_progress_file_count": summary.get("stale_in_progress_file_count", 0),
        "stale_worker_count": summary.get("stale_worker_count", 0),
        "recommended_watchdog_action_count": summary.get("recommended_watchdog_action_count", 0),
        "recommended_watchdog_actions": summary.get("recommended_watchdog_actions", []),
    }

    if batch_payload is not None:
        batch_reason = build_batch_selection_reason(progress, batch_payload)
        evidence["batch_selection_reason_code"] = batch_reason["code"]
        evidence["batch_selection_reason"] = batch_reason["message"]

    return evidence


def set_scope_decision(progress: dict, decision: str) -> dict:
    if decision not in SCOPE_DECISION_OPTIONS:
        raise ValueError(f"unsupported scope decision: {decision}")

    selected_priority_scope, scope_finalized, skipped_priority_tiers = _normalize_scope_decision(decision)
    _validate_scope_transition(progress, selected_priority_scope, skipped_priority_tiers)

    now = utc_timestamp()
    progress["selected_priority_scope"] = selected_priority_scope
    progress["scope_finalized"] = scope_finalized
    progress["skipped_priority_tiers"] = skipped_priority_tiers
    progress.setdefault("scope_history", []).append(
        {
            "decision": decision,
            "selected_priority_scope": selected_priority_scope,
            "scope_finalized": scope_finalized,
            "skipped_priority_tiers": skipped_priority_tiers,
            "decided_at": now,
        }
    )
    progress["updated_at"] = now
    _refresh_scope_state(progress)
    progress["summary"] = summarize_progress(progress)
    return {
        "decision": decision,
        "selected_priority_scope": selected_priority_scope,
        "scope_finalized": scope_finalized,
        "skipped_priority_tiers": skipped_priority_tiers,
        "awaiting_scope_decision": progress["awaiting_scope_decision"],
        "next_locked_tier": progress["next_locked_tier"],
        "summary": progress["summary"],
    }


def build_originals_lock(manifest: dict) -> dict:
    src_root = Path(manifest["src_root"])
    dst_root = Path(manifest["dst_root"])
    source_files = []
    copied_original_files = []

    for item in manifest.get("items", []):
        source_files.append(_build_fingerprint_record(src_root / item["rel_path"], item["rel_path"]))
        copied_original_files.append(
            _build_fingerprint_record(dst_root / item["copied_rel_path"], item["rel_path"])
        )

    return {
        "src_root": manifest["src_root"],
        "dst_root": manifest["dst_root"],
        "created_at": utc_timestamp(),
        "source_files": source_files,
        "copied_original_files": copied_original_files,
    }


def verify_originals_lock(lock: dict) -> dict:
    src_root = Path(lock["src_root"])
    dst_root = Path(lock["dst_root"])
    modified_source_files = []
    modified_original_copies = []

    for record in lock.get("source_files", []):
        diff = _compare_fingerprint_record(src_root, record)
        if diff:
            modified_source_files.append(diff)

    for record in lock.get("copied_original_files", []):
        diff = _compare_fingerprint_record(dst_root, record)
        if diff:
            modified_original_copies.append(diff)

    return {
        "modified_source_files": modified_source_files,
        "modified_original_copies": modified_original_copies,
        "source_integrity_ok": not modified_source_files,
        "copied_original_integrity_ok": not modified_original_copies,
    }


def is_item_in_selected_scope(progress: dict, item: dict) -> bool:
    if item.get("priority_tier") in progress.get("skipped_priority_tiers", []):
        return False
    return item.get("priority_tier") in _selected_allowed_tiers(progress)


def should_expect_cn_file(progress: dict | None, item: dict) -> bool:
    if item.get("category") not in LLM_CATEGORIES:
        return False
    if progress is None:
        return True
    return is_item_in_selected_scope(progress, item)


def _verify_completed_output(item: dict) -> dict:
    if item.get("category") not in LLM_CATEGORIES:
        return {"ok": True, "reason": None}

    cn_file = item.get("cn_file")
    if not cn_file:
        return {"ok": False, "reason": f"cn_file missing: {item.get('rel_path')}"}

    cn_path = Path(cn_file)
    if not cn_path.is_file():
        return {"ok": False, "reason": f"cn_file missing: {cn_file}"}
    if cn_path.stat().st_size <= 0:
        return {"ok": False, "reason": f"cn_file is empty: {cn_file}"}

    return {"ok": True, "reason": None}


def _verify_batch_outputs(progress: dict, batch_index: int, checked_at: str) -> dict:
    batch_items = [
        item
        for item in progress["items"]
        if item.get("processed_by_batch") == batch_index and item["category"] in LLM_CATEGORIES
    ]
    missing_cn_files = []
    missing_original_copies = []

    for item in batch_items:
        copied_file = item.get("copied_file")
        if copied_file and not Path(copied_file).exists():
            missing_original_copies.append(
                {
                    "file_id": item.get("file_id"),
                    "rel_path": item.get("rel_path"),
                    "expected_original_file": copied_file,
                }
            )

        verification = _verify_completed_output(item)
        if not verification["ok"]:
            missing_cn_files.append(
                {
                    "file_id": item.get("file_id"),
                    "rel_path": item.get("rel_path"),
                    "expected_cn_file": item.get("cn_file"),
                    "reason": verification["reason"],
                }
            )

    verified = not missing_cn_files and not missing_original_copies
    return {
        "batch_index": batch_index,
        "verified": verified,
        "missing_cn_files": missing_cn_files,
        "missing_original_copies": missing_original_copies,
        "checked_at": checked_at,
        "verified_at": checked_at if verified else None,
    }


def _record_batch_verification(progress: dict, verification: dict) -> None:
    verifications = progress.setdefault("batch_verifications", [])
    for index, existing in enumerate(verifications):
        if existing.get("batch_index") == verification.get("batch_index"):
            verifications[index] = verification
            return
    verifications.append(verification)


def _record_watchdog_check(progress: dict, snapshot: dict) -> None:
    checks = progress.setdefault("watchdog_checks", [])
    checks.append(snapshot)


def _refresh_worker_activity(progress: dict) -> None:
    active_by_worker: dict[str, list[str]] = {}
    for item in progress.get("items", []):
        worker_id = item.get("assigned_worker_id")
        if not worker_id or item.get("status") != STATUS_IN_PROGRESS:
            continue
        active_by_worker.setdefault(worker_id, []).append(item["file_id"])

    worker_activity = progress.setdefault("worker_activity", {})
    for worker_id, entry in list(worker_activity.items()):
        file_ids = sorted(active_by_worker.get(worker_id, []))
        entry["active_file_ids"] = file_ids
        if not file_ids and not entry.get("last_heartbeat_at"):
            worker_activity.pop(worker_id, None)


def _build_watchdog_snapshot(progress: dict, checked_at: str | None = None) -> dict:
    checked_at = checked_at or utc_timestamp()
    active_batch = progress.get("active_batch")
    watchdog_interval = progress.get("watchdog_check_interval_seconds", DEFAULT_WATCHDOG_CHECK_INTERVAL_SECONDS)

    if not active_batch:
        return {
            "checked_at": checked_at,
            "status": "no_active_batch",
            "check_due": False,
            "intervention_required": False,
            "stale_items": [],
            "stale_workers": [],
            "recommended_actions": [],
            "next_check_due_at": None,
        }

    if active_batch.get("blocked_by_verification"):
        return {
            "checked_at": checked_at,
            "batch_index": active_batch.get("batch_index"),
            "status": "blocked_by_batch_verification",
            "check_due": False,
            "intervention_required": False,
            "stale_items": [],
            "stale_workers": [],
            "recommended_actions": [],
            "next_check_due_at": None,
        }

    batch_index = active_batch.get("batch_index")
    stale_items = []
    active_items = [
        item
        for item in progress.get("items", [])
        if item.get("processed_by_batch") == batch_index and item.get("status") == STATUS_IN_PROGRESS
    ]

    for item in active_items:
        item_snapshot = _inspect_in_progress_item(progress, item, checked_at, active_batch)
        item["watchdog_state"] = item_snapshot["watchdog_state"]
        item["last_watchdog_reason"] = item_snapshot["reason_code"]
        item["stale_detected_at"] = item_snapshot["stale_detected_at"]
        if item_snapshot["is_stale"]:
            stale_items.append(
                {
                    "file_id": item["file_id"],
                    "rel_path": item["rel_path"],
                    "assigned_worker_id": item.get("assigned_worker_id"),
                    "reason_code": item_snapshot["reason_code"],
                    "reason": item_snapshot["reason"],
                    "seconds_since_signal": item_snapshot["seconds_since_signal"],
                    "last_signal_at": item_snapshot["last_signal_at"],
                }
            )

    stale_workers = []
    for worker_id, file_ids in _group_stale_items_by_worker(stale_items).items():
        if not worker_id:
            continue
        worker_entry = progress.get("worker_activity", {}).get(worker_id, {})
        stale_workers.append(
            {
                "worker_id": worker_id,
                "file_ids": sorted(file_ids),
                "stale_file_count": len(file_ids),
                "last_heartbeat_at": worker_entry.get("last_heartbeat_at"),
            }
        )

    recommended_actions = _build_watchdog_recommended_actions(stale_items, stale_workers)

    check_anchor = progress.get("last_watchdog_check_at") or active_batch.get("started_at")
    check_due = _is_watchdog_check_due(check_anchor, checked_at, watchdog_interval)
    if stale_items:
        status = "intervention_required"
        check_due = False
    elif check_due:
        status = "check_due"
    else:
        status = "healthy"

    return {
        "checked_at": checked_at,
        "batch_index": batch_index,
        "status": status,
        "check_due": check_due,
        "intervention_required": bool(stale_items),
        "stale_items": stale_items,
        "stale_workers": stale_workers,
        "recommended_actions": recommended_actions,
        "next_check_due_at": _compute_next_check_due_at(checked_at, watchdog_interval),
    }


def _build_watchdog_recommended_actions(stale_items: list[dict], stale_workers: list[dict]) -> list[dict]:
    actions: list[dict] = []
    stale_items_by_worker = _group_stale_items_by_worker(stale_items)

    for worker in sorted(stale_workers, key=lambda item: item["worker_id"]):
        file_ids = sorted(worker.get("file_ids", []))
        related_items = [item for item in stale_items if item.get("assigned_worker_id") == worker["worker_id"]]
        reason_codes = sorted({item.get("reason_code") for item in related_items if item.get("reason_code")})
        instruction = (
            "先检查该子代理是否还在正常工作；如果没有恢复心跳，就回收这些 file_id 并重分配给新的子代理。"
        )
        actions.append(
            {
                "action": "check_or_replace_worker",
                "priority": "high",
                "worker_id": worker["worker_id"],
                "file_ids": file_ids,
                "reason_codes": reason_codes,
                "reason_summary": _summarize_watchdog_reason_codes(reason_codes),
                "instruction": instruction,
            }
        )

    unassigned_items = sorted(
        [item for item in stale_items if not item.get("assigned_worker_id")],
        key=lambda item: item["file_id"],
    )
    if unassigned_items:
        reason_codes = sorted({item.get("reason_code") for item in unassigned_items if item.get("reason_code")})
        actions.append(
            {
                "action": "reassign_unclaimed_files",
                "priority": "high",
                "worker_id": None,
                "file_ids": [item["file_id"] for item in unassigned_items],
                "reason_codes": reason_codes,
                "reason_summary": _summarize_watchdog_reason_codes(reason_codes),
                "instruction": "这些文件已经进入 in_progress，但还没有有效心跳；请检查是否漏分配，并立即重新分配。",
            }
        )

    return actions


def _summarize_watchdog_reason_codes(reason_codes: list[str]) -> str:
    normalized = set(reason_codes)
    if not normalized:
        return "watchdog 已检测到需要人工介入的异常。"
    if normalized == {"heartbeat_timeout"}:
        return "子代理曾经接手文件，但后续心跳已经超时。"
    if normalized == {"no_heartbeat_since_batch_dispatch"}:
        return "文件进入批次后一直没有收到首个心跳。"
    if normalized == {"heartbeat_timeout", "no_heartbeat_since_batch_dispatch"}:
        return "部分文件心跳已超时，部分文件则一直没有收到首个心跳。"
    return "watchdog 检测到多种心跳异常，建议先人工排查后再继续。"


def _default_selected_scope(manifest: dict) -> str:
    if manifest.get("summary", {}).get("priority_tier_decision_recommended"):
        return SCOPE_TIER_1_ONLY
    return SCOPE_ALL_TIERS


def _initial_status(manifest_item: dict) -> str:
    if manifest_item.get("copy_failure"):
        return STATUS_FAILED
    if manifest_item["category"] in LLM_CATEGORIES:
        return STATUS_PENDING
    return STATUS_SKIPPED


def _count_by_status(items: list[dict], status: str) -> int:
    return sum(1 for item in items if item["status"] == status)


def _scope_candidates(progress: dict, status: str) -> list[dict]:
    candidates = [
        item
        for item in progress["items"]
        if item["category"] in LLM_CATEGORIES
        and item["status"] == status
        and is_item_in_selected_scope(progress, item)
    ]
    candidates.sort(key=lambda item: ((item.get("batch_index") or 0), item["file_id"]))
    return candidates


def _current_in_progress_items(progress: dict) -> list[dict]:
    items = [item for item in progress["items"] if item["status"] == STATUS_IN_PROGRESS]
    items.sort(key=lambda item: ((item.get("batch_index") or 0), item["file_id"]))
    return items


def _inspect_in_progress_item(progress: dict, item: dict, checked_at: str, active_batch: dict) -> dict:
    checked_dt = parse_utc_timestamp(checked_at)
    last_signal_at = item.get("last_heartbeat_at") or item.get("claimed_at") or item.get("started_at") or active_batch.get("started_at")
    last_signal_dt = parse_utc_timestamp(last_signal_at)
    seconds_since_signal = _seconds_since(last_signal_dt, checked_dt)

    if item.get("last_heartbeat_at"):
        timeout_seconds = progress.get("heartbeat_stale_after_seconds", DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS)
        if seconds_since_signal is not None and seconds_since_signal >= timeout_seconds:
            return {
                "is_stale": True,
                "watchdog_state": "stale",
                "reason_code": "heartbeat_timeout",
                "reason": f"子代理心跳已超时 {seconds_since_signal} 秒。",
                "seconds_since_signal": seconds_since_signal,
                "last_signal_at": last_signal_at,
                "stale_detected_at": checked_at,
            }
        return {
            "is_stale": False,
            "watchdog_state": "healthy",
            "reason_code": None,
            "reason": None,
            "seconds_since_signal": seconds_since_signal,
            "last_signal_at": last_signal_at,
            "stale_detected_at": None,
        }

    claim_grace_seconds = progress.get("heartbeat_claim_grace_seconds", DEFAULT_HEARTBEAT_CLAIM_GRACE_SECONDS)
    if seconds_since_signal is not None and seconds_since_signal >= claim_grace_seconds:
        return {
            "is_stale": True,
            "watchdog_state": "silent",
            "reason_code": "no_heartbeat_since_batch_dispatch",
            "reason": f"子代理在批次发放后 {seconds_since_signal} 秒内仍未回写首次心跳。",
            "seconds_since_signal": seconds_since_signal,
            "last_signal_at": last_signal_at,
            "stale_detected_at": checked_at,
        }

    return {
        "is_stale": False,
        "watchdog_state": "awaiting_first_heartbeat",
        "reason_code": None,
        "reason": None,
        "seconds_since_signal": seconds_since_signal,
        "last_signal_at": last_signal_at,
        "stale_detected_at": None,
    }


def _group_stale_items_by_worker(stale_items: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in stale_items:
        worker_id = item.get("assigned_worker_id")
        grouped.setdefault(worker_id, []).append(item["file_id"])
    return grouped


def _seconds_since(older: datetime | None, newer: datetime | None) -> int | None:
    if older is None or newer is None:
        return None
    return max(0, int((newer - older).total_seconds()))


def _is_watchdog_check_due(last_check_at: str | None, checked_at: str, interval_seconds: int) -> bool:
    checked_dt = parse_utc_timestamp(checked_at)
    if checked_dt is None:
        return False
    last_check_dt = parse_utc_timestamp(last_check_at)
    if last_check_dt is None:
        return True
    return _seconds_since(last_check_dt, checked_dt) >= interval_seconds


def _compute_next_check_due_at(checked_at: str, interval_seconds: int) -> str | None:
    checked_dt = parse_utc_timestamp(checked_at)
    if checked_dt is None:
        return None
    return datetime.fromtimestamp(
        checked_dt.timestamp() + interval_seconds,
        tz=timezone.utc,
    ).replace(microsecond=0).isoformat()


def _find_refresh_checkpoint(progress: dict, checkpoint_id: str | None) -> dict | None:
    if checkpoint_id is None:
        return None
    for checkpoint in progress.get("refresh_checkpoints", []):
        if checkpoint["checkpoint_id"] == checkpoint_id:
            return checkpoint
    return None


def _append_refresh_checkpoint(progress: dict, batch_index: int) -> dict:
    checkpoint_id = f"CP{len(progress.get('refresh_checkpoints', [])) + 1:06d}"
    checkpoint = {
        "checkpoint_id": checkpoint_id,
        "batch_index": batch_index,
        "triggered_at": utc_timestamp(),
        "required_reads": [
            str(SKILL_ROOT / "SKILL.md"),
            str(SKILL_ROOT / "references" / "document-rules.md"),
            str(SKILL_ROOT / "references" / "code-rules.md"),
            str(resolve_job_dir(progress["dst_root"]) / PROGRESS_FILE),
            str(resolve_job_dir(progress["dst_root"]) / MANIFEST_FILE),
        ],
    }
    progress.setdefault("refresh_checkpoints", []).append(checkpoint)
    return checkpoint


def _build_batch_payload(
    progress: dict,
    batch_index: int,
    batch_items: list[dict],
    checkpoint: dict | None,
    reused: bool,
) -> dict:
    items = [
        {
            "file_id": item["file_id"],
            "rel_path": item["rel_path"],
            "category": item["category"],
            "llm_action": item.get("llm_action"),
            "copied_file": item.get("copied_file"),
            "cn_file": item.get("cn_file"),
            "batch_index": item.get("batch_index"),
            "priority_tier": item.get("priority_tier"),
            "priority_tier_label": item.get("priority_tier_label"),
            "status": item["status"],
            "attempt_count": item["attempt_count"],
            "refresh_checkpoint_id": item.get("refresh_checkpoint_id"),
            "assigned_worker_id": item.get("assigned_worker_id"),
            "last_heartbeat_at": item.get("last_heartbeat_at"),
            "heartbeat_count": item.get("heartbeat_count", 0),
            "watchdog_state": item.get("watchdog_state"),
        }
        for item in batch_items
    ]
    items.sort(key=lambda item: item["file_id"])

    return {
        "batch_index": batch_index,
        "refresh_checkpoint_id": checkpoint["checkpoint_id"] if checkpoint else None,
        "required_reads": checkpoint.get("required_reads", []) if checkpoint else [],
        "items": items,
        "selected_priority_scope": progress.get("selected_priority_scope"),
        "selected_priority_scope_label": SCOPE_LABELS.get(progress.get("selected_priority_scope")),
        "reused_in_progress_batch": reused,
        "status": "ready",
    }


def _build_scope_wait_payload(progress: dict) -> dict:
    return {
        "batch_index": None,
        "refresh_checkpoint_id": None,
        "required_reads": [],
        "items": [],
        "reused_in_progress_batch": False,
        "selected_priority_scope": progress.get("selected_priority_scope"),
        "selected_priority_scope_label": SCOPE_LABELS.get(progress.get("selected_priority_scope")),
        "next_locked_tier": progress.get("next_locked_tier"),
        "decision_options": list(SCOPE_DECISION_OPTIONS),
        "status": "awaiting_scope_decision",
    }


def _build_complete_payload(progress: dict) -> dict:
    return {
        "batch_index": None,
        "refresh_checkpoint_id": None,
        "required_reads": [],
        "items": [],
        "reused_in_progress_batch": False,
        "selected_priority_scope": progress.get("selected_priority_scope"),
        "selected_priority_scope_label": SCOPE_LABELS.get(progress.get("selected_priority_scope")),
        "status": "complete",
    }


def _build_blocked_batch_payload(
    progress: dict,
    batch_index: int,
    batch_items: list[dict],
    checkpoint: dict | None,
    verification: dict | None,
) -> dict:
    payload = _build_batch_payload(progress, batch_index, batch_items, checkpoint, reused=True)
    payload["status"] = "blocked_by_batch_verification"
    payload["verification"] = verification or {}
    return payload


def _build_stale_batch_payload(
    progress: dict,
    batch_index: int,
    batch_items: list[dict],
    checkpoint: dict | None,
    watchdog: dict,
) -> dict:
    payload = _build_batch_payload(progress, batch_index, batch_items, checkpoint, reused=True)
    payload["status"] = "stale_active_batch"
    payload["watchdog"] = watchdog
    return payload


def _finalize_active_batch(progress: dict, finished_at: str) -> None:
    active_batch = progress.get("active_batch")
    if not active_batch:
        return

    batch_index = active_batch.get("batch_index")
    if batch_index is None:
        return

    batch_items = [
        item
        for item in progress["items"]
        if item.get("processed_by_batch") == batch_index and item["category"] in LLM_CATEGORIES
    ]
    if any(item["status"] in {STATUS_IN_PROGRESS, STATUS_PENDING} for item in batch_items):
        return

    verification = _verify_batch_outputs(progress, batch_index, finished_at)
    _record_batch_verification(progress, verification)
    if not verification["verified"]:
        progress["active_batch"] = {
            **active_batch,
            "blocked_by_verification": True,
            "verification": verification,
            "completed_at": finished_at,
        }
        return

    progress.setdefault("batch_history", []).append(
        {
            "batch_index": batch_index,
            "checkpoint_id": active_batch.get("checkpoint_id"),
            "started_at": active_batch.get("started_at"),
            "completed_at": finished_at,
            "priority_tier": active_batch.get("priority_tier"),
            "file_ids": active_batch.get("file_ids", []),
        }
    )
    progress["active_batch"] = None


def _selected_allowed_tiers(progress: dict) -> set[int]:
    selected = progress.get("selected_priority_scope", SCOPE_ALL_TIERS)
    allowed_tiers = set(SCOPE_ALLOWED_TIERS.get(selected, {1, 2, 3}))
    allowed_tiers.difference_update(progress.get("skipped_priority_tiers", []))
    return allowed_tiers


def _refresh_scope_state(progress: dict) -> None:
    next_locked_tier = _compute_next_locked_tier(progress)
    has_in_scope_unresolved = any(
        is_item_in_selected_scope(progress, item) and _is_unresolved_item(item)
        for item in progress["items"]
        if item["category"] in LLM_CATEGORIES
    )
    has_active_batch = progress.get("active_batch") is not None
    awaiting_scope_decision = (
        not progress.get("scope_finalized", False)
        and not has_active_batch
        and not has_in_scope_unresolved
        and next_locked_tier is not None
    )

    progress["next_locked_tier"] = next_locked_tier
    progress["awaiting_scope_decision"] = awaiting_scope_decision


def _compute_next_locked_tier(progress: dict) -> int | None:
    locked_tiers = _locked_priority_tiers(progress)
    return locked_tiers[0] if locked_tiers else None


def _locked_priority_tiers(progress: dict) -> list[int]:
    allowed_tiers = _selected_allowed_tiers(progress)
    skipped_tiers = set(progress.get("skipped_priority_tiers", []))
    locked_tiers = sorted(
        {
            item.get("priority_tier")
            for item in progress["items"]
            if item["category"] in LLM_CATEGORIES
            and _is_unresolved_item(item)
            and item.get("priority_tier") not in allowed_tiers
            and item.get("priority_tier") not in skipped_tiers
        }
    )
    return [tier for tier in locked_tiers if tier is not None]


def _count_locked_llm_files(progress: dict) -> int:
    return sum(
        1
        for item in progress["items"]
        if item["category"] in LLM_CATEGORIES
        and _is_unresolved_item(item)
        and not is_item_in_selected_scope(progress, item)
        and item.get("priority_tier") not in progress.get("skipped_priority_tiers", [])
    )


def _build_remaining_priority_tiers(progress: dict) -> dict:
    allowed_tiers = _selected_allowed_tiers(progress)
    skipped_tiers = set(progress.get("skipped_priority_tiers", []))
    remaining = {}

    for tier in (1, 2, 3):
        unresolved_items = [
            item
            for item in progress["items"]
            if item.get("priority_tier") == tier and _is_unresolved_item(item)
        ]
        remaining[f"tier_{tier}"] = {
            "tier": tier,
            "label": PRIORITY_TIER_LABELS[tier],
            "allowed": tier in allowed_tiers,
            "locked": tier not in allowed_tiers and tier not in skipped_tiers and bool(unresolved_items),
            "skipped": tier in skipped_tiers,
            "remaining_files": len(unresolved_items),
            "remaining_document_files": sum(1 for item in unresolved_items if item["category"] == "document"),
            "remaining_code_files": sum(1 for item in unresolved_items if item["category"] == "code"),
            "remaining_other_files": sum(1 for item in unresolved_items if item["category"] == "other"),
        }

    return remaining


def _build_context_usage_hint(progress: dict, summary: dict) -> dict:
    refresh_threshold = progress.get("refresh_every_files", DEFAULT_REFRESH_EVERY_FILES) or DEFAULT_REFRESH_EVERY_FILES
    completed_llm_files = summary.get("completed_llm_files", 0)
    pending_llm_files_in_scope = summary.get("pending_llm_files_in_scope", 0)
    awaiting_scope_decision = summary.get("awaiting_scope_decision", False)
    has_follow_up_work = pending_llm_files_in_scope > 0 or summary.get("active_batch_index") is not None

    should_start_fresh_context = False
    reason = "current-context-ok"
    if summary.get("watchdog_intervention_required", False):
        should_start_fresh_context = True
        reason = "watchdog-intervention-required"
    elif awaiting_scope_decision:
        should_start_fresh_context = True
        reason = "scope-decision-gate-reached"
    elif completed_llm_files >= refresh_threshold and has_follow_up_work:
        should_start_fresh_context = True
        reason = "completed-files-threshold-reached"

    return {
        "completed_file_context_policy": COMPLETED_FILE_CONTEXT_POLICY,
        "allow_completed_file_full_content_in_context": False,
        "pending_file_context_policy": "current-batch-only",
        "should_start_fresh_context": should_start_fresh_context,
        "reason": reason,
        "state_files_to_read": [
            PROGRESS_FILE,
            MANIFEST_FILE,
        ],
        "rule_files_to_read": [
            "SKILL.md",
            "references/document-rules.md",
            "references/code-rules.md",
        ],
        "disallowed_context_sources": [
            "completed_copied_file_full_content",
            "completed_cn_file_full_content",
            "previous_batch_full_text_dump",
        ],
        "explicit_reopen_conditions": [
            "verification-failure",
            "explicit-user-request",
            "single-file-debug",
        ],
    }


def _build_next_action_reason(summary: dict) -> dict:
    next_action = summary.get("next_action")

    if next_action == "fix_current_batch_outputs":
        return {
            "code": "current_batch_failed_disk_verification",
            "message": "当前批次已经回写完成状态，但磁盘验证未通过，所以必须先修复这批输出。",
        }
    if next_action == "finish_current_batch":
        return {
            "code": "current_batch_still_in_progress",
            "message": "当前批次仍在处理中，状态机要求先把这一批完成。",
        }
    if next_action == "check_subagent_heartbeat":
        return {
            "code": "active_batch_requires_periodic_watchdog_check",
            "message": "当前批次仍在处理中，但已经到达巡检时间点，应该先检查子代理心跳是否正常。",
        }
    if next_action == "investigate_stuck_subagents":
        stale_files = summary.get("stale_in_progress_file_count", 0)
        stale_workers = summary.get("stale_worker_count", 0)
        return {
            "code": "active_batch_has_stuck_subagents",
            "message": f"当前批次检测到 {stale_files} 个疑似卡住文件、{stale_workers} 个需要介入的子代理，必须先处理卡住项。",
        }
        
    if next_action == "resume":
        return {
            "code": "pending_batches_exist_in_scope",
            "message": "当前允许范围内仍有待处理批次，可以继续领取下一批。",
        }
    if next_action == "ask_user_about_tier_2":
        return {
            "code": "tier_1_complete_waiting_for_tier_2_decision",
            "message": "1 档已处理完，下一步必须先问用户是否放开 2 档。",
        }
    if next_action == "ask_user_about_tier_3":
        return {
            "code": "tier_1_and_2_complete_waiting_for_tier_3_decision",
            "message": "1+2 档已处理完，下一步必须先问用户是否放开 3 档。",
        }
    if next_action == "await_scope_decision":
        return {
            "code": "scope_gate_waiting_for_user",
            "message": "当前状态机停在档位闸门，必须先等待用户写入范围决策。",
        }
    if next_action == "report":
        return {
            "code": "all_allowed_work_complete",
            "message": "当前允许范围内已经没有待处理工作，可以进入报告阶段。",
        }

    return {
        "code": "unknown_next_action_reason",
        "message": f"下一动作是 {next_action}，但没有命中更具体的解释。",
    }


def _next_action(progress: dict, summary: dict | None = None) -> str:
    summary = summary or {}
    active_batch = progress.get("active_batch")
    if active_batch and active_batch.get("blocked_by_verification"):
        return "fix_current_batch_outputs"
    if summary.get("watchdog_intervention_required", False):
        return "investigate_stuck_subagents"
    if summary.get("watchdog_check_due", False):
        return "check_subagent_heartbeat"
    if progress.get("active_batch"):
        return "finish_current_batch"
    if progress.get("awaiting_scope_decision"):
        next_locked_tier = progress.get("next_locked_tier")
        if next_locked_tier is not None:
            return f"ask_user_about_tier_{next_locked_tier}"
        return "await_scope_decision"
    if _scope_candidates(progress, STATUS_PENDING) or _scope_candidates(progress, STATUS_FAILED):
        return "resume"
    return "report"


def _is_unresolved_item(item: dict) -> bool:
    return item["status"] in UNRESOLVED_STATUSES


def _normalize_scope_decision(decision: str) -> tuple[str, bool, list[int]]:
    if decision == SCOPE_TIER_1_ONLY:
        return SCOPE_TIER_1_ONLY, False, []
    if decision == SCOPE_TIER_1_AND_2:
        return SCOPE_TIER_1_AND_2, False, []
    if decision == SCOPE_ALL_TIERS:
        return SCOPE_ALL_TIERS, True, []
    if decision == SCOPE_DECISION_SKIP_TIER_3:
        return SCOPE_TIER_1_AND_2, True, [3]
    raise ValueError(f"unsupported scope decision: {decision}")


def _validate_scope_transition(progress: dict, selected_priority_scope: str, skipped_priority_tiers: list[int]) -> None:
    active_batch = progress.get("active_batch")
    if active_batch and active_batch.get("blocked_by_verification"):
        raise ValueError("cannot change scope while current batch is blocked by batch verification")
    if active_batch and _build_watchdog_snapshot(progress).get("intervention_required"):
        raise ValueError("cannot change scope while current batch requires watchdog intervention")

    allowed_tiers = set(SCOPE_ALLOWED_TIERS[selected_priority_scope])
    allowed_tiers.difference_update(skipped_priority_tiers)
    blocked_in_progress_tiers = sorted(
        {
            item.get("priority_tier")
            for item in progress["items"]
            if item["status"] == STATUS_IN_PROGRESS and item.get("priority_tier") not in allowed_tiers
        }
    )
    if blocked_in_progress_tiers:
        joined = ", ".join(str(tier) for tier in blocked_in_progress_tiers if tier is not None)
        raise ValueError(f"cannot narrow scope below active in-progress tiers: {joined}")


def _build_fingerprint_record(path: Path, rel_path: str) -> dict:
    if not path.exists():
        return {
            "rel_path": rel_path,
            "exists": False,
            "size_bytes": None,
            "mtime_ns": None,
            "sha256": None,
        }

    stat = path.stat()
    return {
        "rel_path": rel_path,
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256_file(path),
    }


def _compare_fingerprint_record(root: Path, record: dict) -> dict | None:
    current_path = root / record["rel_path"]
    if not record.get("exists", True):
        if current_path.exists():
            current = _build_fingerprint_record(current_path, record["rel_path"])
            return {
                "rel_path": record["rel_path"],
                "reason": "file-appeared-after-lock",
                "current": current,
            }
        return None

    if not current_path.exists():
        return {
            "rel_path": record["rel_path"],
            "reason": "missing",
        }

    current = _build_fingerprint_record(current_path, record["rel_path"])
    changed_fields = [
        field
        for field in ("size_bytes", "mtime_ns", "sha256")
        if current.get(field) != record.get(field)
    ]
    if not changed_fields:
        return None

    return {
        "rel_path": record["rel_path"],
        "reason": "fingerprint-mismatch",
        "changed_fields": changed_fields,
        "expected": {
            "size_bytes": record.get("size_bytes"),
            "mtime_ns": record.get("mtime_ns"),
            "sha256": record.get("sha256"),
        },
        "current": {
            "size_bytes": current.get("size_bytes"),
            "mtime_ns": current.get("mtime_ns"),
            "sha256": current.get("sha256"),
        },
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
