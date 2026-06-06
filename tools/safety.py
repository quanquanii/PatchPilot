from __future__ import annotations

import re
from pathlib import Path

_FORBIDDEN_SUBSTRINGS = ("../", ".env", "id_rsa", "secret", "token", "credentials")
_ALLOWED_EXTENSIONS = frozenset({".py"})
_PAT_DIFF_HDR = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)


def _extract_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for m in _PAT_DIFF_HDR.finditer(patch_text):
        for p in (m.group(1), m.group(2)):
            if p and p not in seen and p != "/dev/null":
                paths.append(p)
                seen.add(p)
    return paths


def extract_modified_files(patch_text: str) -> list[str]:
    """Return the b/ (new-version) paths from all diff --git headers."""
    files: list[str] = []
    seen: set[str] = set()
    for m in _PAT_DIFF_HDR.finditer(patch_text):
        p = m.group(2)
        if p and p not in seen and p != "/dev/null":
            files.append(p)
            seen.add(p)
    return files


def validate_patch(patch_text: str) -> tuple[bool, str | None]:
    """Return (ok, error_message).  None means the patch passed all checks."""
    if not patch_text.strip().startswith("diff --git"):
        return False, "Patch must start with 'diff --git'"

    paths = _extract_paths(patch_text)
    if not paths:
        return False, "No file paths found in patch headers"

    for path in paths:
        if path.startswith("/"):
            return False, f"Invalid patch path (absolute): {path}"
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            if forbidden in path:
                return False, f"Invalid patch path: {path!r} contains {forbidden!r}"
        ext = Path(path).suffix
        if ext not in _ALLOWED_EXTENSIONS:
            return False, f"Only .py files are allowed, got: {path!r}"
        p = Path(path)
        is_test = p.name.startswith("test_") or any(
            part in ("tests", "test") for part in p.parts[:-1]
        )
        if is_test:
            return False, "Test file modification is not allowed"

    return True, None
