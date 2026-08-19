"""Primary extraction pass: for every PDF in the input directory, detects its
format, sends the whole PDF straight to Claude alongside the matching prompt,
and writes the validated CreditReport to output/json/<stem>.json.

Claude reads the PDF directly (no OCR/text-extraction step of our own) --
detect.py's job is only to pick which prompt to prepend and how to label the
output, per schemas.py.

CreditReport is too large for the API's strict structured-output grammar
(see schemas.py), so this pass embeds the JSON schema in the prompt, asks
for a plain-JSON reply, validates it with Pydantic locally, and retries once
with the validation error if the first reply doesn't parse.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import anthropic
import pydantic

import config
import detect
from schemas import CreditReport, Money

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_FORMAT_PROMPT_FILES = {
    config.FORMAT_CREDITSAFE: "creditsafe.txt",
    config.FORMAT_CRIF_INTL: "crif_intl.txt",
    config.FORMAT_GLADTRUST: "gladtrust.txt",
    config.FORMAT_UNKNOWN: "unknown.txt",
}

_BASE_PROMPT = (_PROMPTS_DIR / "base.txt").read_text()

_JSON_INSTRUCTIONS = (
    "Respond with a single JSON object that conforms to this JSON schema, and "
    "nothing else -- no markdown fences, no commentary before or after:\n\n{schema}"
)


def system_prompt(report_format: str) -> str:
    format_prompt = (_PROMPTS_DIR / _FORMAT_PROMPT_FILES[report_format]).read_text()
    schema = json.dumps(CreditReport.model_json_schema())
    return f"{_BASE_PROMPT}\n\n{format_prompt}\n\n{_JSON_INSTRUCTIONS.format(schema=schema)}"


def _pdf_content_block(pdf_path: Path) -> dict:
    data = base64.standard_b64encode(pdf_path.read_bytes()).decode()
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}


_SUFFIX_MULTIPLIERS = {
    "thousand": 1_000,
    "k": 1_000,
    "million": 1_000_000,
    "mn": 1_000_000,
    "m": 1_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "b": 1_000_000_000,
}
_AMOUNT_RE = re.compile(
    r"(?P<number>-?[\d,]*\.?\d+)\s*(?P<suffix>thousand|million|billion|mn|bn|[kmb])?",
    re.IGNORECASE,
)


def _parse_amount(raw: str | None) -> float | None:
    """Deterministically parses the numeric value out of a Money.raw string,
    expanding an embedded K/M/B/thousand/million/billion suffix. Used instead
    of trusting the model's own arithmetic for `value` -- see schemas.py's
    Money docstring."""
    if not raw:
        return None
    match = _AMOUNT_RE.search(raw)
    if match is None:
        return None
    try:
        number = float(match.group("number").replace(",", ""))
    except ValueError:
        return None
    before = raw[: match.start()].rstrip()
    after = raw[match.end() :].lstrip()
    if before.endswith("(") and after.startswith(")"):
        # Accounting-style negative, e.g. "$(1,234)" -- not just any raw
        # string that happens to contain parentheses elsewhere, like
        # "4.7 million (US dollars)".
        number = -abs(number)
    suffix = match.group("suffix")
    if suffix:
        number *= _SUFFIX_MULTIPLIERS[suffix.lower()]
    return number


def _recompute_money(money: Money | None, multiplier: float = 1) -> None:
    if money is None or not money.raw:
        return
    parsed = _parse_amount(money.raw)
    if parsed is not None:
        money.value = parsed * multiplier


def _apply_deterministic_values(report: CreditReport) -> CreditReport:
    """Overrides every Money.value with a value computed deterministically
    from Money.raw (plus, for financials rows, that row's unit_multiplier),
    rather than the model's own arithmetic -- confirmed empirically 2026-08-10
    that the model can silently miss a table-wide unit multiplier (e.g. "UNIT:
    CNY 1,000") and be off by 1000x on every figure in that table."""
    for field in (report.credit_limit, report.contract_limit, report.share_capital, report.base_credit_limit):
        _recompute_money(field)
    for year in report.financials:
        multiplier = year.unit_multiplier or 1
        for field in (year.turnover, year.pre_tax_profit, year.net_worth, year.total_assets, year.total_liabilities):
            _recompute_money(field, multiplier)
    return report


def _response_text(response) -> str:
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return text.strip()


def extract_report(client: anthropic.Anthropic, pdf_path: Path, report_format: str) -> tuple[CreditReport, dict]:
    """Returns (report, usage) where usage is {"input_tokens", "output_tokens"}
    summed across every call this took, including retries -- each is a
    separately billed request."""
    messages = [
        {
            "role": "user",
            "content": [
                _pdf_content_block(pdf_path),
                {"type": "text", "text": "Extract the data from this credit report."},
            ],
        }
    ]

    usage = {"input_tokens": 0, "output_tokens": 0}
    last_error: Exception | None = None
    for _ in range(2):
        response = client.messages.create(
            model=config.EXTRACTION_MODEL,
            max_tokens=config.EXTRACTION_MAX_TOKENS,
            temperature=0,
            system=system_prompt(report_format),
            messages=messages,
        )
        usage["input_tokens"] += response.usage.input_tokens
        usage["output_tokens"] += response.usage.output_tokens

        text = _response_text(response)
        try:
            report = CreditReport.model_validate_json(text)
            return _apply_deterministic_values(report), usage
        except pydantic.ValidationError as e:
            last_error = e
            messages = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        "That reply was not valid JSON for the schema. Errors:\n"
                        f"{e}\n\nReturn the corrected JSON object and nothing else."
                    ),
                },
            ]

    raise ValueError(f"Claude did not return schema-valid JSON for {pdf_path.name}: {last_error}")


