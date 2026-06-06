from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess


@dataclass
class PytestResult:
    passed: bool
    returncode: int
    timeout: bool
    stdout: str
    stderr: str


def run_pytest(
    repo_path: Path,
    pytest_cmd: str,
    timeout_s: int,
    log_path: Path,
) -> PytestResult:
    repo_path = repo_path.resolve()
    log_path = log_path.resolve()

    try:
        cmd = shlex.split(pytest_cmd)
        proc = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        log_path.write_text(
            _format_pytest_log(pytest_cmd, proc.returncode, False, stdout, stderr),
            encoding="utf-8",
        )
        return PytestResult(
            passed=(proc.returncode == 0),
            returncode=proc.returncode,
            timeout=False,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
        stderr = (e.stderr or "") if isinstance(e.stderr, str) else ""
        log_path.write_text(
            _format_pytest_log(pytest_cmd, returncode=124, timeout=True, stdout=stdout, stderr=stderr),
            encoding="utf-8",
        )
        return PytestResult(
            passed=False,
            returncode=124,
            timeout=True,
            stdout=stdout,
            stderr=stderr,
        )
    except Exception as e:  # basic guardrail; keep harness running
        msg = f"Failed to run pytest: {type(e).__name__}: {e}\n"
        log_path.write_text(msg, encoding="utf-8")
        return PytestResult(
            passed=False,
            returncode=1,
            timeout=False,
            stdout="",
            stderr=msg,
        )


def _format_pytest_log(
    pytest_cmd: str,
    returncode: int,
    timeout: bool,
    stdout: str,
    stderr: str,
) -> str:
    header = [
        f"pytest_cmd: {pytest_cmd}",
        f"returncode: {returncode}",
        f"timeout: {timeout}",
        "",
        "===== STDOUT =====",
        stdout.rstrip("\n"),
        "",
        "===== STDERR =====",
        stderr.rstrip("\n"),
        "",
    ]
    return "\n".join(header)

