from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True


DOCUMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".adoc",
    ".asciidoc",
    ".text",
    ".mdx",
}

DOCUMENT_BASENAMES = {
    "readme",
    "changelog",
    "contributing",
    "license",
    "notes",
    "guide",
    "faq",
    "manual",
}

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".cc",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".kts",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".sql",
    ".lua",
    ".r",
    ".dart",
    ".groovy",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".vue",
    ".svelte",
}

CODE_BASENAMES = {
    "dockerfile",
    "makefile",
    "cmakelists.txt",
    "rakefile",
    "build.gradle",
}

OTHER_EXTENSIONS = {
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".properties",
    ".xml",
    ".plist",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".bmp",
    ".tiff",
    ".mp3",
    ".wav",
    ".ogg",
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".bin",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".wasm",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
}


def classify_file(path: str | Path) -> str:
    file_path = Path(path)
    lower_name = file_path.name.lower()

    if _matches_name(lower_name, DOCUMENT_EXTENSIONS):
        return "document"
    if lower_name in DOCUMENT_BASENAMES and not file_path.suffix:
        return "document"

    if _matches_name(lower_name, CODE_EXTENSIONS):
        return "code"
    if lower_name in CODE_BASENAMES:
        return "code"
    if _has_shebang(file_path):
        return "code"

    if _matches_name(lower_name, OTHER_EXTENSIONS):
        return "other"
    if _looks_binary(file_path):
        return "other"

    return "other"


def build_cn_filename(path: str | Path) -> str:
    file_path = Path(path)
    if file_path.suffix:
        return f"{file_path.stem}-CN{file_path.suffix}"
    return f"{file_path.name}-CN"


def _matches_name(lower_name: str, suffixes: set[str]) -> bool:
    return any(lower_name.endswith(suffix) for suffix in suffixes)


def _has_shebang(path: Path) -> bool:
    try:
        with path.open("rb") as file_obj:
            first_line = file_obj.readline(256)
    except OSError:
        return False

    return first_line.startswith(b"#!")


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as file_obj:
            chunk = file_obj.read(8192)
    except OSError:
        return True

    if b"\x00" in chunk:
        return True
    if not chunk:
        return False

    printable = sum(
        1
        for byte in chunk
        if byte in b"\n\r\t\f\b" or 32 <= byte <= 126 or byte >= 128
    )
    return printable / len(chunk) < 0.75
