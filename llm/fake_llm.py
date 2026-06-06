from __future__ import annotations

from pathlib import Path


def generate_patch_from_file(patch_path: Path) -> str:
    """模拟 LLM 生成 patch：从本地文件读取 patch 文本并返回。"""
    patch_path = patch_path.resolve()
    if not patch_path.exists():
        raise FileNotFoundError(f"Patch file does not exist: {patch_path}")
    return patch_path.read_text(encoding="utf-8")
