"""JSON Schema Draft 2020-12 Validator for NAS Air Intelligence exports.

Validates that analysis.json outputs strictly adhere to the specification.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schemas" / "analysis.schema.json"


def load_analysis_schema() -> dict[str, Any]:
    with SCHEMA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_analysis_json(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate a dictionary against the Draft 2020-12 analysis schema.

    Returns:
        (is_valid, list_of_error_messages)
    """
    try:
        from jsonschema import Draft202012Validator

        schema = load_analysis_schema()
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
        if not errors:
            return True, []
        messages = [
            f"{' -> '.join(str(p) for p in err.path) or 'root'}: {err.message}" for err in errors
        ]
        return False, messages
    except ImportError:
        # Fallback basic structural validation if jsonschema not installed
        required_keys = [
            "schema_version",
            "generator",
            "session",
            "status",
            "environment",
            "quality",
            "capture_metrics",
            "analysis_metrics",
            "timeline",
            "content_blocks",
            "program_candidates",
            "clock_patterns",
            "dayparts",
            "recurrent_elements",
            "programming_intelligence",
            "incidents",
        ]
        missing = [k for k in required_keys if k not in data]
        if missing:
            return False, [f"Missing top-level keys: {missing}"]
        return True, []
