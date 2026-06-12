from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_POLICY_PATH = Path(__file__).parent.parent / "policies" / "policy.yaml"

_DEFAULTS: dict[str, Any] = {
    "allowed_file_extensions": [".py"],
    "blocked_paths": [".env", "id_rsa", "secret", "token", "credentials"],
    "protected_paths": ["policies/", ".github/workflows/"],
    "max_changed_files": 3,
    "max_patch_lines": 120,
    "allow_test_modification": False,
    "require_tests": True,
    "require_git_apply_check": True,
}


def load_policy(policy_path: str | Path | None = None) -> dict:
    """Load policy from YAML, falling back to built-in defaults for missing keys."""
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required: pip install PyYAML") from exc

    path = Path(policy_path) if policy_path else DEFAULT_POLICY_PATH

    raw: dict = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                raw = loaded

    policy = dict(_DEFAULTS)
    policy.update(raw)
    return policy