def extract_all(
    input_dir: Path,
    output_json_dir: Path,
    *,
    log=print,
    force: bool = False,
) -> dict:
    """Extracts every PDF in input_dir, writing one JSON per report.

    A report that fails is recorded and skipped rather than aborting the whole
    batch -- one malformed PDF in a folder of 50 should not throw away the 49
    that worked (and the money already spent on them).

    Reports that already have output JSON are skipped unless force=True, so
    re-running after a partial failure only pays for what is missing.

    Returns {"usage", "succeeded", "skipped", "errors"} where errors is a list
    of {"source_file", "error"} dicts.
    """
    output_json_dir.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic()

    pdf_paths = sorted(input_dir.glob("*.pdf"))
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    succeeded: list[tuple[Path, str, CreditReport]] = []
    errors: list[dict] = []
    skipped = 0

    for i, pdf_path in enumerate(pdf_paths, 1):
        out_path = output_json_dir / f"{pdf_path.stem}.json"
        if out_path.exists() and not force:
            skipped += 1
            log(f"[{i}/{len(pdf_paths)}] {pdf_path.name} -> already extracted, skipping")
            continue

        try:
            report_format = detect.detect_format(pdf_path)
            suffix = "  (!) unrecognized format" if report_format == config.FORMAT_UNKNOWN else ""
            log(f"[{i}/{len(pdf_paths)}] {pdf_path.name} -> {report_format}{suffix}")

            report, usage = extract_report(client, pdf_path, report_format)
            total_usage["input_tokens"] += usage["input_tokens"]
            total_usage["output_tokens"] += usage["output_tokens"]

            payload = {"source_file": pdf_path.name, "format": report_format, **report.model_dump()}
            out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            succeeded.append((pdf_path, report_format, report))
        except Exception as e:  # noqa: BLE001 - one bad report must not kill the batch
            message = f"{type(e).__name__}: {e}"
            errors.append({"source_file": pdf_path.name, "error": message})
            log(f"        FAILED: {message}")

    return {"usage": total_usage, "succeeded": succeeded, "skipped": skipped, "errors": errors}


if __name__ == "__main__":
    input_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "reports"
    output_dir = Path(__file__).parent / "output"
    extract_all(input_dir, output_dir / config.OUTPUT_JSON_DIRNAME)
