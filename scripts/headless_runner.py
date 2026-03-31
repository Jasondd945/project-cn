from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

import job_runner
import job_state


HEADLESS_RUN_SUMMARY_FILE = "headless-run-summary.json"


def run_job(
    job_ref: str | Path,
    max_batches: int = 1,
    retry_failed: bool = False,
    exclude_dirs: list[str] | None = None,
    replace_existing: bool = True,
    batch_size: int = job_state.DEFAULT_BATCH_SIZE,
) -> dict:
    if max_batches < 1:
        raise ValueError("max_batches must be >= 1")

    initial = _start_or_resume(
        job_ref,
        retry_failed=retry_failed,
        exclude_dirs=exclude_dirs,
        replace_existing=replace_existing,
        batch_size=batch_size,
    )
    next_batch = initial["next_batch"]
    batch_status = next_batch.get("status")
    dispatched_batches: list[int] = []
    processed_batches = 0

    if batch_status == "ready":
        batch_index = next_batch.get("batch_index")
        if batch_index is not None:
            dispatched_batches.append(batch_index)
        processed_batches = 1
        stop_reason = "max_batches_reached"
    elif batch_status == "awaiting_scope_decision":
        stop_reason = "awaiting_scope_decision"
    elif batch_status == "blocked_by_batch_verification":
        stop_reason = "blocked_by_batch_verification"
    elif batch_status == "stale_active_batch":
        stop_reason = "stale_active_batch"
    elif batch_status == "complete":
        stop_reason = "complete"
    else:
        stop_reason = batch_status or "unknown"

    status_payload = job_runner.get_job_status(initial["job_dir"])
    payload = {
        "job_id": initial.get("job_id", status_payload.get("job_id")),
        "job_dir": initial["job_dir"],
        "src_root": status_payload.get("src_root") or initial.get("src_root"),
        "dst_root": status_payload.get("dst_root") or initial.get("dst_root"),
        "started_new_job": initial["started_new_job"],
        "max_batches": max_batches,
        "processed_batches": processed_batches,
        "dispatched_batches": dispatched_batches,
        "stop_reason": stop_reason,
        "next_batch": next_batch,
        "summary": status_payload.get("summary", {}),
        "preflight_summary": status_payload.get("preflight_summary", {}),
        "context_usage_hint": status_payload.get("context_usage_hint", {}),
        "watchdog": status_payload.get("watchdog", {}),
        "project_profile": status_payload.get("project_profile", {}),
        "project_profile_summary": status_payload.get("project_profile_summary"),
        "user_message": status_payload.get("user_message", initial.get("user_message")),
        "internal_reason": status_payload.get("internal_reason", initial.get("internal_reason")),
        "operator_advice": status_payload.get("operator_advice", initial.get("operator_advice")),
        "reported_at": _utc_timestamp(),
    }

    job_dir = Path(initial["job_dir"])
    job_state.atomic_write_json(job_dir / HEADLESS_RUN_SUMMARY_FILE, payload)
    return payload


def _start_or_resume(
    job_ref: str | Path,
    retry_failed: bool,
    exclude_dirs: list[str] | None,
    replace_existing: bool,
    batch_size: int,
) -> dict:
    candidate = Path(job_ref).expanduser()
    if _has_existing_job(candidate):
        resumed = job_runner.resume_job(candidate, retry_failed=retry_failed)
        return {
            **resumed,
            "started_new_job": False,
        }

    started = job_runner.start_job(
        candidate,
        exclude_dirs=exclude_dirs,
        replace_existing=replace_existing,
        batch_size=batch_size,
    )
    return {
        **started,
        "started_new_job": True,
    }


def _has_existing_job(candidate: Path) -> bool:
    try:
        job_dir = job_state.resolve_job_dir(candidate)
    except FileNotFoundError:
        return False

    return (job_dir / job_state.PROGRESS_FILE).is_file() and (job_dir / job_state.MANIFEST_FILE).is_file()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="有界的 headless 项目翻译调度器。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="对现有作业做一次有界调度；若尚未建作业，则先 start 再返回首批。",
    )
    run_parser.add_argument("job_ref", help="源项目目录、A-CN 目录或 AAA-translate-output 目录")
    run_parser.add_argument("--max-batches", type=int, default=1, help="本次最多发放多少批次，默认 1")
    run_parser.add_argument("--retry-failed", action="store_true", help="允许优先重试失败项")
    run_parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        dest="exclude_dirs",
        help="start 新作业时额外排除的目录名，可重复传入",
    )
    run_parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="start 新作业时若目标目录已存在则保留而不是默认重建",
    )
    run_parser.add_argument("--batch-size", type=int, default=job_state.DEFAULT_BATCH_SIZE)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        payload = run_job(
            args.job_ref,
            max_batches=args.max_batches,
            retry_failed=args.retry_failed,
            exclude_dirs=args.exclude_dirs,
            replace_existing=not args.keep_existing,
            batch_size=args.batch_size,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    parser.error(f"unsupported command: {args.command}")
    return 2


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    raise SystemExit(main())
