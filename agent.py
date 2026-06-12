from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from llm.client import LLMClient
from llm.parser import extract_diff_from_response
from llm.prompt_builder import build_repair_prompt
from report.markdown_reporter import write_markdown_report
from report.reporter import write_report
from tools.classifier import classify_failure
from tools.patcher import check_patch, apply_patch
from tools.retriever import retrieve_files_from_pytest_log
from tools.safety import validate_patch, extract_modified_files
from tools.tester import PytestResult, run_pytest


@dataclass
class Report:
    """一次修复任务的结构化结果，用于 JSON/Markdown 报告输出。"""

    success: bool  # 最终是否成功修复（测试通过）
    baseline_passed: bool # 基线测试是否通过（修复前状态）
    final_passed: bool # 最终测试结果（修复后状态）
    iterations: int # 实际执行的修复迭代次数（1 表示仅基线测试，无修复尝试）
    max_iters: int # 允许的最大修复迭代次数（仅 LLM 模式相关）
    pytest_cmd: str # 用于测试的 pytest 命令（原样记录以便复现）
    patch_source: str   # 补丁来源：manual（用户提供）或 llm（模型生成）
    file_selection_mode: str | None # 相关文件选择模式：manual（用户指定）或 auto（从日志提取），仅 LLM 模式相关
    selected_files: list[str] | None # 用户选择的相关文件列表（仅 manual 模式相关）
    modified_files: list[str] | None # 实际修改的文件列表
    failure_category: str | None # 失败类别
    baseline_log_path: str # 基线测试日志路径
    final_log_path: str # 最终测试日志路径
    warning: str | None # 警告信息（例如基线已通过但仍尝试修复的情况）  
    history: list[dict] # 每次修复迭代的详细记录，包括选文件、提示词、模型响应、补丁应用、测试结果等


def _compute_warning(baseline_passed: bool) -> str | None:
    # 基线测试已通过时提醒用户：此次修复可能不是必须的。
    if baseline_passed:
        return "baseline tests already passed; patch may be unnecessary"
    return None


def _run_id() -> str:
    # 使用时间戳作为本次运行目录名，便于追踪和排序。
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_repo_files(repo: Path, file_paths: list[str]) -> list[tuple[str, str]]:
    # 读取候选源码文件，并确保路径不会越过仓库根目录。
    files: list[tuple[str, str]] = [] 
    for rel_path in file_paths:
        path = (repo / rel_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found in repo: {rel_path}")
        if repo not in path.parents and path != repo:
            raise ValueError(f"File escapes repo root: {rel_path}")
        files.append((rel_path, path.read_text(encoding="utf-8")))
    return files


def parse_args(argv: list[str]) -> argparse.Namespace:
    # 命令行参数定义：支持手动补丁模式与 LLM 修复模式。
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
    args = p.parse_args(argv)

    # 参数组合约束，避免互斥选项或无效配置。
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
) -> Report:
    # 手动补丁模式：读取补丁 -> 校验 -> apply -> pytest 验证。
    patch_file = Path(args.patch).expanduser().resolve()
    patch_text = patch_file.read_text(encoding="utf-8", errors="replace")

    iter_pytest_log = run_dir / "pytest_iter_1.log"
    patch_error: str | None = None
    patch_applied = False
    pytest_passed = False
    final_log_path = str(baseline_log_path)

    ok, err = validate_patch(patch_text)
    if not ok:
        patch_error = err

    if not patch_error:
        check_res = check_patch(repo, patch_file)
        if not check_res.ok:
            patch_error = check_res.error
        else:
            apply_res = apply_patch(repo, patch_file)
            patch_applied = apply_res.ok
            if not apply_res.ok:
                patch_error = apply_res.error

    modified_files: list[str] = extract_modified_files(patch_text) if patch_applied else []

    if patch_applied:
        # 仅在补丁成功应用后执行测试。
        iter_result = run_pytest(repo, args.pytest, args.timeout, iter_pytest_log)
        final_log_path = str(iter_pytest_log)
        pytest_passed = iter_result.passed
    else:
        iter_pytest_log.write_text("Patch not applied; pytest skipped.\n", encoding="utf-8")

    success = patch_applied and pytest_passed

    failure_category: str | None = None
    if not success:
        # 优先使用补丁错误，否则根据 pytest 日志进行失败分类。
        fail_text = patch_error or iter_pytest_log.read_text(encoding="utf-8")
        failure_category = classify_failure(fail_text)

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
    )


