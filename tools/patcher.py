from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass
class PatchApplyResult:
    ok: bool
    error: str | None
    stdout: str
    stderr: str
    returncode: int


def check_patch(repo_path: Path, patch_path: Path) -> PatchApplyResult:
    return _git_apply(repo_path=repo_path, patch_path=patch_path, check_only=True)


def apply_patch(repo_path: Path, patch_path: Path) -> PatchApplyResult:
    return _git_apply(repo_path=repo_path, patch_path=patch_path, check_only=False)


def _git_apply(repo_path: Path, patch_path: Path, check_only: bool) -> PatchApplyResult:
    repo_path = repo_path.resolve()
    patch_path = patch_path.resolve()

    if not repo_path.exists():
        return PatchApplyResult(
            ok=False,
            error=f"Repo path does not exist: {repo_path}",
            stdout="",
            stderr="",
            returncode=2,
        )
    if not patch_path.exists():
        return PatchApplyResult(
            ok=False,
            error=f"Patch file does not exist: {patch_path}",
            stdout="",
            stderr="",
            returncode=2,
        )

    cmd = ["git", "-C", str(repo_path), "apply"]
    if check_only:
        cmd.append("--check")
    cmd.append(str(patch_path))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        ok = proc.returncode == 0
        err = None if ok else (stderr.strip() or stdout.strip() or "git apply failed")
        return PatchApplyResult(
            ok=ok,
            error=err,
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode,
        )
    except Exception as e:
        msg = f"Failed to run git apply: {type(e).__name__}: {e}"
        return PatchApplyResult(
            ok=False,
            error=msg,
            stdout="",
            stderr=msg,
            returncode=1,
        )

