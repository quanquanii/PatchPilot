from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from llm.client import LLMClient, create_llm_client
from llm.parser import extract_diff_from_response
from llm.prompt_builder import build_repair_prompt
from report.markdown_reporter import write_markdown_report
from report.reporter import write_report
from tools.classifier import classify_failure
from tools.patch_guard import run_patch_guard
from tools.patcher import apply_patch, check_patch
from tools.policy_loader import load_policy
from tools.retriever import retrieve_files_from_pytest_log
from tools.safety import validate_patch, extract_modified_files
from tools.tester import PytestResult, run_pytest


@dataclass
class Report:
    """Structured result for one repair run, written to report.json / report.md."""

    success: bool
    baseline_passed: bool
    final_passed: bool
    iterations: int
    max_iters: int
    pytest_cmd: str
    patch_source: str
    file_selection_mode: str | None
    selected_files: list[str] | None
    modified_files: list[str] | None
    failure_category: str | None
    baseline_log_path: str
    final_log_path: str
    warning: str | None
    history: list[dict]
    # policy fields
    policy_passed: bool
    policy_result_path: str | None
    git_apply_check_passed: bool
    human_review_required: bool
    final_decision: str          # BLOCKED | NEEDS_HUMAN_REVIEW
    decision_reasons: list[str]


