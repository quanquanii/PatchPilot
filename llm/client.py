from __future__ import annotations

import json
import os
import textwrap

import certifi
import requests


class LLMClient:
    def __init__(self) -> None:
        self.api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get(
            "OPENAI_API_KEY"
        )
        if not self.api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY (or OPENAI_API_KEY) environment variable is required"
            )
        self.base_url = (
            os.environ.get("DEEPSEEK_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.deepseek.com"
        ).rstrip("/")
        self.model = (
            os.environ.get("DEEPSEEK_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "deepseek-chat"
        )

    def _call_api(self, messages: list[dict]) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {"model": self.model, "messages": messages, "temperature": 0}
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=120,
                verify=certifi.where(),
            )
            resp.raise_for_status()
            body = resp.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"LLM API request failed: {e}") from e
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected LLM API response: {body}") from e

    def generate_patch(self, prompt: str) -> str:
        return self._call_api([
            {"role": "system", "content": "You are a senior software engineer. Return only a valid unified diff patch."},
            {"role": "user", "content": prompt},
        ])

    def generate(self, prompt: str) -> str:
        """General-purpose generation (spec-review, etc.)."""
        return self._call_api([
            {"role": "system", "content": "You are a senior software engineer. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ])


class MockLLMClient:
    """Hardcoded responses for demos and CI runs that have no API key.

    Detects spec-review prompts (contain 'SPEC-REVIEW') and returns stable JSON.
    All other prompts get the repair patch for examples/demo_project/calculator.py.
    """

    _PATCH_RESPONSE = textwrap.dedent("""\
        Here is the fix for the failing test:

        ```diff
        diff --git a/calculator.py b/calculator.py
        index 0000001..0000002 100644
        --- a/calculator.py
        +++ b/calculator.py
        @@ -1,2 +1,2 @@
         def add(a, b):
        -    return a - b
        +    return a + b
        ```
        """)

    _SPEC_REVIEW_RESPONSE = textwrap.dedent("""\
        ```json
        {
          "clarifying_questions": [
            "What should happen when non-numeric inputs (e.g. strings, None) are passed?",
            "Is there a defined behavior for floating-point overflow or precision loss?",
            "Should the function raise an exception or return a default value for invalid inputs?"
          ],
          "functional_scope": [
            "add(a, b) returns the arithmetic sum of a and b",
            "Supports integer inputs",
            "Supports float inputs"
          ],
          "out_of_scope": [
            "Input validation and type checking",
            "Error handling for non-numeric types",
            "Overflow and precision guarantees",
            "Logging and observability"
          ],
          "non_functional_requirements": [
            "No explicit performance requirements stated",
            "No precision or overflow constraints defined"
          ],
          "risks": [
            "Undefined behavior for non-number inputs may cause silent bugs downstream",
            "No precision specification risks inconsistent results for large floats",
            "Missing error handling could lead to unhandled exceptions in production"
          ],
          "acceptance_criteria": [
            "add(2, 3) == 5",
            "add(1.5, 2.5) == 4.0",
            "add(0, 0) == 0",
            "add(-1, 1) == 0"
          ],
          "suggested_test_cases": [
            "test_add_positive_integers: add(2, 3) == 5",
            "test_add_floats: add(1.5, 2.5) == 4.0",
            "test_add_zero: add(0, 0) == 0",
            "test_add_negative: add(-1, 1) == 0",
            "test_add_large_numbers: add(1e308, 1e308) behavior is defined"
          ],
          "human_review_required": true,
          "final_decision": "NEEDS_HUMAN_REVIEW"
        }
        ```
        """)

    _DESIGN_REVIEW_RESPONSE = textwrap.dedent("""\
        ```json
        {
          "requirement_coverage": [
            "add(a, b) returns the sum — directly implemented as 'return a + b'",
            "Integer support — covered by the implementation and existing test",
            "Float support — implementation supports floats; tests do not yet cover this"
          ],
          "missing_requirements": [
            "Behavior for non-number inputs is unspecified in both requirements and design",
            "No precision or overflow requirements addressed in the design"
          ],
          "design_risks": [
            "No input validation means silent wrong results for non-numeric inputs",
            "Module boundary is unclear; no plan for growth beyond add()",
            "Single flat file will become hard to maintain if more operations are added"
          ],
          "edge_cases": [
            "add(None, 1) — will raise TypeError at runtime, not caught",
            "add('2', 3) — will raise TypeError, behavior undocumented",
            "add(1e308, 1e308) — float overflow to inf, no guard"
          ],
          "security_risks": [
            "No input validation; malicious or unexpected inputs propagate unchecked",
            "If exposed via an API layer, lack of type enforcement is a surface risk"
          ],
          "test_strategy": [
            "Add float test: add(1.5, 2.5) == 4.0",
            "Add zero test: add(0, 0) == 0",
            "Add negative test: add(-1, 1) == 0",
            "Consider parametrize for integer and float cases",
            "Add a test that documents expected behavior for non-numeric inputs"
          ],
          "interfaces_and_boundaries": [
            "Currently a flat module; consider a calculator/ package if operations grow",
            "No public API contract (docstring, type hints) defined on add()",
            "Type hints would make the intended interface explicit"
          ],
          "human_review_required": true,
          "final_decision": "NEEDS_HUMAN_REVIEW"
        }
        ```
        """)

    def generate(self, prompt: str) -> str:
        if "DESIGN-REVIEW" in prompt:
            return self._DESIGN_REVIEW_RESPONSE
        if "SPEC-REVIEW" in prompt:
            return self._SPEC_REVIEW_RESPONSE
        return self._PATCH_RESPONSE

    def generate_patch(self, prompt: str) -> str:
        return self.generate(prompt)


def create_llm_client(mode: str) -> "LLMClient | MockLLMClient":
    """Factory: return the right LLM client for the given mode string."""
    if mode == "mock":
        return MockLLMClient()
    return LLMClient()  # raises ValueError if API key is missing — intentional
