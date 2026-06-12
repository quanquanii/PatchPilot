from __future__ import annotations

import re
import subprocess
from pathlib import Path

_PAT_DIFF_HDR = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)


def _parse_changed_files(patch_text: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for m in _PAT_DIFF_HDR.finditer(patch_text):
        p = m.group(2)
        if p and p not in seen and p != "/dev/null":
            files.append(p)
            seen.add(p)
    return files


def _count_patch_lines(patch_text: str) -> int:
    count = 0
    for line in patch_text.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            count += 1
    return count


def _is_test_file(path: str) -> bool:
    p = Path(path)
    return (
        p.name.startswith("test_")
        or p.name.endswith("_test.py")
        or any(part in ("tests", "test") for part in p.parts[:-1])
    )


def run_patch_guard(
    patch_text: str,
    policy: dict,
    repo_path: Path | None = None,
    patch_file: Path | None = None,
) -> dict:
    """Validate patch_text against all policy rules.

    Returns a structured result dict:
    {
        "passed": bool,
        "changed_files": [...],
        "patch_lines": int,
        "checks": [{"name": str, "passed": bool, "reason": str|null}, ...],
        "git_apply_check": {"required": bool, "passed": bool, "stderr": str},
    }
    """
    checks: list[dict] = []
    all_passed = True

    def add(name: str, passed: bool, reason: str | None = None) -> None:
        nonlocal all_passed
        checks.append({"name": name, "passed": passed, "reason": reason})
        if not passed:
            all_passed = False

    # 1. Unified diff format
    is_unified = patch_text.strip().startswith("diff --git")
    add("unified_diff", is_unified, None if is_unified else "Patch must start with 'diff --git'")

    # 2. Parseable file headers
    changed_files = _parse_changed_files(patch_text) if is_unified else []
    has_files = bool(changed_files)
    add("parseable_files", has_files, None if has_files else "No file paths found in patch headers")

    # 3. No absolute paths
    abs_hit = next((f for f in changed_files if f.startswith("/")), None)
    add("no_absolute_paths", abs_hit is None, f"Absolute path in patch: {abs_hit}" if abs_hit else None)

    # 4. No path traversal
    trav_hit = next((f for f in changed_files if "../" in f), None)
    add("no_path_traversal", trav_hit is None, f"Path traversal detected: {trav_hit}" if trav_hit else None)

    # 5. Allowed file extensions
    allowed_exts = set(policy.get("allowed_file_extensions", [".py"]))
    ext_hits = [f for f in changed_files if Path(f).suffix not in allowed_exts]
    add(
        "allowed_extensions",
        not ext_hits,
        f"Disallowed extension(s): {ext_hits}" if ext_hits else None,
    )

    # 6. Blocked paths
    blocked = policy.get("blocked_paths", [])
    blocked_hits = [f for f in changed_files if any(b in f for b in blocked)]
    add(
        "no_blocked_paths",
        not blocked_hits,
        f"Blocked path(s): {blocked_hits}" if blocked_hits else None,
    )

    # 7. Protected paths
    protected = policy.get("protected_paths", [])
    protected_hits = [f for f in changed_files if any(f.startswith(pp) for pp in protected)]
    add(
        "no_protected_paths",
        not protected_hits,
        f"Protected path(s): {protected_hits}" if protected_hits else None,
    )

    # 8. Test file modification
    allow_tests = policy.get("allow_test_modification", False)
    test_hits = [f for f in changed_files if _is_test_file(f)]
    if not allow_tests and test_hits:
        add("no_test_modification", False, f"Test file modification not allowed: {test_hits}")
    else:
        add("no_test_modification", True, None)

    # 9. Max changed files
    max_files = int(policy.get("max_changed_files", 3))
    n_files = len(changed_files)
    add(
        "max_changed_files",
        n_files <= max_files,
        f"Too many changed files: {n_files} > {max_files}" if n_files > max_files else None,
    )

    # 10. Max patch lines
    max_lines = int(policy.get("max_patch_lines", 120))
    patch_lines = _count_patch_lines(patch_text)
    add(
        "max_patch_lines",
        patch_lines <= max_lines,
        f"Patch too large: {patch_lines} lines > {max_lines}" if patch_lines > max_lines else None,
    )

    # 11. git apply --check (conditional on policy)
    git_apply_check: dict = {"required": False, "passed": True, "stderr": ""}
    if policy.get("require_git_apply_check", True) and repo_path is not None and patch_file is not None:
        git_apply_check["required"] = True
        try:
            cmd = ["git", "-C", str(repo_path), "apply", "--check", str(patch_file)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            git_apply_check["passed"] = proc.returncode == 0
            git_apply_check["stderr"] = proc.stderr.strip()
            if not git_apply_check["passed"]:
                all_passed = False
        except Exception as e:
            git_apply_check["passed"] = False
            git_apply_check["stderr"] = f"{type(e).__name__}: {e}"
            all_passed = False

    return {
        "passed": all_passed,
        "changed_files": changed_files,
        "patch_lines": patch_lines if is_unified else 0,
        "checks": checks,
        "git_apply_check": git_apply_check,
    }
