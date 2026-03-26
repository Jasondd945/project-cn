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

FINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_SKIPPED}
LLM_CATEGORIES = {"document", "code"}


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
                "status": status,
                "attempt_count": 0,
                "last_error": last_error,
                "started_at": None,
                "completed_at": completed_at,
                "processed_by_batch": None,
                "refresh_checkpoint_id": None,
            }
        )

    progress = {
        "job_id": job_id,
        "src_root": manifest["src_root"],
        "dst_root": manifest["dst_root"],
        "batch_size": batch_size,
        "refresh_every_batches": refresh_every_batches,
        "refresh_every_files": refresh_every_files,
        "started_at": created_at,
        "updated_at": created_at,
        "summary": {},
        "active_batch": None,
        "batch_history": [],
        "refresh_checkpoints": [],
        "items": items,
    }
    progress["summary"] = summarize_progress(progress)
    return progress


def summarize_progress(progress: dict) -> dict:
    items = progress.get("items", [])
    llm_items = [item for item in items if item["category"] in LLM_CATEGORIES]
    all_batches = sorted({item["batch_index"] for item in llm_items if item.get("batch_index")})
    pending_batches = sorted(
        {
            item["batch_index"]
            for item in llm_items
            if item["status"] == STATUS_PENDING and item.get("batch_index") is not None
        }
    )

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
        "next_pending_batch_index": pending_batches[0] if pending_batches else None,
        "active_batch_index": None,
        "refresh_checkpoint_count": len(progress.get("refresh_checkpoints", [])),
    }

    active_batch = progress.get("active_batch")
    if active_batch:
        summary["active_batch_index"] = active_batch.get("batch_index")

    return summary


def checkout_next_batch(progress: dict, retry_failed: bool = False) -> dict:
    active_items = _current_in_progress_items(progress)
    if active_items:
        batch_index = active_items[0]["batch_index"]
        checkpoint_id = active_items[0].get("refresh_checkpoint_id")
        checkpoint = _find_refresh_checkpoint(progress, checkpoint_id)
        progress["summary"] = summarize_progress(progress)
        progress["updated_at"] = utc_timestamp()
        return _build_batch_payload(progress, batch_index, active_items, checkpoint, reused=True)

    candidates = [
        item for item in progress["items"] if item["category"] in LLM_CATEGORIES and item["status"] == STATUS_PENDING
    ]
    if not candidates and retry_failed:
        candidates = [
            item for item in progress["items"] if item["category"] in LLM_CATEGORIES and item["status"] == STATUS_FAILED
        ]

    if not candidates:
        progress["active_batch"] = None
        progress["summary"] = summarize_progress(progress)
        progress["updated_at"] = utc_timestamp()
        return {
            "batch_index": None,
            "refresh_checkpoint_id": None,
            "required_reads": [],
            "items": [],
            "reused_in_progress_batch": False,
            "status": "complete",
        }

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
        "file_ids": [item["file_id"] for item in batch_items],
    }
    progress["updated_at"] = started_at
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
    progress["summary"] = summarize_progress(progress)
    return item


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


def _initial_status(manifest_item: dict) -> str:
    if manifest_item.get("copy_failure"):
        return STATUS_FAILED
    if manifest_item["category"] in LLM_CATEGORIES:
        return STATUS_PENDING
    return STATUS_SKIPPED


def _count_by_status(items: list[dict], status: str) -> int:
    return sum(1 for item in items if item["status"] == status)


def _current_in_progress_items(progress: dict) -> list[dict]:
    items = [item for item in progress["items"] if item["status"] == STATUS_IN_PROGRESS]
    items.sort(key=lambda item: (item.get("batch_index") or 0, item["file_id"]))
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
        "reused_in_progress_batch": reused,
        "status": "ready",
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
            "file_ids": active_batch.get("file_ids", []),
        }
    )
    progress["active_batch"] = None


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
