from __future__ import annotations

import re

_INVALID_DIFF_MSG = "No valid unified diff patch found"


def normalize_patch_text(patch_text: str) -> str:
    text = patch_text.strip()
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def extract_diff_from_response(response_text: str) -> str:
    for pattern in (r"```diff\s*\n(.*?)```", r"```\s*\n(.*?)```"):
        match = re.search(pattern, response_text, re.DOTALL)
        if match:
            candidate = match.group(1).strip()
            if candidate.startswith("diff --git"):
                return normalize_patch_text(candidate)

    idx = response_text.find("diff --git")
    if idx != -1:
        patch = normalize_patch_text(response_text[idx:])
        if patch.startswith("diff --git"):
            return patch

    raise ValueError(_INVALID_DIFF_MSG)
