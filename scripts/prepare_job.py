#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import planning


def _reject_output_inside_source_root(output_path: Path, src_root: Path) -> None:
    try:
        output_path.relative_to(src_root)
    except ValueError:
        return
    raise ValueError(
        f"output path must not be inside source root: {output_path}. "
        "Write extra artifacts to the sibling A-CN/AAA-translate-output directory instead."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="评估项目工作量，或复制项目到 A-CN 并生成 CN 副本清单。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    assess_parser = subparsers.add_parser("assess", help="只评估，不复制目录")
    assess_parser.add_argument("src_root", help="原项目根目录")
    assess_parser.add_argument("--dst-root", help="可选目标目录，默认使用 A-CN")
    assess_parser.add_argument(
        "--exclude-dir",
        action="append",
        default=None,
        help="额外排除的目录名，可重复传入",
    )
    assess_parser.add_argument("--output", help="把评估 JSON 写入文件")

    prepare_parser = subparsers.add_parser("prepare", help="评估后复制目录并输出清单")
    prepare_parser.add_argument("src_root", help="原项目根目录")
    prepare_parser.add_argument("--dst-root", help="可选目标目录，默认使用 A-CN")
    prepare_parser.add_argument(
        "--exclude-dir",
        action="append",
        default=None,
        help="额外排除的目录名，可重复传入",
    )
    prepare_parser.add_argument("--output", help="把清单 JSON 写入文件")
    prepare_parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="保留已有目标目录，不先删除旧的 A-CN",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    src_root = Path(args.src_root).expanduser().resolve()

    try:
        if args.command == "assess":
            payload = planning.assess_project(
                src_root,
                dst_root=args.dst_root,
                exclude_dirs=args.exclude_dir,
            )
        else:
            payload = planning.prepare_project_copy(
                src_root,
                dst_root=args.dst_root,
                replace_existing=not args.keep_existing,
                exclude_dirs=args.exclude_dir,
            )

        if args.output:
            output_path = Path(args.output).expanduser().resolve()
            _reject_output_inside_source_root(output_path, src_root)
            planning.write_json(output_path, payload)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    raise SystemExit(main())