def _run_llm_repair_loop(
    args: argparse.Namespace,
    repo: Path,
    run_dir: Path,
    baseline_log_path: Path,
    baseline_result: PytestResult,
) -> Report:
    # LLM 迭代修复模式：选文件 -> 组 prompt -> 生成补丁 -> 校验/apply -> pytest。
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

    for iter_n in range(1, args.max_iters + 1):
        # Paths for this iteration's artifacts
        prompt_path = run_dir / f"repair_prompt_iter_{iter_n}.txt"
        llm_response_path = run_dir / f"llm_response_iter_{iter_n}.txt"
        generated_patch_path = run_dir / f"generated_patch_iter_{iter_n}.diff"
        iter_pytest_log = run_dir / f"pytest_iter_{iter_n}.log"

        # Per-iteration state
        llm_error: str | None = None
        patch_error: str | None = None
        patch_applied = False
        pytest_passed = False
        patch_text: str | None = None
        prompt_path_str: str | None = None
        llm_response_path_str: str | None = None
        generated_patch_path_str: str | None = None
        retrieved_files_path_str: str | None = None

        # ── a. Select files ──────────────────────────────────────────────────
        if args.files:
            file_paths = list(args.files)
        else:
            # 自动模式下从当前失败日志提取最相关文件，并落盘留痕。
            retrieved = retrieve_files_from_pytest_log(repo, current_feedback)
            rf_json = run_dir / f"retrieved_files_iter_{iter_n}.json"
            rf_json.write_text(
                json.dumps(
                    {
                        "iteration": iter_n,
                        "mode": "auto",
                        "files": [str(p) for p in retrieved],
                    },
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
                # 将失败上下文与候选源码拼装成修复提示词。
                file_contents = _read_repo_files(repo, file_paths)
                prompt = build_repair_prompt(
                    current_feedback,
                    file_contents,
                    iteration=iter_n,
                    previous_error=previous_error,
                )
                prompt_path.write_text(prompt, encoding="utf-8")
                prompt_path_str = str(prompt_path)

                client = LLMClient()
                response = client.generate_patch(prompt)
                llm_response_path.write_text(response, encoding="utf-8")
                llm_response_path_str = str(llm_response_path)

                # 从模型响应中抽取 unified diff 补丁文本。
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

        # ── e. Safety validation ──────────────────────────────────────────────
        if patch_text and not llm_error:
            # 安全检查阶段拒绝危险或不合法补丁。
            ok, err = validate_patch(patch_text)
            if not ok:
                patch_error = err

        # ── f. git apply ──────────────────────────────────────────────────────
        if patch_text and not llm_error and not patch_error:
            check_res = check_patch(repo, generated_patch_path)
            if not check_res.ok:
                patch_error = check_res.error
            else:
                apply_res = apply_patch(repo, generated_patch_path)
                patch_applied = apply_res.ok
                if not apply_res.ok:
                    patch_error = apply_res.error
                else:
                    # 汇总本次运行中所有被修改的文件（去重后保序）。
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
            iter_pytest_log.write_text(
                "Patch not applied; pytest skipped.\n", encoding="utf-8"
            )

        # ── Classify failure ──────────────────────────────────────────────────
        failure_category: str | None = None
        if not pytest_passed or patch_error or llm_error:
            # 分类优先级：LLM 错误 > 补丁错误 > pytest 日志。
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
        }
        if retrieved_files_path_str:
            entry["retrieved_files_path"] = retrieved_files_path_str
        history.append(entry)

        # ── h/i. Stop or update feedback ─────────────────────────────────────
        if pytest_passed:
            # 一旦通过立即结束迭代。
            final_passed = True
            success = True
            break

        if patch_applied:
            # 补丁已应用但测试未通过：用新日志继续驱动下一轮修复。
            current_feedback = iter_pytest_log.read_text(encoding="utf-8")
            previous_error = None
        else:
            # 补丁未应用：记录错误作为下一轮提示。
            previous_error = patch_error or llm_error or "Unknown error"
            if not file_paths:
                break  # retriever won't improve without new feedback

    top_failure: str | None = None
    if not success and history:
        top_failure = history[-1].get("failure_category")

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
    )


def main(argv: list[str]) -> int:
    # 主流程：解析参数 -> 跑基线测试 -> 执行对应修复模式 -> 写报告。
    args = parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    runs_dir = Path(args.runs_dir).expanduser().resolve()

    run_dir = runs_dir / _run_id()
    run_dir.mkdir(parents=True, exist_ok=True)

    baseline_log_path = run_dir / "baseline_pytest.log"
    report_path = run_dir / "report.json"
    report_md_path = run_dir / "report.md"

    baseline_result = run_pytest(
        repo_path=repo,
        pytest_cmd=args.pytest,
        timeout_s=args.timeout,
        log_path=baseline_log_path,
    )

    if args.patch:
        report = _run_patch_mode(args, repo, run_dir, baseline_log_path, baseline_result)
    else:
        report = _run_llm_repair_loop(args, repo, run_dir, baseline_log_path, baseline_result)

    report_dict = asdict(report)
    write_report(report_path, report_dict)
    write_markdown_report(report_md_path, report_dict)

    # 标准输出仅打印报告路径，便于外部脚本消费。
    sys.stdout.write(
        json.dumps({"report": str(report_path), "report_md": str(report_md_path)}, ensure_ascii=False)
        + "\n"
    )
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
