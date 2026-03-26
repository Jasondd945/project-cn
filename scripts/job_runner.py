#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

import planning
import verify_outputs


OUTPUT_DIR_NAME = "AAA-translate-output"
JOB_INFO_FILE = "translate-job.json"
MANIFEST_FILE = "translate-manifest.json"
VERIFY_REPORT_FILE = "translate-verify-report.json"
TEXT_REPORT_FILE = "translate-final-report.txt"


def start_job(
    src_root: str | Path,
    dst_root: str | Path | None = None,
    exclude_dirs: list[str] | None = None,
    replace_existing: bool = True,
) -> dict:
    manifest = planning.prepare_project_copy(
        src_root,
        dst_root=dst_root,
        replace_existing=replace_existing,
        exclude_dirs=exclude_dirs,
    )

    job_id = _new_job_id()
    job_dir = _ensure_output_dir(Path(manifest["dst_root"]))
    _remove_stale_internal_outputs(job_dir)

    manifest_path = job_dir / MANIFEST_FILE
    planning.write_json(manifest_path, manifest)

    job_info = {
        "job_id": job_id,
        "job_dir": str(job_dir),
        "manifest_path": str(manifest_path),
        "src_root": manifest["src_root"],
        "dst_root": manifest["dst_root"],
        "summary": manifest["summary"],
        "created_at": _utc_timestamp(),
    }
    _write_json(job_dir / JOB_INFO_FILE, job_info)
    return job_info


def build_job_report(job_ref: str | Path) -> dict:
    job_dir = resolve_job_dir(job_ref)
    manifest_path = job_dir / MANIFEST_FILE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    job_info = _read_json_if_exists(job_dir / JOB_INFO_FILE)
    verify_report = verify_outputs.build_report(manifest)

    final_report = {
        "job_id": job_info.get("job_id", job_dir.name),
        "job_dir": str(job_dir),
        "src_root": manifest["src_root"],
        "dst_root": manifest["dst_root"],
        "summary": manifest.get("summary", {}),
        "generated": verify_report["generated"],
        "missing_original_copies": verify_report["missing_original_copies"],
        "missing_cn_files": verify_report["missing_cn_files"],
        "status": "ok"
        if not verify_report["missing_original_copies"] and not verify_report["missing_cn_files"]
        else "incomplete",
        "reported_at": _utc_timestamp(),
    }

    _write_json(job_dir / VERIFY_REPORT_FILE, final_report)
    (job_dir / TEXT_REPORT_FILE).write_text(_format_text_report(final_report), encoding="utf-8")
    return final_report


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


def _format_text_report(report: dict) -> str:
    summary = report["summary"]
    generated = report["generated"]
    root_mode = "严格使用用户给定路径" if summary.get("root_interpretation", "exact-user-path") == "exact-user-path" else summary.get("root_interpretation")
    wrapper_detected = "是" if summary.get("single_child_wrapper_detected", False) else "否"
    status_text = {
        "ok": "完成",
        "incomplete": "未完成",
    }.get(report["status"], report["status"])
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
        "工作量摘要：",
        f"- 文件总数：{summary.get('total_files', 0)}",
        f"- 文档文件数：{summary.get('document_files', 0)}",
        f"- 代码文件数：{summary.get('code_files', 0)}",
        f"- 其他文件数：{summary.get('other_files', 0)}",
        f"- 预计输入 token：{summary.get('estimated_input_tokens', 0)}",
        f"- 预计总 token 范围：{summary.get('estimated_tokens_low', 0)}-{summary.get('estimated_tokens_high', 0)}",
        "",
        "产出结果：",
        f"- 已确认存在的原始复制文件：{generated.get('original_copied_files_present', 0)}",
        f"- 文档 -CN 文件数：{generated.get('document_cn_files', 0)}",
        f"- 代码 -CN 文件数：{generated.get('code_cn_files', 0)}",
        f"- 缺失的原始复制文件数：{len(report.get('missing_original_copies', []))}",
        f"- 缺失的 -CN 文件数：{len(report.get('missing_cn_files', []))}",
    ]

    if report.get("missing_cn_files"):
        lines.extend(["", "缺失的 -CN 文件："])
        for item in report["missing_cn_files"]:
            lines.append(f"- {item['rel_path']} -> {item['expected_cn_file']}")

    if report.get("missing_original_copies"):
        lines.extend(["", "缺失的原始复制文件："])
        for item in report["missing_original_copies"]:
            lines.append(f"- {item}")

    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_if_exists(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_output_dir(dst_root: Path) -> Path:
    output_dir = dst_root / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _remove_stale_internal_outputs(job_dir: Path) -> None:
    for filename in (JOB_INFO_FILE, MANIFEST_FILE, VERIFY_REPORT_FILE, TEXT_REPORT_FILE):
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
    parser = argparse.ArgumentParser(description="以结果为导向运行项目翻译工作流。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser(
        "start",
        help="启动翻译作业，并把内部产物放进 A-CN/AAA-translate-output。",
    )
    start_parser.add_argument("src_root", help="原项目根目录")
    start_parser.add_argument("--dst-root", help="可选目标目录，默认使用 A-CN")
    start_parser.add_argument("--exclude-dir", action="append", default=None, help="额外排除目录名，可重复传入")
    start_parser.add_argument("--keep-existing", action="store_true", help="保留已有目标目录，不先删除旧的 A-CN")

    report_parser = subparsers.add_parser(
        "report",
        help="基于 A-CN/AAA-translate-output 中的 manifest 生成最终校验报告。",
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
        else:
            payload = build_job_report(args.job_ref)
            if args.output:
                output_path = Path(args.output).expanduser().resolve()
                _reject_output_inside_source_root(output_path, payload["src_root"])
                _write_json(output_path, payload)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    raise SystemExit(main())
