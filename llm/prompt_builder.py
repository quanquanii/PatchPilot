from __future__ import annotations


def _guess_lang(rel_path: str) -> str:
    if rel_path.endswith(".py"):
        return "python"
    return ""


def build_repair_prompt(
    issue_or_log: str,
    files: list[tuple[str, str]],
    iteration: int = 1,
    previous_error: str | None = None,
) -> str:
    file_sections = []
    for rel_path, content in files:
        lang = _guess_lang(rel_path)
        fence = f"```{lang}" if lang else "```"
        file_sections.append(f"### File: {rel_path}\n{fence}\n{content}\n```")

    files_block = "\n\n".join(file_sections)

    prev_section = ""
    if previous_error:
        prev_section = f"""
## Previous attempt failed (iteration {iteration - 1})
```
{previous_error.strip()}
```
"""

    return f"""## Task
You are fixing a Python pytest failure. This is repair attempt {iteration}.
{prev_section}
## Pytest failure log
```
{issue_or_log.strip()}
```

## Related files
{files_block}

## Constraints
1. Output ONLY a valid unified diff patch. No explanations. No prose.
2. The patch MUST start with `diff --git` on the very first line.
3. All file paths must be relative to the repo root. No absolute paths. No `../`.
4. Do NOT modify any test files (files under tests/ or named test_*.py).
5. Make only the minimal changes required to fix the failing tests.
6. Do not refactor unrelated code.
7. Do not introduce new dependencies.
8. Wrap the patch in a ```diff code block.
"""
