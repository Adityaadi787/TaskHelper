"""Small structured reporting helpers kept separate from execution."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any


def result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "__dataclass_fields__"):
        return asdict(result)
    if isinstance(result, dict):
        return result
    return {"result": result}


def format_structured_result(result: Any) -> str:
    return json.dumps(result_to_dict(result), ensure_ascii=False, indent=2, default=str)
