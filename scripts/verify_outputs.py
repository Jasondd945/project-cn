#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def load_manifest(path: str | Path) -> dict:
    manifest_path = Path(path).expanduser().resolve()
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def build_report(manifest: dict) -> dict:
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
            missing_original_copies.append(item["rel_path"])

        if category == "document":
            if cn_file and Path(cn_file).exists():
                generated["document_cn_files"] += 1
            else:
                missing_cn_files.append(
                    {"rel_path": item["rel_path"], "expected_cn_file": cn_file, "category": category}
                )
        elif category == "code":
            if cn_file and Path(cn_file).exists():
                generated["code_cn_files"] += 1
            else:
                missing_cn_files.append(
                    {"rel_path": item["rel_path"], "expected_cn_file": cn_file, "category": category}
                )

    return {
        "src_root": manifest.get("src_root"),
        "dst_root": manifest.get("dst_root"),
        "summary": manifest.get("summary", {}),
        "generated": generated,
        "missing_original_copies": missing_original_copies,
        "missing_cn_files": missing_cn_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 A-CN 中预期的 -CN 文件是否已经生成。")
    parser.add_argument("manifest", help="prepare_job.py prepare 输出的清单 JSON")
    parser.add_argument("--output", help="把校验报告写入文件")
    args = parser.parse_args()

    report = build_report(load_manifest(args.manifest))
    payload = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")

    print(payload)

    has_missing = bool(report["missing_original_copies"] or report["missing_cn_files"])
    return 1 if has_missing else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    raise SystemExit(main())
