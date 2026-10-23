from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def manual_doc(
    machines: list[dict[str, Any]],
    *,
    observed_at: str = "2026-10-20T23:30:00+09:00",
    business_date: str | None = "2026-10-20",
    hall_code: str = "H001",
    **extra: Any,
) -> dict[str, Any]:
    doc = {
        "format": "slot-data/manual-v1",
        "hall_code": hall_code,
        "observed_at": observed_at,
        "business_date": business_date,
        "machines": machines,
        **extra,
    }
    return doc


def write_doc(directory: Path, name: str, doc: dict[str, Any]) -> Path:
    path = directory / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path