def _compute_warning(baseline_passed: bool) -> str | None:
    if baseline_passed:
        return "baseline tests already passed; patch may be unnecessary"
    return None


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_repo_files(repo: Path, file_paths: list[str]) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for rel_path in file_paths:
        path = (repo / rel_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found in repo: {rel_path}")
        if repo not in path.parents and path != repo:
            raise ValueError(f"File escapes repo root: {rel_path}")
        files.append((rel_path, path.read_text(encoding="utf-8")))
    return files


def _save_policy_result(run_dir: Path, result: dict, name: str = "policy_result.json") -> Path:
    p = run_dir / name
    p.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _build_decision(
    policy_result: dict,
    patch_applied: bool,
    pytest_passed: bool,
) -> tuple[str, list[str]]:
    """Return (final_decision, decision_reasons)."""
    if not policy_result["passed"]:
        reasons: list[str] = []
        for c in policy_result["checks"]:
            if not c["passed"] and c["reason"]:
                reasons.append(c["reason"])
        gac = policy_result.get("git_apply_check", {})
        if gac.get("required") and not gac.get("passed", True):
            reasons.append(f"git apply --check failed: {gac.get('stderr', '')}")
        return "BLOCKED", reasons

    reasons = []
    if not patch_applied:
        reasons.append("Patch could not be applied")
    elif not pytest_passed:
        reasons.append("Tests failed after patch applied")
    else:
        reasons.append("AI-generated patch requires human review before merging")
    return "NEEDS_HUMAN_REVIEW", reasons


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PatchPilot harness.")
    p.add_argument("--repo", required=True, help="Target repo path")
    p.add_argument(
        "--pytest",
        required=True,
        help='Pytest command as a single string, e.g. "pytest -q"',
    )
    p.add_argument("--patch", help="Path to a .patch file (manual mode)")
    p.add_argument("--llm", action="store_true", help="Use DeepSeek LLM to generate patch")
    p.add_argument(
        "--llm-mode",
        choices=["openai", "mock"],
        default="openai",
        help="LLM backend for --llm mode: openai (default, requires API key) or mock (no key needed)",
    )
    p.add_argument(
        "--files",
        nargs="+",
        help="Related source files relative to repo root (manual file selection)",
    )
    p.add_argument(
        "--auto-files",
        action="store_true",
        help="Auto-extract related files from pytest log (alternative to --files)",
    )
    p.add_argument(
        "--max-iters",
        type=int,
        default=1,
        help="Maximum repair iterations for --llm mode (1-5, default: 1)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Pytest timeout in seconds for each run (default: 600)",
    )
    p.add_argument(
        "--runs-dir",
        default="runs",
        help="Directory to store run artifacts (default: runs/)",
    )
    p.add_argument(
        "--policy",
        default=None,
        help="Path to policy YAML file (default: policies/policy.yaml)",
    )
    args = p.parse_args(argv)

    if bool(args.patch) == bool(args.llm):
        p.error("Exactly one of --patch or --llm is required.")
    if args.patch and args.files:
        p.error("--files cannot be used with --patch.")
    if args.patch and args.auto_files:
        p.error("--auto-files cannot be used with --patch.")
    if args.patch and args.max_iters > 1:
        p.error("--max-iters > 1 cannot be used with --patch.")
    if args.files and args.auto_files:
        p.error("--files and --auto-files are mutually exclusive.")
    if args.llm and not args.files and not args.auto_files:
        p.error("--llm requires either --files or --auto-files.")
    if args.max_iters < 1:
        p.error("--max-iters must be at least 1.")
    if args.max_iters > 5:
        p.error("--max-iters must be at most 5.")

    return args


def _run_patch_mode(
    args: argparse.Namespace,
    repo: Path,
    run_dir: Path,
    baseline_log_path: Path,
    baseline_result: PytestResult,
    policy: dict,
) -> Report:
    """Manual patch mode: read patch -> policy guard -> apply -> pytest verify."""
    patch_file = Path(args.patch).expanduser().resolve()
    patch_text = patch_file.read_text(encoding="utf-8", errors="replace")

    iter_pytest_log = run_dir / "pytest_iter_1.log"
    patch_error: str | None = None
    patch_applied = False
    pytest_passed = False
    final_log_path = str(baseline_log_path)

    # Policy guard (includes git apply --check when required)
    policy_result = run_patch_guard(patch_text, policy, repo_path=repo, patch_file=patch_file)
    policy_result_path = _save_policy_result(run_dir, policy_result)

    if not policy_result["passed"]:
        reasons = [c["reason"] for c in policy_result["checks"] if not c["passed"] and c["reason"]]
        gac = policy_result.get("git_apply_check", {})
        if gac.get("required") and not gac.get("passed", True) and gac.get("stderr"):
            reasons.append(f"git apply --check: {gac['stderr']}")
        patch_error = "Policy check failed: " + "; ".join(reasons)
    else:
        # Secondary legacy check (kept per spec — not the sole guard)
        ok, err = validate_patch(patch_text)
        if not ok:
            patch_error = err

    if not patch_error:
        # If require_git_apply_check=false, guard skipped it — run check_patch as safety net
        if not policy.get("require_git_apply_check", True):
            check_res = check_patch(repo, patch_file)
            if not check_res.ok:
                patch_error = check_res.error

    if not patch_error:
        apply_res = apply_patch(repo, patch_file)
        patch_applied = apply_res.ok
        if not apply_res.ok:
            patch_error = apply_res.error

    modified_files: list[str] = extract_modified_files(patch_text) if patch_applied else []

    if patch_applied:
        iter_result = run_pytest(repo, args.pytest, args.timeout, iter_pytest_log)
        final_log_path = str(iter_pytest_log)
        pytest_passed = iter_result.passed
    else:
        iter_pytest_log.write_text("Patch not applied; pytest skipped.\n", encoding="utf-8")

    success = patch_applied and pytest_passed

    failure_category: str | None = None
    if not success:
        fail_text = patch_error or iter_pytest_log.read_text(encoding="utf-8")
        failure_category = classify_failure(fail_text)

    final_decision, decision_reasons = _build_decision(policy_result, patch_applied, pytest_passed)
    gac = policy_result.get("git_apply_check", {})
    git_apply_check_passed = gac.get("passed", True) if gac.get("required") else True

    history = [
        {
            "iteration": 1,
            "selected_files": None,
            "prompt_path": None,
            "llm_response_path": None,
            "generated_patch_path": str(patch_file),
            "patch_applied": patch_applied,
            "pytest_passed": pytest_passed,
            "pytest_log_path": str(iter_pytest_log),
            "llm_error": None,
            "patch_error": patch_error,
            "failure_category": failure_category,
            "policy_result_path": str(policy_result_path),
        }
    ]

    return Report(
        success=success,
        baseline_passed=baseline_result.passed,
        final_passed=pytest_passed,
        iterations=1,
        max_iters=1,
        pytest_cmd=args.pytest,
        patch_source="manual",
        file_selection_mode=None,
        selected_files=None,
        modified_files=modified_files or None,
        failure_category=failure_category,
        baseline_log_path=str(baseline_log_path),
        final_log_path=final_log_path,
        warning=_compute_warning(baseline_result.passed),
        history=history,
        policy_passed=policy_result["passed"],
        policy_result_path=str(policy_result_path),
        git_apply_check_passed=git_apply_check_passed,
        human_review_required=(final_decision != "BLOCKED"),
        final_decision=final_decision,
        decision_reasons=decision_reasons,
    )


def _run_llm_repair_loop(
    args: argparse.Namespace,
    repo: Path,
    run_dir: Path,
    baseline_log_path: Path,
    baseline_result: PytestResult,
    policy: dict,
) -> Report:
    """LLM iterative repair: select files -> prompt -> generate patch -> guard -> apply -> pytest."""
    file_selection_mode = "manual" if args.files else "auto"

    current_feedback = baseline_log_path.read_text(encoding="utf-8")
    previous_error: str | None = None
    history: list[dict] = []

    final_log_path = str(baseline_log_path)
    final_passed = False
    success = False
    last_selected_files: list[str] = []
    all_modified_files: list[str] = []
    all_modified_seen: set[str] = set()

    last_policy_result: dict = {"passed": True, "checks": [], "git_apply_check": {"required": False, "passed": True, "stderr": ""}}
    last_policy_result_path: Path | None = None

    for iter_n in range(1, args.max_iters + 1):
        prompt_path = run_dir / f"repair_prompt_iter_{iter_n}.txt"
        llm_response_path = run_dir / f"llm_response_iter_{iter_n}.txt"
        generated_patch_path = run_dir / f"generated_patch_iter_{iter_n}.diff"
        iter_pytest_log = run_dir / f"pytest_iter_{iter_n}.log"
        iter_policy_result_path = run_dir / f"policy_result_iter_{iter_n}.json"

        llm_error: str | None = None
        patch_error: str | None = None
        patch_applied = False
        pytest_passed = False
        patch_text: str | None = None
        prompt_path_str: str | None = None
        llm_response_path_str: str | None = None
        generated_patch_path_str: str | None = None
        retrieved_files_path_str: str | None = None
        iter_policy_result: dict | None = None

        # ── a. Select files ──────────────────────────────────────────────────
        if args.files:
            file_paths = list(args.files)
        else:
            retrieved = retrieve_files_from_pytest_log(repo, current_feedback)
            rf_json = run_dir / f"retrieved_files_iter_{iter_n}.json"
            rf_json.write_text(
                json.dumps(
                    {"iteration": iter_n, "mode": "auto", "files": [str(p) for p in retrieved]},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            retrieved_files_path_str = str(rf_json)
            file_paths = [str(p) for p in retrieved]

        last_selected_files = file_paths

        if not file_paths:
            llm_error = "No related files found from pytest log"
        else:
            # ── b–d. Prompt → LLM → parse patch ─────────────────────────────
            try:
                file_contents = _read_repo_files(repo, file_paths)
                prompt = build_repair_prompt(
                    current_feedback,
                    file_contents,
                    iteration=iter_n,
                    previous_error=previous_error,
                )
                prompt_path.write_text(prompt, encoding="utf-8")
                prompt_path_str = str(prompt_path)

                client = create_llm_client(args.llm_mode)
                response = client.generate_patch(prompt)
                llm_response_path.write_text(response, encoding="utf-8")
                llm_response_path_str = str(llm_response_path)

                patch_text = extract_diff_from_response(response)
                generated_patch_path.write_text(patch_text, encoding="utf-8")
                generated_patch_path_str = str(generated_patch_path)

            except ValueError as e:
                if "No valid unified diff patch" in str(e):
                    llm_error = "No valid unified diff patch found"
                else:
                    llm_error = f"{type(e).__name__}: {e}"
            except Exception as e:
                llm_error = f"{type(e).__name__}: {e}"

        # ── e. Policy guard ───────────────────────────────────────────────────
        if patch_text and not llm_error:
            iter_policy_result = run_patch_guard(
                patch_text, policy, repo_path=repo, patch_file=generated_patch_path
            )
            _save_policy_result(run_dir, iter_policy_result, iter_policy_result_path.name)
            last_policy_result = iter_policy_result
            last_policy_result_path = iter_policy_result_path

            if not iter_policy_result["passed"]:
                reasons = [c["reason"] for c in iter_policy_result["checks"] if not c["passed"] and c["reason"]]
                gac = iter_policy_result.get("git_apply_check", {})
                if gac.get("required") and not gac.get("passed", True) and gac.get("stderr"):
                    reasons.append(f"git apply --check: {gac['stderr']}")
                patch_error = "Policy check failed: " + "; ".join(reasons)
            else:
                # Secondary legacy check
                ok, err = validate_patch(patch_text)
                if not ok:
                    patch_error = err

        # ── f. git apply ──────────────────────────────────────────────────────
        if patch_text and not llm_error and not patch_error:
            # If require_git_apply_check=false, guard skipped it — run check_patch as safety net
            if not policy.get("require_git_apply_check", True):
                check_res = check_patch(repo, generated_patch_path)
                if not check_res.ok:
                    patch_error = check_res.error

        if patch_text and not llm_error and not patch_error:
            apply_res = apply_patch(repo, generated_patch_path)
            patch_applied = apply_res.ok
            if not apply_res.ok:
                patch_error = apply_res.error
            else:
                for mf in extract_modified_files(patch_text):
                    if mf not in all_modified_seen:
                        all_modified_files.append(mf)
                        all_modified_seen.add(mf)

        # ── g. Run pytest ─────────────────────────────────────────────────────
        if patch_applied:
            iter_result = run_pytest(repo, args.pytest, args.timeout, iter_pytest_log)
            final_log_path = str(iter_pytest_log)
            pytest_passed = iter_result.passed
        else:
            iter_pytest_log.write_text("Patch not applied; pytest skipped.\n", encoding="utf-8")

        # ── Classify failure ──────────────────────────────────────────────────
        failure_category: str | None = None
        if not pytest_passed or patch_error or llm_error:
            if llm_error:
                fail_text = llm_error
            elif patch_error:
                fail_text = patch_error
            elif patch_applied:
                fail_text = iter_pytest_log.read_text(encoding="utf-8")
            else:
                fail_text = current_feedback
            failure_category = classify_failure(fail_text)

        entry: dict = {
            "iteration": iter_n,
            "selected_files": file_paths,
            "prompt_path": prompt_path_str,
            "llm_response_path": llm_response_path_str,
            "generated_patch_path": generated_patch_path_str,
            "patch_applied": patch_applied,
            "pytest_passed": pytest_passed,
            "pytest_log_path": str(iter_pytest_log),
            "llm_error": llm_error,
            "patch_error": patch_error,
            "failure_category": failure_category,
            "policy_result_path": str(iter_policy_result_path) if iter_policy_result else None,
        }
        if retrieved_files_path_str:
            entry["retrieved_files_path"] = retrieved_files_path_str
        history.append(entry)

        # ── h/i. Stop or update feedback ─────────────────────────────────────
        if pytest_passed:
            final_passed = True
            success = True
            break

        if patch_applied:
            current_feedback = iter_pytest_log.read_text(encoding="utf-8")
            previous_error = None
        else:
            previous_error = patch_error or llm_error or "Unknown error"
            if not file_paths:
                break

    top_failure: str | None = None
    if not success and history:
        top_failure = history[-1].get("failure_category")

    final_decision, decision_reasons = _build_decision(
        last_policy_result,
        patch_applied=(history[-1]["patch_applied"] if history else False),
        pytest_passed=final_passed,
    )
    gac = last_policy_result.get("git_apply_check", {})
    git_apply_check_passed = gac.get("passed", True) if gac.get("required") else True

    return Report(
        success=success,
        baseline_passed=baseline_result.passed,
        final_passed=final_passed,
        iterations=len(history),
        max_iters=args.max_iters,
        pytest_cmd=args.pytest,
        patch_source="llm",
        file_selection_mode=file_selection_mode,
        selected_files=last_selected_files or None,
        modified_files=all_modified_files or None,
        failure_category=top_failure,
        baseline_log_path=str(baseline_log_path),
        final_log_path=final_log_path,
        warning=_compute_warning(baseline_result.passed),
        history=history,
        policy_passed=last_policy_result["passed"],
        policy_result_path=str(last_policy_result_path) if last_policy_result_path else None,
        git_apply_check_passed=git_apply_check_passed,
        human_review_required=(final_decision != "BLOCKED"),
        final_decision=final_decision,
        decision_reasons=decision_reasons,
    )


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    runs_dir = Path(args.runs_dir).expanduser().resolve()

    run_dir = runs_dir / _run_id()
    run_dir.mkdir(parents=True, exist_ok=True)

    baseline_log_path = run_dir / "baseline_pytest.log"
    report_path = run_dir / "report.json"
    report_md_path = run_dir / "report.md"

    policy = load_policy(args.policy)

    baseline_result = run_pytest(
        repo_path=repo,
        pytest_cmd=args.pytest,
        timeout_s=args.timeout,
        log_path=baseline_log_path,
    )

    if args.patch:
        report = _run_patch_mode(args, repo, run_dir, baseline_log_path, baseline_result, policy)
    else:
        report = _run_llm_repair_loop(args, repo, run_dir, baseline_log_path, baseline_result, policy)

    report_dict = asdict(report)
    write_report(report_path, report_dict)
    write_markdown_report(report_md_path, report_dict)

    sys.stdout.write(
        json.dumps({"report": str(report_path), "report_md": str(report_md_path)}, ensure_ascii=False)
        + "\n"
    )
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
