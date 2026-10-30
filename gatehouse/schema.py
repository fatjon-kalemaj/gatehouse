"""Validation of worker step reports against schemas/step-result.schema.json.

A small hand-written validator for exactly this schema, so the offline path has no extra dependency.
Tests check that it agrees with the JSON Schema file.
"""

from __future__ import annotations

STATUSES = {"ok", "needs_review", "failed"}
SEVERITIES = {"low", "medium", "high"}
TOP_KEYS = {"agent", "status", "summary", "output", "issues", "confidence"}
REQUIRED = {"agent", "status", "summary", "output", "issues"}


def validate_step_result(report: object, expected_agent: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report is not a JSON object"]
    missing = REQUIRED - report.keys()
    if missing:
        errors.append(f"missing fields: {sorted(missing)}")
    extra = report.keys() - TOP_KEYS
    if extra:
        errors.append(f"unexpected fields: {sorted(extra)}")
    if report.get("agent") != expected_agent:
        errors.append(f"agent is {report.get('agent')!r}, expected {expected_agent!r}")
    if report.get("status") not in STATUSES:
        errors.append(f"status {report.get('status')!r} not in {sorted(STATUSES)}")
    summary = report.get("summary")
    if not isinstance(summary, str) or len(summary) > 400:
        errors.append("summary must be a string of at most 400 characters")
    output = report.get("output")
    if not isinstance(output, dict) or not isinstance(output.get("artifact"), str):
        errors.append("output.artifact must be a string path")
    issues = report.get("issues")
    if not isinstance(issues, list):
        errors.append("issues must be a list")
    else:
        for i, issue in enumerate(issues):
            if not isinstance(issue, dict) or set(issue) != {"code", "severity", "message"}:
                errors.append(f"issues[{i}] must have exactly code, severity, message")
            elif issue["severity"] not in SEVERITIES:
                errors.append(f"issues[{i}].severity {issue['severity']!r} invalid")
    conf = report.get("confidence")
    if conf is not None and not (isinstance(conf, (int, float)) and 0 <= conf <= 1):
        errors.append("confidence must be a number between 0 and 1")
    return errors
