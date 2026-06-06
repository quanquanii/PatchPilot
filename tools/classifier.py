from __future__ import annotations


def classify_failure(log_or_error: str) -> str:
    s = log_or_error
    if "SSL: CERTIFICATE_VERIFY_FAILED" in s or "LLM API request failed" in s:
        return "llm_api_error"
    if "No valid unified diff patch found" in s:
        return "no_diff_generated"
    if "Invalid patch path" in s:
        return "invalid_patch_path"
    if "Test file modification is not allowed" in s:
        return "unsafe_patch"
    if "git apply" in s or "patch does not apply" in s:
        return "patch_apply_failed"
    if "SyntaxError" in s:
        return "syntax_error"
    if "ModuleNotFoundError" in s or "ImportError" in s:
        return "import_error"
    if "AssertionError" in s or "assert " in s:
        return "assertion_mismatch"
    if "TimeoutExpired" in s or "TIMEOUT" in s:
        return "timeout"
    if "collected 0 items" in s:
        return "test_collection_error"
    return "unknown"
