#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import job_state

SOURCE_ROOT_POLLUTION_EXACT_NAMES = {
    "scan_result.json",
    "final_report.json",
    "manifest.json",
    "report.json",
    "headless-run-summary.json",
}

SOURCE_ROOT_POLLUTION_GLOBS = [
    "translate-*.json",
    "translate-*.txt",
]


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
            if cn_file and Path(cn_file).is_file() and Path(cn_file).stat().st_size > 0:
                generated[f"{category}_cn_files"] += 1
            else:
                missing_cn_files.append(
                    {
                        "file_id": item.get("file_id"),
                        "rel_path": item["rel_path"],
                        "expected_cn_file": cn_file,
                        "category": category,
                        "reason": (
                            "empty"
                            if cn_file and Path(cn_file).is_file() and Path(cn_file).stat().st_size == 0
                            else "missing"
                        ),
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
    source_root_pollution = _detect_source_root_pollution(manifest.get("src_root"))

    return {
        "src_root": manifest.get("src_root"),
        "dst_root": manifest.get("dst_root"),
        "summary": manifest.get("summary", {}),
        "progress_summary": progress_summary,
        "batch_verifications": progress.get("batch_verifications", []) if progress else [],
        "active_batch_verification": progress_summary.get("active_batch_verification", {}),
        "generated": generated,
        "missing_original_copies": missing_original_copies,
        "missing_cn_files": missing_cn_files,
        "modified_source_files": integrity_report["modified_source_files"],
        "modified_original_copies": integrity_report["modified_original_copies"],
        "source_integrity_ok": integrity_report["source_integrity_ok"],
        "copied_original_integrity_ok": integrity_report["copied_original_integrity_ok"],
        "source_root_pollution": source_root_pollution,
        "source_root_pollution_detected": bool(source_root_pollution),
    }


def _detect_source_root_pollution(src_root: str | Path | None) -> list[dict]:
    if not src_root:
        return []

    root = Path(src_root).expanduser().resolve()
    if not root.is_dir():
        return []

    findings = []
    for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
        if not child.is_file():
            continue

        matched_rule = None
        if child.name in SOURCE_ROOT_POLLUTION_EXACT_NAMES:
            matched_rule = child.name
        else:
            for pattern in SOURCE_ROOT_POLLUTION_GLOBS:
                if child.match(pattern):
                    matched_rule = pattern
                    break

        if matched_rule:
            findings.append(
                {
                    "rel_path": child.name,
                    "absolute_path": str(child),
                    "reason": "forbidden-runtime-artifact-in-source-root",
                    "matched_rule": matched_rule,
                }
            )

    return findings


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
        or report["source_root_pollution"]
    )
    return 1 if has_findings else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    raise SystemExit(main())
