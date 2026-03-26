#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import job_state


def load_manifest(path: str | Path) -> dict:
    manifest_path = Path(path).expanduser().resolve()
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def build_report(manifest: dict, progress: dict | None = None, originals_lock: dict | None = None) -> dict:
    generated = {
        "document_cn_files": 0,
        "code_cn_files": 0,
        "original_copied_files_present": 0,
    }
    missing_cn_files = []
    missing_original_copies = []

    for item in manifest.get("items", []):
        copied_file = item.get("copied_file")
        cn_file = item.get("cn_file")
        category = item.get("category")

        if copied_file and Path(copied_file).exists():
            generated["original_copied_files_present"] += 1
        elif copied_file:
            missing_original_copies.append(
                {
                    "file_id": item.get("file_id"),
                    "rel_path": item["rel_path"],
                    "expected_original_file": copied_file,
                }
            )

        if job_state.should_expect_cn_file(progress, item):
            if cn_file and Path(cn_file).exists():
                generated[f"{category}_cn_files"] += 1
            else:
                missing_cn_files.append(
                    {
                        "file_id": item.get("file_id"),
                        "rel_path": item["rel_path"],
                        "expected_cn_file": cn_file,
                        "category": category,
                    }
                )

    progress_summary = job_state.summarize_progress(progress) if progress else {}
    integrity_report = (
        job_state.verify_originals_lock(originals_lock)
        if originals_lock
        else {
            "modified_source_files": [],
            "modified_original_copies": [],
            "source_integrity_ok": True,
            "copied_original_integrity_ok": True,
        }
    )

    return {
        "src_root": manifest.get("src_root"),
        "dst_root": manifest.get("dst_root"),
        "summary": manifest.get("summary", {}),
        "progress_summary": progress_summary,
        "generated": generated,
        "missing_original_copies": missing_original_copies,
        "missing_cn_files": missing_cn_files,
        "modified_source_files": integrity_report["modified_source_files"],
        "modified_original_copies": integrity_report["modified_original_copies"],
        "source_integrity_ok": integrity_report["source_integrity_ok"],
        "copied_original_integrity_ok": integrity_report["copied_original_integrity_ok"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 A-CN 中预期的 -CN 文件和原文件完整性。")
    parser.add_argument("manifest", help="translate-manifest.json 路径")
    parser.add_argument("--progress", help="translate-progress.json 路径")
    parser.add_argument("--lock", help="translate-originals-lock.json 路径")
    parser.add_argument("--output", help="把校验报告写入文件")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    progress = job_state.load_json(args.progress) if args.progress else None
    originals_lock = job_state.load_json(args.lock) if args.lock else None
    report = build_report(manifest, progress=progress, originals_lock=originals_lock)
    payload = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")

    print(payload)

    has_findings = bool(
        report["missing_original_copies"]
        or report["missing_cn_files"]
        or report["modified_source_files"]
        or report["modified_original_copies"]
    )
    return 1 if has_findings else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    raise SystemExit(main())
