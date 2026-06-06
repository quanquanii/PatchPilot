from __future__ import annotations

import json
from pathlib import Path


def write_report(report_path: Path, report_dict: dict) -> None:
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report_dict, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

