from __future__ import annotations


def build_spec_review_prompt(requirements_text: str) -> str:
    """Build a prompt that asks the LLM to review a requirements document as JSON."""
    return f"""## SPEC-REVIEW

You are a senior software engineer reviewing a requirements document before implementation begins.
Analyse the requirements and return your review as a single JSON object.

## Requirements Document

{requirements_text.strip()}

## Output Format

Return ONLY a JSON object wrapped in a ```json code block with exactly these fields:

```json
{{
  "clarifying_questions": ["question 1", "question 2"],
  "functional_scope": ["item 1", "item 2"],
  "out_of_scope": ["item 1", "item 2"],
  "non_functional_requirements": ["item 1"],
  "risks": ["risk 1", "risk 2"],
  "acceptance_criteria": ["criterion 1", "criterion 2"],
  "suggested_test_cases": ["test case 1", "test case 2"],
  "human_review_required": true,
  "final_decision": "NEEDS_HUMAN_REVIEW"
}}
```
"""


def build_design_review_prompt(requirements_text: str, design_text: str) -> str:
    """Build a prompt that asks the LLM to review a design document against requirements."""
    return f"""## DESIGN-REVIEW

You are a senior software engineer reviewing a design document against its requirements.
Analyse both documents and return your review as a single JSON object.

## Requirements Document

{requirements_text.strip()}

## Design Document

{design_text.strip()}

## Output Format

Return ONLY a JSON object wrapped in a ```json code block with exactly these fields:

```json
{{
  "requirement_coverage": ["requirement 1 is covered by ...", "requirement 2 is covered by ..."],
  "missing_requirements": ["requirement not addressed in design"],
  "design_risks": ["risk 1", "risk 2"],
  "edge_cases": ["edge case 1", "edge case 2"],
  "security_risks": ["security concern 1"],
  "test_strategy": ["test approach 1", "test approach 2"],
  "interfaces_and_boundaries": ["boundary observation 1"],
  "human_review_required": true,
  "final_decision": "NEEDS_HUMAN_REVIEW"
}}
```
"""


def build_diff_review_prompt(diff_text: str) -> str:
    """Build a prompt that asks the LLM to review a unified diff as JSON."""
    return f"""## REVIEW-DIFF

You are a senior software engineer reviewing a code change (unified diff) before it is merged.
Analyse the diff and return your review as a single JSON object.

## Diff

```diff
{diff_text.strip()}
```

## Output Format

Return ONLY a JSON object wrapped in a ```json code block with exactly these fields:

```json
{{
  "summary": ["one-line description of what the change does"],
  "bug_risks": ["potential bug introduced or risk exposed by this change"],
  "security_risks": ["security concern 1"],
  "maintainability_findings": ["readability or maintainability observation"],
  "test_coverage_gaps": ["test that is missing or should be added"],
  "suggested_followups": ["follow-up task or improvement to consider"],
  "blocking_findings": ["critical issue that must be fixed before merging"],
  "human_review_required": true,
  "final_decision": "NEEDS_HUMAN_REVIEW"
}}
```
"""


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
