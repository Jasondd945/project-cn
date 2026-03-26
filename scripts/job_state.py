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
        "refresh_checkpoints": [],
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
        "next_action": _next_action(progress),
    }
    return summary


def checkout_next_batch(progress: dict, retry_failed: bool = False) -> dict:
    _refresh_scope_state(progress)

    active_items = _current_in_progress_items(progress)
    if active_items:
        batch_index = active_items[0]["batch_index"]
        checkpoint_id = active_items[0].get("refresh_checkpoint_id")
        checkpoint = _find_refresh_checkpoint(progress, checkpoint_id)
        progress["summary"] = summarize_progress(progress)
        progress["updated_at"] = utc_timestamp()
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
    item["status"] = status
    item["last_error"] = error if status == STATUS_FAILED else None

    if status == STATUS_PENDING:
        item["completed_at"] = None
        item["processed_by_batch"] = None
        item["refresh_checkpoint_id"] = None
    else:
        item["completed_at"] = now

    _finalize_active_batch(progress, now)
    progress["updated_at"] = now
    _refresh_scope_state(progress)
    progress["summary"] = summarize_progress(progress)
    return item


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
    if any(item["status"] == STATUS_IN_PROGRESS for item in batch_items):
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


def _next_action(progress: dict) -> str:
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
