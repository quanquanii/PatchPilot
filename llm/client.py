from __future__ import annotations

import json
import os

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

    def generate_patch(self, prompt: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior software engineer. "
                        "Return only a valid unified diff patch."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
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
