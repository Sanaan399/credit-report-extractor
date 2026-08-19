"""Independent cross-check pass: re-reads each PDF from scratch with a
second, separate Claude call restricted to schemas.CriticalFields, then
diffs those fields against the primary extraction already sitting in
output/json/. Disagreements are written to output/review_flags.csv so a
human only has to spot-check the handful of reports (and fields) where the
two passes disagree, rather than re-reading every report.
"""

from __future__ import annotations

import base64
import csv
import json
import sys
from pathlib import Path

import anthropic

import config
from schemas import CriticalFields

SYSTEM_PROMPT = (
    "Read this business credit report and extract only the requested fields. "
    "Leave a field null if it is not present. Never guess or infer a value "
    "that is not actually in the document.\n\n"
    "For every date, output ISO format YYYY-MM-DD when the day, month, and "
    "year are all given; otherwise reproduce the value exactly as printed. "
    "All-numeric dates are ambiguous between DD/MM/YYYY and MM/DD/YYYY when "
    "both parts are 12 or less -- scan the rest of the document for a date "
    "where one part exceeds 12 to determine which convention this report "
    "uses, and apply that convention consistently, including to the "
    "ambiguous dates.\n\n"
    "registration_number is the company registration / AIC number, never the "
    "VAT number, tax ID, or unified social credit code -- put that in "
    "tax_vat_number instead, even if it looks like the more prominent "
    "identifier in the report. credit_rating is a letter/band rating (e.g. "
    "'A+', 'BB', 'C'); risk_level is the separate plain-English risk "
    "description (e.g. 'Low Risk', 'LOW') -- do not put one in the other."
)

_REVIEW_FIELDNAMES = ["source_file", "field", "primary_value", "cross_check_value"]


def _pdf_content_block(pdf_path: Path) -> dict:
    data = base64.standard_b64encode(pdf_path.read_bytes()).decode()
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}


def cross_check_report(client: anthropic.Anthropic, pdf_path: Path) -> tuple[CriticalFields, dict]:
    response = client.messages.parse(
        model=config.CROSS_CHECK_MODEL,
        max_tokens=config.CROSS_CHECK_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    _pdf_content_block(pdf_path),
                    {"type": "text", "text": "Extract the data from this credit report."},
                ],
            }
        ],
        output_format=CriticalFields,
    )
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}

    for block in response.content:
        if block.type == "text" and block.parsed_output is not None:
            return block.parsed_output, usage

    raise ValueError(f"Claude did not return a parseable cross-check for {pdf_path.name}")


def _display(value) -> str:
    if isinstance(value, dict):
        return value.get("raw") or ("" if value.get("value") is None else str(value["value"])) or ""
    return "" if value is None else str(value)


def _normalize(value) -> str:
    return _display(value).strip().lower()


def _mismatches(primary: dict, cross_check: CriticalFields) -> list[tuple[str, str, str]]:
    diffs = []
    for field in CriticalFields.model_fields:
        primary_value = primary.get(field)
        cross_value = getattr(cross_check, field)
        cross_value = cross_value.model_dump() if hasattr(cross_value, "model_dump") else cross_value
        if _normalize(primary_value) != _normalize(cross_value):
            diffs.append((field, _display(primary_value), _display(cross_value)))
    return diffs


def validate_all(input_dir: Path, json_dir: Path, review_flags_path: Path, *, log=print) -> dict:
    """Cross-checks every extracted report. Like extract_all, a single failed
    report is recorded and skipped rather than aborting the batch.

    Returns {"usage", "flags", "errors"}."""
    client = anthropic.Anthropic()
    rows = []
    errors: list[dict] = []
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    json_paths = sorted(json_dir.glob("*.json"))
    for i, json_path in enumerate(json_paths, 1):
        primary = json.loads(json_path.read_text(encoding="utf-8"))
        pdf_path = input_dir / primary["source_file"]
        log(f"[{i}/{len(json_paths)}] cross-checking {pdf_path.name}")

        try:
            cross_check, usage = cross_check_report(client, pdf_path)
            total_usage["input_tokens"] += usage["input_tokens"]
            total_usage["output_tokens"] += usage["output_tokens"]

            for field, primary_value, cross_value in _mismatches(primary, cross_check):
                rows.append(
                    {
                        "source_file": primary["source_file"],
                        "field": field,
                        "primary_value": primary_value,
                        "cross_check_value": cross_value,
                    }
                )
        except Exception as e:  # noqa: BLE001 - one bad report must not kill the batch
            message = f"{type(e).__name__}: {e}"
            errors.append({"source_file": primary["source_file"], "error": f"cross-check: {message}"})
            log(f"        FAILED: {message}")

    with review_flags_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_REVIEW_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    log(f"{len(rows)} flags across {len(json_paths)} reports -> {review_flags_path}")
    return {"usage": total_usage, "flags": len(rows), "errors": errors}


if __name__ == "__main__":
    input_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "reports"
    output_dir = Path(__file__).parent / "output"
    validate_all(input_dir, output_dir / config.OUTPUT_JSON_DIRNAME, output_dir / config.REVIEW_FLAGS_FILENAME)
