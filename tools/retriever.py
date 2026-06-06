from __future__ import annotations

import re
from pathlib import Path

_EXCLUDE_DIRS = frozenset({".venv", "venv", "__pycache__", ".pytest_cache", "site-packages", ".git"})

# FAILED tests/test_calc.py::test_add
_PAT_FAILED = re.compile(r"^FAILED\s+([\w/\\.\-]+\.py)::", re.MULTILINE)
# File "calculator.py", line 2  /  File "/abs/path/calc.py", line 10
_PAT_FILE_QUOTED = re.compile(r'File\s+"([^"]+\.py)"')
# tests/test_calc.py:4  (also catches absolute paths)
_PAT_PATH_LINENO = re.compile(r'([\w./\\-]+\.py):\d+')


def _is_excluded(rel: Path) -> bool:
    return any(part in _EXCLUDE_DIRS for part in rel.parts)


def _is_test_file(p: Path) -> bool:
    return p.name.startswith("test_") or any(
        part in ("tests", "test") for part in p.parts[:-1]
    )


def _to_rel(raw: str, repo: Path) -> Path | None:
    p = Path(raw)
    if p.is_absolute():
        try:
            rel = p.relative_to(repo)
        except ValueError:
            return None
    else:
        rel = p
        # Guard against ../../ escapes
        abs_path = (repo / rel).resolve()
        try:
            abs_path.relative_to(repo)
        except ValueError:
            return None
    if rel.suffix != ".py":
        return None
    if _is_excluded(rel):
        return None
    if not (repo / rel).exists():
        return None
    return rel


def _extract_keywords(log: str) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()

    for m in re.finditer(r"FAILED\s+[\w/\\.\-]+\.py::(\w+)", log):
        name = m.group(1)
        kw = name[5:] if name.startswith("test_") else name
        if len(kw) >= 3 and kw not in seen:
            keywords.append(kw)
            seen.add(kw)

    for m in re.finditer(r"\bassert\s+(\w+)\s*\(", log):
        kw = m.group(1)
        if len(kw) >= 3 and kw not in seen:
            keywords.append(kw)
            seen.add(kw)

    return keywords


def _keyword_search(
    repo: Path,
    keywords: list[str],
    already_found: set[str],
) -> list[Path]:
    if not keywords:
        return []

    candidates: list[tuple[bool, int, Path]] = []  # (is_test, -matches, rel)

    for py_file in sorted(repo.rglob("*.py")):
        try:
            rel = py_file.relative_to(repo)
        except ValueError:
            continue
        if _is_excluded(rel):
            continue
        if str(rel) in already_found:
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match_count = sum(1 for kw in keywords if kw in content)
        if match_count > 0:
            candidates.append((_is_test_file(rel), -match_count, rel))

    candidates.sort()
    return [rel for _, _, rel in candidates]


def retrieve_files_from_pytest_log(
    repo_path: Path,
    pytest_log: str,
    max_files: int = 8,
) -> list[Path]:
    repo = repo_path.resolve()

    failed: list[Path] = []
    traceback_files: list[Path] = []
    other_files: list[Path] = []
    seen_f: set[str] = set()
    seen_t: set[str] = set()
    seen_o: set[str] = set()

    def _add(dest: list[Path], seen: set[str], rel: Path) -> None:
        key = str(rel)
        if key not in seen:
            dest.append(rel)
            seen.add(key)

    for m in _PAT_FAILED.finditer(pytest_log):
        rel = _to_rel(m.group(1), repo)
        if rel:
            _add(failed, seen_f, rel)

    for m in _PAT_FILE_QUOTED.finditer(pytest_log):
        rel = _to_rel(m.group(1), repo)
        if rel:
            _add(traceback_files, seen_t, rel)

    for m in _PAT_PATH_LINENO.finditer(pytest_log):
        rel = _to_rel(m.group(1), repo)
        if rel:
            _add(other_files, seen_o, rel)

    result: list[Path] = []
    all_seen: set[str] = set()

    def _merge(source: list[Path]) -> None:
        for p in source:
            key = str(p)
            if key not in all_seen:
                result.append(p)
                all_seen.add(key)

    _merge(failed)
    _merge(traceback_files)
    _merge(other_files)

    if len(result) >= max_files:
        return result[:max_files]

    # Supplement: if all found files are test files, search for source files by keyword
    if result and all(_is_test_file(p) for p in result):
        keywords = _extract_keywords(pytest_log)
        for p in _keyword_search(repo, keywords, all_seen):
            key = str(p)
            if key not in all_seen:
                result.append(p)
                all_seen.add(key)
                if len(result) >= max_files:
                    break

    return result[:max_files]
