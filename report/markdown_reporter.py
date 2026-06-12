from __future__ import annotations

from pathlib import Path


def write_markdown_report(path: Path, report_dict: dict) -> None:
    path.write_text(_render(report_dict), encoding="utf-8")


def _render(r: dict) -> str:
    lines: list[str] = []

    lines.append("# PatchPilot Report\n")
    lines.append(f"**Result:** {'Success' if r.get('success') else 'Failure'}")
    lines.append(f"**Final decision:** `{r.get('final_decision', 'n/a')}`")
    lines.append(f"**Human review required:** {'Yes' if r.get('human_review_required') else 'No'}")
    lines.append(f"**Test command:** `{r.get('pytest_cmd', '')}`")
    lines.append(f"**Patch source:** {r.get('patch_source', 'n/a')}")
    lines.append(f"**Iterations:** {r.get('iterations', 0)} / {r.get('max_iters', 1)}")
    lines.append(f"**File selection:** {r.get('file_selection_mode') or 'n/a'}")

    sel = r.get("selected_files") or []
    lines.append(f"**Selected files:** {', '.join(sel) if sel else '-'}")

    mod = r.get("modified_files") or []
    lines.append(f"**Modified files:** {', '.join(mod) if mod else '-'}")

    lines.append(f"**Failure category:** {r.get('failure_category') or '-'}")
    lines.append(f"**Warning:** {r.get('warning') or '-'}")

    lines.append("\n## Policy Guard\n")
    lines.append(f"**Policy passed:** {'Yes' if r.get('policy_passed') else 'No'}")
    lines.append(f"**git apply --check passed:** {'Yes' if r.get('git_apply_check_passed') else 'No'}")

    reasons = r.get("decision_reasons") or []
    if reasons:
        lines.append(f"**Decision reasons:**")
        for reason in reasons:
            lines.append(f"  - {reason}")

    policy_path = r.get("policy_result_path")
    if policy_path:
        lines.append(f"**Policy result:** `{policy_path}`")

    lines.append("\n## Log paths\n")
    lines.append(f"- Baseline log: `{r.get('baseline_log_path', '')}`")
    lines.append(f"- Final log: `{r.get('final_log_path', '')}`")

    history = r.get("history") or []
    if history:
        lines.append("\n## History\n")
        for rec in history:
            n = rec.get("iteration", "?")
            lines.append(f"### Iteration {n}\n")
            lines.append("| Field | Value |")
            lines.append("|---|---|")

            sel_iter = rec.get("selected_files") or []
            lines.append(f"| Selected files | {', '.join(sel_iter) if sel_iter else '-'} |")

            for label, key in (
                ("Prompt", "prompt_path"),
                ("LLM response", "llm_response_path"),
                ("Generated patch", "generated_patch_path"),
                ("Pytest log", "pytest_log_path"),
                ("Retrieved files", "retrieved_files_path"),
                ("Policy result", "policy_result_path"),
            ):
                val = rec.get(key)
                lines.append(f"| {label} | {f'`{val}`' if val else '-'} |")

            lines.append(f"| Patch applied | {'Yes' if rec.get('patch_applied') else 'No'} |")
            lines.append(f"| Tests passed | {'Yes' if rec.get('pytest_passed') else 'No'} |")

            for label, key in (("LLM error", "llm_error"), ("Patch error", "patch_error")):
                val = rec.get(key)
                lines.append(f"| {label} | {val if val else '-'} |")

            lines.append(f"| Failure category | {rec.get('failure_category') or '-'} |")
            lines.append("")

    return "\n".join(lines) + "\n"
