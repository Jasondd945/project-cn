from __future__ import annotations

import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from classification import classify_file


CANDIDATE_ENCODINGS = (
    "utf-8-sig",
    "utf-8",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "gb18030",
    "gbk",
    "big5",
)

FULL_READ_LIMIT_BYTES = 2 * 1024 * 1024
SAMPLE_READ_BYTES = 256 * 1024


def collect_text_metrics(path: str | Path) -> dict:
    file_path = Path(path)
    size_bytes = file_path.stat().st_size
    category = classify_file(file_path)

    if category == "other":
        return {
            "size_bytes": size_bytes,
            "estimated_chars": 0,
            "encoding": None,
            "read_error": None,
            "sample_based": False,
        }

    sample_size = size_bytes if size_bytes <= FULL_READ_LIMIT_BYTES else SAMPLE_READ_BYTES

    try:
        with file_path.open("rb") as file_obj:
            raw = file_obj.read(sample_size)
    except OSError as exc:
        return {
            "size_bytes": size_bytes,
            "estimated_chars": max(1, math.ceil(size_bytes * 0.6)),
            "encoding": None,
            "read_error": str(exc),
            "sample_based": size_bytes > FULL_READ_LIMIT_BYTES,
        }

    for encoding in CANDIDATE_ENCODINGS:
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            continue

        if size_bytes <= FULL_READ_LIMIT_BYTES or len(raw) == 0:
            estimated_chars = len(decoded)
            sample_based = False
        else:
            estimated_chars = math.ceil(len(decoded) / len(raw) * size_bytes)
            sample_based = True

        return {
            "size_bytes": size_bytes,
            "estimated_chars": estimated_chars,
            "encoding": encoding,
            "read_error": None,
            "sample_based": sample_based,
        }

    return {
        "size_bytes": size_bytes,
        "estimated_chars": max(1, math.ceil(size_bytes * 0.6)),
        "encoding": None,
        "read_error": "unable to decode with built-in fallback encodings",
        "sample_based": size_bytes > FULL_READ_LIMIT_BYTES,
    }


def estimate_input_tokens(char_count: int) -> int:
    return math.ceil(char_count / 4) if char_count > 0 else 0
